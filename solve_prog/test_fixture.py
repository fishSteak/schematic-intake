# -*- coding: utf-8 -*-
"""冻结夹具与门禁。"""

from __future__ import annotations

import ast
from pathlib import Path

from solve_prog.connection import connection_card
from solve_prog.deliver import OUT_DIR, run

ROOT = Path(__file__).resolve().parent


def fixture(*, vf: float | None = None, vce: float | None = None) -> dict:
    def resistor(ref: str, a: str, b: str) -> dict:
        return {
            "ref": ref,
            "type": "resistor",
            "value": "120",
            "p_rated": 0.1,
            "pins": [{"pin": "1", "net": a}, {"pin": "2", "net": b}],
        }

    diode = {
        "ref": "D8",
        "type": "diode",
        "mpn": "MMBD4148CA",
        "pins": [
            {"pin": "1", "net": "DS_ORI"},
            {"pin": "2", "net": "SPDL"},
            {"pin": "3", "net": "N_DS_FEED"},
        ],
    }
    if vf is not None:
        diode["pintable"] = {"params": {"vf_v": vf}}
    switch = {
        "ref": "Q4",
        "type": "transistor",
        "pins": [
            {"pin": "1", "net": "N_Q4_BASE"},
            {"pin": "2", "net": "GND"},
            {"pin": "3", "net": "SPDL"},
        ],
    }
    if vce is not None:
        switch["pintable"] = {"params": {"vce_sat_v": vce}}
    return {
        "components": {
            "R4": resistor("R4", "N_R52_R4", "+24V"),
            "R52": resistor("R52", "N_R47_R52", "N_R52_R4"),
            "R47": resistor("R47", "N_R46_R47", "N_R47_R52"),
            "R46": resistor("R46", "N_DS_FEED", "N_R46_R47"),
            "D8": diode,
            "D10": {
                "ref": "D10",
                "type": "diode",
                "mpn": "1N5241B",
                "pins": [{"pin": "1", "net": "GND"}, {"pin": "2", "net": "DS_ORI"}],
            },
            "Q4": switch,
            "CN1": {"ref": "CN1", "type": "connector", "pins": [{"pin": "1", "net": "SPDL"}]},
        },
        "nets": {
            "+24V": {"rail": {"status": "closed", "value": 24.0, "source": {"kind": "netname_voltage"}}},
            "DS_ORI": {
                "rail": {
                    "status": "closed",
                    "value": 11.55,
                    "source": {"kind": "zener_clamp", "ref": "D10", "vz_min_v": 10.45, "vz_max_v": 11.55},
                }
            },
            "GND": {},
        },
    }


def test_connection() -> None:
    card = connection_card(fixture(), "R52")
    by_net = {item["net"]: item for item in card["nets"]}
    left = by_net["N_R52_R4"]
    right = by_net["N_R47_R52"]
    assert left["kind"] == "intermediate" and left["voltage_v"] is None
    assert right["kind"] == "intermediate" and right["voltage_v"] is None
    assert {(row["ref"], row["pin"]) for row in left["pins"]} == {("R52", "2"), ("R4", "1")}
    assert {(row["ref"], row["pin"]) for row in right["pins"]} == {("R52", "1"), ("R47", "2")}
    diode = connection_card(fixture(), "D8")
    assert len(diode["pins"]) == 3
    assert all(item["voltage_v"] is None for item in diode["nets"])


def test_delivery() -> None:
    payload = run(fixture(), "P11-A01", "R52")
    assert payload["deliverable"] is True, payload["gates"]
    assert payload["conclusion"] == "否"
    assert payload["source"] in {"formula", "ngspice"}
    if payload["source"] == "formula":
        assert payload["spice"]["detail"] == "本机没有 ngspice"
        assert payload["source"] != "ngspice"
    for ref in ("R4", "R52", "R47", "R46"):
        assert payload["objects"][ref]["conclusion"] == "否"
    names = " ".join(state["name"] for state in payload["states"])
    assert "未上电" in names and "下限" in names and "上限" in names and "导通" in names and "外部接地" in names
    assert all(not state["participates"] for state in payload["states"] if "外部接地" in state["name"])
    assert any(item["origin"] == "stamped_zero" for item in payload["stimulus"]["model"])
    steps = " ".join(step for state in payload["states"] for step in state["steps"])
    assert "V(N_R52_R4)" in steps and "V(N_R47_R52)" in steps
    failed = run(fixture(), "P99-A01", "R52")
    assert failed["conclusion"] == "未过门禁"
    assert failed["objects"] == {}
    assert "是" not in failed["conclusion"] and "否" not in failed["conclusion"]


def test_isolation() -> None:
    for path in ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(name.startswith("kernel.run") or name.startswith("kernel.checkers") for name in names), path
    assert OUT_DIR.parent == ROOT


def main() -> None:
    from solve_prog.spice import pint_power_w, write_deck

    deck, _mapping = write_deck(
        [("R52", "N_A", "GND", 120.0)],
        [{"plus": "N_A", "minus": "GND", "volts": 24.0, "role": "supply", "ref": "N_A", "origin": "supply"}],
    )
    assert "DC 24.0" in deck or "DC 24" in deck
    assert abs(pint_power_w(0.05, 120.0) - 0.3) < 1e-9
    test_connection()
    test_delivery()
    test_isolation()
    print("OK")


if __name__ == "__main__":
    main()
