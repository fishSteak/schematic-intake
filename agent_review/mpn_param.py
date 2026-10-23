# -*- coding: utf-8 -*-
"""通用料号额定字段 Agent 查参（按 field 区分，电容 Vr 仍可用 mpn_vr）。"""

from __future__ import annotations

from agent_review.llm_auxiliary import run_jobs

import json
import re
from pathlib import Path
from typing import Any

from kernel.pintable import safe_mpn_dir
from kernel.rated_hunt import is_placeholder_mpn
from kernel.rated_registry import FIELD_META
from kernel.util import ROOT, out_dir, write_json

_FIELD_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def field_safe(field: str) -> str:
    return _FIELD_SAFE.sub("_", str(field or "")).strip("_")[:80] or "field"


def agent_param_cache_path(board: Path, mpn: str, field: str) -> Path:
    return out_dir(board) / "cache" / "mpn" / safe_mpn_dir(mpn) / f"agent_{field_safe(field)}.json"


def read_agent_param_cache(board: Path, mpn: str, field: str) -> dict[str, Any] | None:
    p = agent_param_cache_path(board, mpn, field)
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    return doc if isinstance(doc, dict) else None


def write_agent_param_cache(board: Path, mpn: str, field: str, rec: dict[str, Any]) -> Path:
    p = agent_param_cache_path(board, mpn, field)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(rec)
    payload.setdefault("mpn", mpn)
    payload.setdefault("field", field)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def _validate(doc: dict[str, Any], mpn: str, field: str) -> dict[str, Any]:
    meta = FIELD_META.get(field) or {}
    out: dict[str, Any] = {
        "mpn": mpn,
        "field": field,
        "unit": str(doc.get("unit") or meta.get("unit") or ""),
    }
    raw = doc.get("value")
    if raw is None and doc.get("value_text"):
        out["value"] = None
        out["value_text"] = str(doc.get("value_text"))
        out["cite"] = str(doc.get("cite") or "").strip()
        out["confidence"] = str(doc.get("confidence") or "low").lower()
        return out
    if raw is None:
        out["value"] = None
        out["cite"] = str(doc.get("cite") or "").strip()
        out["confidence"] = "low"
        out["error"] = "null_value"
        return out
    try:
        v = float(raw)
    except (TypeError, ValueError):
        out["value"] = None
        out["value_text"] = str(raw)
        out["cite"] = str(doc.get("cite") or "").strip()
        out["confidence"] = str(doc.get("confidence") or "low").lower()
        return out
    cite = str(doc.get("cite") or "").strip()
    conf = str(doc.get("confidence") or "").strip().lower()
    if conf not in {"high", "low"}:
        conf = "low"
    if not cite:
        conf = "low"
    out["value"] = v
    out["cite"] = cite
    out["confidence"] = conf
    return out


def agent_may_seal(rec: dict[str, Any] | None) -> bool:
    if not isinstance(rec, dict) or rec.get("value") is None:
        return False
    return bool(str(rec.get("cite") or "").strip()) and str(rec.get("confidence") or "").lower() == "high"


def prepare_mpn_param_jobs(
    board: Path, pairs: list[tuple[str, str]]
) -> dict[str, Any]:
    board = Path(board).resolve()
    o = out_dir(board)
    jobs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for mpn, field in pairs:
        m = (mpn or "").strip()
        f = (field or "").strip()
        if not m or not f or is_placeholder_mpn(m) or (m, f) in seen:
            continue
        seen.add((m, f))
        cached = read_agent_param_cache(board, m, f)
        # 已有有效值则跳过；仅 agent 未落盘/坏 JSON 允许重试
        if cached is not None:
            if cached.get("value") is not None or cached.get("value_text"):
                continue
            if str(cached.get("error") or "") not in {"agent_did_not_write", "invalid_json"}:
                continue
        meta = FIELD_META.get(f) or {}
        out_path = agent_param_cache_path(board, m, f)
        jobs.append(
            {
                "job_id": f"mpn_param:{safe_mpn_dir(m)}:{field_safe(f)}",
                "mpn": m,
                "field": f,
                "field_zh": meta.get("zh") or f,
                "unit": meta.get("unit") or "",
                "out_path": str(out_path.relative_to(board)).replace("\\", "/"),
            }
        )
    doc = {"board": str(board), "jobs": jobs}
    write_json(o / "mpn_param_jobs.json", doc)
    return doc


def run_mpn_param_jobs(
    board: Path,
    pairs: list[tuple[str, str]],
    *,
    concurrency: int = 4,
    model: str | None = None,
) -> dict[str, Any]:
    board = Path(board).resolve()
    prepared = prepare_mpn_param_jobs(board, pairs)
    jobs = list(prepared.get("jobs") or [])
    if not jobs:
        return {"ok": True, "jobs": 0, "skipped": True, "reason": "all_cached_or_empty"}

    result = run_jobs(board, "mpn_param", jobs, concurrency=concurrency, model=model)
    ok_n = bad_n = 0
    for j in jobs:
        mpn = str(j.get("mpn") or "")
        field = str(j.get("field") or "")
        p = agent_param_cache_path(board, mpn, field)
        if not p.is_file():
            write_agent_param_cache(
                board,
                mpn,
                field,
                {"value": None, "cite": "", "confidence": "low", "error": "agent_did_not_write"},
            )
            bad_n += 1
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            write_agent_param_cache(
                board,
                mpn,
                field,
                {"value": None, "cite": "", "confidence": "low", "error": "invalid_json"},
            )
            bad_n += 1
            continue
        rec = _validate(raw if isinstance(raw, dict) else {}, mpn, field)
        write_agent_param_cache(board, mpn, field, rec)
        if rec.get("value") is not None or rec.get("value_text"):
            ok_n += 1
        else:
            bad_n += 1
    return {
        "ok": result["ok"],
        "returncode": result["returncode"],
        "jobs": len(jobs),
        "ok_n": ok_n,
        "bad_n": bad_n,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }
