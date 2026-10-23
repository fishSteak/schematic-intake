# -*- coding: utf-8 -*-
"""ngspice 网表与读数对照。没有可执行文件时来源保持 formula。"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

POWER_TOL_W = 0.5e-3


def pint_power_w(current_a: float, ohm: float) -> float:
    import pint

    ureg = pint.UnitRegistry()
    power = (current_a * ureg.ampere) ** 2 * (ohm * ureg.ohm)
    return float(power.to(ureg.watt).magnitude)


def _alias(name: str, mapping: dict[str, str]) -> str:
    if name not in mapping:
        mapping[name] = f"n{len(mapping)}"
    return mapping[name]


def _as_volt(value: float) -> float:
    import pint

    ureg = pint.UnitRegistry()
    return float((value * ureg.volt).to(ureg.volt).magnitude)


def _as_ohm(value: float) -> float:
    import pint

    ureg = pint.UnitRegistry()
    return float((value * ureg.ohm).to(ureg.ohm).magnitude)


def write_deck(
    resistors: list[tuple[str, str, str, float]],
    sources: list[dict[str, Any]],
) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {"GND": "0"}
    lines = ["* solve_prog ideal sources and resistors"]
    for index, source in enumerate(sources, start=1):
        plus = _alias(str(source["plus"]), mapping)
        minus = _alias(str(source["minus"]), mapping)
        lines.append(f"V{index} {plus} {minus} DC {_as_volt(float(source['volts']))}")
    for ref, a, b, ohm in resistors:
        lines.append(f"R{ref} {_alias(a, mapping)} {_alias(b, mapping)} {_as_ohm(ohm)}")
    watch = []
    for ref, _a, _b, _ohm in resistors:
        watch.append(f"i(R{ref})")
    lines.append(".control")
    lines.append("op")
    lines.append("print " + " ".join(watch))
    lines.append(".endc")
    lines.append(".end")
    return "\n".join(lines) + "\n", mapping


def ngspice_bin() -> str | None:
    return shutil.which("ngspice")


def run_deck(deck: str) -> dict[str, float] | None:
    binary = ngspice_bin()
    if not binary:
        return None
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "deck.cir"
        path.write_text(deck, encoding="utf-8")
        done = subprocess.run(
            [binary, "-b", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    text = (done.stdout or "") + "\n" + (done.stderr or "")
    currents: dict[str, float] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower().startswith("r") and parts[0][1:2].isalpha():
            try:
                currents[parts[0][1:]] = float(parts[-1])
            except ValueError:
                continue
    return currents


def compare(
    resistors: list[tuple[str, str, str, float]],
    solved_states: list[dict[str, Any]],
) -> dict[str, Any]:
    if ngspice_bin() is None:
        return {"source": "formula", "matched": True, "detail": "本机没有 ngspice"}
    ohms = {ref: ohm for ref, _a, _b, ohm in resistors}
    worst = 0.0
    for state in solved_states:
        deck, _mapping = write_deck(resistors, state["sources"])
        currents = run_deck(deck)
        if currents is None:
            return {"source": "formula", "matched": False, "detail": "ngspice 没有返回电流"}
        for ref, ohm in ohms.items():
            if ref not in state["powers_w"]:
                continue
            spice_i = currents.get(ref)
            if spice_i is None:
                return {"source": "formula", "matched": False, "detail": f"ngspice 缺少 {ref}"}
            spice_p = pint_power_w(spice_i, ohm)
            delta = abs(spice_p - float(state["powers_w"][ref]))
            worst = max(worst, delta)
    if worst > POWER_TOL_W:
        return {"source": "formula", "matched": False, "detail": f"两路功耗差 {worst * 1e3:.4f} mW"}
    return {"source": "ngspice", "matched": True, "detail": f"两路功耗差 {worst * 1e3:.4f} mW"}
