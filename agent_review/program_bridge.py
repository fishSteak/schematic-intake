# -*- coding: utf-8 -*-
"""Run local program checkers in a subprocess."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from agent_review.batching import safe_object_name
from kernel.util import ROOT, out_dir, read_json, write_json

_WORKER = r'''
import json, sys
from pathlib import Path

kg_path = Path(sys.argv[1])
rule_ids = json.loads(sys.argv[2])
out_path = Path(sys.argv[3])

from kernel.util import read_json
from kernel.checkers.all15 import REGISTRY

kg = read_json(kg_path)
results = []
for rid in rule_ids:
    fn = REGISTRY.get(rid)
    if fn is None:
        results.append({
            "rule_id": rid,
            "verdict_zh": "INSUFFICIENT",
            "title": rid,
            "object_cards": [],
            "violations": ["检查函数未实现"],
            "method": "program_missing",
        })
        continue
    results.append(fn(kg).to_dict())

out_path.write_text(json.dumps({"results": results}, ensure_ascii=False), encoding="utf-8")
'''


def review_root() -> Path:
    """Return the self-contained intake runtime root."""
    return ROOT


def _loc_one_liner(card: dict[str, Any], obj: str) -> str:
    loc = card.get("location")
    if isinstance(loc, dict):
        page = loc.get("page_name") or loc.get("pdf_page") or ""
        if page:
            return f"{page} · {obj}"
    if isinstance(loc, str) and loc.strip():
        return loc.strip()
    return str(obj)


def _normalize_verdict(vz: Any) -> str:
    s = str(vz or "")
    if s in {"是", "否", "待确认", "不适用"}:
        return s
    if s.startswith("INSUFFICIENT") or s == "INSUFFICIENT":
        return "待确认"
    return "待确认"


def program_card_to_judge_card(card: dict[str, Any], *, rule_id: str) -> dict[str, Any]:
    obj = str(card.get("object") or card.get("ref") or "")
    vz = _normalize_verdict(card.get("verdict_zh"))
    phenomenon = str(card.get("phenomenon") or "").strip() or f"{obj} 程序检查"
    calc = str(card.get("calc") or "").strip() or "见程序推导"
    stuck = str(card.get("stuck") or "").strip() or "无"
    conclusion = str(card.get("conclusion") or "").strip() or vz
    summary = str(card.get("summary") or "").strip() or conclusion
    action = ""
    sol = card.get("solution")
    if isinstance(sol, dict):
        action = str(sol.get("action") or sol.get("fix") or "").strip()
    if not action:
        fix = card.get("fix")
        if isinstance(fix, list) and fix:
            action = "；".join(str(x) for x in fix)
        elif isinstance(fix, str):
            action = fix
    if vz in {"否", "待确认"} and not action:
        action = "按判定说明整改或补齐资料后复测"
    steps = []
    for s in card.get("steps") or []:
        if isinstance(s, dict):
            bit = s.get("observation") or s.get("action") or ""
            if bit:
                steps.append(str(bit))
    if not steps:
        steps = [phenomenon, calc[:200] if calc else "程序比较", conclusion]
    refs = card.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        refs = [{"path": "review_out/program_candidate.json", "note": "program"}]
    out = {
        "rule_id": rule_id,
        "object": obj,
        "verdict_zh": vz,
        "location_one_liner": _loc_one_liner(card, obj),
        "reasoning_steps": steps[:12],
        "summary": summary,
        "action": action or "无需改板",
        "phenomenon": phenomenon,
        "calc": calc,
        "stuck": stuck,
        "conclusion": conclusion,
        "evidence_refs": refs,
        "context_keys": [],
        "context_hashes": {},
        "electrical": card.get("electrical") or {},
        "meta": {
            "method": "program",
            "block_class": card.get("block_class"),
            "recipe_id": card.get("recipe_id"),
            "source": "schematic-intake.checkers",
        },
        # keep program extras for expert SPA / workbench
        "location": card.get("location"),
        "formula": card.get("formula"),
        "solution": card.get("solution"),
        "identity": card.get("identity"),
        "block_class": card.get("block_class"),
        "recipe_id": card.get("recipe_id"),
    }
    return out


def run_program_checkers(
    board: Path,
    kg: dict[str, Any],
    program_rule_ids: list[str],
) -> dict[str, Any]:
    """Execute program checkers; write program_candidate.json + agent_jobs cards."""
    o = out_dir(board)
    if not program_rule_ids:
        empty = {
            "board": board.name,
            "rule_ids": [],
            "summary": {"是": 0, "否": 0, "不适用": 0, "INSUFFICIENT": 0},
            "results": [],
            "role": "program_final",
            "note": "无程序检查项",
        }
        write_json(o / "program_candidate.json", empty)
        return empty

    rev = review_root()
    kg_path = o / "board_kg.json"
    if not kg_path.is_file():
        write_json(kg_path, kg)

    with tempfile.TemporaryDirectory(prefix="prog_chk_") as td:
        raw_out = Path(td) / "raw.json"
        cmd = [
            sys.executable,
            "-c",
            _WORKER,
            str(kg_path),
            json.dumps(list(program_rule_ids), ensure_ascii=False),
            str(raw_out),
        ]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(rev)
        proc = subprocess.run(
            cmd,
            cwd=str(rev),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"program checkers failed code={proc.returncode}\n"
                f"stderr={proc.stderr[-2000:]}\nstdout={proc.stdout[-1000:]}"
            )
        raw = json.loads(raw_out.read_text(encoding="utf-8"))

    results = list(raw.get("results") or [])
    summary = {
        "是": sum(1 for r in results if r.get("verdict_zh") == "是"),
        "否": sum(1 for r in results if r.get("verdict_zh") == "否"),
        "不适用": sum(1 for r in results if r.get("verdict_zh") == "不适用"),
        "INSUFFICIENT": sum(
            1 for r in results if str(r.get("verdict_zh") or "").startswith("INSUFFICIENT")
        ),
    }
    data = {
        "board": board.name,
        "rule_ids": list(program_rule_ids),
        "summary": summary,
        "results": results,
        "role": "program_final",
        "note": "程序自评；Agent 白名单外检查项由此产出",
        "review_root": str(rev),
    }
    write_json(o / "program_candidate.json", data)

    # Materialize judge-compatible cards for aggregate/gate
    import shutil

    # 写卡前清空本批程序规则目录，避免旧 Agent/HTML 残留
    for rid in program_rule_ids:
        rule_dir = o / "agent_jobs" / str(rid)
        if rule_dir.is_dir():
            shutil.rmtree(rule_dir)

    by_rule_objects: dict[str, list[str]] = {}
    for r in results:
        rid = str(r.get("rule_id") or "")
        cards_in = list(r.get("object_cards") or [])
        objs: list[str] = []
        if not cards_in:
            # empty → one 不适用 placeholder if rule truly has no objects
            if str(r.get("verdict_zh")) == "不适用":
                card = program_card_to_judge_card(
                    {
                        "object": "(none)",
                        "verdict_zh": "不适用",
                        "phenomenon": "本项无适用对象",
                        "calc": "无",
                        "stuck": "无",
                        "conclusion": "不适用",
                        "summary": "本项无适用对象",
                    },
                    rule_id=rid,
                )
                dest = o / "agent_jobs" / rid / f"{safe_object_name('(none)')}.json"
                write_json(dest, card)
                objs = ["(none)"]
            by_rule_objects[rid] = objs
            continue
        for c in cards_in:
            jc = program_card_to_judge_card(c, rule_id=rid)
            obj = str(jc.get("object") or "")
            if not obj:
                continue
            write_json(o / "agent_jobs" / rid / f"{safe_object_name(obj)}.json", jc)
            objs.append(obj)
        by_rule_objects[rid] = objs

    data["by_rule_objects"] = by_rule_objects
    write_json(o / "program_candidate.json", data)
    return data


def sync_enumeration_objects(
    enumeration: dict[str, Any],
    program_data: dict[str, Any],
) -> dict[str, Any]:
    """Align by_rule object lists with program-emitted cards for program rules."""
    by_rule = dict(enumeration.get("by_rule") or {})
    for rid, objs in (program_data.get("by_rule_objects") or {}).items():
        by_rule[rid] = list(objs)
    out = dict(enumeration)
    out["by_rule"] = by_rule
    st = dict(enumeration.get("stats") or {})
    st["judge_pairs"] = sum(len(v) for v in by_rule.values())
    out["stats"] = st
    return out
