# -*- coding: utf-8 -*-
"""Delivery gate: sealed context (Agent rules) + all judge cards + report present."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_review.batching import safe_object_name
from agent_review.context_store import ContextStore
from agent_review.rule_modes import load_agent_rule_ids
from agent_review.validate_job import validate_judge_card
from kernel.util import read_json, write_json


def run_gate(
    out_dir: Path,
    enumeration: dict[str, Any],
    *,
    allow_failed: bool = False,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    passed = True

    gate_failed = out_dir / "gate_failed.json"
    if gate_failed.is_file():
        checks.append({"id": "materials", "ok": False, "detail": "gate_failed.json exists"})
        passed = False
    else:
        checks.append({"id": "materials", "ok": True})

    agent_ids = set(enumeration.get("agent_rule_ids") or load_agent_rule_ids())
    agent_ids &= set(enumeration.get("rule_ids") or [])
    store = ContextStore(out_dir)
    cov = store.ready_coverage()
    if not agent_ids:
        ctx_ok = True
        checks.append({"id": "context_seal", "ok": True, "detail": {"skipped": True, "reason": "no agent rules"}})
    else:
        pollution = cov.get("pollution") or []
        ctx_ok = bool(cov.get("sealed")) and cov.get("ready") == cov.get("planned") and not pollution
        checks.append({"id": "context_seal", "ok": ctx_ok, "detail": cov})
        if not ctx_ok:
            passed = False

    missing = []
    invalid = []
    valid_cards: dict[tuple[str, str], dict[str, Any]] = {}
    for rid in enumeration.get("rule_ids") or []:
        objs = list((enumeration.get("by_rule") or {}).get(rid) or [])
        if not objs:
            continue
        for obj in objs:
            p = out_dir / "agent_jobs" / rid / f"{safe_object_name(obj)}.json"
            if not p.is_file():
                missing.append(f"{rid}::{obj}")
                continue
            card = read_json(p)
            errs = validate_judge_card(card, rule_id=rid, obj=obj, board=out_dir.parent)
            if errs:
                invalid.append({"id": f"{rid}::{obj}", "errors": errs})
            else:
                valid_cards[(rid, str(obj))] = card

    cards_ok = not missing and not invalid
    if allow_failed and missing:
        cards_ok = not invalid
    checks.append(
        {
            "id": "judge_cards",
            "ok": cards_ok,
            "missing_n": len(missing),
            "invalid_n": len(invalid),
            "missing_sample": missing[:20],
            "invalid_sample": invalid[:10],
        }
    )
    if not cards_ok:
        passed = False

    evidence_ok = any((out_dir / "evidence").glob("*.json")) and (out_dir / "report_data.json").is_file()
    checks.append({"id": "engineer_report", "ok": evidence_ok, "detail": "evidence packs for report_ui"})
    if not evidence_ok:
        passed = False

    verdicts_ok = (out_dir / "verdicts.json").is_file()
    checks.append({"id": "verdicts", "ok": verdicts_ok})
    if not verdicts_ok:
        passed = False

    # board_paths：允许 unmapped_n>0，但必须可见；禁止静默删除
    bp_index = out_dir / "board_paths" / "index.json"
    if bp_index.is_file():
        idx = read_json(bp_index)
        counts = (idx.get("meta") or {}).get("status_counts") or {}
        unmapped_n = int(counts.get("unmapped") or 0)
        preserved = bool((idx.get("meta") or {}).get("unmapped_preserved", True))
        # 扫描密封事件
        sealed_unmapped = 0
        for p in idx.get("paths") or []:
            for ev in p.get("events") or []:
                if isinstance(ev, dict) and ev.get("status") == "unmapped":
                    sealed_unmapped += 1
        ok_bp = preserved and sealed_unmapped == unmapped_n
        # 若卡上有升绿但对应对象含 unmapped → 失败
        green_with_unmapped = []
        validated_envelope_cards = []
        if unmapped_n > 0:
            for rid in ("P11-A01",):
                for obj_p in (out_dir / "operating_cases" / rid).glob("*.json"):
                    if obj_p.name.startswith("_"):
                        continue
                    cdoc = read_json(obj_p)
                    if not any(
                        isinstance(c, dict) and c.get("status") == "unmapped"
                        for c in (cdoc.get("cases") or [])
                    ):
                        continue
                    ref = str(cdoc.get("ref") or obj_p.stem)
                    card_p = out_dir / "agent_jobs" / rid / f"{safe_object_name(ref)}.json"
                    if card_p.is_file():
                        card = read_json(card_p)
                        if card.get("verdict_zh") != "是":
                            continue
                        from agent_review.analysis_contract import has_direct_analysis
                        main = card.get("main_analysis") or {}
                        # A validated independent envelope can cover operating
                        # paths whose exact working point remains unresolved.
                        # Keep those source gaps and their counts unchanged.
                        if (valid_cards.get((rid, ref)) == card
                                and card.get("rule_id") == rid and card.get("object") == ref
                                and has_direct_analysis(card)
                                and (card.get("decision_basis") or {}).get("kind") == "envelope_upper_bound"
                                and main.get("kind") == "cross_case_bound"
                                and main.get("proof_status") == "verified"):
                            validated_envelope_cards.append(f"{rid}::{ref}")
                        else:
                            green_with_unmapped.append(f"{rid}::{ref}")
        if green_with_unmapped:
            ok_bp = False
        checks.append(
            {
                "id": "board_paths_unmapped",
                "ok": ok_bp,
                "unmapped_n": unmapped_n,
                "sealed_unmapped": sealed_unmapped,
                "preserved": preserved,
                "green_with_unmapped": green_with_unmapped[:10],
                "validated_envelope_cards": validated_envelope_cards[:10],
            }
        )
        if not ok_bp:
            passed = False
    else:
        checks.append({"id": "board_paths_unmapped", "ok": True, "detail": "no index yet"})

    result = {
        "passed": passed,
        "checks": checks,
        "allow_failed": allow_failed,
        "agent_rule_ids": sorted(agent_ids),
    }
    write_json(out_dir / "agent_review_gate.json", result)
    return result
