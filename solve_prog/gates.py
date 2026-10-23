# -*- coding: utf-8 -*-
"""交付门禁 G1–G7。未通过时不产生是/否。"""

from __future__ import annotations

from typing import Any

from solve_prog.netclass import GROUND_NETS, pin_pairs, resistor_ohms, components

POWER_TOL_W = 0.5e-3


def _fail(code: str, detail: str) -> dict[str, Any]:
    return {"id": code, "pass": False, "detail": detail}


def _ok(code: str, detail: str) -> dict[str, Any]:
    return {"id": code, "pass": True, "detail": detail}


def gate_g1(kg: dict[str, Any], resistors: list[tuple[str, str, str, float]], sources: list[dict[str, Any]]) -> dict[str, Any]:
    ends = resistor_ohms(kg)
    missing = []
    for ref, a, b, _ohm in resistors:
        known = ends.get(ref)
        if known is None or {a, b} != {known[0], known[1]}:
            missing.append(ref)
    for source in sources:
        ref = str(source.get("ref") or "")
        if source["role"] == "supply" or ref not in components(kg):
            continue
        pins = {net for _pin, net in pin_pairs(components(kg)[ref])}
        if str(source["plus"]) not in pins and str(source["minus"]) not in pins:
            missing.append(ref)
    if missing:
        return _fail("G1", "脚不一致：" + ",".join(missing))
    return _ok("G1", "网表中的脚与工程数据差 0 只")


def gate_g2(expected: list[dict[str, Any]], used: list[dict[str, Any]]) -> dict[str, Any]:
    def key(source: dict[str, Any]) -> tuple[str, str, str, float]:
        return (str(source["role"]), str(source["plus"]), str(source["minus"]), round(float(source["volts"]), 9))

    exp = sorted(key(source) for source in expected)
    got = sorted(key(source) for source in used)
    extra = [item for item in got if item not in exp]
    if extra:
        return _fail("G2", f"多出电压源 {extra}")
    return _ok("G2", "电压源都能指回激励卡")


def gate_g3(sources: list[dict[str, Any]]) -> dict[str, Any]:
    for source in sources:
        if source["role"] not in {"vf", "vce"}:
            continue
        origin = source.get("origin")
        volts = float(source["volts"])
        if origin == "stamped_zero" and volts == 0.0:
            continue
        if origin == "extracted":
            continue
        return _fail("G3", f"{source.get('ref')} {source['role']}={volts} V 不是模型卡或写明的 0")
    return _ok("G3", "压降只来自模型卡或激励卡写明的 0")


def gate_g4(resistors: list[tuple[str, str, str, float]], solved: dict[str, Any]) -> dict[str, Any]:
    if solved.get("singular"):
        return _fail("G4", "电路矩阵奇异")
    leave: dict[str, float] = {}
    peak: dict[str, float] = {}

    def add(node: str, amp: float) -> None:
        if not node or node in GROUND_NETS:
            return
        leave[node] = leave.get(node, 0.0) + amp
        peak[node] = max(peak.get(node, 0.0), abs(amp))

    currents = solved["currents_a"]
    for ref, a, b, _ohm in resistors:
        amp = float(currents.get(ref, 0.0))
        add(a, amp)
        add(b, -amp)
    for index, source in enumerate(solved["sources"]):
        amp = float(solved["source_i"].get(index, 0.0))
        add(str(source["plus"]), amp)
        add(str(source["minus"]), -amp)
    worst = 0.0
    worst_net = ""
    for node, amp in leave.items():
        limit = max(1e-6, 0.001 * peak.get(node, 0.0))
        if abs(amp) > limit and abs(amp) > worst:
            worst = abs(amp)
            worst_net = node
    if worst_net:
        return _fail("G4", f"{worst_net} 电流差 {worst:.6g} A")
    return _ok("G4", "中间节点电流平衡")


