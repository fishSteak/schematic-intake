# -*- coding: utf-8 -*-
"""通路情况分析：先认出每只电阻的通路种类，再只列该种类的稳态。

P11-A01 全部走这里。种类按通路认，不按位号。未登记的种类不闭合是/否。
同一串电流共用一份情况列表。种类内部的态是并列的，不是嵌套 if 的笛卡尔积。
"""

from __future__ import annotations

from typing import Any

from kernel.resistor_network import (
    GROUND_NETS,
    _closed_rail_v,
    _comp_dnp,
    _dc_open_stress,
    _ic_vdd_net,
    _is_cmos_output_pin,
    _is_high_z_ic_pin,
    _is_oc_output_pin,
    _parse_res_ohm,
    _pin_nets,
    _series_unknown_load_net,
    _switch_base_hits,
    _switch_ce_nets,
    _type_of,
    _unique_pin_nets,
    is_voltage_follower_net,
    two_terminal_dc_open,
)

CLAMP_TYPES = {"diode", "led"}
PORT_TYPES = {"connector", "header"}
SWITCH_TYPES = {"transistor", "mosfet", "bjt", "fet", "jfet"}


def _nets(kg: dict[str, Any]) -> dict[str, Any]:
    return kg.get("nets") or {}


def _comps(kg: dict[str, Any]) -> dict[str, Any]:
    return kg.get("components") or {}


def _resistor_ohm(c: dict[str, Any]) -> float | None:
    return _parse_res_ohm(str(c.get("value") or ""))


def _attachments(net: str, comps: dict[str, Any]) -> list[tuple[str, str, str]]:
    out = []
    for ref, c in comps.items():
        if _comp_dnp(c):
            continue
        for pin, n in _pin_nets(c):
            if n == net:
                out.append((ref, pin, _type_of(ref, c)))
    return out


def _resistor_ends(comps: dict[str, Any]) -> dict[str, tuple[str, str, float]]:
    ends: dict[str, tuple[str, str, float]] = {}
    for ref, c in comps.items():
        if _type_of(ref, c) != "resistor" or _comp_dnp(c):
            continue
        ohm = _resistor_ohm(c)
        uniq = _unique_pin_nets(c)
        if ohm is None or ohm <= 0 or len(uniq) != 2:
            continue
        ends[ref] = (uniq[0], uniq[1], float(ohm))
    return ends


def _resistors_on(net: str, ends: dict[str, tuple[str, str, float]]) -> list[str]:
    return [ref for ref, (a, b, _) in ends.items() if net in (a, b)]


def _other(ref: str, net: str, ends: dict[str, tuple[str, str, float]]) -> str:
    a, b, _ = ends[ref]
    return b if a == net else a


def _walk(start_net: str, came: str | None, ends: dict[str, tuple[str, str, float]]) -> tuple[list[str], str]:
    """Walk a resistor-only chain. Stop at a rail, a branch, or a non-series node."""
    refs: list[str] = []
    net = start_net
    prev = came
    while True:
        here = [r for r in _resistors_on(net, ends) if r != prev]
        if len(here) != 1:
            return refs, net
        ref = here[0]
        refs.append(ref)
        nxt = _other(ref, net, ends)
        prev = ref
        net = nxt
        if len(_resistors_on(net, ends)) != 2:
            return refs, net


def _is_supply(net: str, nets: dict[str, Any]) -> bool:
    if net in GROUND_NETS:
        return False
    return _closed_rail_v(nets.get(net) or {}) is not None


def _steering_only(net: str, comps: dict[str, Any]) -> bool:
    """钳位端只有接到 CMOS 脚的转向二极管，没有稳压、开关或端口。"""
    devs = _clamp_devices(net, comps)
    if not devs:
        return False
    if any(typ in PORT_TYPES | SWITCH_TYPES for _ref, _pin, typ in devs):
        return False
    for ref, _pin, typ in devs:
        if typ not in CLAMP_TYPES:
            return False
        other = [n for _p, n in _pin_nets(comps.get(ref) or {}) if n and n != net]
        if not other:
            return False
        for n in other:
            cmos = False
            for r2, pin, t2 in _attachments(n, comps):
                if t2 == "ic" and (
                    _is_cmos_output_pin(r2, pin, comps) or _is_high_z_ic_pin(r2, pin, comps)
                ):
                    cmos = True
            if not cmos:
                return False
    return True


def _clamp_devices(net: str, comps: dict[str, Any]) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for ref, pin, typ in _attachments(net, comps):
        if typ in CLAMP_TYPES | PORT_TYPES:
            out.append((ref, pin, typ))
            continue
        if typ not in SWITCH_TYPES:
            continue
        ce = _switch_ce_nets(ref, comps.get(ref) or {})
        if ce and net in ce:
            out.append((ref, pin, typ))
    return out


def find_series_clamp_groups(kg: dict[str, Any]) -> list[dict[str, Any]]:
    """串联电阻，一端是已闭合电源，另一端只接一只电阻并且挂着钳位/开关/端口。"""
    comps = _comps(kg)
    nets = _nets(kg)
    ends = _resistor_ends(comps)
    seen: set[frozenset[str]] = set()
    groups: list[dict[str, Any]] = []
    for ref, (na, nb, _) in ends.items():
        left_refs, left_net = _walk(na, ref, ends)
        right_refs, right_net = _walk(nb, ref, ends)
        chain = list(reversed(left_refs)) + [ref] + right_refs
        key = frozenset(chain)
        if key in seen:
            continue
        seen.add(key)
        supply = exit_net = None
        if _is_supply(left_net, nets) and not _is_supply(right_net, nets):
            supply, exit_net = left_net, right_net
        elif _is_supply(right_net, nets) and not _is_supply(left_net, nets):
            supply, exit_net = right_net, left_net
        if not supply or not exit_net:
            continue
        if exit_net in GROUND_NETS:
            continue
        if len(_resistors_on(exit_net, ends)) != 1:
            continue
        if not _clamp_devices(exit_net, comps):
            continue
        if _steering_only(exit_net, comps):
            continue
        ordered: list[str] = []
        nodes = [supply]
        net = supply
        prev = None
        guard = 0
        while guard < 32:
            guard += 1
            here = [r for r in _resistors_on(net, ends) if r != prev and r in key]
            if len(here) != 1:
                break
            nxt_ref = here[0]
            ordered.append(nxt_ref)
            prev = nxt_ref
            net = _other(nxt_ref, net, ends)
            nodes.append(net)
            if net == exit_net:
                break
        if set(ordered) != key:
            continue
        r_sum = sum(ends[r][2] for r in ordered)
        groups.append(
            {
                "refs": ordered,
                "ohms": {r: ends[r][2] for r in ordered},
                "supply_net": supply,
                "exit_net": exit_net,
                "v_supply": float(_closed_rail_v(nets.get(supply) or {}) or 0.0),
                "r_sum": r_sum,
                "nodes": nodes,
                "group_id": "series:" + "-".join(ordered),
            }
        )
    return groups


def _zener_on_net(net: str, nets: dict[str, Any]) -> tuple[float, float, str] | None:
    rail = (nets.get(net) or {}).get("rail") or {}
    src = rail.get("source") if isinstance(rail.get("source"), dict) else {}
    if src.get("kind") != "zener_clamp":
        return None
    if rail.get("value") is None:
        return None
    hi = float(src.get("vz_max_v") if src.get("vz_max_v") is not None else rail["value"])
    lo = float(src.get("vz_min_v") if src.get("vz_min_v") is not None else hi)
    return lo, hi, str(src.get("ref") or "稳压管")


def _vf(ref: str, comps: dict[str, Any]) -> float | None:
    params = ((comps.get(ref) or {}).get("pintable") or {}).get("params") or {}
    val = params.get("vf_v")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _vce(ref: str, comps: dict[str, Any]) -> float | None:
    params = ((comps.get(ref) or {}).get("pintable") or {}).get("params") or {}
    for key in ("vce_sat_v", "vce_v"):
        if params.get(key) is not None:
            try:
                return float(params[key])
            except (TypeError, ValueError):
                return None
    return None


def _hub_cathodes(exit_net: str, comps: dict[str, Any]) -> list[str]:
    """三端器件的 3 脚在钳位节点上时，其余非地脚才是钳位阴极。"""
    out: list[str] = []
    for ref, _pin, typ in _clamp_devices(exit_net, comps):
        if typ not in CLAMP_TYPES:
            continue
        pins = _pin_nets(comps.get(ref) or {})
        by_pin = {p: n for p, n in pins}
        if by_pin.get("3") != exit_net:
            continue
        for pin, net in pins:
            if pin == "3" or not net or net == exit_net or net in GROUND_NETS:
                continue
            if net not in out:
                out.append(net)
    return out


