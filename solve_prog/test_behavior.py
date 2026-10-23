# -*- coding: utf-8 -*-
"""脚行为求解：手工入参，不按板卡分支。"""

from __future__ import annotations

from kernel.pintable import extract_vf_max
from solve_prog.behavior_solve import solve
from solve_prog.extract_behavior import fill_elements, params_for
from solve_prog.gates import gate_g6


def _r4_elements() -> list[dict]:
    chain = [
        ("R4", "+24V", "N_R52_R4"),
        ("R52", "N_R52_R4", "N_R47_R52"),
        ("R47", "N_R47_R52", "N_R46_R47"),
        ("R46", "N_R46_R47", "N_DS_FEED"),
    ]
    elements: list[dict] = [
        {
            "ref": ref,
            "behavior": "resistor",
            "plus": a,
            "minus": b,
            "ohm": 120.0,
            "p_rated_w": 0.1,
        }
        for ref, a, b in chain
    ]
    elements.append(
        {
            "ref": "+24V",
            "behavior": "voltage",
            "plus": "+24V",
            "minus": "GND",
            "volts": 24.0,
            "can_be_off": True,
        }
    )
    for ref, minus in (("D8_ORI", "DS_ORI"), ("D8_SPDL", "SPDL")):
        elements.append(
            {
                "ref": ref,
                "behavior": "diode",
                "plus": "N_DS_FEED",
                "minus": minus,
                "vf_max_v": 1.0,
                "vf_model_v": 0.7,
            }
        )
    elements.append(
        {
            "ref": "D10",
            "behavior": "zener",
            "plus": "DS_ORI",
            "minus": "GND",
            "vz_min_v": 10.45,
            "vz_nom_v": 11.0,
            "vz_max_v": 11.55,
            "izt_a": 0.02,
            "zzt_ohm": 22.0,
            "izk_a": 0.00025,
        }
    )
    elements.append(
        {
            "ref": "Q4",
            "behavior": "switch",
            "plus": "SPDL",
            "minus": "GND",
            "on_drop_v": 0.3,
        }
    )
    for ref, ohm in (
        ("U5A", 3900.0),
        ("U5B", 3900.0),
        ("U5C", 3900.0),
        ("U5D", 3900.0),
        ("R21R20", 30000.0),
        ("R1", 47000.0),
    ):
        net = "SPDL" if ref == "R1" else "DS_ORI"
        elements.append(
            {
                "ref": ref,
                "behavior": "switch",
                "plus": net,
                "minus": "GND",
                "on_ohm": ohm,
                "tied": "on",
            }
        )
    elements.append(
        {
            "ref": "CN1",
            "behavior": "port",
            "plus": "SPDL",
            "minus": "GND",
            "participates": False,
        }
    )
    return elements


def _row(result: dict, name: str) -> dict:
    found = [row for row in result["rows"] if name in row["name"]]
    assert found, result["rows"]
    return found[0]


def _span(row: dict, ref: str) -> tuple[float, float]:
    power = row["powers"][ref]
    return power["lo"], power["hi"]


def test_r4() -> None:
    result = solve(_r4_elements())
    assert result["conclusion"] == "否"
    assert result["objects"]["R4"]["conclusion"] == "否"
    assert result["objects"]["R4"]["limit_w"] == 0.05

    off = _row(result, "未上电")
    assert _span(off, "R4") == (0.0, 0.0)
    assert off["verdicts"]["R4"] == "通过"

    low = _row(result, "稳压 D10 下限")
    lo, hi = _span(low, "R4")
    assert abs(lo - 0.08600) < 5e-4 and abs(hi - lo) < 1e-6
    assert low["verdicts"]["R4"] == "否"

    low_max = _row(result, "下限 10.45 V，Vf 1")
    lo, hi = _span(low_max, "R4")
    assert abs(lo - 0.08203) < 5e-4
    assert low_max["verdicts"]["R4"] == "否"

    high = _row(result, "上限 11.55 V，Vf 0.7")
    lo, hi = _span(high, "R4")
    assert abs(lo - 0.07191) < 5e-4 and abs(hi - lo) < 1e-6

    high_max = _row(result, "上限 11.55 V，Vf 1")
    lo, hi = _span(high_max, "R4")
    assert abs(lo - 0.06828) < 5e-4

    nominal = _row(result, "稳压 D10 标称")
    lo, hi = _span(nominal, "R4")
    assert abs(lo - hi) < 1e-9
    assert 0.079 < lo < 0.082

    switched = _row(result, "Q4 导通，Vf 0.7")
    lo, hi = _span(switched, "R4")
    assert abs(lo - 0.27552) < 5e-4
    assert switched["verdicts"]["R4"] == "否"
    switched_max = next(row for row in result["rows"] if row["name"].startswith("Q4") and "Vf 1" in row["name"])
    lo, hi = _span(switched_max, "R4")
    assert abs(lo - 0.26838) < 5e-4
    assert switched_max["verdicts"]["R4"] == "否"

    port = _row(result, "外部接地，Vf 0.7")
    assert port["verdicts"]["R4"] == "不参评"
    lo, hi = _span(port, "R4")
    assert abs(lo - 0.28276) < 5e-4


