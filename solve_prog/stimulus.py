# -*- coding: utf-8 -*-
"""激励卡。检查项只注册点名方式，字段统一。"""

from __future__ import annotations

from typing import Any, Callable

from solve_prog.netclass import (
    GROUND_NETS,
    attachments,
    components,
    nets,
    param_float,
    pin_pairs,
    resistor_ohms,
    supply_volts,
)

Namer = Callable[[dict[str, Any], str], dict[str, Any] | None]
NAMERS: dict[str, Namer] = {}


def register(rule_id: str) -> Callable[[Namer], Namer]:
    def wrap(fn: Namer) -> Namer:
        NAMERS[rule_id] = fn
        return fn

    return wrap


def name_stimulus(kg: dict[str, Any], rule_id: str, ref: str) -> dict[str, Any] | None:
    fn = NAMERS.get(rule_id)
    if fn is None:
        return None
    return fn(kg, ref)


def _resistors_on(net: str, ends: dict[str, tuple[str, str, float]]) -> list[str]:
    return [ref for ref, (a, b, _ohm) in ends.items() if net in (a, b)]


def _other(ref: str, net: str, ends: dict[str, tuple[str, str, float]]) -> str:
    a, b, _ohm = ends[ref]
    return b if a == net else a


def _walk(start: str, came: str | None, ends: dict[str, tuple[str, str, float]]) -> tuple[list[str], str]:
    refs: list[str] = []
    net = start
    prev = came
    while True:
        here = [item for item in _resistors_on(net, ends) if item != prev]
        if len(here) != 1:
            return refs, net
        ref = here[0]
        refs.append(ref)
        nxt = _other(ref, net, ends)
        prev = ref
        net = nxt
        if len(_resistors_on(net, ends)) != 2:
            return refs, net


def _is_supply_net(name: str, kg: dict[str, Any]) -> bool:
    return supply_volts((nets(kg).get(name) or {})) is not None and name not in GROUND_NETS


def find_series_groups(kg: dict[str, Any]) -> list[dict[str, Any]]:
    comps = components(kg)
    ends = resistor_ohms(kg)
    seen: set[frozenset[str]] = set()
    groups: list[dict[str, Any]] = []
    clamp_types = {"diode", "led", "connector", "header", "transistor", "mosfet", "bjt", "fet", "jfet"}
    for ref, (na, nb, _ohm) in ends.items():
        left_refs, left_net = _walk(na, ref, ends)
        right_refs, right_net = _walk(nb, ref, ends)
        chain = list(reversed(left_refs)) + [ref] + right_refs
        key = frozenset(chain)
        if key in seen:
            continue
        seen.add(key)
        supply = exit_net = None
        if _is_supply_net(left_net, kg) and not _is_supply_net(right_net, kg):
            supply, exit_net = left_net, right_net
        elif _is_supply_net(right_net, kg) and not _is_supply_net(left_net, kg):
            supply, exit_net = right_net, left_net
        if not supply or not exit_net:
            continue
        if len(_resistors_on(exit_net, ends)) != 1:
            continue
        if not any(row["type"] in clamp_types for row in attachments(exit_net, kg)):
            continue
        ordered: list[str] = []
        net = supply
        prev = None
        for _guard in range(32):
            here = [item for item in _resistors_on(net, ends) if item != prev and item in key]
            if len(here) != 1:
                break
            nxt_ref = here[0]
            ordered.append(nxt_ref)
            prev = nxt_ref
            net = _other(nxt_ref, net, ends)
            if net == exit_net:
                break
        if set(ordered) != set(chain):
            continue
        groups.append(
            {
                "refs": ordered,
                "ohms": {item: ends[item][2] for item in ordered},
                "ends": {item: (ends[item][0], ends[item][1]) for item in ordered},
                "supply_net": supply,
                "exit_net": exit_net,
                "v_supply": float(supply_volts(nets(kg).get(supply) or {}) or 0.0),
                "group_id": "series:" + "-".join(ordered),
            }
        )
    return groups


def _hub_cathodes(exit_net: str, kg: dict[str, Any]) -> list[str]:
    out: list[str] = []
    comps = components(kg)
    for row in attachments(exit_net, kg):
        if row["type"] not in {"diode", "led"}:
            continue
        pins = pin_pairs(comps.get(row["ref"]) or {})
        by_pin = {pin: net for pin, net in pins}
        if by_pin.get("3") != exit_net:
            continue
        for pin, net in pins:
            if pin == "3" or not net or net == exit_net or net in GROUND_NETS:
                continue
            if net not in out:
                out.append(net)
    return out


def _zener(net: str, kg: dict[str, Any]) -> tuple[float, float, str] | None:
    rail = (nets(kg).get(net) or {}).get("rail") or {}
    source = rail.get("source") if isinstance(rail.get("source"), dict) else {}
    if source.get("kind") != "zener_clamp" or rail.get("value") is None:
        return None
    hi = float(source.get("vz_max_v") if source.get("vz_max_v") is not None else rail["value"])
    lo = float(source.get("vz_min_v") if source.get("vz_min_v") is not None else hi)
    return lo, hi, str(source.get("ref") or "稳压管")


def _drop(ref: str, kg: dict[str, Any], keys: tuple[str, ...]) -> tuple[float, str]:
    comp = components(kg).get(ref) or {}
    for key in keys:
        value = param_float(comp, key)
        if value is not None:
            return value, "extracted"
    return 0.0, "stamped_zero"


