# -*- coding: utf-8 -*-
"""脚行为入参上的直流情况表。求解不按位号、不按板卡分支。"""

from __future__ import annotations

from typing import Any

from solve_prog.mna import operating_point
from solve_prog.netclass import GROUND_NETS

CATALOG = {
    "hiz",
    "voltage",
    "current",
    "switch",
    "open_drain",
    "diode",
    "zener",
    "resistor",
    "port",
}
_SWITCH = {"switch", "open_drain"}
_LIMIT_RATIO = 0.5


def solve(elements: list[dict[str, Any]]) -> dict[str, Any]:
    """由脚行为入参列出情况并给出每只电阻的功耗区间。"""
    known = [item for item in elements if item.get("behavior") in CATALOG]
    unknown = [item for item in elements if item.get("behavior") not in CATALOG]
    resistors = [item for item in known if item["behavior"] == "resistor"]
    supplies = [item for item in known if item["behavior"] == "voltage"]
    currents = [item for item in known if item["behavior"] == "current"]
    diodes = [item for item in known if item["behavior"] == "diode"]
    zeners = [item for item in known if item["behavior"] == "zener"]
    ports = [item for item in known if item["behavior"] == "port"]
    switches = [item for item in known if item["behavior"] in _SWITCH]
    branches = [_branch(item) for item in resistors]
    tied_on = [item for item in switches if item.get("tied") == "on"]
    for item in tied_on:
        ohm = item.get("on_ohm")
        if ohm:
            branches.append((item["ref"], item["plus"], item["minus"], float(ohm)))
    tied_sources = []
    for item in tied_on:
        if item.get("on_ohm"):
            continue
        source = _drop_source(item)
        source["load"] = True
        tied_sources.append(source)
    free = [item for item in switches if item.get("tied") not in {"on", "off"}]
    rows = _unknown_rows(unknown, resistors)
    overflow = 2 ** (len(free) + len(ports)) > 32
    if overflow:
        rows.append(
            {
                "name": "离散组合超过 32 组",
                "participates": True,
                "powers": {item["ref"]: None for item in resistors},
                "verdicts": {item["ref"]: "待确认" for item in resistors},
                "note": "自由开关与端口的组合超过 32 组，不编功耗",
            }
        )
    else:
        if any(item.get("can_be_off", True) for item in supplies):
            rows.extend(_supply_off(resistors, branches, supplies, currents))
        rows.extend(
            _supply_on(
                resistors,
                branches,
                supplies,
                currents,
                diodes,
                zeners,
                ports,
                free,
                tied_sources,
            )
        )
    rows = _merge(rows)
    objects = {
        item["ref"]: _object_result(item, rows)
        for item in resistors
    }
    return {
        "rows": rows,
        "objects": objects,
        "conclusion": _card(list(objects.values())),
    }


def _branch(item: dict[str, Any]) -> tuple[str, str, str, float]:
    return (item["ref"], item["plus"], item["minus"], float(item["ohm"]))


def _drop_source(item: dict[str, Any]) -> dict[str, Any]:
    source = {
        "plus": item["plus"],
        "minus": item["minus"],
        "volts": float(item.get("on_drop_v") or 0.0),
        "ref": item["ref"],
    }
    for key in ("vce_sat_ic_a", "vce_sat_ib_a", "drive_ib_a", "drive_v_v", "drive_r_ohm", "drive_vbe_v", "drive_from_supply", "on_ohm_typ"):
        if item.get(key) is not None:
            source[key] = item[key]
    return source