def test_divider() -> None:
    result = solve(
        [
            {"ref": "V5", "behavior": "voltage", "plus": "V5", "minus": "GND", "volts": 5.0},
            {"ref": "RA", "behavior": "resistor", "plus": "V5", "minus": "MID", "ohm": 10000.0, "p_rated_w": 0.1},
            {"ref": "RB", "behavior": "resistor", "plus": "MID", "minus": "GND", "ohm": 10000.0, "p_rated_w": 0.1},
        ]
    )
    assert result["conclusion"] == "通过"
    on = _row(result, "直流工作")
    lo, hi = _span(on, "RA")
    assert abs(lo - 6.25e-4) < 1e-6
    assert abs(hi - lo) < 1e-12


def test_current() -> None:
    result = solve(
        [
            {"ref": "I1", "behavior": "current", "plus": "GND", "minus": "N1", "amps": 0.001},
            {"ref": "R1", "behavior": "resistor", "plus": "N1", "minus": "GND", "ohm": 1000.0, "p_rated_w": 0.1},
        ]
    )
    assert result["conclusion"] == "通过"
    row = result["rows"][0]
    lo, hi = _span(row, "R1")
    assert abs(lo - 0.001) < 1e-9


def test_unknown_behavior() -> None:
    result = solve(
        [
            {"ref": "V1", "behavior": "voltage", "plus": "V1", "minus": "GND", "volts": 12.0, "can_be_off": False},
            {"ref": "R1", "behavior": "resistor", "plus": "V1", "minus": "OUT", "ohm": 1000.0, "p_rated_w": 0.1},
            {"ref": "U9", "behavior": "led_driver", "plus": "OUT", "minus": "GND"},
        ]
    )
    assert result["conclusion"] == "待确认"
    unknown = _row(result, "不在清单")
    assert unknown["verdicts"]["R1"] == "待确认"
    assert unknown["powers"]["R1"] is None


def test_extract_fills_r4_without_hand_numbers() -> None:
    text = "VF Forward Voltage IF=10mA --- 1.00 V"
    assert extract_vf_max(text) == 1.0
    diode_params = params_for("MMBD4148CA", text)
    assert diode_params["vf_max_v"] == 1.0
    assert "vf_v" not in diode_params
    zener_params = params_for("1N5241B")
    assert zener_params["vz_min_v"] == 10.45
    assert zener_params["izt_a"] == 0.02
    assert zener_params["zzt_ohm"] == 22.0
    skeleton = []
    for item in _r4_elements():
        blank = dict(item)
        for key in (
            "vf_max_v",
            "vf_model_v",
            "vz_min_v",
            "vz_nom_v",
            "vz_max_v",
            "izt_a",
            "zzt_ohm",
            "izk_a",
            "on_drop_v",
            "p_rated_w",
        ):
            blank.pop(key, None)
        if item["ref"] in {"D8_ORI", "D8_SPDL"}:
            blank["mpn"] = "MMBD4148CA"
        elif item["ref"] == "D10":
            blank["mpn"] = "1N5241B"
        elif item["ref"] == "Q4":
            blank["mpn"] = "2SC1623"
        elif item["behavior"] == "resistor":
            blank["mpn"] = "0603WAF1200T5E"
        skeleton.append(blank)
    by_ref = {
        "D8_ORI": diode_params,
        "D8_SPDL": diode_params,
        "Q4": {"vce_sat_v": 0.3},
    }
    filled = fill_elements(skeleton, by_ref)
    result = solve(filled)
    assert result["conclusion"] == "否"
    low = _row(result, "稳压 D10 下限")
    lo, hi = _span(low, "R4")
    assert abs(lo - 0.08600) < 5e-4 and abs(hi - lo) < 1e-6
    nominal = _row(result, "标称")
    lo, hi = _span(nominal, "R4")
    assert 0.079 < lo < 0.082


def test_gate_uses_interval() -> None:
    ohms = {ref: 120.0 for ref in ("R4", "R52", "R47", "R46")}
    r_sum = 480.0
    vs = 24.0
    vz = 10.45

    def power(v_exit: float) -> float:
        amp = (vs - v_exit) / r_sum
        return amp * amp * 120.0

    upper = power(vz)
    stimulus = {
        "model": [{"param": "vf_max_v", "volts": 1.0, "origin": "extracted"}],
        "group": {"ohms": ohms, "v_supply": vs},
        "states": [
            {
                "id": "zener_min",
                "participates": True,
                "sources": [
                    {"role": "supply", "volts": vs, "plus": "+24V", "minus": "GND"},
                    {"role": "zener", "volts": vz, "plus": "DS_ORI", "minus": "GND"},
                    {"role": "vf", "volts": 0.0, "plus": "N_DS_FEED", "minus": "DS_ORI"},
                ],
            }
        ],
    }
    inside = {
        "state_id": "zener_min",
        "sources": stimulus["states"][0]["sources"],
        "powers_w": {ref: upper for ref in ohms},
    }
    assert gate_g6(stimulus, [inside])["pass"]
    outside = dict(inside)
    outside["powers_w"] = {ref: 0.05 for ref in ohms}
    failed = gate_g6(stimulus, [outside])
    assert failed["pass"] is False
    assert "区间" in failed["detail"]


def main() -> None:
    test_r4()
    test_divider()
    test_current()
    test_unknown_behavior()
    test_extract_fills_r4_without_hand_numbers()
    test_gate_uses_interval()
    print("OK")


if __name__ == "__main__":
    main()
