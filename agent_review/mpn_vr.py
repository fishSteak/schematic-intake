# -*- coding: utf-8 -*-
"""电容 Vr 查参：应用直接调用 LLM，验证后写入材料 cache。"""

from __future__ import annotations

from agent_review.llm_auxiliary import run_jobs

import json
from pathlib import Path
from typing import Any

from kernel.pintable import safe_mpn_dir
from kernel.rated_hunt import (
    agent_vr_cache_path,
    is_placeholder_mpn,
    read_agent_vr_cache,
    write_agent_vr_cache,
)
from kernel.util import ROOT, out_dir, write_json


def _validate_agent_rec(doc: dict[str, Any], mpn: str) -> dict[str, Any] | None:
    if not isinstance(doc, dict):
        return None
    out: dict[str, Any] = {"mpn": mpn}
    raw_v = doc.get("value_v")
    if raw_v is None:
        out["value_v"] = None
        out["cite"] = str(doc.get("cite") or "").strip()
        out["confidence"] = str(doc.get("confidence") or "low").strip().lower() or "low"
        out["error"] = "null_value"
        return out
    try:
        v = float(raw_v)
    except (TypeError, ValueError):
        return {
            "mpn": mpn,
            "value_v": None,
            "cite": "",
            "confidence": "low",
            "error": "bad_value_v",
        }
    if not (0.5 <= v <= 2000):
        return {
            "mpn": mpn,
            "value_v": None,
            "cite": "",
            "confidence": "low",
            "error": "value_out_of_range",
        }
    cite = str(doc.get("cite") or "").strip()
    conf = str(doc.get("confidence") or "").strip().lower()
    if conf not in {"high", "low"}:
        conf = "low"
    if not cite:
        conf = "low"
    out["value_v"] = v
    out["cite"] = cite
    out["confidence"] = conf
    return out


def prepare_mpn_vr_jobs(board: Path, mpns: list[str]) -> dict[str, Any]:
    """写出 mpn_vr_jobs.json；已有有效缓存的跳过。"""
    board = Path(board).resolve()
    o = out_dir(board)
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mpn in mpns:
        m = (mpn or "").strip()
        if not m or is_placeholder_mpn(m) or m in seen:
            continue
        seen.add(m)
        cached = read_agent_vr_cache(board, m)
        if cached is not None and (
            cached.get("value_v") is not None or cached.get("error")
        ) and cached.get("error") not in {"agent_did_not_write", "invalid_json"}:
            continue
        safe = safe_mpn_dir(m)
        out_path = o / "cache" / "mpn" / safe / "vr_agent.json"
        jobs.append(
            {
                "job_id": f"mpn_vr:{safe}",
                "mpn": m,
                "out_path": str(out_path.relative_to(board)).replace("\\", "/"),
            }
        )
    doc = {"board": str(board), "jobs": jobs}
    write_json(o / "mpn_vr_jobs.json", doc)
    return doc


def run_mpn_vr_jobs(
    board: Path,
    mpns: list[str],
    *,
    concurrency: int = 4,
    model: str | None = None,
) -> dict[str, Any]:
    """准备 jobs → LLM 工具循环 → 校验并规范化缓存文件。"""
    board = Path(board).resolve()
    prepared = prepare_mpn_vr_jobs(board, mpns)
    jobs = list(prepared.get("jobs") or [])
    if not jobs:
        return {"ok": True, "jobs": 0, "skipped": True, "reason": "all_cached_or_empty"}

    result = run_jobs(board, "mpn_vr", jobs, concurrency=concurrency, model=model)
    ok_n = 0
    bad_n = 0
    for j in jobs:
        mpn = str(j.get("mpn") or "")
        p = agent_vr_cache_path(board, mpn)
        if not p.is_file():
            write_agent_vr_cache(
                board,
                mpn,
                {
                    "mpn": mpn,
                    "value_v": None,
                    "cite": "",
                    "confidence": "low",
                    "error": "agent_did_not_write",
                },
            )
            bad_n += 1
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            write_agent_vr_cache(
                board,
                mpn,
                {
                    "mpn": mpn,
                    "value_v": None,
                    "cite": "",
                    "confidence": "low",
                    "error": "invalid_json",
                },
            )
            bad_n += 1
            continue
        rec = _validate_agent_rec(raw if isinstance(raw, dict) else {}, mpn)
        if rec is None:
            bad_n += 1
            continue
        write_agent_vr_cache(board, mpn, rec)
        if rec.get("value_v") is not None:
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


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Batch Agent MPN Vr lookup")
    ap.add_argument("--board", required=True)
    ap.add_argument("--mpn", action="append", default=[])
    ap.add_argument("--from-kg", action="store_true", help="从 board_kg 收集缺密封 Vr 的电容料号")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args(argv)
    board = Path(args.board)
    if not board.is_absolute():
        board = (ROOT / board).resolve()
    mpns = list(args.mpn or [])
    if args.from_kg:
        kg_path = out_dir(board) / "board_kg.json"
        kg = json.loads(kg_path.read_text(encoding="utf-8-sig"))
        for ref, c in (kg.get("components") or {}).items():
            if not isinstance(c, dict) or str(c.get("type") or "") != "capacitor":
                continue
            params = ((c.get("pintable") or {}).get("params") or {})
            if params.get("rated_voltage_v") is not None:
                continue
            m = str(c.get("mpn") or "").strip()
            if m and not is_placeholder_mpn(m):
                mpns.append(m)
    result = run_mpn_vr_jobs(board, mpns, concurrency=args.concurrency)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
