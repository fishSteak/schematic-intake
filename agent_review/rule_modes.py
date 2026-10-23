# -*- coding: utf-8 -*-
"""Split rule_ids into Agent Judge vs program self-check."""

from __future__ import annotations

from typing import Any

from kernel.util import ROOT, read_json

_DEFAULT_AGENT = (
    "P11-A01",
    "P11-A02",
    "P11-A03",
    "P11-A04",
    "P11-A05",
    "P11-A06",
    "P11-A07",
    "P12-A01",
    "P12-A02",
    "P12-A03",
    "P12-A04",
    "P12-A05",
    "P12-A06",
    "P12-A07",
    "P12-A08",
    "P21-A01",
)
DEEP_THREE_TURN_RULES = frozenset(_DEFAULT_AGENT)
_NET_RULES = frozenset(
    {"P04-A01", "P04-A03", "P07-A05", "P19-A03", "P21-A01", "P12-A02", "P16-A05"}
)


def load_agent_rule_ids() -> list[str]:
    path = ROOT / "rules" / "agent_rule_ids.json"
    if path.is_file():
        data = read_json(path)
        ids = [str(x) for x in (data.get("agent_rule_ids") or []) if x]
        if ids:
            return ids
    return list(_DEFAULT_AGENT)


def is_deep_three_turn(rule_id: str) -> bool:
    """是否走分析→自检→落卡（及可选 --critique）：以白名单为准（须另有 .analysis.md）。"""
    return str(rule_id) in set(load_agent_rule_ids())


def split_rule_ids(rule_ids: list[str] | None) -> dict[str, list[str]]:
    all_ids = [str(x) for x in (rule_ids or []) if x]
    agent_set = set(load_agent_rule_ids())
    agent = [r for r in all_ids if r in agent_set]
    program = [r for r in all_ids if r not in agent_set]
    return {"agent": agent, "program": program, "all": all_ids}


def _planned_for_rules(
    kg: dict[str, Any],
    by_rule: dict[str, list[str]],
) -> tuple[list[dict[str, str]], dict[str, int]]:
    object_keys: set[str] = set()
    net_keys: set[str] = set()
    for rid, objs in by_rule.items():
        if rid in _NET_RULES:
            net_keys.update(objs)
        else:
            object_keys.update(objs)
    comps = kg.get("components") or {}
    mpn_keys: set[str] = set()
    for ref in object_keys:
        c = comps.get(ref) or {}
        mpn = (c.get("mpn") or "").strip()
        if mpn and mpn.upper() not in {"TBD", "CAP", "RES", "NC", "-"}:
            mpn_keys.add(mpn)
        for p in c.get("pins") or []:
            n = p.get("net") if isinstance(p, dict) else None
            if n:
                net_keys.add(str(n))
    planned = (
        [{"kind": "object", "key": k} for k in sorted(object_keys)]
        + [{"kind": "mpn", "key": k} for k in sorted(mpn_keys)]
        + [{"kind": "net", "key": k} for k in sorted(net_keys)]
    )
    stats = {
        "objects": len(object_keys),
        "mpns": len(mpn_keys),
        "nets": len(net_keys),
        "judge_pairs": sum(len(v) for v in by_rule.values()),
        "rules": len(by_rule),
    }
    return planned, stats


def filter_enumeration_for_agent(
    enumeration: dict[str, Any],
    kg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy of enumeration limited to Agent rule_ids (for context/judge)."""
    modes = split_rule_ids(list(enumeration.get("rule_ids") or []))
    agent_ids = modes["agent"]
    by_rule_full = enumeration.get("by_rule") or {}
    filtered_by = {rid: list(by_rule_full.get(rid) or []) for rid in agent_ids}
    planned, st = _planned_for_rules(kg or {}, filtered_by)
    out = dict(enumeration)
    out["rule_ids"] = agent_ids
    out["by_rule"] = filtered_by
    out["planned_context"] = planned
    out["agent_rule_ids"] = agent_ids
    out["program_rule_ids"] = modes["program"]
    out["stats"] = st
    # 保留整项不适用原因（如 P12-A03 缺 Tamb），供 aggregate 落卡
    if enumeration.get("rule_na_reasons"):
        out["rule_na_reasons"] = dict(enumeration["rule_na_reasons"])
    if enumeration.get("rule_gated"):
        out["rule_gated"] = dict(enumeration["rule_gated"])
    return out