def gate_g5(state: dict[str, Any], solved: dict[str, Any]) -> dict[str, Any]:
    if not state.get("clamp"):
        return _ok("G5", "本态不是钳位")
    zener_sources = [source for source in solved["sources"] if source["role"] == "zener"]
    if solved.get("dropped"):
        if zener_sources:
            return _fail("G5", "脱离钳位后仍保留钳位电压源")
        return _ok("G5", "钳位电流不足，已脱离钳位并重算")
    if not zener_sources:
        return _fail("G5", "钳位态没有稳压电压源")
    source = zener_sources[0]
    index = solved["sources"].index(source)
    current = float(solved["source_i"].get(index, 0.0))
    node = str(source["plus"])
    error = abs(float(solved["voltages"].get(node, 0.0)) - float(source["volts"]))
    if error > 1e-3 or current < 1e-3:
        return _fail("G5", f"{node} 偏差 {error:.6g} V，钳位电流 {current:.6g} A")
    return _ok("G5", f"钳位电流 {current:.6g} A")


def _series_power(v_supply: float, v_exit: float, ohm: float, r_sum: float) -> float:
    if r_sum <= 0:
        return 0.0
    amp = max(0.0, v_supply - v_exit) / r_sum
    return amp * amp * ohm


def _vf_max(stimulus: dict[str, Any]) -> float | None:
    found = None
    for item in stimulus.get("model") or []:
        if item.get("param") != "vf_max_v" or item.get("volts") is None:
            continue
        found = float(item["volts"])
    return found


def _power_interval(
    sources: list[dict[str, Any]],
    ohm: float,
    r_sum: float,
    vf_max: float | None,
) -> tuple[float, float] | None:
    supplies = [float(source["volts"]) for source in sources if source.get("role") == "supply"]
    if not supplies:
        return None
    if all(abs(value) <= 1e-12 for value in supplies):
        return 0.0, 0.0
    v_supply = max(supplies)
    vf_used = 0.0
    v_fixed = None
    for source in sources:
        role = source.get("role")
        volts = float(source["volts"])
        if role == "vf":
            vf_used = volts
        elif role in {"zener", "vce"}:
            v_fixed = volts
        elif role == "port":
            v_fixed = 0.0
    if v_fixed is None:
        return None
    upper_vf = vf_used if vf_max is None else max(vf_used, vf_max)
    low = _series_power(v_supply, v_fixed + upper_vf, ohm, r_sum)
    high = _series_power(v_supply, v_fixed + min(vf_used, upper_vf), ohm, r_sum)
    return min(low, high), max(low, high)


def gate_g6(stimulus: dict[str, Any], solved_states: list[dict[str, Any]]) -> dict[str, Any]:
    group = stimulus.get("group") or {}
    ohms = {ref: float(value) for ref, value in (group.get("ohms") or {}).items()}
    if not ohms:
        return _ok("G6", "没有串联电阻，不核区间")
    r_sum = sum(ohms.values())
    vf_max = _vf_max(stimulus)
    by_id = {item["state_id"]: item for item in solved_states}
    stim = {item["id"]: item for item in stimulus.get("states") or []}
    for state_id, solved in by_id.items():
        state = stim.get(state_id) or {}
        if not state.get("participates", True):
            continue
        if solved.get("dropped") and str(state_id).startswith("zener_"):
            return _fail("G6", f"{state_id} 脱离钳位，不能核区间")
        for ref, ohm in ohms.items():
            span = _power_interval(solved.get("sources") or state.get("sources") or [], ohm, r_sum, vf_max)
            if span is None:
                continue
            got = float(solved["powers_w"].get(ref, -1))
            if got < span[0] - POWER_TOL_W or got > span[1] + POWER_TOL_W:
                return _fail(
                    "G6",
                    f"{ref} {state_id} {got * 1e3:.4f} mW，区间 {span[0] * 1e3:.4f}–{span[1] * 1e3:.4f} mW",
                )
    return _ok("G6", "功耗落在压降区间内")


def evaluate(
    kg: dict[str, Any],
    stimulus: dict[str, Any],
    resistors: list[tuple[str, str, str, float]],
    solved_states: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    results = [gate_g1(kg, resistors, stimulus["states"][0]["sources"])]
    for state, solved in zip(stimulus["states"], solved_states):
        results.append(gate_g2(state["sources"], solved["sources"]))
        results.append(gate_g3(solved["sources"]))
        results.append(gate_g4(resistors, solved))
        results.append(gate_g5(state, solved))
    results.append(gate_g6(stimulus, solved_states))
    passed = all(item["pass"] for item in results) and bool(stimulus.get("kind"))
    results.append(_ok("G7", f"激励卡种类 {stimulus.get('kind')}") if passed else _fail("G7", "G1–G6 未全部通过或缺少种类名"))
    return results
