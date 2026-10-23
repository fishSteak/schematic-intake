# -*- coding: utf-8 -*-
"""L4 critic pack: validate reason[] and overlay challenge (demote only)."""

from __future__ import annotations

from typing import Any

from kernel.facts_patch import SOURCE_KINDS

CRITIC_PROMPT = """你是配方匹配批评员，不是判定员。
只读已闭合卡：recipe_id、该配方公开的 match 条件、subgraph 引脚网。
问：match 是否仍成立？
- 成立：action=skip（不改词、不改数）
- 不成立：action=challenge，写出 solver_assumed vs netlist_shows，pins 必须能在 board_kg 对上
- 子图出现配方表里没有的可命名结构：action=new_recipe_candidate（只给 id 建议与 match 草稿，不算电压）
禁止：产品是/否、口头电压、凡运放/凡 CMOS 一律 challenge、整板故事。
"""

_CLOSED = {"是", "否"}


def _stamp_challenge(card: dict[str, Any], stuck: str) -> None:
    """Demote a closed card. Rewrite 整卡 so 公式代入 matches 待确认; leave 本态算式."""
    card["verdict_zh"] = "待确认"
    card["block_class"] = "rule_gap"
    card["stuck"] = stuck
    card["conclusion"] = f"待确认 · {stuck}"
    card["summary"] = card["conclusion"]
    sol = card.get("solution")
    if not isinstance(sol, dict):
        return
    gates = [g for g in (sol.get("gates") or []) if isinstance(g, dict)]
    detail = stuck[3:].lstrip("：: ").strip() if stuck.startswith("批评") else stuck
    gates.append({"name": "批评", "ok": False, "detail": detail, "verdict_zh": "待确认"})
    sol["gates"] = gates
    sol["verdict_zh"] = "待确认"
    sol["aggregate_line"] = "本卡：批评员打回 → 待确认"
    from kernel.solution import render_solution

    body = render_solution(sol)
    card["formula"] = body
    card["calc"] = body


def extract_reason_entries(pack: dict[str, Any]) -> list[dict[str, Any]]:
    raw = pack.get("reason") or pack.get("reasons") or pack.get("critic") or []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def _pin_net_in_kg(kg: dict[str, Any], ref: str, pin: str, net: str) -> bool:
    c = (kg.get("components") or {}).get(ref) or {}
    want_pin = str(pin)
    want_net = str(net)
    for p in c.get("pins") or []:
        if not isinstance(p, dict):
            continue
        pp = str(p.get("pin") or p.get("name") or "")
        if "." in pp:
            pp = pp.split(".")[-1]
        nn = str(p.get("net") or "")
        if pp == want_pin and nn == want_net:
            return True
    return False


def validate_reason(
    entry: dict[str, Any],
    *,
    kg: dict[str, Any],
    objects: set[str],
) -> tuple[bool, str]:
    obj = str(entry.get("object") or "")
    if not obj or obj not in objects:
        return False, "object_not_in_cards"
    action = str(entry.get("action") or "")
    if action not in {"skip", "challenge", "facts_patch", "new_recipe_candidate"}:
        return False, f"bad_action:{action}"
    src = entry.get("source")
    if action in {"challenge", "facts_patch"}:
        if not isinstance(src, dict) or src.get("kind") not in SOURCE_KINDS:
            return False, "bad_source"
    if action == "challenge":
        mm = entry.get("mismatch")
        if not isinstance(mm, dict):
            return False, "challenge_needs_mismatch"
        if not (mm.get("solver_assumed") and mm.get("netlist_shows")):
            return False, "mismatch_incomplete"
        pins = entry.get("pins") or []
        if not isinstance(pins, list) or not pins:
            return False, "challenge_needs_pins"
        for p in pins:
            if not isinstance(p, dict):
                return False, "pin_not_object"
            if not _pin_net_in_kg(kg, str(p.get("ref") or ""), str(p.get("pin") or ""), str(p.get("net") or "")):
                return False, "pin_net_mismatch_kg"
    return True, "ok"


def overlay_challenges(
    result: dict[str, Any],
    entries: list[dict[str, Any]],
    kg: dict[str, Any],
) -> dict[str, Any]:
    """Demote challenged 是/否 cards to 待确认 after the last checker run. Never promote."""
    cards = result.get("object_cards") or []
    objects = {str(c.get("object") or "") for c in cards if isinstance(c, dict)}
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    demoted: list[str] = []
    candidates: list[dict[str, Any]] = []
    by_obj = {str(c.get("object") or ""): c for c in cards if isinstance(c, dict)}

    for raw in entries:
        ok, reason = validate_reason(raw, kg=kg, objects=objects)
        if not ok:
            rejected.append({"entry": raw, "reason": reason})
            continue
        action = str(raw.get("action") or "")
        obj = str(raw.get("object") or "")
        card = by_obj.get(obj)
        if action == "skip":
            applied.append({"object": obj, "action": "skip"})
            continue
        if action == "facts_patch":
            applied.append({"object": obj, "action": "facts_patch"})
            continue
        if action == "new_recipe_candidate":
            candidates.append(
                {
                    "object": obj,
                    "id": raw.get("id") or raw.get("recipe_id"),
                    "match": raw.get("match"),
                    "note": raw.get("mismatch") or raw.get("note"),
                }
            )
            applied.append({"object": obj, "action": "new_recipe_candidate"})
            continue
        if action == "challenge" and card is not None:
            zh = str(card.get("verdict_zh") or "")
            if zh not in _CLOSED:
                rejected.append({"entry": raw, "reason": "challenge_not_closed"})
                continue
            mm = raw.get("mismatch") or {}
            # 报告只写网表事实；solver_assumed 留在 mismatch 给机读，不堆进 stuck/门文案
            shows = str(mm.get("netlist_shows") or "").strip()
            stuck = f"批评：{shows}" if shows else "批评：配方与网表不符"
            _stamp_challenge(card, stuck)
            demoted.append(obj)
            applied.append({"object": obj, "action": "challenge"})

    fails = [c for c in cards if isinstance(c, dict) and c.get("verdict_zh") == "否"]
    pending = [
        c
        for c in cards
        if isinstance(c, dict)
        and (
            str(c.get("verdict_zh") or "").startswith("INSUFFICIENT")
            or c.get("verdict_zh") == "待确认"
        )
    ]
    if fails:
        result["verdict_zh"] = "否"
        result["violations"] = [
            str(c.get("aggregate_line") or c.get("object") or "") for c in fails
        ]
    elif pending:
        result["verdict_zh"] = "INSUFFICIENT"
        result["violations"] = []
    elif cards:
        result["verdict_zh"] = "是"
        result["violations"] = []

    return {
        "applied": applied,
        "rejected": rejected,
        "demoted": demoted,
        "new_recipe_candidates": candidates,
    }
