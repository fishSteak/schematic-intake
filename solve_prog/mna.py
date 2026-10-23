# -*- coding: utf-8 -*-
"""理想电压源加电阻的直流工作点。"""

from __future__ import annotations

from typing import Any

from solve_prog.netclass import GROUND_NETS, resistor_ohms


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    size = len(rhs)
    rows = [matrix[i][:] + [rhs[i]] for i in range(size)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(rows[row][col]))
        if abs(rows[pivot][col]) < 1e-15:
            raise ValueError("电路矩阵奇异")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        scale = rows[col][col]
        for col2 in range(col, size + 1):
            rows[col][col2] /= scale
        for row in range(size):
            if row == col:
                continue
            factor = rows[row][col]
            if factor == 0:
                continue
            for col2 in range(col, size + 1):
                rows[row][col2] -= factor * rows[col][col2]
    return [rows[i][size] for i in range(size)]


def expand_resistors(
    group: dict[str, Any],
    kg: dict[str, Any],
    extra_nets: list[str] | None = None,
) -> list[tuple[str, str, str, float]]:
    ends = resistor_ohms(kg)
    included = set(group["refs"])
    frontier = {group["supply_net"], group["exit_net"], *(extra_nets or [])}
    for ref in group["refs"]:
        a, b = group["ends"][ref]
        frontier.add(a)
        frontier.add(b)
    changed = True
    while changed:
        changed = False
        for ref, (a, b, _ohm) in ends.items():
            if ref in included:
                continue
            if a in frontier or b in frontier:
                included.add(ref)
                frontier.add(a)
                frontier.add(b)
                changed = True
    branches = []
    for ref in included:
        a, b, ohm = ends[ref]
        branches.append((ref, a, b, ohm))
    return branches


def operating_point(
    resistors: list[tuple[str, str, str, float]],
    sources: list[dict[str, Any]],
    currents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    point = _operating_point(resistors, sources, currents, use_gmin=False)
    if not point.get("singular"):
        point["gmin"] = False
        return point
    point = _operating_point(resistors, sources, currents, use_gmin=True)
    point["gmin"] = True
    return point


def _operating_point(
    resistors: list[tuple[str, str, str, float]],
    sources: list[dict[str, Any]],
    currents: list[dict[str, Any]] | None,
    *,
    use_gmin: bool,
) -> dict[str, Any]:
    nodes: list[str] = []

    def add(name: str) -> None:
        if name and name not in GROUND_NETS and name not in nodes:
            nodes.append(name)

    for _ref, a, b, _ohm in resistors:
        add(a)
        add(b)
    for source in sources:
        add(str(source["plus"]))
        add(str(source["minus"]))
    for current in currents or []:
        add(str(current["plus"]))
        add(str(current["minus"]))
    index = {name: i for i, name in enumerate(nodes)}
    n = len(nodes)
    m = len(sources)
    size = n + m
    if size == 0:
        return {"voltages": {}, "resistor_i": {}, "source_i": {}, "singular": False}
    matrix = [[0.0 for _ in range(size)] for _ in range(size)]
    rhs = [0.0 for _ in range(size)]
    # 只有无 gmin 时矩阵奇异，才退回这档。整块都靠它才解出来的，不当工作点。
    if use_gmin:
        for node in nodes:
            matrix[index[node]][index[node]] += 1e-12
    for _ref, a, b, ohm in resistors:
        g = 1.0 / ohm
        for node, sign in ((a, 1.0), (b, -1.0)):
            if node in GROUND_NETS:
                continue
            matrix[index[node]][index[node]] += g
        if a not in GROUND_NETS and b not in GROUND_NETS:
            matrix[index[a]][index[b]] -= g
            matrix[index[b]][index[a]] -= g
    for k, source in enumerate(sources):
        row = n + k
        plus = str(source["plus"])
        minus = str(source["minus"])
        if plus not in GROUND_NETS:
            matrix[row][index[plus]] += 1.0
            matrix[index[plus]][row] += 1.0
        if minus not in GROUND_NETS:
            matrix[row][index[minus]] -= 1.0
            matrix[index[minus]][row] -= 1.0
        rhs[row] = float(source["volts"])
    for current in currents or []:
        amps = float(current["amps"])
        plus = str(current["plus"])
        minus = str(current["minus"])
        if plus not in GROUND_NETS:
            rhs[index[plus]] -= amps
        if minus not in GROUND_NETS:
            rhs[index[minus]] += amps
    try:
        solved = _solve(matrix, rhs)
    except ValueError:
        return {"voltages": {}, "resistor_i": {}, "source_i": {}, "singular": True}
    voltages = {name: solved[index[name]] for name in nodes}
    for name in GROUND_NETS:
        voltages[name] = 0.0
    resistor_i = {}
    for ref, a, b, ohm in resistors:
        resistor_i[ref] = (voltages.get(a, 0.0) - voltages.get(b, 0.0)) / ohm
    source_i = {}
    for k, source in enumerate(sources):
        source_i[k] = solved[n + k]
    return {"voltages": voltages, "resistor_i": resistor_i, "source_i": source_i, "singular": False}


def solve_state(
    resistors: list[tuple[str, str, str, float]],
    state: dict[str, Any],
) -> dict[str, Any]:
    sources = list(state["sources"])
    point = operating_point(resistors, sources)
    dropped = False
    if state.get("clamp") and not point["singular"]:
        zener_idx = next(i for i, source in enumerate(sources) if source["role"] == "zener")
        current = point["source_i"].get(zener_idx, 0.0)
        zener_net = str(sources[zener_idx]["plus"])
        held = abs(point["voltages"].get(zener_net, 0.0) - float(sources[zener_idx]["volts"])) <= 1e-3
        if current < 1e-3 or not held:
            sources = [source for source in sources if source["role"] not in {"zener", "vf"}]
            point = operating_point(resistors, sources)
            dropped = True
            state = dict(state)
            state["sources"] = sources
    steps = []
    for net, volts in sorted(point["voltages"].items()):
        if net in GROUND_NETS:
            continue
        if any(str(source["plus"]) == net and source["role"] == "supply" for source in sources):
            continue
        steps.append(f"V({net}) = {volts:.6g} V")
    powers = {ref: (amp * amp) * ohm for ref, _a, _b, ohm in resistors for amp in [point["resistor_i"].get(ref, 0.0)]}
    return {
        "state_id": state["id"],
        "name": state["name"],
        "participates": bool(state.get("participates")),
        "dropped": dropped,
        "voltages": point["voltages"],
        "currents_a": point["resistor_i"],
        "source_i": point["source_i"],
        "sources": sources,
        "powers_w": powers,
        "steps": steps,
        "singular": point["singular"],
    }
