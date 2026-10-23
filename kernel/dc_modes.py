# -*- coding: utf-8 -*-
"""直流工况分析器。先列出连通块上的工况，检查项再取电流或节点电压。"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from kernel.resistor_network import GROUND_NETS
from kernel.util import ROOT

_CACHE: dict[int, dict[str, Any]] = {}


@lru_cache(maxsize=1)
def _registered() -> frozenset[str]:
    path = ROOT / "rules" / "product_center_15.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return frozenset(str(item) for item in (data.get("needs_dc_modes") or []))


def rule_needs_dc_modes(rule_id: str) -> bool:
    """检查项自行登记后才调用。分析器不扫描全部检查项。"""
    return rule_id in _registered()


def index_of(kg: dict[str, Any]) -> dict[str, Any]:
    key = id(kg)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    built = _build(kg)
    _CACHE[key] = built
    if len(_CACHE) > 4:
        _CACHE.pop(next(iter(_CACHE)))
    return built


def dc_modes(kg: dict[str, Any], ref: str) -> dict[str, Any]:
    """一只器件所在直流连通块：工况、节点电压、支路电流、每个数的来源。"""
    found = index_of(kg)["by_ref"].get(ref)
    if found:
        return found
    return _empty(ref)


def project_resistor(
    block: dict[str, Any],
    ref: str,
    ohm: float,
    limit: float | None,
) -> list[dict[str, Any]]:
    """从工况取该电阻电流，P = I²R。不在这里决定有哪些档。"""
    if not block.get("in_index"):
        return []
    modes = list(block.get("modes") or [])
    if not modes:
        return [_pending("脚行为求解没有返回这只电阻")]
    out: list[dict[str, Any]] = []
    for mode in modes:
        out.append(_project_one(mode, ref, ohm, limit))
    return out or [_pending("脚行为求解没有返回这只电阻")]


def capacitor_view(kg: dict[str, Any], n_a: str, n_b: str) -> dict[str, Any]:
    """电容两端电压取自同一档节点电位。电容上没有直流电流。"""
    if n_a and n_a == n_b:
        return {
            "status": "closed",
            "cases": [
                {
                    "name_zh": "两脚同网",
                    "v_across": 0.0,
                    "va": 0.0,
                    "vb": 0.0,
                    "note": f"两脚同网 {n_a}：|Va−Vb|=0 V",
                    "lines": [
                        {"expr": f"V({n_a}) = V({n_b})", "src": "两脚同一网络"},
                        {"expr": "|Va−Vb| = 0 V", "src": "两端相减"},
                    ],
                }
            ],
            "v_max": 0.0,
            "drive_kind": "常通",
            "block_id": "",
            "note": "",
        }
    best: tuple[float, int, dict[str, Any], list[tuple[dict[str, Any], float, float]]] | None = None
    for block in index_of(kg)["blocks"].values():
        usable: list[tuple[dict[str, Any], float, float]] = []
        for mode in block.get("modes") or []:
            va = _net_v(mode, n_a)
            vb = _net_v(mode, n_b)
            if va is None or vb is None:
                continue
            usable.append((mode, va, vb))
        if not usable:
            continue
        span = max(abs(va - vb) for _mode, va, vb in usable)
        rank = (span, len(usable))
        if best is None or rank > (best[0], best[1]):
            best = (span, len(usable), block, usable)
    if best is None:
        return {
            "status": "open",
            "cases": [],
            "v_max": None,
            "drive_kind": "直流工况",
            "block_id": "",
            "note": "两端没有落在同一档已解工况",
        }
    _span, _count, block, usable = best
    cases = []
    for mode, va, vb in usable:
        across = abs(va - vb)
        cases.append(
            {
                "name_zh": mode["name"],
                "v_across": across,
                "va": va,
                "vb": vb,
                "note": f"{mode['name']}：|Va−Vb|={across:.6g} V",
                "lines": [
                    {"expr": f"V({n_a}) = {va:.6g} V", "src": "同一档节点电压"},
                    {"expr": f"V({n_b}) = {vb:.6g} V", "src": "同一档节点电压"},
                    {"expr": f"|Va−Vb| = |{va:.6g} − {vb:.6g}| = {across:.6g} V", "src": "两端相减"},
                ],
            }
        )
    return {
        "status": "closed",
        "cases": cases,
        "v_max": max(float(item["v_across"]) for item in cases),
        "drive_kind": str(block.get("drive") or "直流工况"),
        "block_id": str(block.get("block_id") or ""),
        "note": "",
    }


def stress_map(kg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """每只已解电阻的应力，功耗来自工况电流。"""
    from kernel.situations import _before_nets, _comps, _half_rated, _resistor_ends, _states_of

    ends = _resistor_ends(_comps(kg))
    comps = _comps(kg)
    out: dict[str, dict[str, Any]] = {}
    for ref, (n_a, n_b, ohm) in ends.items():
        block = dc_modes(kg, ref)
        limit = _half_rated(comps.get(ref) or {})
        cases = project_resistor(block, ref, float(ohm), limit)
        out[ref] = _as_stress(kg, block, ref, n_a, n_b, float(ohm), cases, _before_nets, _states_of)
    return out


def as_stress(
    kg: dict[str, Any],
    block: dict[str, Any],
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    from kernel.situations import _before_nets, _states_of

    return _as_stress(kg, block, ref, n_a, n_b, ohm, cases, _before_nets, _states_of)


def _as_stress(kg, block, ref, n_a, n_b, ohm, cases, before_nets, states_of) -> dict[str, Any]:
    values = [float(item["v_across"]) for item in cases if item.get("v_across") is not None]
    return {
        "nets": {"a": n_a, "b": n_b},
        "ohm": ohm,
        "before": before_nets(kg, n_a, n_b),
        "v": max(values) if values else None,
        "stress_kind": block.get("kind") or "pin_behavior",
        "drive_kind": block.get("drive") or "脚行为",
        "cases": cases,
        "states": states_of(cases),
        "drivers": [],
        "assumed_zero_drops": [],
        "v_source": block.get("v_source") or {"kind": block.get("kind") or "pin_behavior"},
        "false_close": None,
        "block_class": None,
        "note": block.get("note") or "",
        "fanout": {},
        "situation_group": block.get("block_id") or None,
    }


def _build(kg: dict[str, Any]) -> dict[str, Any]:
    from kernel.behavior_bridge import block_solve
    from kernel.situations import _comps, _resistor_ends, find_series_clamp_groups

    ends = _resistor_ends(_comps(kg))
    blocks: dict[str, dict[str, Any]] = {}
    by_ref: dict[str, dict[str, Any]] = {}
    claimed: set[str] = set()
    for group in find_series_clamp_groups(kg):
        refs = [ref for ref in group["refs"] if ref in ends]
        if not refs:
            continue
        ohms = {ref: float(ends[ref][2]) for ref in refs}
        packed = block_solve(kg, refs, ohms)
        block = _pack(
            str(group["group_id"]),
            refs,
            "series_clamp",
            "串联钳位",
            packed,
            ends,
            {
                "kind": "series_clamp",
                "supply": group["supply_net"],
                "exit": group["exit_net"],
                "refs": list(refs),
            },
            ohms,
        )
        blocks[block["block_id"]] = block
        for ref in refs:
            by_ref[ref] = block
            claimed.add(ref)
        _claim_side(block, by_ref, claimed)
    for ref, (_n_a, _n_b, ohm) in ends.items():
        if ref in claimed or ohm is None:
            continue
        ohms = {ref: float(ohm)}
        packed = block_solve(kg, [ref], ohms)
        block = _pack(
            f"pin:{ref}",
            [ref],
            "pin_behavior",
            "脚行为",
            packed,
            ends,
            {"kind": "pin_behavior"},
            ohms,
        )
        blocks[block["block_id"]] = block
        by_ref[ref] = block
        claimed.add(ref)
        _claim_side(block, by_ref, claimed)
    return {"blocks": blocks, "by_ref": by_ref}


def _claim_side(block: dict[str, Any], by_ref: dict[str, dict[str, Any]], claimed: set[str]) -> None:
    for ref in block.get("members") or []:
        if ref in by_ref:
            continue
        by_ref[ref] = block
        claimed.add(ref)


def _pack(block_id, refs, kind, drive, packed, ends, v_source, ohms) -> dict[str, Any]:
    elements = list(packed.get("elements") or [])
    rows = ((packed.get("result") or {}).get("rows") or [])
    modes = [_mode(row) for row in rows]
    referenced = any(mode.get("corners") and not mode.get("gmin") for mode in modes)
    for mode in modes:
        mode["unreferenced"] = bool(mode.get("corners")) and not referenced
    _stamp_drive_current(modes, elements)
    members: list[str] = []
    seen: set[str] = set()

    def take(ref: str) -> None:
        if ref and ref in ends and ref not in seen:
            seen.add(ref)
            members.append(ref)

    for ref in refs:
        take(ref)
    for item in elements:
        if item.get("behavior") == "resistor":
            take(str(item.get("ref") or ""))
        take(str(item.get("drive_r_ref") or ""))
    ohm_map = {ref: float(ends[ref][2]) for ref in members}
    ohm_map.update({key: float(value) for key, value in ohms.items()})
    nets: set[str] = set()
    for item in elements:
        for key in ("plus", "minus"):
            if item.get(key):
                nets.add(str(item[key]))
    for mode in modes:
        nets.update(mode["voltages"])
    return {
        "block_id": block_id,
        "in_index": True,
        "members": members,
        "kind": kind,
        "drive": drive,
        "nets": sorted(nets),
        "modes": modes,
        "ends": {ref: (ends[ref][0], ends[ref][1]) for ref in members},
        "ohms": ohm_map,
        "v_source": v_source,
        "note": "",
    }


def _stamp_drive_current(modes: list[dict[str, Any]], elements: list[dict[str, Any]]) -> None:
    """基极电阻不放进节点方程。导通档的电流用已经算出的基极电流。"""
    for item in elements:
        if item.get("behavior") != "switch" or item.get("drive_ib_a") is None:
            continue
        ref = str(item.get("drive_r_ref") or "")
        if not ref:
            continue
        token = f"{item.get('ref')} 导通"
        ib = abs(float(item["drive_ib_a"]))
        for mode in modes:
            amp = ib if token in str(mode.get("name") or "") else 0.0
            mode.setdefault("currents", {})[ref] = amp
            for corner in mode.get("corners") or []:
                corner.setdefault("currents", {})[ref] = amp


def _mode(row: dict[str, Any]) -> dict[str, Any]:
    corners = []
    voltages: dict[str, float] = {}
    currents: dict[str, float] = {}
    sources: list[str] = []
    for corner in row.get("corners") or []:
        volts = {str(net): float(value) for net, value in (corner.get("voltages") or {}).items()}
        for end in (corner.get("ends") or {}).values():
            if end.get("a"):
                volts[str(end["a"])] = float(end.get("va") or 0.0)
            if end.get("b"):
                volts[str(end["b"])] = float(end.get("vb") or 0.0)
        amps = {str(ref): float(value) for ref, value in (corner.get("currents") or {}).items()}
        facts = [str(item) for item in (corner.get("facts") or [])]
        corners.append(
            {
                "label": str(corner.get("label") or ""),
                "voltages": volts,
                "currents": amps,
                "ends": dict(corner.get("ends") or {}),
                "sources": facts,
            }
        )
        voltages = volts
        currents = amps
        sources = facts
    note = str(row.get("note") or "")
    return {
        "name": str(row.get("name") or "直流工作"),
        "participates": bool(row.get("participates", True)),
        "note": note,
        "period_open": "周期和占空比未从网表闭合" in note,
        "unstable": "区段回代未稳定" in note,
        "voltages": voltages,
        "currents": currents,
        "sources": sources,
        "corners": corners,
        "gmin": bool(row.get("gmin")),
    }


def _supply_off_zero(mode: dict[str, Any], samples: list[tuple[Any, float, float]]) -> bool:
    if not str(mode.get("name") or "").startswith("未上电"):
        return False
    return bool(samples) and all(power <= 1e-9 for _corner, _amp, power in samples)


def _project_one(mode: dict[str, Any], ref: str, ohm: float, limit: float | None) -> dict[str, Any]:
    samples = []
    for corner in mode.get("corners") or []:
        currents = corner.get("currents") or {}
        if ref not in currents:
            continue
        amp = abs(float(currents[ref]))
        samples.append((corner, amp, amp * amp * float(ohm)))
    note = str(mode.get("note") or "")
    if mode.get("gmin") and not _supply_off_zero(mode, samples):
        return _pending("这一档没有闭合到电压源，节点电压不作为工作点", str(mode.get("name") or "直流工作"))
    if not samples:
        return _pending(note or "本档没有这只电阻的电流", str(mode.get("name") or "脚行为未闭合"))
    lo = min(power for _corner, _amp, power in samples)
    hi = max(power for _corner, _amp, power in samples)
    lines = _power_lines(mode, ref, ohm, samples)
    name = str(mode.get("name") or "直流工作")
    if not mode.get("participates", True):
        lines.append({"expr": "不参评", "src": "端口对地不单独决定整卡"})
        return {
            "name_zh": name,
            "v_across": (hi * ohm) ** 0.5 if ohm else 0.0,
            "p_w": hi,
            "p_lo_w": lo,
            "p_hi_w": hi,
            "i_dc": None,
            "closes_verdict": False,
            "note": note,
            "reason": note,
            "lines": lines,
        }
    if mode.get("unstable"):
        return _forced(name, ohm, hi, lo, lines, "待确认", note or "区段回代未稳定")
    if mode.get("period_open"):
        if limit is not None and hi <= float(limit):
            return _forced(
                name,
                ohm,
                hi,
                lo,
                lines,
                "是",
                "高电平瞬时功耗未超过限额，占空比再低也不会超过",
            )
        return _forced(name, ohm, hi, lo, lines, "待确认", note or "周期和占空比未从网表闭合")
    if abs(hi) <= 1e-18 and abs(lo) <= 1e-18:
        return {
            "name_zh": name,
            "v_across": 0.0,
            "p_w": 0.0,
            "i_dc": 0.0,
            "closes_verdict": True,
            "dc_open_cite": note or "电源未建立",
            "note": note,
            "reason": note,
            "lines": lines or [
                {"expr": "I = 0", "src": note or "电源未建立"},
                {"expr": "P = I²R = 0", "src": "电流为 0"},
            ],
        }
    if limit is None:
        return _forced(name, ohm, hi, lo, lines, "待确认", note or "缺额定功率")
    if lo > float(limit) and hi > float(limit):
        zh = "否"
    elif hi <= float(limit):
        zh = "是"
    else:
        zh = "待确认"
    return _forced(name, ohm, hi, lo, lines, zh, note)


def _forced(name, ohm, hi, lo, lines, zh, note) -> dict[str, Any]:
    text = _power_text(lo, hi)
    return {
        "name_zh": name,
        "v_across": (hi * ohm) ** 0.5 if ohm else 0.0,
        "p_w": hi,
        "p_lo_w": lo,
        "p_hi_w": hi,
        "closes_verdict": True,
        "force_verdict": zh,
        "force_note": f"{text} → {zh}",
        "note": note,
        "reason": note,
        "lines": lines,
    }


def _power_lines(mode, ref, ohm, samples) -> list[dict[str, str]]:
    lines: list[dict[str, str]] = []
    for corner, amp, power in samples:
        label = str(corner.get("label") or mode.get("name") or "")
        end = (corner.get("ends") or {}).get(ref) or {}
        pins = {str(end.get("a") or ""), str(end.get("b") or "")}
        for fact in corner.get("sources") or []:
            if str(fact).startswith("V("):
                net = str(fact)[2:str(fact).find(")")]
                if net in pins:
                    continue
            lines.append({"expr": str(fact), "src": label or "入参"})
        if end:
            va = float(end.get("va") or 0.0)
            vb = float(end.get("vb") or 0.0)
            lines.append({"expr": f"V({end.get('a')}) = {va:.6g} V", "src": "节点方程"})
            lines.append({"expr": f"V({end.get('b')}) = {vb:.6g} V", "src": "节点方程"})
            lines.append(
                {
                    "expr": f"V({ref}) = |{va:.6g} − {vb:.6g}| = {abs(va - vb):.6g} V",
                    "src": "两端电压相减",
                }
            )
        lines.append({"expr": f"I({ref}) = {amp:.6g} A", "src": label or "节点方程"})
        lines.append(
            {
                "expr": f"P({ref}) = I²R = ({amp:.6g})² × {ohm:g} = {_mw(power)}",
                "src": "电流的平方乘本只电阻",
            }
        )
    if len(samples) > 1:
        powers = [power for _corner, _amp, power in samples]
        lines.append({"expr": _power_text(min(powers), max(powers)), "src": "各角点取齐"})
    return lines


def _pending(note: str, name: str = "脚行为未闭合") -> dict[str, Any]:
    return {
        "name_zh": name,
        "v_across": 0.0,
        "p_w": None,
        "closes_verdict": True,
        "force_verdict": "待确认",
        "force_note": note + " → 待确认",
        "note": note,
        "reason": note,
        "lines": [{"expr": note, "src": "脚行为入参"}],
    }


def _power_text(lo: float, hi: float) -> str:
    if abs(hi - lo) < 1e-6:
        return f"P = I²R = {_mw(hi)}"
    return f"P = I²R = {_mw(lo)}–{_mw(hi)}"


def _mw(watts: float) -> str:
    if watts < 1:
        return f"{watts * 1000:.2f} mW"
    return f"{watts:.4g} W"


def _net_v(mode: dict[str, Any], net: str) -> float | None:
    if not net:
        return None
    if net in GROUND_NETS:
        return 0.0
    volts = mode.get("voltages") or {}
    if net not in volts:
        return None
    return float(volts[net])


def _empty(ref: str) -> dict[str, Any]:
    return {
        "block_id": "",
        "in_index": False,
        "members": [ref],
        "kind": "",
        "drive": "",
        "nets": [],
        "modes": [],
        "ends": {},
        "ohms": {},
        "v_source": {},
        "note": "没有进入已解直流连通块",
    }