def _unknown_rows(unknown: list[dict[str, Any]], resistors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in unknown:
        behavior = item.get("behavior")
        touched = {
            ref["ref"]
            for ref in resistors
            if ref["plus"] in {item.get("plus"), item.get("minus")}
            or ref["minus"] in {item.get("plus"), item.get("minus")}
        }
        powers = {ref: None for ref in touched} or {item["ref"]: None}
        rows.append(
            {
                "name": f"{item.get('ref')} 的脚行为不在清单",
                "participates": True,
                "powers": powers,
                "verdicts": {ref: "待确认" for ref in powers},
                "note": f"行为 {behavior} 不在清单，不填功耗",
            }
        )
    return rows


def _supply_off(resistors, branches, supplies, currents) -> list[dict[str, Any]]:
    sources = [
        {"plus": item["plus"], "minus": item["minus"], "volts": 0.0, "ref": item["ref"]}
        for item in supplies
    ]
    point = operating_point(branches, sources, _current_stamps(currents))
    facts = [f"V({item['plus']}) = 0 V" for item in supplies]
    return [_power_row("未上电", True, resistors, [_corner(point, branches, "未上电", facts)], "电源为 0")]


def _supply_on(
    resistors,
    branches,
    supplies,
    currents,
    diodes,
    zeners,
    ports,
    free,
    tied_sources,
) -> list[dict[str, Any]]:
    rows = []
    masks = range(2 ** (len(free) + len(ports)))
    for mask in masks:
        switch_on = {}
        for index, item in enumerate(free):
            switch_on[item["ref"]] = bool(mask & (1 << index))
        port_short = {}
        for index, item in enumerate(ports):
            port_short[item["ref"]] = bool(mask & (1 << (len(free) + index)))
        rows.extend(
            _one_mode(
                resistors,
                branches,
                supplies,
                currents,
                diodes,
                zeners,
                ports,
                free,
                tied_sources,
                switch_on,
                port_short,
            )
        )
    return rows


def _one_mode(
    resistors,
    branches,
    supplies,
    currents,
    diodes,
    zeners,
    ports,
    free,
    tied_sources,
    switch_on,
    port_short,
) -> list[dict[str, Any]]:
    name, participates = _mode_name(free, ports, switch_on, port_short)
    fixed, extra = _fixed_sources(supplies, free, ports, tied_sources, switch_on, port_short)
    network = branches + extra
    if not diodes and not zeners:
        point = operating_point(network, fixed, _current_stamps(currents))
        facts = [f"V({source['plus']}) = {float(source['volts']):g} V" for source in fixed]
        return [_power_row(name, participates, resistors, [_corner(point, network, name, facts)], "电阻与电压源")]
    missing = [item["ref"] for item in diodes if item.get("vf_max_v") is None]
    if missing:
        return [
            {
                "name": name,
                "participates": participates,
                "powers": {item["ref"]: None for item in resistors},
                "verdicts": {item["ref"]: "待确认" if participates else "不参评" for item in resistors},
                "note": "、".join(missing) + " 缺少 vf_max_v",
            }
        ]
    failed = False
    zener_held = False
    refs = "、".join(item["ref"] for item in zeners)
    rows = []
    has_model = bool(diodes) and all(item.get("vf_model_v") is not None for item in diodes)
    probe = _solve_vf(
        network, fixed, currents, diodes, zeners, "vz_max_v", has_model, None if has_model else 1.0
    )
    zener_held = bool(probe[2])
    switched = any(switch_on.values())
    grounded = any(port_short.values())
    if zener_held and zeners and not grounded and not switched:
        for label, picker in (("下限", "vz_min_v"), ("上限", "vz_max_v")):
            for use_model, scale in _vf_points(diodes, extra_max=True):
                point, on_d, on_z, ok = _solve_vf(
                    network, fixed, currents, diodes, zeners, picker, use_model, scale
                )
                failed = failed or not ok
                if not on_z:
                    continue
                vz = float(zeners[0][picker if picker in zeners[0] else "vz_max_v"])
                vf = _vf_value(diodes, use_model, scale)
                tag = f"稳压 {refs} {label} {vz:g} V，Vf {vf:g} V"
                facts = _stamp_facts(fixed, diodes, zeners, on_d, on_z, scale, _vz_picker(picker), use_model, point)
                rows.append(_power_row(tag, participates, resistors, [_corner(point, network, tag, facts)], tag))
        rows.extend(_nominal_rows(name, participates, resistors, network, fixed, currents, diodes, zeners))
    else:
        points = _vf_points(diodes, extra_max=True)
        if not points:
            points = [(False, 0.0)]
        for use_model, scale in points:
            point, on_d, on_z, ok = _solve_vf(
                network, fixed, currents, diodes, zeners, "vz_max_v", use_model, scale
            )
            failed = failed or not ok
            vf = _vf_value(diodes, use_model, scale)
            tag = f"{name}，Vf {vf:g} V" if diodes else name
            facts = _stamp_facts(fixed, diodes, zeners, on_d, on_z, scale, _vz_picker("vz_max_v"), use_model, point)
            rows.append(_power_row(tag, participates, resistors, [_corner(point, network, tag, facts)], tag))
        if switched and not grounded and any(item.get("pulsed") for item in free if switch_on.get(item["ref"])):
            for row in rows:
                if not row.get("participates"):
                    continue
                row["note"] = (row.get("note") or "") + "；高电平瞬时功耗。驱动不是闭合电源，周期和占空比未从网表闭合"
                for ref in row["verdicts"]:
                    row["verdicts"][ref] = "待确认"
    if failed:
        for row in rows:
            row["note"] += "；区段回代未稳定"
            for ref in row["verdicts"]:
                if row["participates"]:
                    row["verdicts"][ref] = "待确认"
    return rows


def _vz_picker(picker: str):
    return lambda item, picker=picker: float(item[picker if picker in item else "vz_max_v"])


def _vf_value(diodes, use_model: bool, scale) -> float:
    if not diodes:
        return 0.0
    if use_model:
        return float(diodes[0]["vf_model_v"])
    return float(diodes[0]["vf_max_v"]) * float(0.0 if scale is None else scale)


def _vf_points(diodes, extra_max: bool) -> list[tuple[bool, float | None]]:
    if not diodes:
        return []
    points: list[tuple[bool, float | None]] = []
    if all(item.get("vf_model_v") is not None for item in diodes):
        points.append((True, None))
    if extra_max and all(item.get("vf_max_v") is not None for item in diodes):
        model = diodes[0].get("vf_model_v")
        vmax = float(diodes[0]["vf_max_v"])
        if model is None or abs(float(model) - vmax) > 1e-6:
            points.append((False, 1.0))
    if not points:
        points.append((False, 1.0))
        points.append((False, 0.0))
    return points


def _solve_vf(network, fixed, currents, diodes, zeners, picker, use_model, scale):
    return _regions(
        network,
        fixed,
        currents,
        diodes,
        zeners,
        scale,
        _vz_picker(picker),
        use_model=use_model,
    )


def _nominal_rows(name, participates, resistors, network, fixed, currents, diodes, zeners) -> list[dict[str, Any]]:
    if "导通" in name or "接地" in name:
        return []
    ready = [
        item
        for item in zeners
        if item.get("vz_nom_v") is not None and item.get("izt_a") is not None and item.get("zzt_ohm") is not None
    ]
    if not ready or any(item.get("vf_model_v") is None for item in diodes):
        return []
    vz = {item["ref"]: float(item["vz_nom_v"]) for item in ready}
    point = None
    on_z: set[int] = set()
    for _ in range(8):
        vz_of = lambda item, vz=vz: vz.get(item["ref"], float(item.get("vz_nom_v") or item["vz_max_v"]))
        point, on_d, on_z, ok = _regions(
            network,
            fixed,
            currents,
            diodes,
            zeners,
            None,
            vz_of,
            use_model=True,
        )
        if not ok or not on_z:
            return []
        updated = dict(vz)
        for index in on_z:
            item = zeners[index]
            if item["ref"] not in vz:
                continue
            iz = _current_of_ref(point, item["ref"])
            updated[item["ref"]] = float(item["vz_nom_v"]) + (iz - float(item["izt_a"])) * float(item["zzt_ohm"])
        if all(abs(updated[ref] - vz[ref]) < 1e-4 for ref in vz):
            vz = updated
            break
        vz = updated
    if point is None:
        return []
    facts = _stamp_facts(fixed, diodes, zeners, on_d, on_z, None, lambda item: float(vz.get(item["ref"], 0.0)), True, point)
    corner = _corner(point, network, "标称", facts)
    refs = "、".join(item["ref"] for item in ready)
    nom = float(ready[0]["vz_nom_v"])
    vf = _vf_value(diodes, True, None)
    row = _power_row(
        f"稳压 {refs} 标称 {nom:g} V，Vf {vf:g} V，计入动态电阻",
        participates,
        resistors,
        [corner],
        "标称电压加动态电阻，正向压降取模型值",
    )
    row["model"] = True
    _append_ron_typ(corner, network, fixed, currents, diodes, zeners, ready)
    return [row]


def _append_ron_typ(corner, network, fixed, currents, diodes, zeners, ready) -> None:
    loads = [source for source in fixed if source.get("load") and source.get("on_ohm_typ")]
    if not loads or corner is None:
        return
    typ = float(loads[0]["on_ohm_typ"])
    fixed_typ = [source for source in fixed if not source.get("load")]
    alt = list(network) + [(source["ref"], source["plus"], source["minus"], typ) for source in loads]
    vz = {item["ref"]: float(item["vz_nom_v"]) for item in ready}
    point = None
    on_z: set[int] = set()
    for _ in range(8):
        vz_of = lambda item, vz=vz: vz.get(item["ref"], float(item["vz_nom_v"]))
        point, _on_d, on_z, ok = _regions(alt, fixed_typ, currents, diodes, zeners, None, vz_of, use_model=True)
        if not ok or not on_z:
            return
        updated = dict(vz)
        for index in on_z:
            item = zeners[index]
            if item["ref"] not in updated:
                continue
            iz = _current_of_ref(point, item["ref"])
            updated[item["ref"]] = float(item["vz_nom_v"]) + (iz - float(item["izt_a"])) * float(item["zzt_ohm"])
        if all(abs(updated[ref] - vz[ref]) < 1e-4 for ref in vz):
            vz = updated
            break
        vz = updated
    if point is None or not on_z:
        return
    item = zeners[next(iter(on_z))]
    iz = _current_of_ref(point, item["ref"])
    corner["facts"].append(
        f"对照：导通电阻按手册典型 {typ:g} Ω 时，Vz({item['ref']}) = {vz[item['ref']]:.4g} V，"
        f"Iz = {iz * 1000:.4g} mA。上面的 Iz 仍是导通电阻按 0 的下界"
    )


def _regions(network, fixed, currents, diodes, zeners, scale, vz_of, use_model=False):
    on_d: set[int] = set()
    on_z: set[int] = set()
    point: dict[str, Any] = {"voltages": {}, "source_i": {}, "singular": True, "labels": []}
    for _ in range(12):
        sources = list(fixed)
        labels: list[tuple[int, str, int, str]] = []
        for index, item in enumerate(diodes):
            if index not in on_d:
                continue
            vf = float(item["vf_model_v"]) if use_model else float(item["vf_max_v"]) * float(scale or 0.0)
            sources.append({"plus": item["plus"], "minus": item["minus"], "volts": vf, "ref": item["ref"]})
            labels.append((len(sources) - 1, "diode", index, item["ref"]))
        for index, item in enumerate(zeners):
            if index not in on_z:
                continue
            sources.append({"plus": item["plus"], "minus": item["minus"], "volts": vz_of(item), "ref": item["ref"]})
            labels.append((len(sources) - 1, "zener", index, item["ref"]))
        point = operating_point(network, sources, _current_stamps(currents))
        point["labels"] = labels
        if point["singular"]:
            return point, on_d, on_z, False
        new_d: set[int] = set()
        new_z: set[int] = set()
        for index, item in enumerate(diodes):
            vf = float(item["vf_model_v"]) if use_model else float(item["vf_max_v"]) * float(scale or 0.0)
            forward = _volts(point, item["plus"]) - _volts(point, item["minus"])
            if index in on_d:
                current = _stamp_current(point, "diode", index)
                # 导通后电流接近 0 仍保持钳位，避免空载二极管来回通断。
                if current > 1e-6 or (current > -1e-6 and forward + 1e-3 >= vf):
                    new_d.add(index)
            elif forward > vf + 1e-4:
                new_d.add(index)
        for index, item in enumerate(zeners):
            reverse = _volts(point, item["plus"]) - _volts(point, item["minus"])
            knee = float(item.get("izk_a") or 0.0)
            if index in on_z:
                if _stamp_current(point, "zener", index) > knee:
                    new_z.add(index)
            elif reverse > vz_of(item) + 1e-4:
                new_z.add(index)
        if new_d == on_d and new_z == on_z:
            return point, on_d, on_z, True
        on_d, on_z = new_d, new_z
    return point, on_d, on_z, False


def _fixed_sources(supplies, free, ports, tied_sources, switch_on, port_short):
    sources = [
        {"plus": item["plus"], "minus": item["minus"], "volts": float(item["volts"]), "ref": item["ref"]}
        for item in supplies
    ]
    sources.extend(tied_sources)
    shorted = set()
    for item in ports:
        if not port_short.get(item["ref"]):
            continue
        sources.append({"plus": item["plus"], "minus": item["minus"], "volts": 0.0, "ref": item["ref"]})
        shorted.add(frozenset((item["plus"], item["minus"])))
    extra = []
    for item in free:
        if not switch_on.get(item["ref"]):
            continue
        pair = frozenset((item["plus"], item["minus"]))
        if pair in shorted:
            continue
        if item.get("on_ohm") is not None:
            extra.append((item["ref"], item["plus"], item["minus"], float(item["on_ohm"])))
        else:
            sources.append(_drop_source(item))
    return sources, extra


def _mode_name(free, ports, switch_on, port_short) -> tuple[str, bool]:
    parts = []
    participates = True
    for item in free:
        if switch_on.get(item["ref"]):
            parts.append(f"{item['ref']} 导通")
    for item in ports:
        if not port_short.get(item["ref"]):
            continue
        parts.append(f"{item['plus']} 被 {item['ref']} 外部接地")
        if not item.get("participates", False):
            participates = False
    if not parts:
        parts.append("直流工作")
    return "，".join(parts), participates


def _corner(point, network, label, facts) -> dict[str, Any] | None:
    if not point or point.get("singular"):
        return None
    powers = {}
    currents = {}
    ends = {}
    for ref, a, b, ohm in network:
        amp = float(point["resistor_i"].get(ref, 0.0))
        powers[ref] = amp * amp * ohm
        currents[ref] = amp
        ends[ref] = {"a": a, "b": b, "va": _volts(point, a), "vb": _volts(point, b)}
    voltages = {str(net): float(value) for net, value in (point.get("voltages") or {}).items()}
    return {
        "label": label,
        "powers": powers,
        "currents": currents,
        "ends": ends,
        "voltages": voltages,
        "facts": list(facts),
        "gmin": bool(point.get("gmin")),
    }


def _stamp_facts(fixed, diodes, zeners, on_d, on_z, scale, vz_of, use_model, point=None) -> list[str]:
    facts = []
    for i, source in enumerate(fixed):
        if source.get("load"):
            continue
        volts = float(source["volts"])
        plus, minus = str(source["plus"]), str(source["minus"])
        if source.get("vce_sat_ic_a") is not None:
            text = f"V({plus}) = {volts:g} V，{source.get('ref')} 饱和压降"
            if source.get("vce_sat_ib_a") is not None:
                text += (
                    f"，手册条件 Ic={float(source['vce_sat_ic_a']) * 1000:g} mA、"
                    f"Ib={float(source['vce_sat_ib_a']) * 1000:g} mA"
                )
            facts.append(text)
            if point is not None:
                ic = abs(float(point["source_i"].get(i, 0.0)))
                facts.append(f"Ic({source.get('ref')}) = {ic * 1000:.4g} mA，节点方程")
            if source.get("drive_ib_a") is not None:
                how = "高电平按芯片供电轨估计" if source.get("drive_from_supply") else "驱动端为闭合电源"
                facts.append(
                    f"Ib({source.get('ref')}) = {float(source['drive_ib_a']) * 1000:.4g} mA，"
                    f"{how} {float(source['drive_v_v']):g} V，基极电阻 {float(source['drive_r_ohm']):g} Ω，"
                    f"VBE 取手册 {float(source.get('drive_vbe_v') or 0):g} V"
                )
        elif minus in GROUND_NETS and abs(volts) > 1e-9:
            facts.append(f"V({plus}) = {volts:g} V，电压源")
        elif abs(volts) <= 1e-12:
            facts.append(f"{source.get('ref')} 把 {plus} 接到 {minus}")
        else:
            text = f"V({plus}) − V({minus}) = {volts:g} V，{source.get('ref')}"
            if source.get("vce_sat_ic_a") is not None and source.get("vce_sat_ib_a") is not None:
                text += (
                    f"，手册条件 Ic={float(source['vce_sat_ic_a']) * 1000:g} mA、"
                    f"Ib={float(source['vce_sat_ib_a']) * 1000:g} mA"
                )
            facts.append(text)
            if point is not None and source.get("vce_sat_ic_a") is not None:
                ic = abs(float(point["source_i"].get(i, 0.0)))
                facts.append(f"Ic({source.get('ref')}) = {ic * 1000:.4g} mA，节点方程")
            if source.get("drive_ib_a") is not None:
                how = "高电平按芯片供电轨估计" if source.get("drive_from_supply") else "驱动端为闭合电源"
                facts.append(
                    f"Ib({source.get('ref')}) = {float(source['drive_ib_a']) * 1000:.4g} mA，"
                    f"{how} {float(source['drive_v_v']):g} V，基极电阻 {float(source['drive_r_ohm']):g} Ω，"
                    f"VBE 取手册 {float(source.get('drive_vbe_v') or 0):g} V"
                )
    seen_feed = set()
    for index in on_d:
        item = diodes[index]
        vf = float(item["vf_model_v"]) if use_model else float(item["vf_max_v"]) * float(scale or 0.0)
        if use_model:
            origin = "工作点模型" if item.get("vf_model_v_origin") == "model" or item.get("vf_model_origin") == "model" else "规格书"
            extra = ""
            if item.get("vf_max_v") is not None:
                extra = f"，手册上限 {float(item['vf_max_v']):g} V"
            facts.append(f"Vf({item['ref']}) = {vf:g} V，{origin}{extra}")
        else:
            facts.append(f"Vf({item['ref']}) = {vf:g} V，手册上限")
        feed = str(item["plus"])
        if point is not None and feed not in seen_feed:
            seen_feed.add(feed)
            facts.append(f"V({feed}) = {_volts(point, feed):.6g} V，{item['ref']} 阳极，节点方程")
    for index in on_z:
        item = zeners[index]
        vz = float(vz_of(item))
        facts.append(f"Vz({item['ref']}) = {vz:g} V，{_vz_source(item, vz)}")
        if point is None:
            continue
        iz = _current_of_ref(point, item["ref"])
        izk = item.get("izk_a")
        text = f"Iz({item['ref']}) = {iz * 1000:.4g} mA，节点方程"
        if any(source.get("load") for source in fixed):
            text += "；模拟开关导通电阻按 0，本值为下界"
        if izk is not None:
            text += f"；Izk = {float(izk) * 1000:.4g} mA，规格书"
            text += "；高于膝点" if iz > float(izk) else "；不高于膝点"
        facts.append(text)
    unique = []
    seen = set()
    for fact in facts:
        if fact in seen:
            continue
        seen.add(fact)
        unique.append(fact)
    return unique


def _vz_source(item: dict[str, Any], vz: float) -> str:
    lo = item.get("vz_min_v")
    hi = item.get("vz_max_v")
    nom = item.get("vz_nom_v")
    if lo is not None and abs(vz - float(lo)) <= 1e-3:
        return "规格书下限"
    if hi is not None and abs(vz - float(hi)) <= 1e-3:
        return "规格书上限"
    if nom is not None:
        return "标称加动态电阻"
    return "规格书"


def _power_row(name, participates, resistors, samples, note) -> dict[str, Any]:
    corners = [sample for sample in samples if sample]
    powers = {}
    verdicts = {}
    for item in resistors:
        ref = item["ref"]
        values = [corner["powers"][ref] for corner in corners if ref in corner["powers"]]
        if not values:
            powers[ref] = None
            verdicts[ref] = "待确认" if participates else "不参评"
            continue
        lo, hi = min(values), max(values)
        powers[ref] = {"lo": lo, "hi": hi}
        verdicts[ref] = _judge(lo, hi, _limit(item), participates)
    return {
        "name": name,
        "participates": participates,
        "powers": powers,
        "verdicts": verdicts,
        "note": note,
        "corners": corners,
        "gmin": any(bool(corner.get("gmin")) for corner in corners),
    }


def _judge(lo: float, hi: float, limit: float | None, participates: bool) -> str:
    if not participates:
        return "不参评"
    if limit is None:
        return "待确认"
    if lo > limit and hi > limit:
        return "否"
    if hi <= limit:
        return "通过"
    return "待确认"


def _limit(item: dict[str, Any]) -> float | None:
    rated = item.get("p_rated_w")
    if rated is None:
        return None
    return float(item.get("limit_ratio") or _LIMIT_RATIO) * float(rated)


def _powers(point, branches) -> dict[str, float]:
    if point.get("singular"):
        return {}
    out = {}
    for ref, _a, _b, ohm in branches:
        amp = point["resistor_i"].get(ref, 0.0)
        out[ref] = amp * amp * ohm
    return out


def _current_stamps(currents) -> list[dict[str, Any]]:
    return [
        {"plus": item["plus"], "minus": item["minus"], "amps": float(item["amps"])}
        for item in currents
    ]


def _volts(point, net: str) -> float:
    if net in GROUND_NETS:
        return 0.0
    return float(point["voltages"].get(net, 0.0))


def _stamp_current(point, kind: str, index: int) -> float:
    for source_index, label, owner, _ref in point.get("labels") or []:
        if label == kind and owner == index:
            return float(point["source_i"].get(source_index, 0.0))
    return 0.0


def _current_of_ref(point, ref: str) -> float:
    for source_index, _kind, _owner, label_ref in point.get("labels") or []:
        if label_ref == ref:
            return float(point["source_i"].get(source_index, 0.0))
    return 0.0


def _merge(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    index: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = (
            row["participates"],
            tuple(sorted((ref, _span(value)) for ref, value in row["powers"].items())),
            tuple(sorted(row["verdicts"].items())),
        )
        found = index.get(key)
        if found is None:
            index[key] = row
            merged.append(row)
            continue
        if row["name"] not in found["name"]:
            found["name"] += "；" + row["name"]
    return merged


def _span(value) -> tuple:
    if not isinstance(value, dict):
        return (None, None)
    return (round(value["lo"], 6), round(value["hi"], 6))


def _object_result(item: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    ref = item["ref"]
    verdicts = [
        row["verdicts"].get(ref)
        for row in rows
        if row.get("participates") and ref in row["verdicts"]
    ]
    if "否" in verdicts:
        conclusion = "否"
    elif "待确认" in verdicts:
        conclusion = "待确认"
    elif verdicts:
        conclusion = "通过"
    else:
        conclusion = "待确认"
    return {"conclusion": conclusion, "limit_w": _limit(item)}


def _card(objects: list[dict[str, Any]]) -> str:
    conclusions = [item["conclusion"] for item in objects]
    if "否" in conclusions:
        return "否"
    if "待确认" in conclusions:
        return "待确认"
    if conclusions:
        return "通过"
    return "待确认"