def _source(plus: str, minus: str, volts: float, role: str, ref: str, origin: str) -> dict[str, Any]:
    return {
        "plus": plus,
        "minus": minus,
        "volts": float(volts),
        "role": role,
        "ref": ref,
        "origin": origin,
    }


def _ground(kg: dict[str, Any]) -> str:
    for name in nets(kg):
        if name in GROUND_NETS:
            return name
    return "GND"


@register("P11-A01")
def series_clamp_stimulus(kg: dict[str, Any], ref: str) -> dict[str, Any] | None:
    group = next((item for item in find_series_groups(kg) if ref in item["refs"]), None)
    if group is None:
        return None
    gnd = _ground(kg)
    exit_net = group["exit_net"]
    supply = group["supply_net"]
    vs = float(group["v_supply"])
    cathodes = _hub_cathodes(exit_net, kg)
    diode_refs = [
        row["ref"]
        for row in attachments(exit_net, kg)
        if row["type"] in {"diode", "led"}
    ]
    zener_net = next((net for net in cathodes if _zener(net, kg)), None)
    zener = _zener(zener_net, kg) if zener_net else None
    model: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = [
        {
            "id": "supply_off",
            "name": "未上电",
            "participates": True,
            "clamp": False,
            "sources": [_source(supply, gnd, 0.0, "supply", supply, "supply")],
        }
    ]
    if zener and zener_net:
        lo, hi, zref = zener
        for label, vz in (("min", lo), ("max", hi)):
            model.append({"ref": zref, "param": f"vz_{label}_v", "volts": vz, "origin": "extracted"})
            vf, origin = _drop(diode_refs[0], kg, ("vf_v",)) if diode_refs else (0.0, "stamped_zero")
            if diode_refs:
                model.append({"ref": diode_refs[0], "param": "vf_v", "volts": vf, "origin": origin})
            sources = [
                _source(supply, gnd, vs, "supply", supply, "supply"),
                _source(zener_net, gnd, vz, "zener", zref, "extracted"),
            ]
            if diode_refs:
                sources.append(_source(exit_net, zener_net, vf, "vf", diode_refs[0], origin))
            states.append(
                {
                    "id": f"zener_{label}",
                    "name": f"稳压管钳位 {zref} {'下限' if label == 'min' else '上限'} {vz:g} V",
                    "participates": True,
                    "clamp": True,
                    "zener_ref": zref,
                    "sources": sources,
                }
            )
    switches: list[str] = []
    for net in [exit_net, *cathodes]:
        for row in attachments(net, kg):
            if row["type"] not in {"transistor", "mosfet", "bjt", "fet", "jfet"} or row["ref"] in switches:
                continue
            ce = _ce(row["ref"], kg)
            if ce and net in ce and net not in GROUND_NETS:
                switches.append(row["ref"])
    for sref in switches:
        vce, origin = _drop(sref, kg, ("vce_sat_v", "vce_v"))
        model.append({"ref": sref, "param": "vce_sat_v", "volts": vce, "origin": origin})
        collector, emitter = _ce(sref, kg) or (exit_net, gnd)
        vf, vf_origin = _drop(diode_refs[0], kg, ("vf_v",)) if diode_refs else (0.0, "stamped_zero")
        sources = [
            _source(supply, gnd, vs, "supply", supply, "supply"),
            _source(collector, emitter, vce, "vce", sref, origin),
        ]
        if diode_refs and collector != exit_net:
            sources.append(_source(exit_net, collector, vf, "vf", diode_refs[0], vf_origin))
        states.append(
            {
                "id": f"switch_on_{sref}",
                "name": f"{sref} 导通",
                "participates": True,
                "clamp": False,
                "sources": sources,
            }
        )
    for net in {exit_net, *cathodes}:
        if net == zener_net:
            continue
        for row in attachments(net, kg):
            if row["type"] not in {"connector", "header"}:
                continue
            vf, origin = _drop(diode_refs[0], kg, ("vf_v",)) if diode_refs else (0.0, "stamped_zero")
            sources = [
                _source(supply, gnd, vs, "supply", supply, "supply"),
                _source(net, gnd, 0.0, "port", row["ref"], "stimulus"),
            ]
            if diode_refs and net != exit_net:
                sources.append(_source(exit_net, net, vf, "vf", diode_refs[0], origin))
            states.append(
                {
                    "id": f"port_gnd_{row['ref']}",
                    "name": f"{net} 被 {row['ref']} 外部接地",
                    "participates": False,
                    "clamp": False,
                    "sources": sources,
                }
            )
    return {
        "rule_id": "P11-A01",
        "kind": "series_clamp",
        "group_id": group["group_id"],
        "objects": list(group["refs"]),
        "group": group,
        "states": states,
        "model": model,
    }


def _ce(ref: str, kg: dict[str, Any]) -> tuple[str, str] | None:
    comp = components(kg).get(ref) or {}
    by_pin = {pin.upper(): net for pin, net in pin_pairs(comp)}
    if {"1", "2", "3"} <= set(by_pin):
        return by_pin["3"], by_pin["2"]
    gnd = [net for _pin, net in pin_pairs(comp) if net in GROUND_NETS]
    others = [net for _pin, net in pin_pairs(comp) if net and net not in GROUND_NETS]
    if gnd and others:
        return others[-1], gnd[0]
    if len(others) >= 2:
        return others[-1], others[0]
    return None


def limit_w(rule_id: str, comp: dict[str, Any]) -> float | None:
    if rule_id != "P11-A01":
        return None
    from solve_prog.netclass import rated_power_w

    rated = rated_power_w(comp)
    if rated is None:
        return None
    return 0.5 * rated
