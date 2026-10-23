# -*- coding: utf-8 -*-
"""R4 覆盖验收 + 注入未建模事件不得升绿。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from kernel.util import ROOT, out_dir, read_json, write_json

BOARD = ROOT / "boards" / "0918全量"
# 未上电不参评；R4 须覆盖钳位 / 有源拉低 / 外接接地
REQUIRED = {"clamp_work", "switch_pull", "port_gnd"}


def _case_class(cid: str) -> str:
    s = str(cid or "")
    return s.split("::", 1)[1] if "::" in s else s


def main() -> int:
    os.environ["BOARD_PATHS_SKIP_AGENT"] = "1"
    board = BOARD
    o = out_dir(board)
    kg = read_json(o / "board_kg.json")

    from agent_review.p11_pipeline import run_calc, run_cases
    from agent_review.stages.judge import _p11_dry_run_card

    print("RUN\tcases+calc (assist fallback, skip Agent)")
    run_cases(board, kg, skip_path_agent=True)
    run_calc(board, kg)

    idx = read_json(o / "board_paths" / "index.json")
    r4_cases = read_json(o / "operating_cases" / "P11-A01" / "R4.json")
    r4_stress = read_json(o / "program_stress" / "P11-A01" / "R4.json")

    # 1) 关键工作工况在总表与投影中存在（不含未上电）
    index_classes: set[str] = set()
    for p in idx.get("paths") or []:
        refs = [str(r) for r in (p.get("refs") or [])]
        if "R4" not in refs:
            continue
        for ev in p.get("events") or []:
            if ev.get("status") == "rejected":
                continue
            cls = _case_class(str(ev.get("event_id") or ""))
            if cls == "supply_off":
                continue
            index_classes.add(cls)
    proj_classes = {
        _case_class(str(c.get("case_id") or ""))
        for c in (r4_cases.get("cases") or [])
        if _case_class(str(c.get("case_id") or "")) != "supply_off"
    }
    miss_idx = REQUIRED - index_classes
    miss_proj = REQUIRED - proj_classes
    if miss_idx or miss_proj:
        print(f"FAIL\tmissing classes index={miss_idx} proj={miss_proj}")
        return 2
    print(f"OK\tR4 work classes present index={sorted(index_classes)} proj={sorted(proj_classes)}")

    # 2) closed 态度数量级：钳位 ~80 mW，拉低/接地 270–300 mW
    by_cls = {}
    for s in r4_stress.get("states") or []:
        if s.get("status") == "unmapped":
            continue
        by_cls[_case_class(str(s.get("case_id") or ""))] = s
    clamp_p = by_cls.get("clamp_work", {}).get("P")
    pull_p = by_cls.get("switch_pull", {}).get("P")
    port_p = by_cls.get("port_gnd", {}).get("P")
    if clamp_p is None or not (0.06 <= float(clamp_p) <= 0.11):
        print(f"FAIL\tclamp_work P={clamp_p} expect ~80mW")
        return 2
    worst = max(float(x) for x in (pull_p, port_p) if x is not None)
    if not (0.25 <= worst <= 0.35):
        print(f"FAIL\tworst pull/port P={worst} expect 270–300mW band")
        return 2
    print(f"OK\tclosed magnitudes clamp={float(clamp_p)*1000:.1f}mW worst={worst*1000:.1f}mW")

    # 3) 注入可核对但程序不会的事件 → unmapped 且不得升绿
    inject_id = "injected_unmapped_event"
    path0 = None
    for p in idx.get("paths") or []:
        if "R4" in [str(r) for r in (p.get("refs") or [])]:
            path0 = p
            break
    if not path0:
        print("FAIL\tno sealed path containing R4")
        return 2
    path0.setdefault("events", []).append(
        {
            "event_id": inject_id,
            "name_zh": "注入未建模事件",
            "what_changes": "可核对但程序无配方",
            "class_hint": "other",
            "status": "unmapped",
            "stuck": "程序未建模该事件类型；缺可闭合应力模型",
        }
    )
    # recount
    from agent_review.board_paths.project import index_status_counts

    idx["meta"] = {**(idx.get("meta") or {}), "status_counts": index_status_counts(idx), "unmapped_preserved": True}
    write_json(o / "board_paths" / "index.json", idx)

    from agent_review.board_paths.project import project_device_cases
    from agent_review.board_paths.run import _to_operating_doc

    proj = project_device_cases(idx, ref="R4")
    doc = _to_operating_doc("R4", proj, idx)
    write_json(o / "operating_cases" / "P11-A01" / "R4.json", doc)
    # update bundle
    bundle = read_json(o / "operating_cases" / "_bundle.json")
    bundle.setdefault("P11-A01", {})["R4"] = doc
    write_json(o / "operating_cases" / "_bundle.json", bundle)

    run_calc(board, kg)
    r4_stress2 = read_json(o / "program_stress" / "P11-A01" / "R4.json")
    unmapped_states = [
        s
        for s in (r4_stress2.get("states") or [])
        if s.get("status") == "unmapped" or inject_id in str(s.get("case_id") or "")
    ]
    if not unmapped_states:
        print("FAIL\tinjected event not present as unmapped in stress")
        return 2
    print(f"OK\tinjected unmapped visible n={len(unmapped_states)}")

    card = _p11_dry_run_card(board, "P11-A01", "R4", kg, {})
    if card.get("verdict_zh") == "是":
        print(f"FAIL\tverdict green with unmapped: {card.get('verdict_zh')}")
        return 2
    from agent_review.validate_job import validate_judge_card

    # forge a green card → must fail validation
    forged = dict(card)
    forged["verdict_zh"] = "是"
    forged["evidence_refs"] = [{"path": "x", "note": "forge"}]
    forged["states"] = [
        {"case_id": c.get("case_id"), "name_zh": c.get("name_zh")} for c in (doc.get("cases") or [])
    ]
    errs = validate_judge_card(forged, rule_id="P11-A01", obj="R4", board=board)
    if not any("unmapped" in e or "升绿" in e for e in errs):
        print(f"FAIL\tvalidate did not block green: {errs}")
        return 2
    print(f"OK\tno green: verdict={card.get('verdict_zh')} validate_blocks={errs[:2]}")

    # gate check
    from agent_review.delivery_gate import run_gate

    # write forged green card to trigger gate fail path, then remove
    from agent_review.batching import safe_object_name

    card_p = o / "agent_jobs" / "P11-A01" / f"{safe_object_name('R4')}.json"
    card_p.parent.mkdir(parents=True, exist_ok=True)
    write_json(card_p, forged)
    enum = {"rule_ids": ["P11-A01"], "by_rule": {"P11-A01": ["R4"]}, "agent_rule_ids": ["P11-A01"]}
    # minimal gate pieces may fail materials — only assert board_paths_unmapped check
    gate = run_gate(o, enum, allow_failed=True)
    bp_check = next((c for c in gate.get("checks") or [] if c.get("id") == "board_paths_unmapped"), {})
    if bp_check.get("ok") is not False and not bp_check.get("green_with_unmapped"):
        # forged green should make ok False
        print(f"FAIL\tgate board_paths_unmapped did not catch green: {bp_check}")
        return 2
    print(f"OK\tgate catches green_with_unmapped={bp_check.get('green_with_unmapped')}")

    # restore non-green card
    write_json(card_p, card)
    print("PASS\tboard_paths R4 accept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
