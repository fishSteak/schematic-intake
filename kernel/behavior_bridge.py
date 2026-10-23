# -*- coding: utf-8 -*-
"""串联钳位组收成脚行为入参，交给同一套求解。"""

from __future__ import annotations

from typing import Any

from kernel.knowledge import match_series
from kernel.resistor_network import GROUND_NETS, _closed_rail_v, _parse_res_ohm
from solve_prog.behavior_solve import solve
from solve_prog.extract_behavior import fill_elements

_PASSIVE = {"resistor", "capacitor", "inductor"}


def cases_for_group(kg: dict[str, Any], group: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    refs = list(group["refs"])
    ohms = {ref: float(group["ohms"][ref]) for ref in refs}
    return solve_seeds(kg, refs, ohms)


def block_solve(kg: dict[str, Any], refs: list[str], ohms: dict[str, float]) -> dict[str, Any]:
    """一次连通块求解。返回脚行为入参和工况行，不在这里算检查项结论。"""
    elements, params = _elements(kg, refs, ohms)
    filled = fill_elements(elements, params)
    unknown = [item for item in filled if item.get("behavior") not in {
        "hiz", "voltage", "current", "switch", "open_drain", "diode", "zener", "resistor", "port",
    }]
    if unknown:
        note = "、".join(f"{item.get('ref')} 的脚位没有对照表" for item in unknown)
        rows = [
            {
                "name": "脚行为未闭合",
                "participates": True,
                "powers": {ref: None for ref in refs},
                "verdicts": {ref: "待确认" for ref in refs},
                "note": note,
                "corners": [],
            }
        ]
        return {"elements": filled, "result": {"rows": rows}}
    return {"elements": filled, "result": solve(filled)}


def solve_seeds(kg: dict[str, Any], refs: list[str], ohms: dict[str, float]) -> dict[str, list[dict[str, Any]]]:
    packed = block_solve(kg, refs, ohms)
    return {ref: _cases(packed["result"], ref, float(ohms[ref])) for ref in refs}


def _elements(kg: dict[str, Any], seed_refs: list[str], ohms: dict[str, float]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    from kernel.situations import _attachments, _comps, _nets

    comps = _comps(kg)
    nets = _nets(kg)
    elements: list[dict[str, Any]] = []
    params: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()

    def add(item: dict[str, Any]) -> None:
        ref = str(item.get("ref") or "")
        if ref in seen and item.get("behavior") != "diode":
            return
        if item.get("behavior") == "diode":
            key = f"diode:{item.get('plus')}:{item.get('minus')}"
            if key in seen:
                return
            seen.add(key)
        else:
            seen.add(ref)
        elements.append(item)
        comp = comps.get(ref) or {}
        if ref not in params:
            params[ref] = dict(((comp.get("pintable") or {}).get("params") or {}))

    queue: list[str] = []
    seed_nets: set[str] = set()
    for ref in seed_refs:
        comp = comps.get(ref) or {}
        pins = _pin_nets(comp)
        if len(pins) < 2:
            continue
        add({
            "ref": ref,
            "behavior": "resistor",
            "plus": pins[0][1],
            "minus": pins[1][1],
            "ohm": float(ohms.get(ref) if ohms.get(ref) is not None else _ohm(comp)),
            "mpn": comp.get("mpn") or "",
        })
        for _pin, net in pins:
            if net:
                seed_nets.add(str(net))
                queue.append(str(net))

    def _may_enqueue(other: str) -> bool:
        """电阻对端：仅电源轨 / 与 seed 引脚网直接相连才继续，阻断旁路卷进 LED 等远网。"""
        if not other or other in GROUND_NETS:
            return False
        if other in seed_nets:
            return True
        rail_info = (nets.get(other) or {}).get("rail") or {}
        src = rail_info.get("source")
        rail_kind = src.get("kind") if isinstance(src, dict) else str(src or "")
        rail = _closed_rail_v(nets.get(other) or {})
        return rail is not None and rail_kind in {
            "netname_voltage",
            "regulator",
            "supply",
            "task",
            "oc",
            "oc_task",
        }

    visited: set[str] = set()
    guard = 0
    while queue and guard < 80:
        guard += 1
        net = queue.pop(0)
        if not net or net in visited or net in GROUND_NETS:
            continue
        rail_info = (nets.get(net) or {}).get("rail") or {}
        src = rail_info.get("source")
        rail_kind = src.get("kind") if isinstance(src, dict) else str(src or "")
        rail = _closed_rail_v(nets.get(net) or {})
        # 任务书写成的闭合电源 source 是字符串 "task"。到此为止，不穿过 +24V 去卷熔丝和别的电路。
        if rail is not None and rail_kind in {"netname_voltage", "regulator", "supply", "task", "oc", "oc_task"}:
            add({
                "ref": net,
                "behavior": "voltage",
                "plus": net,
                "minus": "GND",
                "volts": float(rail),
                # P11 等稳态耗散不评「未上电」；电源以工作电压参评
                "can_be_off": False,
            })
            visited.add(net)
            continue
        visited.add(net)
        for ref, _pin, typ in _attachments(net, comps):
            if ref in seed_refs:
                continue
            comp = comps.get(ref) or {}
            mpn = str(comp.get("mpn") or "")
            roles = (match_series(mpn) or {}).get("pin_roles") or {}
            pintable = ((comp.get("pintable") or {}).get("params") or {})
            if typ == "diode" and pintable.get("vz_max_v") is not None:
                _add_zener(add, queue, ref, comp, roles, mpn)
            elif typ == "diode":
                _add_diode(add, queue, ref, comp, roles, mpn)
            elif typ == "led":
                # 仅 seed 引脚网上的 LED 才入网，禁止旁路电阻把显示管卷进来
                led_pins = _pin_nets(comp)
                if not any(str(n) in seed_nets for _p, n in led_pins if n):
                    continue
                _add_led(add, queue, ref, comp, mpn, nets)
            elif typ in {"transistor", "mosfet", "bjt", "fet", "jfet"}:
                _add_switch(add, queue, ref, comp, comps, nets)
            elif typ in {"connector", "header"}:
                add({
                    "ref": ref,
                    "behavior": "port",
                    "plus": net,
                    "minus": "GND",
                    "participates": False,
                })
            elif typ == "resistor":
                pins = _pin_nets(comp)
                if len(pins) < 2:
                    continue
                add({
                    "ref": ref,
                    "behavior": "resistor",
                    "plus": pins[0][1],
                    "minus": pins[1][1],
                    "ohm": _ohm(comp),
                    "mpn": mpn,
                })
                for _p, other in pins:
                    if other != net and _may_enqueue(str(other)):
                        queue.append(str(other))
            elif typ == "ic":
                before = len(elements)
                _add_analog_switches(add, queue, ref, comp, mpn, nets)
                if len(elements) == before:
                    # 无脚行为表：输入脚高阻；同 IC 上已触及的电阻网按开漏自由开关建模
                    add(
                        {
                            "ref": ref,
                            "behavior": "hiz",
                            "plus": net,
                            "minus": "GND",
                            "mpn": mpn,
                        }
                    )
                    oc_nets: set[str] = set()
                    for _pin, pnet in _pin_nets(comp):
                        if not pnet or pnet in GROUND_NETS or str(pnet) == net:
                            continue
                        if _closed_rail_v(nets.get(pnet) or {}) is not None:
                            continue
                        pnet_s = str(pnet)
                        if pnet_s in oc_nets:
                            continue
                        touched = any(
                            e.get("behavior") == "resistor"
                            and (
                                str(e.get("plus") or "") == pnet_s
                                or str(e.get("minus") or "") == pnet_s
                            )
                            for e in elements
                        )
                        if not touched:
                            continue
                        oc_nets.add(pnet_s)
                        add(
                            {
                                "ref": f"{ref}_oc_{pnet_s}",
                                "behavior": "open_drain",
                                "plus": pnet_s,
                                "minus": "GND",
                                "on_drop_v": 0.0,
                                "mpn": mpn,
                            }
                        )
            elif typ not in _PASSIVE:
                add({"ref": ref, "behavior": "unmapped_" + typ, "plus": net, "minus": "GND", "mpn": mpn})
    return elements, params


def _add_zener(add, queue, ref, comp, roles, mpn) -> None:
    pins = _pin_nets(comp)
    by_pin = {pin: net for pin, net in pins}
    if roles:
        anodes = [by_pin[pin] for pin, role in roles.items() if role == "anode" and pin in by_pin]
        cathodes = [by_pin[pin] for pin, role in roles.items() if role == "cathode" and pin in by_pin]
    else:
        grounded = [net for _pin, net in pins if net in GROUND_NETS]
        floating = [net for _pin, net in pins if net and net not in GROUND_NETS]
        anodes = grounded[:1]
        cathodes = floating[:1]
    if len(anodes) != 1 or len(cathodes) != 1:
        add({"ref": ref, "behavior": "unmapped_zener", "plus": pins[0][1] if pins else "", "minus": "GND", "mpn": mpn})
        return
    add({
        "ref": ref,
        "behavior": "zener",
        "plus": cathodes[0],
        "minus": anodes[0],
        "mpn": mpn,
    })
    queue.extend(anodes + cathodes)


def _add_diode(add, queue, ref, comp, roles, mpn) -> None:
    pins = _pin_nets(comp)
    by_pin = {pin: net for pin, net in pins}
    pairs = _diode_pin_pairs(roles, mpn)
    if not pairs:
        add({"ref": ref, "behavior": "unmapped_diode", "plus": pins[0][1] if pins else "", "minus": pins[1][1] if len(pins) > 1 else "GND", "mpn": mpn})
        return
    placed = []
    for anode_pin, cathode_pin in pairs:
        anode = by_pin.get(str(anode_pin))
        cathode = by_pin.get(str(cathode_pin))
        if not anode or not cathode:
            add({"ref": ref, "behavior": "unmapped_diode", "plus": pins[0][1] if pins else "", "minus": "GND", "mpn": mpn})
            return
        placed.append((anode, cathode))
    for anode, cathode in placed:
        add({
            "ref": ref,
            "behavior": "diode",
            "plus": anode,
            "minus": cathode,
            "mpn": mpn,
        })
        queue.append(anode)
        queue.append(cathode)


def _diode_pin_pairs(roles: dict[str, Any], mpn: str) -> list[tuple[str, str]]:
    if roles:
        anodes = [pin for pin, role in roles.items() if role == "anode"]
        cathodes = [pin for pin, role in roles.items() if role == "cathode"]
        if anodes and cathodes:
            return [(anode, cathode) for anode in anodes for cathode in cathodes]
    pairs = []
    for pair in (match_series(mpn) or {}).get("diode_pairs") or []:
        anode = str(pair.get("anode") or "")
        cathode = str(pair.get("cathode") or "")
        if anode and cathode:
            pairs.append((anode, cathode))
    return pairs


def _add_led(add, queue, ref, comp, mpn, nets) -> None:
    roles = _led_roles(comp, nets)
    if not roles:
        pins = _pin_nets(comp)
        add({
            "ref": ref,
            "behavior": "unmapped_led",
            "plus": pins[0][1] if pins else "",
            "minus": pins[1][1] if len(pins) > 1 else "GND",
            "mpn": mpn,
        })
        return
    _add_diode(add, queue, ref, comp, roles, mpn)


def _led_roles(comp: dict[str, Any], nets: dict[str, Any]) -> dict[str, str]:
    pins = _pin_nets(comp)
    params = ((comp.get("pintable") or {}).get("params") or {})
    anode_pin = _pin_from_text(params.get("common_anode"))
    cathode_pin = _pin_from_text(params.get("common_cathode"))
    if anode_pin and any(pin == anode_pin for pin, _net in pins):
        return {pin: ("anode" if pin == anode_pin else "cathode") for pin, net in pins if net}
    if cathode_pin and any(pin == cathode_pin for pin, _net in pins):
        return {pin: ("cathode" if pin == cathode_pin else "anode") for pin, net in pins if net}
    positive = []
    grounded = []
    for pin, net in pins:
        if not net:
            continue
        if net in GROUND_NETS:
            grounded.append(pin)
            continue
        rail = _closed_rail_v(nets.get(net) or {})
        if rail is not None and rail > 1:
            positive.append(pin)
    if len(positive) == 1:
        hub = positive[0]
        return {pin: ("anode" if pin == hub else "cathode") for pin, net in pins if net}
    if len(grounded) == 1 and len([net for _pin, net in pins if net]) > 2:
        hub = grounded[0]
        return {pin: ("cathode" if pin == hub else "anode") for pin, net in pins if net}
    return {}


def _pin_from_text(value: Any) -> str:
    raw = str(value or "")
    mark = raw.lower().find("pin")
    if mark < 0:
        return ""
    digits = ""
    for char in raw[mark + 3:]:
        if char.isdigit():
            digits += char
        elif digits:
            break
    return digits


def _add_analog_switches(add, queue, ref, comp, mpn, nets) -> None:
    channels = (match_series(mpn) or {}).get("switch_channels") or []
    if not channels:
        return
    pins = {pin: net for pin, net in _pin_nets(comp)}
    ron = float((match_series(mpn) or {}).get("ron_ohm") or 0.0)
    for index, channel in enumerate(channels, start=1):
        left = pins.get(str(channel.get("a")))
        right = pins.get(str(channel.get("b")))
        control = pins.get(str(channel.get("control")))
        if not left or not right or not _tied_high(control, nets):
            continue
        row = {
            "ref": f"{ref}_{index}",
            "behavior": "switch",
            "plus": left,
            "minus": right,
            "on_ohm": ron,
            "tied": "on",
            "mpn": mpn,
        }
        typ = (match_series(mpn) or {}).get("ron_typ_ohm")
        if typ is not None:
            row["on_ohm_typ"] = float(typ)
        add(row)
        queue.append(left)
        queue.append(right)


def _tied_high(net: str | None, nets: dict[str, Any]) -> bool:
    if not net:
        return False
    volts = _closed_rail_v(nets.get(net) or {})
    return volts is not None and volts > 1.0


def _add_switch(add, queue, ref, comp, comps: dict[str, Any], nets: dict[str, Any]) -> None:
    mpn = str(comp.get("mpn") or "")
    series = match_series(mpn) or {}
    ce = _ce_from_roles(comp, series.get("pin_roles") or {})
    pintable = ((comp.get("pintable") or {}).get("params") or {})
    if not ce or (series.get("vce_sat_v") is None and pintable.get("vce_sat_v") is None):
        add({"ref": ref, "behavior": "unmapped_switch", "plus": "", "minus": "GND", "mpn": mpn})
        return
    collector, emitter = ce
    item = {
        "ref": ref,
        "behavior": "switch",
        "plus": collector,
        "minus": emitter,
        "mpn": mpn,
    }
    for key in ("vce_sat_ic_a", "vce_sat_ib_a"):
        if series.get(key) is not None:
            item[key] = float(series[key])
    item.update(_switch_drive(comp, comps, nets))
    add(item)
    queue.append(collector)
    queue.append(emitter)


def _switch_drive(comp: dict[str, Any], comps: dict[str, Any], nets: dict[str, Any]) -> dict[str, Any]:
    from kernel.situations import _attachments

    pins = {pin: net for pin, net in _pin_nets(comp)}
    roles = (match_series(str(comp.get("mpn") or "")) or {}).get("pin_roles") or {}
    base_pin = next((pin for pin, role in roles.items() if role == "base"), "1")
    base_net = pins.get(str(base_pin))
    info: dict[str, Any] = {}
    if not base_net:
        return info
    vbe = ((comp.get("pintable") or {}).get("params") or {}).get("vbe_v")
    for ref, _pin, typ in _attachments(base_net, comps):
        if typ != "resistor":
            continue
        ends = [net for _p, net in _pin_nets(comps.get(ref) or {})]
        other = next((net for net in ends if net != base_net), None)
        ohm = _ohm(comps.get(ref) or {})
        if not other or not ohm:
            continue
        rail = _closed_rail_v(nets.get(other) or {})
        if rail is not None and rail > 1:
            info.update(_ib(float(rail), ohm, vbe, from_supply=False))
            return _with_drive_resistor(info, ref, base_net, other)
        for r2, _p2, t2 in _attachments(other, comps):
            if t2 != "ic":
                continue
            info["pulsed"] = True
            for _ip, n in _pin_nets(comps.get(r2) or {}):
                vdd = _closed_rail_v(nets.get(n) or {})
                if vdd is not None and vdd > 1:
                    info.update(_ib(float(vdd), ohm, vbe, from_supply=True))
                    return _with_drive_resistor(info, ref, base_net, other)
            return _with_drive_resistor(info, ref, base_net, other)
    return info


def _ce_from_roles(comp: dict[str, Any], roles: dict[str, Any]) -> tuple[str, str] | None:
    pins = {pin: net for pin, net in _pin_nets(comp)}
    collector_pin = next((pin for pin, role in roles.items() if role == "collector"), None)
    emitter_pin = next((pin for pin, role in roles.items() if role == "emitter"), None)
    if not collector_pin or not emitter_pin:
        return None
    collector = pins.get(str(collector_pin))
    emitter = pins.get(str(emitter_pin))
    if not collector or not emitter:
        return None
    return collector, emitter


def _with_drive_resistor(info: dict[str, Any], ref: str, base_net: str, other: str) -> dict[str, Any]:
    info["drive_r_ref"] = ref
    info["drive_r_a"] = base_net
    info["drive_r_b"] = other
    return info


def _ib(volts: float, ohm: float, vbe: Any, *, from_supply: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"drive_v_v": volts, "drive_r_ohm": ohm, "drive_from_supply": from_supply}
    if vbe is None:
        return out
    out["drive_vbe_v"] = float(vbe)
    out["drive_ib_a"] = (volts - float(vbe)) / ohm
    return out


def _cases(result: dict[str, Any], ref: str, ohm: float) -> list[dict[str, Any]]:
    out = []
    for row in result["rows"]:
        if ref not in (row.get("verdicts") or {}):
            continue
        verdict = row["verdicts"][ref]
        power = (row.get("powers") or {}).get(ref)
        note = str(row.get("note") or "")
        if power is None:
            out.append(_pending(note, row["name"]))
            continue
        lo = float(power["lo"])
        hi = float(power["hi"])
        text = _power_text(lo, hi)
        lines = _process_lines(row, ref, ohm)
        if abs(hi - lo) > 1e-6:
            lines.append({"expr": text, "src": "各角点取齐"})
        if verdict == "不参评":
            lines.append({"expr": "不参评", "src": "端口对地不单独决定整卡"})
            out.append({
                "name_zh": row["name"],
                "v_across": (hi * ohm) ** 0.5 if ohm else 0.0,
                "p_w": hi,
                "p_lo_w": lo,
                "p_hi_w": hi,
                "i_dc": None,
                "closes_verdict": False,
                "note": note,
                "reason": note,
                "lines": lines,
            })
            continue
        if lo == 0.0 and hi == 0.0:
            out.append({
                "name_zh": row["name"],
                "v_across": 0.0,
                "p_w": 0.0,
                "i_dc": 0.0,
                "closes_verdict": True,
                "dc_open_cite": note or "电源未建立",
                "note": note,
                "reason": note,
                "lines": lines or [{"expr": "I = 0", "src": note or "电源未建立"}, {"expr": "P = 0", "src": "I = 0"}],
            })
            continue
        zh = {"通过": "是", "否": "否", "待确认": "待确认"}.get(verdict, "待确认")
        out.append({
            "name_zh": row["name"],
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
        })
    return out or [_pending("脚行为求解没有返回这只电阻")]


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


def _process_lines(row: dict[str, Any], ref: str, ohm: float) -> list[dict[str, str]]:
    lines = []
    for corner in row.get("corners") or []:
        label = str(corner.get("label") or "")
        end = (corner.get("ends") or {}).get(ref) or {}
        pins = {str(end.get("a") or ""), str(end.get("b") or "")}
        for fact in corner.get("facts") or []:
            if fact.startswith("V("):
                net = fact[2:fact.find(")")]
                if net in pins:
                    continue
            lines.append({"expr": fact, "src": label or "入参"})
        amp = abs(float((corner.get("currents") or {}).get(ref, 0.0)))
        power = float((corner.get("powers") or {}).get(ref, 0.0))
        if end:
            va = float(end.get("va") or 0.0)
            vb = float(end.get("vb") or 0.0)
            lines.append({"expr": f"V({end.get('a')}) = {va:.6g} V", "src": "节点方程"})
            lines.append({"expr": f"V({end.get('b')}) = {vb:.6g} V", "src": "节点方程"})
            lines.append({
                "expr": f"V({ref}) = |{va:.6g} − {vb:.6g}| = {abs(va - vb):.6g} V",
                "src": "两端电压相减",
            })
        lines.append({"expr": f"I({ref}) = {amp:.6g} A", "src": label or "节点方程"})
        lines.append({
            "expr": f"P({ref}) = ({amp:.6g})² × {ohm:g} = {_mw(power)}",
            "src": "电流的平方乘本只电阻",
        })
    return lines


def _power_text(lo: float, hi: float) -> str:
    if abs(hi - lo) < 1e-6:
        return f"P = {_mw(hi)}"
    return f"P = {_mw(lo)}–{_mw(hi)}"


def _mw(watts: float) -> str:
    if watts < 1:
        return f"{watts * 1000:.2f} mW"
    return f"{watts:.4g} W"


def _pin_nets(comp: dict[str, Any]) -> list[tuple[str, str]]:
    out = []
    for pin in comp.get("pins") or []:
        if not isinstance(pin, dict):
            continue
        name = str(pin.get("pin") or "")
        if "." in name:
            name = name.split(".")[-1]
        out.append((name, str(pin.get("net") or "")))
    return out


def _ohm(comp: dict[str, Any]) -> float:
    value = _parse_res_ohm(str(comp.get("value") or ""))
    return float(value or 0.0)
