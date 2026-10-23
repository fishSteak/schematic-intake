# -*- coding: utf-8 -*-
"""一期 15 项：优先 intent_steps_15；xlsx 仅归档锚。"""

from __future__ import annotations

from typing import Any

from kernel.util import ROOT, read_json

INTENT_PATH = ROOT / "rules" / "intent_steps_15.json"
DETAIL_PATH = ROOT / "rules" / "xlsx_detail_15.json"
PROCESS_SOURCE = "intent_steps_15"


def load_intent_doc() -> dict[str, Any]:
    if not INTENT_PATH.is_file():
        return {}
    return read_json(INTENT_PATH)


def load_intent_map() -> dict[str, dict[str, Any]]:
    data = load_intent_doc()
    return {it["rule_id"]: it for it in data.get("items") or [] if it.get("rule_id")}


def load_detail_map() -> dict[str, dict[str, Any]]:
    """xlsx 原文归档，停止作为执行过程。"""
    if not DETAIL_PATH.is_file():
        return {}
    data = read_json(DETAIL_PATH)
    return {it["rule_id"]: it for it in data.get("items") or [] if it.get("rule_id")}


def playbook_from_intent(
    item: dict[str, Any],
    short_pb: dict[str, Any] | None = None,
    xlsx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    short_pb = short_pb or {}
    xlsx = xlsx or {}
    steps = list(item.get("steps") or [])
    check_steps = [str(s.get("action") or "") for s in steps]
    process_lines = []
    for s in steps:
        n = s.get("n")
        action = s.get("action") or ""
        read = ",".join(s.get("read") or [])
        take = s.get("take") or ""
        compare = s.get("compare") or ""
        produce = s.get("produce") or ""
        process_lines.append(
            f"{n}、{action}。读:{read}。取:{take}。比:{compare}。产物:{produce}"
        )
    verdict = item.get("verdict") or {}
    rule_detail = {
        "intent": item.get("intent") or "",
        "scope_in": item.get("scope_in") or [],
        "scope_out": item.get("scope_out") or [],
        "verdict": verdict,
        "calibers": item.get("calibers") or [],
        "forbidden": item.get("forbidden") or [],
        "program_can": item.get("program_can") or [],
        "agent_must": item.get("agent_must") or [],
        "evidence_ladder": (load_intent_doc() or {}).get("evidence_ladder") or [],
    }
    return {
        "required_materials": "原理图PDF；BOM；网表；嘉立创工程；工况；设计任务书（六件套）",
        "expert_check_method": short_pb.get("expert_check_method") or "",
        "result_format": xlsx.get("result_format") or short_pb.get("result_format") or "",
        "check_process": "\n".join(process_lines),
        "check_steps": check_steps,
        "steps": steps,
        "intent": item.get("intent") or "",
        "calibers": item.get("calibers") or [],
        "forbidden": item.get("forbidden") or [],
        "check_process_playbook_short": "",
        "rule_detail": rule_detail,
        "name_anchor": item.get("name_anchor") or xlsx.get("name") or "",
        "need_datasheet_query": "AI自决",
        "process_source": PROCESS_SOURCE,
        "ai_self_check": False,
    }


def playbook_from_xlsx(item: dict[str, Any], short_pb: dict[str, Any] | None = None) -> dict[str, Any]:
    """兼容旧调用：有 intent 则走 intent。"""
    rid = (item or {}).get("rule_id") or ""
    intent_item = load_intent_map().get(rid)
    if intent_item:
        return playbook_from_intent(intent_item, short_pb, item)
    short_pb = short_pb or {}
    detail = item.get("rule_detail") or {}
    return {
        "required_materials": item.get("required_materials") or short_pb.get("required_materials") or "",
        "expert_check_method": short_pb.get("expert_check_method") or "",
        "result_format": item.get("result_format") or short_pb.get("result_format") or "",
        "check_process": item.get("check_process_xlsx") or short_pb.get("check_process") or "",
        "check_steps": list(item.get("check_steps") or []),
        "check_process_playbook_short": short_pb.get("check_process") or "",
        "rule_detail": detail,
        "need_datasheet_query": "AI自决",
        "process_source": PROCESS_SOURCE,
        "ai_self_check": bool(short_pb.get("ai_self_check")),
    }
