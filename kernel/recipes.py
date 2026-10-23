# -*- coding: utf-8 -*-
"""Named topology recipes for closed 是/否. Unnamed models cannot ship numbers."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from kernel.util import ROOT

_OPAMP_MPN = re.compile(r"^LM358|^LM2904|^LM324|^LM2902", re.I)
_CMOS_MPN = re.compile(
    r"^MC14584|^CD40106|^74HC14|^74HCT14|^74HC04|^74HCT04|^CD4069",
    re.I,
)


@lru_cache(maxsize=1)
def load_topology_recipes() -> dict[str, Any]:
    p = ROOT / "knowledge" / "topology_recipes.json"
    if not p.is_file():
        return {"recipes": []}
    return json.loads(p.read_text(encoding="utf-8-sig"))


def recipe_by_id(rid: str | None) -> dict[str, Any] | None:
    if not rid:
        return None
    for it in load_topology_recipes().get("recipes") or []:
        if isinstance(it, dict) and it.get("id") == rid:
            return it
    return None


def _mpn_of(kg: dict[str, Any], ref: str | None) -> str:
    if not ref:
        return ""
    c = (kg.get("components") or {}).get(ref) or {}
    return str(c.get("mpn") or c.get("value") or "")


def _origin_kinds(stress: dict[str, Any]) -> set[str]:
    kinds: set[str] = set()
    vs = stress.get("v_source") if isinstance(stress.get("v_source"), dict) else {}
    if vs.get("kind"):
        kinds.add(str(vs.get("kind")))
    for c in stress.get("cases") or []:
        if not isinstance(c, dict):
            continue
        for key in ("va_trace", "vb_trace"):
            tr = c.get(key) if isinstance(c.get(key), dict) else {}
            k = tr.get("origin_kind")
            if k:
                kinds.add(str(k))
    return kinds


def _stress_nets(stress: dict[str, Any]) -> list[str]:
    out: list[str] = []
    nd = stress.get("nets") if isinstance(stress.get("nets"), dict) else {}
    for k in ("a", "b"):
        n = nd.get(k)
        if n:
            out.append(str(n))
    for d in stress.get("drivers") or []:
        if isinstance(d, dict) and d.get("net"):
            out.append(str(d.get("net")))
    return out


def _follower_info(kg: dict[str, Any], stress: dict[str, Any]) -> dict[str, str] | None:
    from kernel.resistor_network import is_voltage_follower_net

    comps = kg.get("components") or {}
    for n in _stress_nets(stress):
        info = is_voltage_follower_net(n, comps)
        if info:
            return info
    return None


def _inplus_closed(kg: dict[str, Any], plus_net: str) -> bool:
    from kernel.resistor_network import GROUND_NETS

    if plus_net in GROUND_NETS:
        return True
    rail = ((kg.get("nets") or {}).get(plus_net) or {}).get("rail") or {}
    if rail.get("status") == "closed" and rail.get("value") is not None:
        return True
    return False


def p11_card_recipe(
    stress: dict[str, Any],
    kg: dict[str, Any],
) -> tuple[str | None, list[dict[str, str]], bool]:
    """Return (recipe_id, assumptions, match_ok). match_ok False → cannot 是/否."""
    assumptions: list[dict[str, str]] = []
    for a in stress.get("assumed_zero_drops") or []:
        assumptions.append({"sym": str(a), "val": "0", "cite": "缺手册按0作上界"})

    drive = str(stress.get("drive_kind") or "")
    drivers = [d for d in (stress.get("drivers") or []) if isinstance(d, dict)]
    kinds = {str(d.get("kind") or "") for d in drivers}
    origins = _origin_kinds(stress)

    kind = str(stress.get("stress_kind") or "")
    if kind == "series_clamp":
        return "series_clamp_states", assumptions, True
    if kind == "pin_behavior":
        return "pin_behavior_states", assumptions, True
    if kind == "dc_modes":
        return "pin_behavior_states", assumptions, bool(stress.get("cases"))
    if kind == "resistive_divider":
        if not any(str(d.get("kind") or "") == "oc" for d in drivers):
            return "pure_r_nodal", assumptions, True
        return "divider_states", assumptions, True
    if kind == "shunt_to_gnd":
        return "shunt_to_gnd_states", assumptions, True
    if kind == "series_switch":
        return "series_switch_states", assumptions, True
    if kind == "led_limit":
        return "led_limit_states", assumptions, True
    if kind == "rc_timing":
        return "rc_timing_states", assumptions, True
    if kind == "rail_rc":
        return "rail_rc_states", assumptions, True
    if kind == "opamp_rail":
        return "opamp_rail_envelope", assumptions, bool(stress.get("cases"))
    if kind == "base_bias":
        return "switch_base_drive_envelope", assumptions, bool(stress.get("cases"))
    if kind == "series_socket":
        return "series_socket_open_gnd", assumptions, True
    if kind == "closed_rails":
        return "pure_r_nodal", assumptions, True
    if kind == "follower":
        plus = ""
        vs = stress.get("v_source") if isinstance(stress.get("v_source"), dict) else {}
        plus = str(vs.get("plus") or "")
        match_ok = bool(stress.get("cases")) and _inplus_closed(kg, plus)
        return "opamp_voltage_follower", assumptions, match_ok
    if kind == "base_drive":
        return "switch_base_drive_envelope", assumptions, True
    if kind == "dc_idle":
        return "pure_r_nodal", assumptions, True

    finfo = _follower_info(kg, stress)
    if finfo:
        plus = str(finfo.get("in_plus_net") or "")
        match_ok = (
            "follower" in kinds
            or "follower" in origins
            or _inplus_closed(kg, plus)
        )
        return "opamp_voltage_follower", assumptions, match_ok

    if drive == "基极" or "base" in kinds or "base_drive" in origins:
        return "switch_base_drive_envelope", assumptions, True
    if drive == "开路/对地":
        return "series_socket_open_gnd", assumptions, True
    if drive == "OC" or "oc" in kinds:
        return "oc_pull_down_states", assumptions, True
    if drive == "CMOS" or "cmos" in kinds:
        refs = [str(d.get("ref") or "") for d in drivers if d.get("kind") == "cmos"]
        mpns = [_mpn_of(kg, r) for r in refs if r]
        if any(_OPAMP_MPN.search(m) for m in mpns):
            return "opamp_rail_envelope", assumptions, True
        if any(_CMOS_MPN.search(m) for m in mpns):
            return "cmos_push_pull", assumptions, True
        return None, assumptions, False
    if drive == "开关" or "switch" in kinds:
        return "switch_ce_envelope", assumptions, True
    if drive == "包络":
        return "rail_pull_to_zero", assumptions, True
    if drive == "常通" or drive == "无直流":
        return "pure_r_nodal", assumptions, True
    vs = stress.get("v_source") if isinstance(stress.get("v_source"), dict) else {}
    if vs.get("kind") in {"divider_solve", "local_multistate", "closed_rail"}:
        return "pure_r_nodal", assumptions, True
    if stress.get("v") is not None or (stress.get("cases") or []):
        return None, assumptions, False
    return None, assumptions, True