def build_clamp_situations(group: dict[str, Any], kg: dict[str, Any]) -> list[dict[str, Any]]:
    comps = _comps(kg)
    nets = _nets(kg)
    exit_net = group["exit_net"]
    situations: list[dict[str, Any]] = [
        {
            "id": "supply_off",
            "name": "未上电",
            "i_dc": 0.0,
            "vc": None,
            "note": f"{group['supply_net']} 未建立，串联电流为 0",
            "closes_verdict": True,
        }
    ]
    cathodes = _hub_cathodes(exit_net, comps)
    zener_net = None
    diode_refs = [
        ref for ref, _pin, typ in _clamp_devices(exit_net, comps) if typ in CLAMP_TYPES
    ]
    for cath in cathodes:
        if _zener_on_net(cath, nets):
            zener_net = cath
            break
    vf_vals = [_vf(r, comps) for r in diode_refs]
    vf_known = [v for v in vf_vals if v is not None]
    vf = vf_known[0] if len(vf_known) == len(diode_refs) and vf_known else None
    vf_missing = [r for r, v in zip(diode_refs, vf_vals) if v is None]

    if zener_net:
        lo, hi, zref = _zener_on_net(zener_net, nets)  # type: ignore[misc]
        for label, vz in (("下限", lo), ("上限", hi)):
            drop = vf or 0.0
            situations.append(
                {
                    "id": f"zener_{label}",
                    "name": f"稳压管钳位 {zref} {label} {vz:g} V",
                    "vc": vz + drop,
                    "vc_src": (
                        f"{zref} 手册{label} {vz:g} V"
                        + (f" + 二极管正向压降 {vf:g} V" if vf is not None else "；二极管正向压降未抽出，按 0，不另加")
                    ),
                    "note": (
                        f"{exit_net} 经二极管到 {zener_net}。"
                        + (f"正向压降 {drop:g} V。" if vf is not None else f"{'、'.join(vf_missing)} 正向压降未抽出，式中先按 0。")
                    ),
                    "closes_verdict": True,
                    "drop_assumed_zero": vf is None,
                }
            )

    switches: list[str] = []
    switch_nets = [exit_net] + cathodes
    for net in switch_nets:
        for sref, _pin, typ in _attachments(net, comps):
            if typ not in SWITCH_TYPES or sref in switches:
                continue
            ce = _switch_ce_nets(sref, comps.get(sref) or {})
            if ce and net in ce and net not in GROUND_NETS:
                switches.append(sref)
    for sref in switches:
        vce = _vce(sref, comps)
        drop = (vce or 0.0) + (vf or 0.0)
        missing = []
        if vce is None:
            missing.append(f"{sref} 饱和压降")
        if vf is None and diode_refs:
            missing.append("二极管正向压降")
        situations.append(
            {
                "id": f"switch_on_{sref}",
                "name": f"{sref} 导通",
                "vc": drop,
                "vc_src": (
                    (f"{sref} 饱和压降 {vce:g} V" if vce is not None else f"{sref} 饱和压降未抽出，按 0")
                    + (" + 二极管正向压降 " + (f"{vf:g} V" if vf is not None else "未抽出，按 0") if diode_refs else "")
                ),
                "note": f"{sref} 导通后钳位节点被拉低。" + ("、".join(missing) + "未抽出，式中先按 0。" if missing else ""),
                "closes_verdict": True,
                "drop_assumed_zero": bool(missing),
            }
        )

    port_nets = {exit_net, *cathodes}
    ports = []
    for net in port_nets:
        if net == zener_net:
            continue
        for ref, _pin, typ in _attachments(net, comps):
            if typ in PORT_TYPES:
                ports.append((ref, net))
    for ref, net in ports:
        situations.append(
            {
                "id": f"port_gnd_{ref}",
                "name": f"{net} 被 {ref} 外部接地",
                "vc": vf or 0.0,
                "vc_src": (
                    f"外部把 {net} 接到地"
                    + (f"，再加二极管正向压降 {vf:g} V" if vf is not None else "，二极管正向压降未抽出，按 0")
                ),
                "note": f"{ref} 在钳位通路上。任务书未把对地写成工作状态，本态只列出，不单独决定整卡。",
                "closes_verdict": False,
            }
        )
    return situations


def cases_for_resistor(group: dict[str, Any], situations: list[dict[str, Any]], ref: str) -> list[dict[str, Any]]:
    ohm = float(group["ohms"][ref])
    r_sum = float(group["r_sum"])
    vs = float(group["v_supply"])
    cases = []
    for sit in situations:
        if sit.get("i_dc") == 0:
            note = sit.get("note") or ""
            cases.append(
                {
                    "name_zh": sit["name"],
                    "v_across": 0.0,
                    "p_w": 0.0,
                    "i_dc": 0.0,
                    "dc_open_cite": note,
                    "note": note,
                    "reason": note,
                    "lines": [
                        {"expr": "I = 0", "src": note or "电源未建立"},
                        {"expr": "P = 0", "src": "I = 0"},
                    ],
                    "closes_verdict": True,
                }
            )
            continue
        vc = float(sit.get("vc") or 0.0)
        i_amp = max(0.0, (vs - vc) / r_sum) if r_sum else 0.0
        p_w = i_amp * i_amp * ohm
        v_across = i_amp * ohm
        note = sit.get("note") or ""
        i_txt = f"{i_amp * 1000:.4g} mA"
        p_txt = f"{p_w * 1000:.4g} mW" if p_w < 1 else f"{p_w:.4g} W"
        refs = list(group["refs"])
        idx = refs.index(ref)
        nodes = list(group.get("nodes") or [])
        up = nodes[idx] if idx < len(nodes) else group["supply_net"]
        down = nodes[idx + 1] if idx + 1 < len(nodes) else group["exit_net"]
        prev_v = i_amp * sum(float(group["ohms"][r]) for r in refs[:idx])
        v_up = vs - prev_v
        v_down = v_up - v_across
        sum_expr = " + ".join(f"{r} {group['ohms'][r]:g} Ω" for r in refs)
        cases.append(
            {
                "name_zh": sit["name"],
                "v_across": v_across,
                "va": v_up,
                "vb": v_down,
                "p_w": p_w,
                "i_dc": i_amp,
                "note": note,
                "reason": note,
                "lines": [
                    {
                        "expr": f"V({group['supply_net']}) = {vs:g} V",
                        "src": f"网名 {group['supply_net']} 含电压，按该值闭合",
                    },
                    {
                        "expr": f"V({group['exit_net']}) = {vc:g} V",
                        "src": sit.get("vc_src") or sit["name"],
                    },
                    {
                        "expr": f"整串 = {sum_expr} = {r_sum:g} Ω",
                        "src": "各只电阻的 BOM 阻值相加",
                    },
                    {
                        "expr": f"I = ({vs:g} − {vc:g}) / {r_sum:g} = {i_txt}",
                        "src": "电源电压减去钳位电压，再除以整串电阻",
                    },
                    {
                        "expr": f"V({up}) = {v_up:.6g} V",
                        "src": "电源电压" if idx == 0 else "电源电压减去本只之前各段的 I×R",
                    },
                    {
                        "expr": f"V({down}) = {v_down:.6g} V",
                        "src": f"V({up}) − I×{ohm:g} Ω",
                    },
                    {
                        "expr": f"V({ref}) = {v_up:.6g} − {v_down:.6g} = {i_amp:.6g} A × {ohm:g} Ω = {v_across:.6g} V",
                        "src": "两端电压相减，等于电流乘本只电阻",
                    },
                    {
                        "expr": f"P = ({i_amp:.6g})² × {ohm:g} = {p_txt}",
                        "src": "电流的平方乘本只电阻",
                    },
                ],
                "closes_verdict": sit.get("closes_verdict", True),
                "drop_assumed_zero": bool(sit.get("drop_assumed_zero")),
            }
        )
    return cases


def _half_rated(c: dict[str, Any]) -> float | None:
    params = ((c.get("pintable") or {}).get("params") or {}) if isinstance(c, dict) else {}
    pr = params.get("ptot_w") or params.get("p_rated_w") or c.get("p_rated")
    if pr is None:
        return None
    try:
        return 0.5 * float(pr)
    except (TypeError, ValueError):
        return None


def _close_assumed_drop(cases: list[dict[str, Any]], limit: float | None) -> None:
    """压降按 0 是功耗上界。上界已超过限额则否；上界未超过且压降未抽出则待确认。"""
    for case in cases:
        if not case.get("drop_assumed_zero") or case.get("closes_verdict") is False:
            continue
        if case.get("i_dc") == 0:
            continue
        p_w = float(case.get("p_w") or 0.0)
        if limit is None:
            case["force_verdict"] = "待确认"
            case["force_note"] = "限额或压降未抽出，本态待确认。"
            continue
        if p_w > limit + 1e-18:
            case["bound_note"] = "正向压降或饱和压降未抽出，压降按 0 的上界已超过限额"
        else:
            case["force_verdict"] = "待确认"
            case["force_note"] = "压降按 0 的上界未超过限额，真实压降未抽出，本态待确认。"


def apply_series_clamp(kg: dict[str, Any], stress_by: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """串联钳位改走脚行为求解。同一组共用一份情况表。"""
    from kernel.behavior_bridge import cases_for_group

    ends = _resistor_ends(_comps(kg))
    groups = find_series_clamp_groups(kg)
    for group in groups:
        by_ref = cases_for_group(kg, group)
        for ref in group["refs"]:
            if ref not in ends:
                continue
            n_a, n_b, ohm = ends[ref]
            cases = by_ref.get(ref) or []
            _put_stress(
                stress_by,
                ref,
                n_a=n_a,
                n_b=n_b,
                ohm=ohm,
                kind="series_clamp",
                drive="串联钳位",
                cases=cases,
                v_source={
                    "kind": "series_clamp",
                    "supply": group["supply_net"],
                    "exit": group["exit_net"],
                    "refs": list(group["refs"]),
                },
                before=_before_nets(kg, n_a, n_b),
                group_id=group["group_id"],
            )
    return groups


def _rail_v(net: str, nets: dict[str, Any]) -> float | None:
    if net in GROUND_NETS:
        return 0.0
    return _closed_rail_v(nets.get(net) or {})


def _before_nets(kg: dict[str, Any], *names: str) -> dict[str, Any]:
    nets = _nets(kg)
    out: dict[str, Any] = {}
    for n in names:
        if not n:
            continue
        rail = (nets.get(n) or {}).get("rail") or {}
        out[n] = {
            "status": "ground" if n in GROUND_NETS else rail.get("status"),
            "value": 0.0 if n in GROUND_NETS else rail.get("value"),
            "candidate_v": rail.get("candidate_v"),
            "source": "ground" if n in GROUND_NETS else rail.get("source"),
        }
    return out


def _states_of(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": c.get("name_zh"),
            "v_across": c.get("v_across"),
            "p_w": c.get("p_w"),
            "va": c.get("va"),
            "vb": c.get("vb"),
            "note": c.get("note"),
            "i_dc": c.get("i_dc"),
            "dc_open_cite": c.get("dc_open_cite"),
        }
        for c in cases
    ]


