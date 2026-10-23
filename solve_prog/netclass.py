# -*- coding: utf-8 -*-
"""脚、网、种类。中间节点不从网名取电压。"""

from __future__ import annotations

import re
from typing import Any

GROUND_NETS = {"GND", "AGND", "DGND", "PGND", "VSS", "AVSS"}
SUPPLY_KINDS = {"netname_voltage", "supply", "regulator"}


def components(kg: dict[str, Any]) -> dict[str, Any]:
    return kg.get("components") or {}


def nets(kg: dict[str, Any]) -> dict[str, Any]:
    return kg.get("nets") or {}


def pin_pairs(comp: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for pin in comp.get("pins") or []:
        if isinstance(pin, dict):
            name = str(pin.get("pin") or pin.get("name") or "")
            if "." in name:
                name = name.split(".")[-1]
            out.append((name, str(pin.get("net") or "")))
        elif isinstance(pin, str):
            out.append(("", pin))
    return out


def device_type(ref: str, comp: dict[str, Any]) -> str:
    raw = str(comp.get("type") or "").lower()
    if raw and raw != "unknown":
        return raw
    match = re.match(r"^([A-Za-z]+)", ref or "")
    prefix = (match.group(1) if match else "").upper()
    return {
        "R": "resistor",
        "RN": "resistor",
        "C": "capacitor",
        "L": "inductor",
        "U": "ic",
        "D": "diode",
        "Q": "transistor",
        "Y": "crystal",
        "F": "fuse",
        "J": "connector",
        "P": "connector",
        "CN": "connector",
    }.get(prefix, "unknown")


def parse_ohm(value: str) -> float | None:
    text = (value or "").strip().replace("Ω", "").replace("ohm", "").replace("Ohm", "")
    if not text or text.upper() == "TBD":
        return None
    match = re.match(r"^(\d+)[Rr](\d+)$", text)
    if match:
        frac = match.group(2)
        return float(match.group(1)) + float(frac) / (10 ** len(frac))
    match = re.match(r"^([0-9]*\.?[0-9]+)\s*([kKmMrR])?$", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit == "k":
        return number * 1e3
    if unit == "m":
        return number * 1e6
    return number


def _source_kind(net: dict[str, Any]) -> str:
    rail = (net or {}).get("rail") or {}
    source = rail.get("source")
    if isinstance(source, dict):
        return str(source.get("kind") or "")
    return str(source or "")


def net_kind(name: str, net: dict[str, Any]) -> str:
    if name in GROUND_NETS or _source_kind(net) == "ground":
        return "ground"
    rail = (net or {}).get("rail") or {}
    kind = _source_kind(net)
    if rail.get("status") == "closed" and rail.get("value") is not None and kind in SUPPLY_KINDS:
        try:
            float(rail["value"])
        except (TypeError, ValueError):
            return "intermediate"
        return "supply"
    return "intermediate"


def supply_volts(net: dict[str, Any]) -> float | None:
    if net_kind("", net) == "intermediate" and _source_kind(net) not in SUPPLY_KINDS:
        return None
    rail = (net or {}).get("rail") or {}
    if _source_kind(net) not in SUPPLY_KINDS or rail.get("value") is None:
        return None
    try:
        return float(rail["value"])
    except (TypeError, ValueError):
        return None


def attachments(net: str, kg: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for ref, comp in components(kg).items():
        for pin, name in pin_pairs(comp):
            if name == net:
                rows.append({"ref": ref, "pin": pin, "type": device_type(ref, comp)})
    return rows


def resistor_ohms(kg: dict[str, Any]) -> dict[str, tuple[str, str, float]]:
    found: dict[str, tuple[str, str, float]] = {}
    for ref, comp in components(kg).items():
        if device_type(ref, comp) != "resistor":
            continue
        ohm = parse_ohm(str(comp.get("value") or ""))
        uniq: list[str] = []
        for _pin, name in pin_pairs(comp):
            if name and name not in uniq:
                uniq.append(name)
        if ohm is None or ohm <= 0 or len(uniq) != 2:
            continue
        found[ref] = (uniq[0], uniq[1], float(ohm))
    return found


def rated_power_w(comp: dict[str, Any]) -> float | None:
    params = ((comp.get("pintable") or {}).get("params") or {})
    for key in ("ptot_w", "p_rated_w"):
        if params.get(key) is not None:
            try:
                return float(params[key])
            except (TypeError, ValueError):
                return None
    if comp.get("p_rated") is not None:
        try:
            return float(comp["p_rated"])
        except (TypeError, ValueError):
            return None
    return None


def param_float(comp: dict[str, Any], key: str) -> float | None:
    params = ((comp.get("pintable") or {}).get("params") or {})
    if params.get(key) is None:
        return None
    try:
        return float(params[key])
    except (TypeError, ValueError):
        return None