def _put_stress(
    stress_by: dict[str, dict[str, Any]],
    ref: str,
    *,
    n_a: str,
    n_b: str,
    ohm: float,
    kind: str | None,
    drive: str,
    cases: list[dict[str, Any]],
    v_source: dict[str, Any] | None,
    before: dict[str, Any] | None = None,
    assumed: list[str] | None = None,
    group_id: str | None = None,
    situations: list[dict[str, Any]] | None = None,
    drivers: list[dict[str, Any]] | None = None,
    block_class: str | None = None,
    note: str = "",
    false_close: str | None = None,
) -> None:
    scored = [c for c in cases if c.get("closes_verdict") is not False]
    vmax = None
    if scored:
        vmax = max(float(c.get("v_across") or 0.0) for c in scored)
    elif cases:
        vmax = max(float(c.get("v_across") or 0.0) for c in cases)
    stress_by[ref] = {
        "nets": {"a": n_a, "b": n_b},
        "ohm": ohm,
        "before": before or {},
        "v": vmax,
        "stress_kind": kind,
        "drive_kind": drive,
        "cases": cases,
        "states": _states_of(cases),
        "drivers": list(drivers or []),
        "assumed_zero_drops": list(assumed or []),
        "v_source": v_source,
        "false_close": false_close,
        "block_class": block_class,
        "note": note,
        "fanout": {},
        "situation_group": group_id,
        "situations": list(situations or []),
    }


def _p_lines(ref: str, n_a: str, n_b: str, ohm: float, va: float, vb: float, src_a: str, src_b: str) -> list[dict[str, str]]:
    v = abs(va - vb)
    p_w = (v * v) / ohm if ohm else 0.0
    p_txt = f"{p_w * 1000:.4g} mW" if p_w < 1 else f"{p_w:.4g} W"
    return [
        {"expr": f"V({n_a}) = {va:g} V", "src": src_a},
        {"expr": f"V({n_b}) = {vb:g} V", "src": src_b},
        {
            "expr": f"V({ref}) = |{va:g} − {vb:g}| = {v:.6g} V",
            "src": f"V({n_a}) 与 V({n_b}) 相减，取绝对值",
        },
        {
            "expr": f"P = ({v:.6g})² / {ohm:g} = {p_txt}",
            "src": "跨压的平方除以本只电阻",
        },
    ]


def _v_case(
    *,
    name: str,
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float,
    va: float,
    vb: float,
    src_a: str,
    src_b: str,
    note: str = "",
    closes_verdict: bool = True,
    drop_assumed_zero: bool = False,
) -> dict[str, Any]:
    v = abs(va - vb)
    p_w = (v * v) / ohm if ohm else 0.0
    return {
        "name_zh": name,
        "v_across": v,
        "va": va,
        "vb": vb,
        "p_w": p_w,
        "note": note or name,
        "reason": note or name,
        "lines": _p_lines(ref, n_a, n_b, ohm, va, vb, src_a, src_b),
        "closes_verdict": closes_verdict,
        "drop_assumed_zero": drop_assumed_zero,
    }


def _zero_current_case(
    *,
    name: str,
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float,
    why: str,
    v_common: float | None = None,
    src: str = "",
) -> dict[str, Any]:
    """电流为 0 时跨压为 0。只有对地电位有来源才写入两端电压，否则不写 0 V。"""
    if v_common is None:
        return {
            "name_zh": name,
            "v_across": 0.0,
            "va": None,
            "vb": None,
            "p_w": 0.0,
            "note": why,
            "reason": why,
            "lines": [
                {"expr": "I = 0", "src": why},
                {"expr": f"V({ref}) = I × {ohm:g} Ω = 0 V", "src": "电流为 0，这是电阻上的压降，不是两端对地电位"},
                {"expr": "P = 0", "src": "I = 0"},
                {"expr": f"V({n_a})、V({n_b}) 不写", "src": src or "没有已闭合的对地电位，不把两端写成 0 V"},
            ],
            "closes_verdict": True,
            "drop_assumed_zero": False,
        }
    return _v_case(
        name=name,
        ref=ref,
        n_a=n_a,
        n_b=n_b,
        ohm=ohm,
        va=v_common,
        vb=v_common,
        src_a=src or why,
        src_b=why,
        note=why,
    )


def _ground_net(n_a: str, n_b: str) -> str:
    if n_a in GROUND_NETS:
        return n_a
    if n_b in GROUND_NETS:
        return n_b
    return ""


def _solve_island(known: dict[str, float], edges: list[tuple[str, float, str, str]]) -> dict[str, float] | None:
    nodes: set[str] = set()
    for _, _, a, b in edges:
        nodes.add(a)
        nodes.add(b)
    unknown = [n for n in nodes if n not in known]
    if not unknown:
        return dict(known)
    idx = {n: i for i, n in enumerate(unknown)}
    n = len(unknown)
    gmat = [[0.0] * n for _ in range(n)]
    rhs = [0.0] * n
    for _, r, a, b in edges:
        if r <= 0:
            continue
        cond = 1.0 / r
        for node, other in ((a, b), (b, a)):
            if node not in idx:
                continue
            i = idx[node]
            gmat[i][i] += cond
            if other in idx:
                gmat[i][idx[other]] -= cond
            elif other in known:
                rhs[i] += cond * known[other]
            else:
                return None
    try:
        volt = _gauss(gmat, rhs)
    except ZeroDivisionError:
        return None
    out = dict(known)
    for net, i in idx.items():
        out[net] = volt[i]
    return out


def _gauss(mat: list[list[float]], vec: list[float]) -> list[float]:
    n = len(vec)
    a = [row[:] + [vec[i]] for i, row in enumerate(mat)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-18:
            raise ZeroDivisionError
        a[col], a[piv] = a[piv], a[col]
        div = a[col][col]
        for j in range(col, n + 1):
            a[col][j] /= div
        for r in range(n):
            if r == col:
                continue
            fac = a[r][col]
            for j in range(col, n + 1):
                a[r][j] -= fac * a[col][j]
    return [a[i][n] for i in range(n)]


def _net_role(net: str, comps: dict[str, Any], nets: dict[str, Any]) -> str:
    if _rail_v(net, nets) is not None:
        return "rail"
    oc = False
    foreign = False
    for ref, pin, typ in _attachments(net, comps):
        if typ in {"resistor", "capacitor"}:
            continue
        if typ in {"inductor", "fuse"}:
            continue
        if typ == "ic" and _is_high_z_ic_pin(ref, pin, comps):
            continue
        if typ == "ic" and _is_oc_output_pin(ref, pin, comps):
            oc = True
            continue
        foreign = True
    if oc:
        return "oc"
    if foreign:
        return "stop"
    return "interior"


def find_divider_groups(kg: dict[str, Any], claimed: set[str]) -> list[dict[str, Any]]:
    """两端是已闭合轨（含地）、中间只走电阻/电容/高阻/开漏的电阻岛。"""
    comps = _comps(kg)
    nets = _nets(kg)
    ends = _resistor_ends(comps)
    seen: set[frozenset[str]] = set()
    groups: list[dict[str, Any]] = []
    for seed, (na, nb, _) in ends.items():
        if seed in claimed:
            continue
        refs: set[str] = {seed}
        island_nets: set[str] = {na, nb}
        rails: set[str] = set()
        oc_nets: set[str] = set()
        aborted = False
        changed = True
        while changed and not aborted:
            changed = False
            for net in list(island_nets):
                role = _net_role(net, comps, nets)
                if role == "rail":
                    rails.add(net)
                    continue
                if role == "oc":
                    oc_nets.add(net)
                    continue
                if role == "stop":
                    aborted = True
                    break
                for r in _resistors_on(net, ends):
                    if r in claimed or r in refs:
                        continue
                    refs.add(r)
                    a, b, _ = ends[r]
                    for nxt in (a, b):
                        if nxt not in island_nets:
                            island_nets.add(nxt)
                            changed = True
        if aborted or len(rails) < 2 or len(refs) < 2:
            continue
        key = frozenset(refs)
        if key in seen:
            continue
        seen.add(key)
        groups.append(
            {
                "refs": sorted(refs),
                "ohms": {r: ends[r][2] for r in refs},
                "ends": {r: (ends[r][0], ends[r][1]) for r in refs},
                "rails": sorted(rails),
                "oc_nets": sorted(oc_nets),
                "group_id": "divider:" + "-".join(sorted(refs)),
            }
        )
    return groups


def _apply_divider(
    kg: dict[str, Any],
    group: dict[str, Any],
    stress_by: dict[str, dict[str, Any]],
) -> None:
    comps = _comps(kg)
    nets = _nets(kg)
    known0 = {n: float(_rail_v(n, nets) or 0.0) for n in group["rails"]}
    edges = [(r, group["ohms"][r], group["ends"][r][0], group["ends"][r][1]) for r in group["refs"]]
    oc_nets = list(group["oc_nets"])
    sit_pairs: list[tuple[str, dict[str, float], str, bool]] = []
    if not oc_nets:
        got = _solve_island(known0, edges)
        if got is None:
            return
        sit_pairs.append(("常通", got, "岛内只有电阻和已闭合电源", False))
    else:
        # 开漏脚悬空，网上的电阻还在。发光管有抽出的正向压降就用该值。
        pulls, pull_known, pull_note, vf_missing = _oc_led_pulls(oc_nets, comps, nets)
        off_known = dict(known0)
        off_known.update(pull_known)
        got_off = _solve_island(off_known, edges + pulls)
        if got_off is None:
            return
        oc_txt = "、".join(oc_nets)
        off_src = f"{oc_txt} 开漏截止，脚悬空，网上电阻仍在"
        if pull_note:
            off_src += "。" + pull_note
        sit_pairs.append((f"{oc_txt} OC截止", got_off, off_src, vf_missing))
        on_known = dict(known0)
        for n in oc_nets:
            on_known[n] = 0.0
        got_on = _solve_island(on_known, edges)
        if got_on is None:
            return
        sit_pairs.append((f"{oc_txt} OC导通", got_on, f"{oc_txt} 开漏导通，网强制 0 V", False))
    for ref in group["refs"]:
        n_a, n_b = group["ends"][ref]
        ohm = group["ohms"][ref]
        cases = []
        limit = _half_rated(comps.get(ref) or {})
        for name, volt, src, vf0 in sit_pairs:
            if n_a not in volt or n_b not in volt:
                continue
            va, vb = float(volt[n_a]), float(volt[n_b])
            case = _v_case(
                name=name,
                ref=ref,
                n_a=n_a,
                n_b=n_b,
                ohm=ohm,
                va=va,
                vb=vb,
                src_a=src if n_a not in group["rails"] else f"网名/轨压闭合 {n_a}",
                src_b=src if n_b not in group["rails"] else f"网名/轨压闭合 {n_b}",
                note=src,
            )
            if vf0 and limit is not None and float(case["p_w"]) > limit:
                case["force_verdict"] = "待确认"
                case["force_note"] = (
                    "发光管正向压降按 0 的上界已超过限额。20 mA 典型值不拿来把节点压低，本态待确认。"
                )
            cases.append(case)
        if not cases:
            continue
        _put_stress(
            stress_by,
            ref,
            n_a=n_a,
            n_b=n_b,
            ohm=ohm,
            kind="resistive_divider",
            drive="开漏分压" if oc_nets else "常通",
            cases=cases,
            v_source={"kind": "resistive_divider", "refs": list(group["refs"]), "oc_nets": oc_nets},
            before=_before_nets(kg, n_a, n_b),
            group_id=group["group_id"],
            drivers=[{"kind": "oc", "net": n, "id": f"oc:{n}"} for n in oc_nets],
        )


def _triode_common(c: dict[str, Any]) -> tuple[str, str, list[str]] | None:
    mpn = str(c.get("mpn") or c.get("value") or "").upper()
    pins = _pin_nets(c)
    by_pin = {p: n for p, n in pins if p and n}
    common = by_pin.get("3")
    if not common:
        return None
    others = [n for p, n in pins if p != "3" and n]
    if "4148CA" in mpn or mpn.endswith("CA"):
        return "anode", common, others
    if "4148CC" in mpn or mpn.endswith("CC"):
        return "cathode", common, others
    return None


def _zener_on(net: str, nets: dict[str, Any]) -> tuple[float, str] | None:
    z = _zener_on_net(net, nets)
    if not z:
        return None
    _lo, hi, zref = z
    return hi, zref


def _oc_led_pulls(
    oc_nets: list[str],
    comps: dict[str, Any],
    nets: dict[str, Any],
) -> tuple[list[tuple[str, float, str, str]], dict[str, float], str, bool]:
    """开漏网上的电阻若经发光管接到正电源，阴极侧用电源减去已抽出的正向压降。"""
    ends = _resistor_ends(comps)
    pulls: list[tuple[str, float, str, str]] = []
    known: dict[str, float] = {}
    notes: list[str] = []
    seen: set[str] = set()
    vf_missing = False
    for oc in oc_nets:
        for ref in _resistors_on(oc, ends):
            if ref in seen:
                continue
            other = _other(ref, oc, ends)
            if other in oc_nets or _rail_v(other, nets) is not None:
                continue
            led = _led_to_rail(other, comps, nets)
            if not led:
                continue
            led_ref, rail_net, rail_v = led
            if rail_v <= 0:
                continue
            seen.add(ref)
            vf = _vf(led_ref, comps)
            if vf is None:
                known[other] = float(rail_v)
                vf_missing = True
                notes.append(f"{ref} 经 {led_ref} 接到 {rail_net}，正向压降未抽出，按 0")
            else:
                known[other] = float(rail_v) - vf
                notes.append(
                    f"{ref} 经 {led_ref} 接到 {rail_net}，正向压降 {vf:g} V，{other} = {rail_v:g} − {vf:g}"
                )
            a, b, ohm = ends[ref]
            pulls.append((ref, ohm, a, b))
    return pulls, known, "；".join(notes), vf_missing


def _anode_from_zener(net: str, comps: dict[str, Any], nets: dict[str, Any]) -> tuple[float, str, bool] | None:
    """共阳在本网、阴极接稳压管时，阳极电压 = Vz + 正向压降。压降未抽出按 0。"""
    for ref, _pin, typ in _attachments(net, comps):
        if typ not in CLAMP_TYPES:
            continue
        info = _triode_common(comps.get(ref) or {})
        if not info:
            continue
        kind, common, others = info
        if kind != "anode" or common != net:
            continue
        for cath in others:
            z = _zener_on(cath, nets)
            if not z:
                continue
            vf = _vf(ref, comps)
            drop = vf or 0.0
            src = (
                f"{ref} 共阳在 {net}，阴极 {cath} 为 {z[1]} {z[0]:g} V，正向压降"
                + (f"{vf:g} V" if vf is not None else "未抽出，按 0")
            )
            return z[0] + drop, src, vf is None
    return None


def _live_clamp_v(live: str, comps: dict[str, Any], nets: dict[str, Any]) -> tuple[float, str, bool] | None:
    """活网只沿二极管正向取上界。共阴的阳极来自共阳到稳压阴极，禁止把另一只阴极电压抄过来。"""
    best: tuple[float, str, bool] | None = None
    for ref, _pin, typ in _attachments(live, comps):
        if typ not in CLAMP_TYPES:
            continue
        info = _triode_common(comps.get(ref) or {})
        if not info:
            continue
        kind, common, others = info
        if kind != "cathode" or common != live:
            continue
        vf = _vf(ref, comps)
        drop = vf or 0.0
        for anode in others:
            if not anode or anode in GROUND_NETS:
                continue
            up = _anode_from_zener(anode, comps, nets)
            if not up:
                continue
            vz, src, assumed_up = up
            text = src + (
                f"；{ref} 阳极 {anode}、阴极 {live}，正向压降"
                + (f"{vf:g} V" if vf is not None else "未抽出，按 0")
            )
            cand = (vz - drop, text, assumed_up or vf is None)
            if best is None or cand[0] > best[0]:
                best = cand
    if best:
        return best
    z = _zener_on(live, nets)
    if z:
        return z[0], f"{z[1]} 稳压上限", False
    return None


def _switches_on(net: str, comps: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for sref, _pin, typ in _attachments(net, comps):
        if typ not in SWITCH_TYPES:
            continue
        ce = _switch_ce_nets(sref, comps.get(sref) or {})
        if ce and net in ce and net not in GROUND_NETS:
            out.append(sref)
    return out


def _ports_on(net: str, comps: dict[str, Any]) -> list[str]:
    return [ref for ref, _pin, typ in _attachments(net, comps) if typ in PORT_TYPES]


def _connector_only_net(net: str, skip_ref: str, comps: dict[str, Any]) -> bool:
    roles: list[str] = []
    for ref, _pin, typ in _attachments(net, comps):
        if ref == skip_ref or typ in {"capacitor"}:
            continue
        roles.append(typ)
    return bool(roles) and all(typ in PORT_TYPES for typ in roles)


def _apply_shunt(
    kg: dict[str, Any],
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float,
    stress_by: dict[str, dict[str, Any]],
) -> bool:
    gnd = n_a if n_a in GROUND_NETS else n_b if n_b in GROUND_NETS else ""
    live = n_b if gnd == n_a else n_a if gnd == n_b else ""
    if not live:
        return False
    comps = _comps(kg)
    nets = _nets(kg)
    switches = _switches_on(live, comps)
    clamp = _live_clamp_v(live, comps, nets)
    if not switches and not clamp:
        return False
    cases: list[dict[str, Any]] = []
    if clamp:
        vz, src, assumed = clamp
        va, vb = (vz, 0.0) if live == n_a else (0.0, vz)
        cases.append(
            _v_case(
                name="开关截止，钳位上界",
                ref=ref,
                n_a=n_a,
                n_b=n_b,
                ohm=ohm,
                va=va,
                vb=vb,
                src_a=src if live == n_a else "地",
                src_b="地" if live == n_a else src,
                note=src,
                drop_assumed_zero=assumed,
            )
        )
    for sref in switches:
        vce = _vce(sref, comps)
        drop = vce or 0.0
        va, vb = (drop, 0.0) if live == n_a else (0.0, drop)
        cases.append(
            _v_case(
                name=f"{sref} 导通",
                ref=ref,
                n_a=n_a,
                n_b=n_b,
                ohm=ohm,
                va=va,
                vb=vb,
                src_a=(f"{sref} 饱和压降 {vce:g} V" if vce is not None else f"{sref} 饱和压降未抽出，按 0")
                if live == n_a
                else "地",
                src_b="地"
                if live == n_a
                else (f"{sref} 饱和压降 {vce:g} V" if vce is not None else f"{sref} 饱和压降未抽出，按 0"),
                note=f"{sref} 导通后 {live} 被拉到饱和压降",
                drop_assumed_zero=vce is None,
            )
        )
    for pref in _ports_on(live, comps):
        va, vb = (0.0, 0.0)
        cases.append(
            _v_case(
                name=f"{live} 被 {pref} 外部接地",
                ref=ref,
                n_a=n_a,
                n_b=n_b,
                ohm=ohm,
                va=va,
                vb=vb,
                src_a=f"{pref} 外部接地",
                src_b="地",
                note=f"{pref} 在 {live} 上。任务书未把对地写成工作状态，本态只列出，不单独决定整卡。",
                closes_verdict=False,
            )
        )
    if not cases:
        return False
    _close_assumed_drop(cases, _half_rated(_comps(kg).get(ref) or {}))
    _put_stress(
        stress_by,
        ref,
        n_a=n_a,
        n_b=n_b,
        ohm=ohm,
        kind="shunt_to_gnd",
        drive="对地电阻",
        cases=cases,
        v_source={"kind": "shunt_to_gnd", "live": live},
        before=_before_nets(kg, n_a, n_b),
        assumed=[],
    )
    return True


def _diode_to_supply(net: str, comps: dict[str, Any], nets: dict[str, Any]) -> tuple[str, str, float | None] | None:
    for ref, _pin, typ in _attachments(net, comps):
        if typ not in CLAMP_TYPES:
            continue
        uniq = _unique_pin_nets(comps.get(ref) or {})
        if len(uniq) != 2:
            continue
        other = uniq[1] if uniq[0] == net else uniq[0] if uniq[1] == net else ""
        if other and _rail_v(other, nets) is not None and other not in GROUND_NETS:
            return ref, other, _vf(ref, comps)
    return None


def find_series_switch_groups(kg: dict[str, Any], claimed: set[str]) -> list[dict[str, Any]]:
    """电源 —（可选二极管）— 串联电阻 — 开关 CE — 对地负载电阻，对侧可有端口。"""
    comps = _comps(kg)
    nets = _nets(kg)
    ends = _resistor_ends(comps)
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sref, c in comps.items():
        if _type_of(sref, c) not in SWITCH_TYPES or _comp_dnp(c):
            continue
        ce = _switch_ce_nets(sref, c)
        if not ce:
            continue
        coll, em = ce
        if em in GROUND_NETS or coll in GROUND_NETS:
            continue
        series = [r for r in _resistors_on(em, ends) if r not in claimed]
        if len(series) != 1:
            continue
        s_r = series[0]
        far = _other(s_r, em, ends)
        diode = _diode_to_supply(far, comps, nets)
        supply = far if _rail_v(far, nets) is not None else None
        dref, dvf = None, None
        if diode:
            dref, supply, dvf = diode
        if supply is None:
            continue
        loads = []
        for r in _resistors_on(coll, ends):
            if r in claimed:
                continue
            other = _other(r, coll, ends)
            if other in GROUND_NETS:
                loads.append(r)
        ports = _ports_on(coll, comps)
        port_resistors: list[str] = []
        for r in _resistors_on(coll, ends):
            if r in claimed or r in loads or r == s_r:
                continue
            other = _other(r, coll, ends)
            if _connector_only_net(other, r, comps):
                port_resistors.append(r)
        key = sref
        if key in seen:
            continue
        seen.add(key)
        groups.append(
            {
                "switch": sref,
                "series": s_r,
                "loads": loads,
                "supply": supply,
                "v_supply": float(_rail_v(supply, nets) or 0.0),
                "diode": dref,
                "vf": dvf,
                "emitter": em,
                "collector": coll,
                "ports": ports,
                "port_resistors": port_resistors,
                "group_id": f"series_switch:{sref}",
            }
        )
    return groups


def _apply_series_switch(
    kg: dict[str, Any],
    group: dict[str, Any],
    stress_by: dict[str, dict[str, Any]],
) -> None:
    comps = _comps(kg)
    ends = _resistor_ends(comps)
    s_r = group["series"]
    r_s = ends[s_r][2]
    r_load = sum(ends[r][2] for r in group["loads"]) if group["loads"] else 0.0
    vs = float(group["v_supply"])
    vf = group["vf"]
    vce = _vce(group["switch"], comps)
    drop = (vf or 0.0) + (vce or 0.0)
    assumed = (group["diode"] and vf is None) or (vce is None)
    drop_src = []
    if group["diode"]:
        drop_src.append(
            f"{group['diode']} 正向压降 {vf:g} V" if vf is not None else f"{group['diode']} 正向压降未抽出，按 0"
        )
    drop_src.append(
        f"{group['switch']} 饱和压降 {vce:g} V" if vce is not None else f"{group['switch']} 饱和压降未抽出，按 0"
    )
    src_on = "；".join(drop_src)
    situations = [
        {"id": "off", "name": f"{group['switch']} 截止", "i": 0.0, "closes": True, "assumed": False},
        {
            "id": "on_board",
            "name": f"{group['switch']} 导通，端口开路",
            "i": max(0.0, (vs - drop) / (r_s + r_load)) if (r_s + r_load) else 0.0,
            "closes": True,
            "assumed": assumed,
        },
    ]
    if group["ports"]:
        situations.append(
            {
                "id": "on_port",
                "name": f"{group['collector']} 被 {group['ports'][0]} 外部接地",
                "i": max(0.0, (vs - drop) / r_s) if r_s else 0.0,
                "closes": True,
                "assumed": assumed,
                "coll_v": 0.0,
            }
        )
    members = [s_r] + list(group["loads"])
    for ref in members:
        n_a, n_b, ohm = ends[ref]
        cases = []
        for sit in situations:
            if sit["i"] == 0:
                if ref == s_r:
                    cases.append(
                        _v_case(
                            name=sit["name"],
                            ref=ref,
                            n_a=n_a,
                            n_b=n_b,
                            ohm=ohm,
                            va=vs,
                            vb=vs,
                            src_a=f"{group['supply']} = {vs:g} V 仍在",
                            src_b=f"{group['switch']} 截止，无电流，两端同电位",
                            note=f"{group['switch']} 截止，{group['supply']} 仍为 {vs:g} V，电阻上没有电流",
                        )
                    )
                else:
                    gnd = _ground_net(n_a, n_b)
                    if gnd:
                        cases.append(
                            _zero_current_case(
                                name=sit["name"],
                                ref=ref,
                                n_a=n_a,
                                n_b=n_b,
                                ohm=ohm,
                                v_common=0.0,
                                why=f"{group['switch']} 截止，集电极电流为 0",
                                src=f"{gnd} 是地，无电流，另一端与地同电位",
                            )
                        )
                    else:
                        cases.append(
                            _zero_current_case(
                                name=sit["name"],
                                ref=ref,
                                n_a=n_a,
                                n_b=n_b,
                                ohm=ohm,
                                why=f"{group['switch']} 截止，集电极电流为 0",
                                src="两端都不是已闭合地或电源",
                            )
                        )
                continue
            i_amp = float(sit["i"])
            if sit["id"] == "on_port":
                if ref == s_r:
                    v_s = i_amp * ohm
                    # series resistor from supply side to emitter
                    supply_side = n_a if n_a != group["emitter"] else n_b
                    em_side = group["emitter"]
                    va = vs - (vf or 0.0) if n_a == supply_side else (vce or 0.0)
                    vb = (vce or 0.0) if n_a == supply_side else vs - (vf or 0.0)
                    if n_a == em_side:
                        va, vb = (vce or 0.0), vs - (vf or 0.0)
                else:
                    coll = group["collector"]
                    other = n_b if n_a == coll else n_a
                    if other in GROUND_NETS:
                        cases.append(
                            _v_case(
                                name=sit["name"],
                                ref=ref,
                                n_a=n_a,
                                n_b=n_b,
                                ohm=ohm,
                                va=0.0,
                                vb=0.0,
                                src_a=f"{coll} 被外部接地" if n_a == coll else f"{other} 是地",
                                src_b=f"{other} 是地" if n_a == coll else f"{coll} 被外部接地",
                                note=f"{coll} 外部接地，{other} 是地",
                                closes_verdict=sit["closes"],
                                drop_assumed_zero=sit["assumed"],
                            )
                        )
                    else:
                        cases.append(
                            _zero_current_case(
                                name=sit["name"],
                                ref=ref,
                                n_a=n_a,
                                n_b=n_b,
                                ohm=ohm,
                                why=f"{coll} 外部接地，{other} 不是已闭合地",
                                src="不把未知端写成 0 V",
                            )
                        )
                    continue
            else:
                v_drop = i_amp * ohm
                if ref == s_r:
                    supply_side = n_a if n_a != group["emitter"] else n_b
                    va = vs - (vf or 0.0) if n_a == supply_side else vs - (vf or 0.0) - v_drop
                    vb = vs - (vf or 0.0) - v_drop if n_a == supply_side else vs - (vf or 0.0)
                else:
                    coll_v = i_amp * ohm
                    va = coll_v if n_a == group["collector"] else 0.0
                    vb = 0.0 if n_a == group["collector"] else coll_v
            cases.append(
                _v_case(
                    name=sit["name"],
                    ref=ref,
                    n_a=n_a,
                    n_b=n_b,
                    ohm=ohm,
                    va=va,
                    vb=vb,
                    src_a=src_on,
                    src_b="地" if n_b in GROUND_NETS else src_on,
                    note=sit["name"] + "。" + src_on,
                    closes_verdict=sit["closes"],
                    drop_assumed_zero=sit["assumed"],
                )
            )
        _close_assumed_drop(cases, _half_rated(comps.get(ref) or {}))
        _put_stress(
            stress_by,
            ref,
            n_a=n_a,
            n_b=n_b,
            ohm=ohm,
            kind="series_switch",
            drive="开关",
            cases=cases,
            v_source={
                "kind": "series_switch",
                "switch": group["switch"],
                "supply": group["supply"],
                "refs": members,
            },
            before=_before_nets(kg, n_a, n_b),
            group_id=group["group_id"],
        )
    _apply_port_resistors(kg, group, stress_by)


def _apply_port_resistors(kg: dict[str, Any], group: dict[str, Any], stress_by: dict[str, dict[str, Any]]) -> None:
    """集电极再串一只电阻到连接器：截止、端口开路电流为 0；端口对地按本只电阻闭合。"""
    refs = list(group.get("port_resistors") or [])
    if not refs:
        return
    comps = _comps(kg)
    ends = _resistor_ends(comps)
    r_s = float(ends[group["series"]][2])
    r_loads = [float(ends[r][2]) for r in group["loads"]]
    vs = float(group["v_supply"])
    vf = group.get("vf")
    vce = _vce(group["switch"], comps)
    assumed = bool((group.get("diode") and vf is None) or vce is None)
    drop = (vf or 0.0) + (vce or 0.0)
    for ref in refs:
        n_a, n_b, ohm = ends[ref]
        parts = [r for r in r_loads + [float(ohm)] if r > 0]
        r_par = 1.0 / sum(1.0 / r for r in parts)
        denom = r_s + r_par
        i_tot = max(0.0, (vs - drop) / denom) if denom else 0.0
        v_coll = i_tot * r_par
        coll = group["collector"]
        va = v_coll if n_a == coll else 0.0
        vb = 0.0 if n_a == coll else v_coll
        src = f"{group['supply']} 经 {group['series']} 与 {group['switch']}，{group['collector']} 再经 {ref} 到端口"
        gnd_load = next(
            (r for r in group["loads"] if _ground_net(*ends[r][:2])),
            "",
        )
        if gnd_load:
            off_case = _zero_current_case(
                name=f"{group['switch']} 截止",
                ref=ref, n_a=n_a, n_b=n_b, ohm=ohm, v_common=0.0,
                why=f"{group['switch']} 截止，集电极电流为 0",
                src=f"{gnd_load} 接到地，无电流，{ref} 两端与地同电位",
            )
        else:
            off_case = _zero_current_case(
                name=f"{group['switch']} 截止",
                ref=ref, n_a=n_a, n_b=n_b, ohm=ohm,
                why=f"{group['switch']} 截止，集电极电流为 0",
                src="集电极没有到地的电阻，不把两端写成 0 V",
            )
        if r_loads:
            r_load = sum(r_loads)
            i_board = max(0.0, (vs - drop) / (r_s + r_load)) if (r_s + r_load) else 0.0
            v_open = i_board * r_load
            open_why = f"端口开路，电流为 0，两端等于集电极电压 {v_open:.6g} V"
            open_case = _zero_current_case(
                name=f"{group['switch']} 导通，端口开路",
                ref=ref, n_a=n_a, n_b=n_b, ohm=ohm, v_common=v_open,
                why=open_why,
                src=f"只计 {group['series']} 与到地负载，端口不取电流",
            )
        else:
            open_case = _zero_current_case(
                name=f"{group['switch']} 导通，端口开路",
                ref=ref, n_a=n_a, n_b=n_b, ohm=ohm, v_common=vs,
                why=f"端口开路且无到地负载，无电流，两端为 {group['supply']} {vs:g} V",
                src=f"{group['supply']} 仍在",
            )
        cases = [
            off_case,
            open_case,
            _v_case(
                name=f"{n_b if n_a == coll else n_a} 对地",
                ref=ref, n_a=n_a, n_b=n_b, ohm=ohm, va=va, vb=vb,
                src_a=src, src_b=src,
                note=src + "。压降未抽出的按 0 作上界。",
                drop_assumed_zero=assumed,
            ),
        ]
        _close_assumed_drop(cases, _half_rated(comps.get(ref) or {}))
        _put_stress(
            stress_by, ref, n_a=n_a, n_b=n_b, ohm=ohm,
            kind="series_switch", drive="开关", cases=cases,
            v_source={"kind": "series_switch", "switch": group["switch"], "supply": group["supply"], "refs": [ref]},
            before=_before_nets(kg, n_a, n_b),
            group_id=group["group_id"],
        )


def _apply_follower(
    kg: dict[str, Any],
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float,
    stress_by: dict[str, dict[str, Any]],
) -> bool:
    comps = _comps(kg)
    nets = _nets(kg)
    info = is_voltage_follower_net(n_a, comps) or is_voltage_follower_net(n_b, comps)
    if not info:
        return False
    plus = str(info.get("in_plus_net") or "")
    out_net = str(info.get("out_net") or "")
    v_plus = _rail_v(plus, nets)
    if v_plus is None:
        _put_stress(
            stress_by,
            ref,
            n_a=n_a,
            n_b=n_b,
            ohm=ohm,
            kind="follower",
            drive="跟随器",
            cases=[],
            v_source={"kind": "opamp_follower", "plus": plus, "out": out_net},
            before=_before_nets(kg, n_a, n_b),
            block_class="miss_stress",
            note=f"{info.get('ref')} 跟随器 IN+ {plus} 未闭合",
        )
        return True
    other = n_b if out_net == n_a else n_a
    v_other = _rail_v(other, nets)
    if v_other is None:
        if _connector_only_net(other, ref, comps):
            va_g = 0.0 if n_a == other else v_plus
            vb_g = v_plus if n_a == other else 0.0
            cases = [
                _v_case(
                    name="开路",
                    ref=ref,
                    n_a=n_a,
                    n_b=n_b,
                    ohm=ohm,
                    va=v_plus,
                    vb=v_plus,
                    src_a=f"{info.get('ref')} 跟随器输出 {v_plus:g} V，端口开路",
                    src_b="端口开路，电流 0",
                    note=f"{other} 开路，I=0",
                ),
                _v_case(
                    name="对地",
                    ref=ref,
                    n_a=n_a,
                    n_b=n_b,
                    ohm=ohm,
                    va=va_g,
                    vb=vb_g,
                    src_a=f"{info.get('ref')} OUT=IN+，V({plus})={v_plus:g} V" if n_a == out_net else f"{other} 外部接地",
                    src_b=f"{other} 外部接地" if n_a == out_net else f"{info.get('ref')} OUT=IN+，V({plus})={v_plus:g} V",
                    note=f"{other} 对地，跨压用跟随器已闭合电压",
                ),
            ]
            _put_stress(
                stress_by,
                ref,
                n_a=n_a,
                n_b=n_b,
                ohm=ohm,
                kind="series_socket",
                drive="开路/对地",
                cases=cases,
                v_source={"kind": "series_socket", "net": other, "board": out_net, "follower": info.get("ref")},
                before=_before_nets(kg, n_a, n_b),
            )
            return True
        _put_stress(
            stress_by,
            ref,
            n_a=n_a,
            n_b=n_b,
            ohm=ohm,
            kind="follower",
            drive="跟随器",
            cases=[],
            v_source={"kind": "opamp_follower", "plus": plus, "out": out_net},
            before=_before_nets(kg, n_a, n_b),
            block_class="miss_stress",
            note=f"跟随器输出已闭合，对端 {other} 未闭合",
        )
        return True
    va = v_plus if n_a == out_net else v_other
    vb = v_other if n_a == out_net else v_plus
    cases = [
        _v_case(
            name="跟随器",
            ref=ref,
            n_a=n_a,
            n_b=n_b,
            ohm=ohm,
            va=va,
            vb=vb,
            src_a=f"{info.get('ref')} OUT=IN+，V({plus})={v_plus:g} V" if n_a == out_net else f"轨压 {other}",
            src_b=f"轨压 {other}" if n_a == out_net else f"{info.get('ref')} OUT=IN+，V({plus})={v_plus:g} V",
        )
    ]
    _put_stress(
        stress_by,
        ref,
        n_a=n_a,
        n_b=n_b,
        ohm=ohm,
        kind="follower",
        drive="跟随器",
        cases=cases,
        v_source={"kind": "opamp_follower", "plus": plus, "out": out_net},
        before=_before_nets(kg, n_a, n_b),
    )
    return True


def _apply_closed_rails(
    kg: dict[str, Any],
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float,
    stress_by: dict[str, dict[str, Any]],
) -> bool:
    nets = _nets(kg)
    va, vb = _rail_v(n_a, nets), _rail_v(n_b, nets)
    if va is None or vb is None:
        return False
    cases = [
        _v_case(
            name="常通",
            ref=ref,
            n_a=n_a,
            n_b=n_b,
            ohm=ohm,
            va=va,
            vb=vb,
            src_a=f"网 {n_a} 已闭合",
            src_b=f"网 {n_b} 已闭合",
        )
    ]
    _put_stress(
        stress_by,
        ref,
        n_a=n_a,
        n_b=n_b,
        ohm=ohm,
        kind="closed_rails",
        drive="常通",
        cases=cases,
        v_source={"kind": "closed_rail", "net_a": n_a, "net_b": n_b},
        before=_before_nets(kg, n_a, n_b),
    )
    return True


def _cmos_drive_v(net: str, comps: dict[str, Any], nets: dict[str, Any]) -> tuple[str, float] | None:
    for ref, pin, typ in _attachments(net, comps):
        if typ != "ic" or not _is_cmos_output_pin(ref, pin, comps):
            continue
        vdd_net = _ic_vdd_net(ref, comps.get(ref) or {})
        if not vdd_net:
            continue
        v = _rail_v(vdd_net, nets)
        if v is None:
            continue
        return ref, float(v)
    return None


def _apply_base_drive(
    kg: dict[str, Any],
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float,
    stress_by: dict[str, dict[str, Any]],
) -> bool:
    comps = _comps(kg)
    nets = _nets(kg)
    hits_a = _switch_base_hits(n_a, comps)
    hits_b = _switch_base_hits(n_b, comps)
    if hits_a and not hits_b:
        base_net, drive_net = n_a, n_b
        qref = hits_a[0][0]
        role = hits_a[0][2]
    elif hits_b and not hits_a:
        base_net, drive_net = n_b, n_a
        qref = hits_b[0][0]
        role = hits_b[0][2]
    else:
        return False
    em = role.get("e") or ""
    v_em = _rail_v(em, nets)
    if v_em is None:
        v_em = 0.0 if em in GROUND_NETS else None
    v_drv = _rail_v(drive_net, nets)
    cmos = None if v_drv is not None else _cmos_drive_v(drive_net, comps, nets)
    if v_drv is None and cmos:
        v_drv = cmos[1]
    if v_drv is None:
        return False
    if v_em is None:
        v_em = 0.0
    hi = abs(float(v_drv) - float(v_em))
    va_hi = float(v_drv) if n_a == drive_net else float(v_em)
    vb_hi = float(v_em) if n_a == drive_net else float(v_drv)
    cases = [
        _v_case(
            name=f"{qref} 截止",
            ref=ref,
            n_a=n_a,
            n_b=n_b,
            ohm=ohm,
            va=float(v_drv),
            vb=float(v_drv),
            src_a="基极开路，两端同电位",
            src_b="基极开路，两端同电位",
            note=f"{qref} 截止，基极不取稳态电流，P=0",
        ),
        _v_case(
            name=f"{qref} 基极拉到发射极",
            ref=ref,
            n_a=n_a,
            n_b=n_b,
            ohm=ohm,
            va=va_hi,
            vb=vb_hi,
            src_a=f"驱动 {drive_net} = {v_drv:g} V" if n_a == drive_net else f"{qref} 发射极",
            src_b=f"{qref} 发射极" if n_a == drive_net else f"驱动 {drive_net} = {v_drv:g} V",
            note=f"基极包络：软网可到发射极，跨压 {hi:g} V",
        ),
    ]
    _put_stress(
        stress_by,
        ref,
        n_a=n_a,
        n_b=n_b,
        ohm=ohm,
        kind="base_drive",
        drive="基极",
        cases=cases,
        v_source={"kind": "base_drive", "switch": qref},
        before=_before_nets(kg, n_a, n_b),
        drivers=[{"kind": "base", "ref": qref, "id": f"base:{qref}", "net": base_net}],
    )
    return True


def _apply_socket(
    kg: dict[str, Any],
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float,
    stress_by: dict[str, dict[str, Any]],
) -> bool:
    comps = _comps(kg)
    nets = _nets(kg)
    series = _series_unknown_load_net(ref, n_a, n_b, comps, nets)
    if not series:
        return False
    board = n_b if series == n_a else n_a
    v_board = _rail_v(board, nets)
    if v_board is None:
        _put_stress(
            stress_by,
            ref,
            n_a=n_a,
            n_b=n_b,
            ohm=ohm,
            kind=None,
            drive="",
            cases=[],
            v_source={"kind": "topology_open", "net": series},
            before=_before_nets(kg, n_a, n_b),
            block_class="topology_open",
            note=f"{series} 去掉 {ref} 后仅连接器，板内脚电位未闭合",
        )
        return True
    va_g, vb_g = (0.0, v_board) if series == n_a else (v_board, 0.0)
    cases = [
        _v_case(
            name="开路",
            ref=ref,
            n_a=n_a,
            n_b=n_b,
            ohm=ohm,
            va=v_board if board == n_a else v_board,
            vb=v_board if board == n_b else v_board,
            src_a="端口开路，电流 0",
            src_b="端口开路，电流 0",
            note=f"{series} 开路，I=0",
        ),
        _v_case(
            name="对地",
            ref=ref,
            n_a=n_a,
            n_b=n_b,
            ohm=ohm,
            va=va_g if series == n_a else v_board,
            vb=v_board if series == n_a else 0.0,
            src_a=f"板内脚 {board} = {v_board:g} V" if n_a == board else f"{series} 外部接地",
            src_b=f"{series} 外部接地" if n_a == board else f"板内脚 {board} = {v_board:g} V",
            note=f"{series} 对地，跨压用已闭合的板内电压，不另编外部电压",
        ),
    ]
    _put_stress(
        stress_by,
        ref,
        n_a=n_a,
        n_b=n_b,
        ohm=ohm,
        kind="series_socket",
        drive="开路/对地",
        cases=cases,
        v_source={"kind": "series_socket", "net": series, "board": board},
        before=_before_nets(kg, n_a, n_b),
    )
    return True


def _apply_dc_open(
    kg: dict[str, Any],
    ref: str,
    c: dict[str, Any],
    n_a: str,
    n_b: str,
    ohm: float,
    stress_by: dict[str, dict[str, Any]],
) -> bool:
    info = two_terminal_dc_open(ref, c, kg)
    if not info:
        return False
    if _series_unknown_load_net(ref, n_a, n_b, _comps(kg), _nets(kg)):
        return False
    payload = _dc_open_stress(ref, n_a, n_b, ohm, info, _nets(kg))
    payload["stress_kind"] = "dc_idle"
    payload["drive_kind"] = payload.get("drive_kind") or "无直流"
    stress_by[ref] = payload
    return True


def _timing_flags(net: str, skip_ref: str, comps: dict[str, Any], nets: dict[str, Any]) -> dict[str, bool]:
    """沿电阻和二极管走，电容只记不穿越。用来认出施密特延时，不把地当电源。"""
    flags = {"cap": False, "cmos_out": False, "cmos_in": False, "rail": False, "switch": False}
    seen: set[str] = set()
    queue = [net]
    while queue:
        cur = queue.pop()
        if not cur or cur in seen:
            continue
        seen.add(cur)
        if cur not in GROUND_NETS and _rail_v(cur, nets) is not None:
            flags["rail"] = True
            continue
        for ref, pin, typ in _attachments(cur, comps):
            if ref == skip_ref:
                continue
            if typ == "capacitor":
                flags["cap"] = True
                continue
            if typ in SWITCH_TYPES:
                flags["switch"] = True
                continue
            if typ == "ic" and _is_cmos_output_pin(ref, pin, comps):
                flags["cmos_out"] = True
                continue
            if typ == "ic" and _is_high_z_ic_pin(ref, pin, comps):
                flags["cmos_in"] = True
                continue
            if typ not in {"resistor", "diode", "led"}:
                continue
            for _p, n2 in _pin_nets(comps.get(ref) or {}):
                if n2 and n2 not in seen:
                    queue.append(n2)
    return flags


def _sinks_on(net: str, comps: dict[str, Any]) -> list[tuple[str, str, float | None]]:
    out: list[tuple[str, str, float | None]] = []
    seen: set[str] = set()
    for ref, pin, typ in _attachments(net, comps):
        if ref in seen:
            continue
        item: tuple[str, str, float | None] | None = None
        if typ == "ic" and _is_oc_output_pin(ref, pin, comps):
            item = ("oc", ref, None)
        elif typ == "ic" and _is_cmos_output_pin(ref, pin, comps):
            item = ("cmos", ref, None)
        elif typ in SWITCH_TYPES:
            ce = _switch_ce_nets(ref, comps.get(ref) or {})
            if ce and net == ce[0] and ce[1] in GROUND_NETS:
                item = ("switch", ref, _vce(ref, comps))
        if item is not None:
            seen.add(ref)
            out.append(item)
    return out


def _open_drive_ends(
    kg: dict[str, Any],
    led_net: str,
    drive_net: str,
    rail_v: float,
    vf: float | None,
) -> tuple[float, float] | None:
    """驱动脚悬空时，沿电阻网求解灯侧和驱动侧电位。灯的正向压降用已抽出的值。"""
    comps = _comps(kg)
    nets = _nets(kg)
    ends = _resistor_ends(comps)
    known: dict[str, float] = {led_net: float(rail_v) - (vf or 0.0)}
    edges: list[tuple[str, float, str, str]] = []
    seen: set[str] = set()
    seen_refs: set[str] = set()
    queue = [led_net, drive_net]
    while queue:
        net = queue.pop()
        if net in seen:
            continue
        seen.add(net)
        if net != led_net and net in GROUND_NETS:
            known[net] = 0.0
            continue
        if net != led_net:
            rail = _rail_v(net, nets)
            if rail is not None:
                known[net] = float(rail)
                continue
        for ref in _resistors_on(net, ends):
            if ref in seen_refs:
                a, b, _ohm = ends[ref]
            else:
                seen_refs.add(ref)
                a, b, ohm = ends[ref]
                edges.append((ref, ohm, a, b))
            for nxt in (a, b):
                if nxt not in seen:
                    queue.append(nxt)
    solved = _solve_island(known, edges)
    if not solved or led_net not in solved or drive_net not in solved:
        return None
    return float(solved[led_net]), float(solved[drive_net])


def _led_to_rail(net: str, comps: dict[str, Any], nets: dict[str, Any]) -> tuple[str, str, float] | None:
    for ref, _pin, typ in _attachments(net, comps):
        if typ != "led":
            continue
        for _p, n2 in _pin_nets(comps.get(ref) or {}):
            if not n2 or n2 == net or n2 in GROUND_NETS:
                continue
            v = _rail_v(n2, nets)
            if v is not None and abs(v) > 0:
                return ref, n2, float(v)
    return None


def _apply_led(
    kg: dict[str, Any],
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float,
    stress_by: dict[str, dict[str, Any]],
) -> bool:
    comps = _comps(kg)
    nets = _nets(kg)
    led_a = _led_to_rail(n_a, comps, nets)
    led_b = _led_to_rail(n_b, comps, nets)
    if bool(led_a) == bool(led_b):
        return False
    led_net = n_a if led_a else n_b
    drive_net = n_b if led_a else n_a
    led_ref, rail_net, rail_v = led_a or led_b  # type: ignore[misc]
    sinks = _sinks_on(drive_net, comps)
    if not sinks:
        return False
    vf = _vf(led_ref, comps)
    limit = _half_rated(comps.get(ref) or {})
    off_pair = _open_drive_ends(kg, led_net, drive_net, float(rail_v), vf)
    if off_pair and abs(off_pair[0] - off_pair[1]) > 1e-3:
        va = off_pair[0] if n_a == led_net else off_pair[1]
        vb = off_pair[1] if n_a == led_net else off_pair[0]
        vf_txt = f"{vf:g} V" if vf is not None else "未抽出，按 0"
        cases = [
            _v_case(
                name="驱动截止",
                ref=ref, n_a=n_a, n_b=n_b, ohm=ohm, va=va, vb=vb,
                src_a=(
                    f"{led_ref} 正向压降 {vf_txt}，{led_net} = {rail_v:g} − {vf or 0:g}"
                    if n_a == led_net
                    else f"{drive_net} 由悬空后的电阻网解出"
                ),
                src_b=(
                    f"{drive_net} 由悬空后的电阻网解出"
                    if n_a == led_net
                    else f"{led_ref} 正向压降 {vf_txt}，{led_net} = {rail_v:g} − {vf or 0:g}"
                ),
                note=f"驱动脚悬空，{led_net} 与 {drive_net} 仍在电阻网里",
            )
        ]
    else:
        cases = [
            _zero_current_case(
                name="驱动截止",
                ref=ref, n_a=n_a, n_b=n_b, ohm=ohm, v_common=float(rail_v),
                why=f"{led_ref} 没有被拉电流，电阻上电流为 0",
                src=f"{rail_net} = {rail_v:g} V 仍在，无电流则两端同为该电压",
            )
        ]
    for kind, sref, vdrop in sinks:
        known_drop = vdrop or 0.0
        v_bound = max(0.0, abs(rail_v) - known_drop)
        v_sheet = max(0.0, abs(rail_v) - known_drop - (vf or 0.0))
        va = v_bound if n_a == led_net else 0.0
        vb = 0.0 if n_a == led_net else v_bound
        label = f"{sref} 导通"
        if kind == "oc":
            src_b = f"{sref} 开漏导通。该电流下的输出低电平没有抽出，式中按 0，不是饱和压降实测值"
        elif kind == "cmos":
            src_b = f"{sref} 推挽输出低。输出低电平没有抽出，式中按 0"
        elif vdrop is not None:
            src_b = f"{sref} 饱和压降 {vdrop:g} V"
        else:
            src_b = f"{sref} 饱和压降未抽出，按 0"
        case = _v_case(
            name=label,
            ref=ref, n_a=n_a, n_b=n_b, ohm=ohm, va=va, vb=vb,
            src_a=f"{rail_net} = {rail_v:g} V。{led_ref} 正向压降未按该电流抽出时，上界先按 0",
            src_b=src_b,
            note=f"{led_ref} 阳极在 {rail_net}，{sref} 把阴极侧拉低",
            drop_assumed_zero=vf is None or vdrop is None,
        )
        if vf is not None and limit is not None:
            p0 = (v_bound * v_bound) / ohm
            p1 = (v_sheet * v_sheet) / ohm
            if (p0 > limit) != (p1 > limit):
                case["drop_assumed_zero"] = False
                case["force_verdict"] = "待确认"
                case["force_note"] = (
                    f"正向压降按 0 时 P={p0:.4g} W，按手册 {vf:g} V 时 P={p1:.4g} W，"
                    "落在限额两侧。手册值是 20 mA 典型值，不能用来把超限算成通过，本态待确认。"
                )
            elif p0 <= limit:
                case["drop_assumed_zero"] = False
                case["note"] = (case.get("note") or "") + "。压降按 0 的上界未超过限额，真实压降只会更小"
        cases.append(case)
    _close_assumed_drop(cases, limit)
    _put_stress(
        stress_by, ref, n_a=n_a, n_b=n_b, ohm=ohm,
        kind="led_limit", drive="发光管", cases=cases,
        v_source={"kind": "led_limit", "led": led_ref, "rail": rail_net},
        before=_before_nets(kg, n_a, n_b),
        assumed=[],
    )
    return True


def _apply_rail_rc(
    kg: dict[str, Any],
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float,
    stress_by: dict[str, dict[str, Any]],
) -> bool:
    nets = _nets(kg)
    va, vb = _rail_v(n_a, nets), _rail_v(n_b, nets)
    if (va is None) == (vb is None):
        return False
    rail_net, timing = (n_a, n_b) if va is not None else (n_b, n_a)
    rail_v = float(va if va is not None else vb)
    if rail_net in GROUND_NETS or abs(rail_v) < 1e-9:
        return False
    flags = _timing_flags(timing, ref, _comps(kg), nets)
    if not (flags["cap"] and flags["cmos_out"] and not flags["switch"] and not flags["rail"]):
        return False
    v_hi = abs(rail_v)
    va_on = v_hi if n_a == rail_net else 0.0
    vb_on = 0.0 if n_a == rail_net else v_hi
    cases = [
        _v_case(
            name="前级输出高",
            ref=ref, n_a=n_a, n_b=n_b, ohm=ohm, va=rail_v if n_a == rail_net else rail_v,
            vb=rail_v, src_a="电容充电结束后电流为 0", src_b="电容充电结束后电流为 0",
            note="转向二极管截止，稳态电流为 0",
        ),
        _v_case(
            name="前级输出低",
            ref=ref, n_a=n_a, n_b=n_b, ohm=ohm, va=va_on, vb=vb_on,
            src_a=f"{rail_net} = {rail_v:g} V",
            src_b="前级输出拉低，二极管正向压降按 0 作上界",
            note=f"{ref} 从 {rail_net} 经转向二极管被前级拉低",
        ),
    ]
    cases[0]["va"] = rail_v
    cases[0]["vb"] = rail_v
    cases[0]["v_across"] = 0.0
    cases[0]["p_w"] = 0.0
    limit = _half_rated(_comps(kg).get(ref) or {})
    p_on = (v_hi * v_hi) / ohm if ohm else 0.0
    if limit is not None and p_on <= limit:
        cases[1]["note"] = "压降按 0 的上界未超过限额，真实正向压降只会让功耗更小"
    else:
        cases[1]["force_verdict"] = "待确认"
        cases[1]["force_note"] = "压降按 0 的上界已超过限额，真实正向压降未按该电流的最小值抽出，本态待确认。"
    _put_stress(
        stress_by, ref, n_a=n_a, n_b=n_b, ohm=ohm,
        kind="rail_rc", drive="延时", cases=cases,
        v_source={"kind": "rail_rc", "rail": rail_net},
        before=_before_nets(kg, n_a, n_b),
    )
    return True


def _apply_rc_timing(
    kg: dict[str, Any],
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float,
    stress_by: dict[str, dict[str, Any]],
) -> bool:
    nets = _nets(kg)
    if _rail_v(n_a, nets) is not None or _rail_v(n_b, nets) is not None:
        return False
    flags = _timing_flags(n_a, ref, _comps(kg), nets)
    other = _timing_flags(n_b, ref, _comps(kg), nets)
    merged = {k: flags[k] or other[k] for k in flags}
    if not (merged["cap"] and merged["cmos_out"] and not merged["rail"] and not merged["switch"]):
        return False
    idle = _zero_current_case(
        name="直流稳态",
        ref=ref, n_a=n_a, n_b=n_b, ohm=ohm,
        why="电容开路，转向二极管不成直流环路，电流为 0",
        src="两端都没有已闭合的对地电位",
    )
    pulse = _zero_current_case(
        name="周期充放电",
        ref=ref, n_a=n_a, n_b=n_b, ohm=ohm,
        why="没有频率和占空比，不计算周期平均功耗",
        src="不编对地电位",
    )
    pulse["p_w"] = 0.0
    pulse["force_verdict"] = "待确认"
    pulse["force_note"] = "没有频率和占空比，不计算周期平均功耗，本态待确认。"
    _put_stress(
        stress_by, ref, n_a=n_a, n_b=n_b, ohm=ohm,
        kind="rc_timing", drive="延时", cases=[idle, pulse],
        v_source={"kind": "rc_timing"},
        before=_before_nets(kg, n_a, n_b),
    )
    return True


def _opamp_output(net: str, comps: dict[str, Any]) -> tuple[str, str] | None:
    from kernel.resistor_network import _opamp_channels, _pin_net_map

    for ref, pin, typ in _attachments(net, comps):
        if typ != "ic":
            continue
        c = comps.get(ref) or {}
        channels = _opamp_channels(str(c.get("mpn") or c.get("value") or ""))
        if not channels or str(pin) not in channels:
            continue
        in_minus, _in_plus = channels[str(pin)]
        if _pin_net_map(c).get(in_minus) == net:
            continue
        return ref, str(pin)
    return None


def _apply_opamp_rail(
    kg: dict[str, Any],
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float,
    stress_by: dict[str, dict[str, Any]],
) -> bool:
    comps = _comps(kg)
    nets = _nets(kg)
    hit_a = _opamp_output(n_a, comps)
    hit_b = _opamp_output(n_b, comps)
    if bool(hit_a) == bool(hit_b):
        return False
    out_net = n_a if hit_a else n_b
    other = n_b if hit_a else n_a
    v_other = _rail_v(other, nets)
    if v_other is None:
        return False
    oref, _pin = hit_a or hit_b  # type: ignore[misc]
    vdd_net = _ic_vdd_net(oref, comps.get(oref) or {})
    vdd = _rail_v(vdd_net, nets) if vdd_net else None
    if vdd is None:
        return False
    _put_stress(
        stress_by,
        ref,
        n_a=n_a,
        n_b=n_b,
        ohm=ohm,
        kind="opamp_rail",
        drive="运放",
        cases=[],
        v_source={"kind": "opamp_rail", "ref": oref, "vdd": vdd_net},
        before=_before_nets(kg, n_a, n_b),
        block_class="miss_stress",
        note=f"{oref} 输出电压未闭合，不把 0 和 {vdd_net} 写成工作电位",
    )
    return True


def _highest_rail(net: str, skip_ref: str, comps: dict[str, Any], nets: dict[str, Any]) -> tuple[str, float] | None:
    best: tuple[str, float] | None = None
    seen: set[str] = set()
    queue = [net]
    while queue:
        cur = queue.pop()
        if not cur or cur in seen:
            continue
        seen.add(cur)
        v = _rail_v(cur, nets)
        if v is not None and cur not in GROUND_NETS:
            if best is None or abs(v) > abs(best[1]):
                best = (cur, float(v))
            continue
        if cur in GROUND_NETS:
            continue
        for ref, _pin, typ in _attachments(cur, comps):
            if ref == skip_ref or typ not in {"diode", "resistor", "led"}:
                continue
            for _p, n2 in _pin_nets(comps.get(ref) or {}):
                if n2 and n2 not in seen:
                    queue.append(n2)
    return best


def _apply_base_bias(
    kg: dict[str, Any],
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float,
    stress_by: dict[str, dict[str, Any]],
) -> bool:
    comps = _comps(kg)
    nets = _nets(kg)
    hits_a = _switch_base_hits(n_a, comps)
    hits_b = _switch_base_hits(n_b, comps)
    if bool(hits_a) == bool(hits_b):
        return False
    far = n_b if hits_a else n_a
    qref = (hits_a or hits_b)[0][0]
    found = _highest_rail(far, ref, comps, nets)
    if not found:
        return False
    rail_net, rail_v = found
    limit = _half_rated(comps.get(ref) or {})
    p_hi = (abs(rail_v) * abs(rail_v)) / ohm if ohm else 0.0
    case = {
        "name_zh": "偏置上界",
        "v_across": abs(rail_v),
        "va": None,
        "vb": None,
        "p_w": p_hi,
        "note": f"基极电位未闭合。上界跨压用邻近电源 {rail_net} = {abs(rail_v):g} V，不把基极写成 0 V",
        "reason": f"基极电位未闭合。上界跨压用邻近电源 {rail_net}",
        "lines": [
            {
                "expr": f"上界跨压 = {abs(rail_v):g} V",
                "src": f"沿二极管或电阻走到 {rail_net}。{qref} 基极电位未闭合，不写成 0 V",
            },
            {
                "expr": f"P = ({abs(rail_v):g})² / {ohm:g}",
                "src": "上界功耗。实际压差不会大于这段电源电压",
            },
        ],
        "closes_verdict": True,
        "drop_assumed_zero": False,
    }
    if limit is not None and p_hi > limit:
        case["force_verdict"] = "待确认"
        case["force_note"] = "按邻近电源电压的上界已超过限额，实际基极压差未闭合，本态待确认。"
    _put_stress(
        stress_by, ref, n_a=n_a, n_b=n_b, ohm=ohm,
        kind="base_bias", drive="基极", cases=[case],
        v_source={"kind": "base_bias", "switch": qref, "rail": rail_net},
        before=_before_nets(kg, n_a, n_b),
    )
    return True


def analyze_all_resistors(kg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """电阻应力从直流工况投影。列档在 dc_modes，功耗是该档电流的 I²R。"""
    from kernel.dc_modes import stress_map

    return stress_map(kg)
