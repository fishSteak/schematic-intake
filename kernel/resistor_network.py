# -*- coding: utf-8 -*-
"""DC resistor stress for P11-A01 (N-hop + switch envelope).

Rules:
- Closed rails + GND are known (incl. netname_voltage); bare netname candidate rejected.
- Interior: R / C(open) / L(DC short) / high-Z IC inputs / LED·diode bridges / switch CE·DS.
- Comparator OC + BJT/MOS: enumerate OFF/ON; P_max = max |Va−Vb| across states.
- LED/diode Vf and Vce_sat/Rds_on only from datasheet/pintable; unknown → assume 0 as
  upper-bound envelope (never invent typical Vf). Raised P over limit → 否.
- Never invent GND on the far pin; never use single-ended rail as drop across a divider arm.
- If removing a 2-terminal leaves an end that cannot reach a DC source (rail/GND/CMOS out/
  OC/switch/unknown_load/unknown_active; C open; whitelist IC inputs ignored), I=0 and
  resistor V=I·R=0. Capacitor voltage is NOT zero from I_dc=0; only same-net plates get V=0.
- Connector/header is dc_unknown_load: not a voltage clamp, not I=0-open. It does not
  block the on-board nodal solve. Series-to-socket enumerates 开路 (P=0) and 对地
  (V=|Vboard|); any state over limit → 否. Shunt on a driven net uses the on-board
  switch envelope.
- Two-terminal diodes with exactly one pin on a closed rail/GND are series-feed bridges
  (same Vf rule as LED). Three-pin diodes whose common pin is a push-pull/opamp/CE
  driver inject common↔other legs only (no anode-anode short). Signal clamps stay out.
- Vbe/Vf/Vce assumed-zero is only allowed when it raises P. Missing Vbe does not insert
  a BE short. Missing Vf/Vce still assume 0 as an upper-bound on the drop path.
- Closed V≈0 is rejected (rule_gap) when fanout used a diode/LED/switch/L hop the
  solver omitted, or Vbe=0 paralleled this resistor with another emitter resistor.
- Empty V with only unknown_load blocking a driven shunt, or a diode hop to push-pull
  left out of the solve, is rule_gap (false-open).
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any

GROUND_NETS = {"GND", "AGND", "DGND", "PGND", "VSS", "AVSS"}
SWITCH_TYPES = {"transistor", "mosfet", "bjt", "fet", "jfet"}
DIODE_TYPES = {"led", "diode"}
FANOUT_MAX_HOPS = 8
FANOUT_MAX_DEVS = 24
SHORT_OHM = 1e-3

_IC_INPUT_PINS: list[tuple[re.Pattern[str], set[str]]] = [
    (re.compile(r"^LM393", re.I), {"2", "3", "5", "6"}),
    (re.compile(r"^LM2903", re.I), {"2", "3", "5", "6"}),
    (re.compile(r"^LM339", re.I), {"4", "5", "6", "7", "8", "9", "10", "11"}),
    (re.compile(r"^LM2901", re.I), {"4", "5", "6", "7", "8", "9", "10", "11"}),
    (re.compile(r"^LM358", re.I), {"2", "3", "5", "6"}),
    (re.compile(r"^LM324", re.I), {"2", "3", "5", "6", "9", "10", "12", "13"}),
    (re.compile(r"^LM392", re.I), {"2", "3", "5", "6"}),
    # CMOS / Schmitt inverter inputs (complement of push-pull outputs)
    (re.compile(r"^MC14584", re.I), {"1", "3", "5", "9", "11", "13"}),
    (re.compile(r"^CD40106|^74HC14|^74HCT14", re.I), {"1", "3", "5", "9", "11", "13"}),
    (re.compile(r"^74HC04|^74HCT04|^CD4069", re.I), {"1", "3", "5", "9", "11", "13"}),
    # Analog-switch *control* pins only (I/O is a channel, not high-Z)
    (re.compile(r"^CD4066|^CD4016|^74HC4066|^74HCT4066", re.I), {"5", "6", "12", "13"}),
    (re.compile(r"^CD4051|^74HC4051|^74HCT4051", re.I), {"6", "9", "10", "11"}),
    (re.compile(r"^CD4052|^74HC4052|^74HCT4052", re.I), {"6", "9", "10"}),
    (re.compile(r"^CD4053|^74HC4053|^74HCT4053", re.I), {"6", "9", "10", "11"}),
]

_IC_OC_OUTPUT_PINS: list[tuple[re.Pattern[str], set[str]]] = [
    (re.compile(r"^LM393", re.I), {"1", "7"}),
    (re.compile(r"^LM2903", re.I), {"1", "7"}),
    (re.compile(r"^LM339", re.I), {"1", "2", "13", "14"}),
    (re.compile(r"^LM2901", re.I), {"1", "2", "13", "14"}),
    (re.compile(r"^LM392", re.I), {"1"}),
]

# Push-pull CMOS / Schmitt outputs: (mpn, output_pins, vdd_pin)
_IC_CMOS_PUSH_PULL: list[tuple[re.Pattern[str], set[str], str]] = [
    (re.compile(r"^MC14584", re.I), {"2", "4", "6", "8", "10", "12"}, "14"),
    (re.compile(r"^CD40106|^74HC14|^74HCT14", re.I), {"2", "4", "6", "8", "10", "12"}, "14"),
    (re.compile(r"^74HC04|^74HCT04|^CD4069", re.I), {"2", "4", "6", "8", "10", "12"}, "14"),
    # Opamp rail envelope (0/VDD) only when NOT a voltage follower
    (re.compile(r"^LM358|^LM2904", re.I), {"1", "7"}, "8"),
    (re.compile(r"^LM324|^LM2902", re.I), {"1", "7", "8", "14"}, "4"),
]

# out_pin -> (in_minus, in_plus); pairing is per channel, never chip-wide IN−
_OPAMP_CHANNELS: list[tuple[re.Pattern[str], dict[str, tuple[str, str]]]] = [
    (re.compile(r"^LM358|^LM2904", re.I), {"1": ("2", "3"), "7": ("6", "5")}),
    (re.compile(r"^LM392", re.I), {"7": ("6", "5")}),
    (
        re.compile(r"^LM324|^LM2902", re.I),
        {"1": ("2", "3"), "7": ("6", "5"), "8": ("9", "10"), "14": ("13", "12")},
    ),
]

CASE_V_TOL = 1e-3  # cluster |Va−Vb| within 1 mV → same case


def _pin_nets(c: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for p in c.get("pins") or []:
        if isinstance(p, dict):
            pin = str(p.get("pin") or p.get("name") or "")
            if "." in pin:
                pin = pin.split(".")[-1]
            out.append((pin, str(p.get("net") or "")))
        elif isinstance(p, str):
            out.append(("", p))
    return out


def _unique_pin_nets(c: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for _, n in _pin_nets(c):
        if n and n not in seen:
            seen.append(n)
    return seen


def _comp_dnp(c: dict[str, Any]) -> bool:
    blob = " ".join(
        str(x or "") for x in (c.get("status"), c.get("value"), c.get("value_bom"), c.get("mpn"))
    ).upper()
    tokens = set(blob.replace(",", " ").split())
    return bool(tokens & {"DNP", "DNI", "NF"})


def _params(c: dict[str, Any]) -> dict[str, Any]:
    pt = c.get("pintable") or {}
    return pt.get("params") or {}


def _parse_res_ohm(val: str) -> float | None:
    s = (val or "").strip().replace("Ω", "").replace("ohm", "").replace("Ohm", "")
    if not s or s.upper() == "TBD":
        return None
    m = re.match(r"^(\d+)[Rr](\d+)$", s)
    if m:
        return float(m.group(1)) + float(m.group(2)) / (10 ** len(m.group(2)))
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*([kKmMrR])?$", s)
    if not m:
        return None
    n = float(m.group(1))
    u = (m.group(2) or "").lower()
    if u == "k":
        return n * 1e3
    if u == "m":
        return n * 1e6
    return n


def _closed_rail_v(nd: dict[str, Any]) -> float | None:
    rail = (nd or {}).get("rail") or {}
    if rail.get("status") != "closed" or rail.get("value") is None:
        return None
    src = rail.get("source")
    if src == "netname" or (isinstance(src, dict) and src.get("kind") == "netname"):
        return None
    try:
        return float(rail["value"])
    except (TypeError, ValueError):
        return None


def _type_of(ref: str, c: dict[str, Any]) -> str:
    t = (c.get("type") or "").lower()
    if t and t != "unknown":
        return t
    m = re.match(r"^([A-Za-z]+)", ref or "")
    p = (m.group(1) if m else "").upper()
    return {
        "R": "resistor",
        "RN": "resistor",
        "C": "capacitor",
        "L": "inductor",
        "U": "ic",
        "D": "diode",
        "Q": "transistor",
        "Y": "crystal",
        "CN": "connector",
        "J": "connector",
        "P": "connector",
        "H": "header",
        "TB": "connector",
        "LED": "led",
        "F": "fuse",
    }.get(p, "unknown")


def _ic_input_pins(mpn: str) -> set[str] | None:
    for pat, pins in _IC_INPUT_PINS:
        if pat.search(mpn or ""):
            return pins
    return None


def _ic_oc_output_pins(mpn: str) -> set[str] | None:
    for pat, pins in _IC_OC_OUTPUT_PINS:
        if pat.search(mpn or ""):
            return pins
    return None


def _is_high_z_ic_pin(ref: str, pin: str, comps: dict[str, Any]) -> bool:
    c = comps.get(ref) or {}
    if _type_of(ref, c) != "ic":
        return False
    allowed = _ic_input_pins(str(c.get("mpn") or c.get("value") or ""))
    if not allowed:
        return False
    return str(pin) in allowed


def _is_oc_output_pin(ref: str, pin: str, comps: dict[str, Any]) -> bool:
    c = comps.get(ref) or {}
    if _type_of(ref, c) != "ic":
        return False
    outs = _ic_oc_output_pins(str(c.get("mpn") or c.get("value") or ""))
    return bool(outs and str(pin) in outs)


def _is_cmos_output_pin(ref: str, pin: str, comps: dict[str, Any]) -> bool:
    c = comps.get(ref) or {}
    if _type_of(ref, c) != "ic":
        return False
    info = _cmos_output_pins(str(c.get("mpn") or c.get("value") or ""))
    return bool(info and str(pin) in info[0])


def _is_oc_pull_down_net(net: str, comps: dict[str, Any]) -> bool:
    for ref, pin, typ in _net_attachments(net, comps):
        if typ == "ic" and _is_oc_output_pin(ref, pin, comps):
            return True
    return False


def _cmos_output_pins(mpn: str) -> tuple[set[str], str] | None:
    for pat, outs, vdd_pin in _IC_CMOS_PUSH_PULL:
        if pat.search(mpn or ""):
            return outs, vdd_pin
    return None


def _opamp_channels(mpn: str) -> dict[str, tuple[str, str]] | None:
    for pat, ch in _OPAMP_CHANNELS:
        if pat.search(mpn or ""):
            return ch
    return None


def _pin_net_map(c: dict[str, Any]) -> dict[str, str]:
    return {p: n for p, n in _pin_nets(c) if p and n}


def follower_channel_on_ic(c: dict[str, Any], out_pin: str) -> dict[str, str] | None:
    """If this OUT pin is shorted to same-channel IN−, return nets. Else None."""
    ch = _opamp_channels(str(c.get("mpn") or c.get("value") or ""))
    if not ch:
        return None
    pair = ch.get(str(out_pin))
    if not pair:
        return None
    in_minus, in_plus = pair
    pmap = _pin_net_map(c)
    out_net = pmap.get(str(out_pin)) or ""
    minus_net = pmap.get(in_minus) or ""
    plus_net = pmap.get(in_plus) or ""
    if not out_net or out_net != minus_net:
        return None
    return {
        "out_pin": str(out_pin),
        "in_minus": in_minus,
        "in_plus": in_plus,
        "out_net": out_net,
        "in_minus_net": minus_net,
        "in_plus_net": plus_net,
    }


def is_voltage_follower_net(net: str, comps: dict[str, Any]) -> dict[str, str] | None:
    """If net is an opamp OUT shorted to same-channel IN−, return channel nets."""
    for ref, pin, typ in _net_attachments(net, comps):
        if typ != "ic":
            continue
        c = comps.get(ref) or {}
        info = follower_channel_on_ic(c, str(pin))
        if info and info.get("out_net") == net:
            info = dict(info)
            info["ref"] = ref
            return info
    return None


def _ic_vdd_net(ref: str, c: dict[str, Any]) -> str | None:
    """Locate VDD/VCC pin net on an IC."""
    mpn = str(c.get("mpn") or c.get("value") or "")
    info = _cmos_output_pins(mpn)
    vdd_pin = info[1] if info else None
    pins = _pin_nets(c)
    by_pin = {p: n for p, n in pins if p and n}
    if vdd_pin and vdd_pin in by_pin:
        return by_pin[vdd_pin]
    for pin, net in pins:
        name = ""
        for p in c.get("pins") or []:
            if isinstance(p, dict) and str(p.get("pin") or "").endswith(pin):
                name = str(p.get("name") or p.get("pin_name") or "").upper()
                break
        if re.search(r"VDD|VCC|AVDD|DVDD", name) or re.match(r"^(VDD|VCC)$", pin, re.I):
            return net
        if net and re.match(r"^(\+?\d+V|VCC|VDD)", net, re.I):
            # Prefer explicit supply-named net on this IC
            if pin == vdd_pin or vdd_pin is None:
                return net
    # Fallback: any pin on a known-looking supply net name
    for pin, net in pins:
        if net and re.match(r"^(\+|VCC|VDD|AVDD)", net, re.I) and net not in GROUND_NETS:
            return net
    return None


def _cmos_drive_on_net(
    net: str,
    comps: dict[str, Any],
    nets: dict[str, Any],
    known: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """If net is a CMOS push-pull output with closed VDD, return drive info."""
    for ref, pin, typ in _net_attachments(net, comps):
        if typ != "ic":
            continue
        c = comps.get(ref) or {}
        mpn = str(c.get("mpn") or c.get("value") or "")
        info = _cmos_output_pins(mpn)
        if not info:
            continue
        outs, _vdd_pin = info
        if str(pin) not in outs:
            continue
        if follower_channel_on_ic(c, str(pin)):
            continue
        vdd_net = _ic_vdd_net(ref, c)
        if not vdd_net:
            continue
        if vdd_net in known:
            vdd_v = float(known[vdd_net]["v"])
        else:
            cv = _closed_rail_v(nets.get(vdd_net) or {})
            if cv is None:
                continue
            vdd_v = float(cv)
        return {
            "kind": "cmos",
            "ref": ref,
            "pin": str(pin),
            "net": net,
            "vdd_net": vdd_net,
            "vdd_v": vdd_v,
            "id": f"cmos:{net}",
        }
    return None


def _local_drivers_for_resistor(
    ref: str,
    n_a: str,
    n_b: str,
    comps: dict[str, Any],
    nets: dict[str, Any],
    known: dict[str, dict[str, Any]],
    sw_by_ref: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drivers that change THIS resistor's |Va−Vb| (not whole-board cartesian)."""
    pin_nets = {n for n in (n_a, n_b) if n}
    # Both ends already closed → no local driver can change |Va−Vb|
    if n_a in known and n_b in known:
        return []
    # One-hop via LED/always bridges already on pin nets (not from rails/GND)
    hop: set[str] = set(pin_nets)
    for n in list(pin_nets):
        if n in known or n in GROUND_NETS:
            continue
        for r2, _p, typ in _net_attachments(n, comps):
            if typ in DIODE_TYPES:
                for _pp, nn in _pin_nets(comps.get(r2) or {}):
                    if nn:
                        hop.add(nn)
            if typ in SWITCH_TYPES and r2 in sw_by_ref:
                ce = _switch_ce_nets(r2, comps.get(r2) or {})
                if ce:
                    hop.add(ce[0])
                    hop.add(ce[1])

    drivers: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(d: dict[str, Any]) -> None:
        i = d.get("id") or ""
        if i and i not in seen:
            seen.add(i)
            drivers.append(d)

    for n in hop:
        if n in known or n in GROUND_NETS:
            continue
        if _is_oc_pull_down_net(n, comps):
            add({"kind": "oc", "net": n, "id": f"oc:{n}", "ref": n})
        cmos = _cmos_drive_on_net(n, comps, nets, known)
        if cmos:
            add(cmos)
        for r2, _p, typ in _net_attachments(n, comps):
            if typ not in SWITCH_TYPES or r2 not in sw_by_ref:
                continue
            ce = _switch_ce_nets(r2, comps.get(r2) or {})
            if not ce:
                continue
            # Only CE on this resistor's pin nets changes |Va−Vb|.
            # Base-only attachment (R7→Q1.B) must NOT count Q as a switch driver.
            if ce[0] in pin_nets or ce[1] in pin_nets:
                add(
                    {
                        "kind": "switch",
                        "ref": r2,
                        "id": f"sw:{r2}",
                        "net_a": ce[0],
                        "net_b": ce[1],
                        "bridge": sw_by_ref[r2],
                    }
                )
    return drivers


def _driver_case_name(drv: dict[str, Any], on: bool, *, led_path: bool) -> str:
    kind = drv.get("kind")
    if kind == "switch":
        r = drv.get("ref") or "?"
        if led_path:
            return f"{r}导通/点亮" if on else f"{r}截止/熄灭"
        return f"{r}导通" if on else f"{r}截止"
    if kind == "oc":
        n = drv.get("net") or "?"
        if led_path:
            return f"{n} OC导通/点亮" if on else f"{n} OC截止/熄灭"
        return f"{n} OC导通" if on else f"{n} OC截止"
    if kind == "cmos":
        n = drv.get("net") or "?"
        return f"{n}高/驱动高" if on else f"{n}低/驱动低"
    return "导通" if on else "截止"


def _driver_indices_that_differ(
    drivers: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
) -> list[int]:
    """Keep only driver bits that actually flip between voltage clusters."""
    n = len(drivers)
    if n <= 1 or len(clusters) <= 1:
        return list(range(n))
    differ: list[int] = []
    for i in range(n):
        bit = 1 << i
        ons = {(int(cl.get("driver_mask") or 0) & bit) != 0 for cl in clusters}
        if len(ons) > 1:
            differ.append(i)
    return differ if differ else list(range(n))


def _cluster_cases(
    raw_states: list[dict[str, Any]],
    drivers: list[dict[str, Any]],
    *,
    led_path: bool,
) -> list[dict[str, Any]]:
    """Cluster by v_across; name from local drivers that differ across clusters."""
    if not raw_states:
        return []
    if not drivers:
        best = max(raw_states, key=lambda s: s.get("v_across") or 0.0)
        case = {
            "name_zh": "常通",
            "v_across": best.get("v_across"),
            "p_w": best.get("p_w"),
            "va": best.get("va"),
            "vb": best.get("vb"),
            "note": f"常通：V={best.get('v_across'):.6g}V → P={best.get('p_w'):.6g}W",
            "driver_mask": 0,
        }
        _copy_traces(case, best)
        return [case]

    # Sort descending V; greedy cluster
    ordered = sorted(raw_states, key=lambda s: -(s.get("v_across") or 0.0))
    clusters: list[dict[str, Any]] = []
    for s in ordered:
        v = float(s.get("v_across") or 0.0)
        hit = None
        for cl in clusters:
            if abs(float(cl["v_across"]) - v) < CASE_V_TOL:
                hit = cl
                break
        if hit is None:
            cl0 = {
                "v_across": v,
                "p_w": s.get("p_w"),
                "va": s.get("va"),
                "vb": s.get("vb"),
                "members": [s],
                "driver_mask": s.get("driver_mask", 0),
            }
            _copy_traces(cl0, s)
            clusters.append(cl0)
        else:
            hit["members"].append(s)
            if (s.get("p_w") or 0) > (hit.get("p_w") or 0):
                hit["p_w"] = s.get("p_w")
                hit["va"] = s.get("va")
                hit["vb"] = s.get("vb")
                hit["driver_mask"] = s.get("driver_mask", 0)
                _copy_traces(hit, s)

    # Prefer one cluster per distinct local pattern when 1 driver
    cases: list[dict[str, Any]] = []
    if len(drivers) == 1:
        # Build ON and OFF explicitly from members if present
        d0 = drivers[0]
        on_s = [s for s in raw_states if s.get("driver_mask", 0) & 1]
        off_s = [s for s in raw_states if not (s.get("driver_mask", 0) & 1)]
        for on, group in ((True, on_s), (False, off_s)):
            if not group:
                continue
            best = max(group, key=lambda s: s.get("v_across") or 0.0)
            name = _driver_case_name(d0, on, led_path=led_path)
            one = {
                "name_zh": name,
                "v_across": best.get("v_across"),
                "p_w": best.get("p_w"),
                "va": best.get("va"),
                "vb": best.get("vb"),
                "note": f"{name}：V={best.get('v_across'):.6g}V → P={best.get('p_w'):.6g}W",
                "driver_mask": best.get("driver_mask", 0),
            }
            _copy_traces(one, best)
            cases.append(one)
        cases.sort(key=lambda c: -(c.get("v_across") or 0.0))
        return cases

    name_ix = _driver_indices_that_differ(drivers, clusters)
    for cl in clusters:
        mask = int(cl.get("driver_mask") or 0)
        bits = []
        for i in name_ix:
            on = bool(mask & (1 << i))
            bits.append(_driver_case_name(drivers[i], on, led_path=led_path))
        name = "；".join(bits) if bits else "常通"
        named = {
            "name_zh": name,
            "v_across": cl["v_across"],
            "p_w": cl.get("p_w"),
            "va": cl.get("va"),
            "vb": cl.get("vb"),
            "note": f"{name}：V={cl['v_across']:.6g}V → P={cl.get('p_w'):.6g}W",
            "driver_mask": mask,
        }
        _copy_traces(named, cl)
        cases.append(named)
    cases.sort(key=lambda c: -(c.get("v_across") or 0.0))
    return cases


def _led_path_resistor(
    n_a: str,
    n_b: str,
    comps: dict[str, Any],
    drivers: list[dict[str, Any]] | None = None,
) -> bool:
    """True when this resistor is on (or driven by) an LED segment path."""
    for n in (n_a, n_b):
        for _r, _p, typ in _net_attachments(n, comps):
            if typ == "led":
                return True
        # One resistor hop to an LED (e.g. R9 on OUT_LED ← R53 ← LED)
        for r2, _p, typ in _net_attachments(n, comps):
            if typ != "resistor":
                continue
            for _pp, nn in _pin_nets(comps.get(r2) or {}):
                if not nn or nn == n:
                    continue
                for _r3, _p3, t3 in _net_attachments(nn, comps):
                    if t3 == "led":
                        return True
    if drivers:
        for d in drivers:
            if d.get("kind") != "switch":
                continue
            for n in (d.get("net_a"), d.get("net_b")):
                if not n:
                    continue
                for _r, _p, typ in _net_attachments(n, comps):
                    if typ == "led":
                        return True
    return False


def _net_attachments(net: str, comps: dict[str, Any]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for ref, c in comps.items():
        for pin, n in _pin_nets(c):
            if n == net:
                rows.append((ref, pin, _type_of(ref, c)))
    return rows


_DC_EDGE_TYPES = {"resistor", "inductor", "diode", "led", "fuse"}

ROLE_DC_EDGE = "dc_edge"
ROLE_DC_DRIVER = "dc_driver"
ROLE_DC_HIGH_Z = "dc_high_z"
ROLE_DC_UNKNOWN_LOAD = "dc_unknown_load"
ROLE_DC_UNKNOWN_ACTIVE = "dc_unknown_active"
_I0_BLOCK_ROLES = {ROLE_DC_DRIVER, ROLE_DC_UNKNOWN_LOAD, ROLE_DC_UNKNOWN_ACTIVE}


def _pin_role(ref: str, pin: str, typ: str, comps: dict[str, Any]) -> str:
    """One role per pin; I=0, solve eligibility, and post-gates share this table."""
    if typ in {"connector", "header"}:
        return ROLE_DC_UNKNOWN_LOAD
    if typ in _DC_EDGE_TYPES:
        return ROLE_DC_EDGE
    if typ == "capacitor":
        return ROLE_DC_HIGH_Z
    if typ in SWITCH_TYPES:
        return ROLE_DC_DRIVER
    if typ == "ic":
        if _is_high_z_ic_pin(ref, pin, comps):
            return ROLE_DC_HIGH_Z
        if _is_oc_output_pin(ref, pin, comps) or _is_cmos_output_pin(ref, pin, comps):
            return ROLE_DC_DRIVER
        return ROLE_DC_UNKNOWN_ACTIVE
    return ROLE_DC_UNKNOWN_ACTIVE


def _net_has_dc_driver(net: str, comps: dict[str, Any]) -> bool:
    if not net:
        return False
    for ref, pin, typ in _net_attachments(net, comps):
        if _pin_role(ref, pin, typ, comps) == ROLE_DC_DRIVER:
            return True
    return False


def _build_dc_graph(comps: dict[str, Any], nets: dict[str, Any]) -> dict[str, Any]:
    """Undirected DC graph: R/L/D/fuse are edges; C is open; IC inputs are not sources."""
    att: dict[str, list[tuple[str, str, str]]] = {}
    edges: list[tuple[str, tuple[str, ...]]] = []
    for ref, c in comps.items():
        if _comp_dnp(c):
            continue
        typ = _type_of(ref, c)
        pins = _pin_nets(c)
        for pin, n in pins:
            if n:
                att.setdefault(n, []).append((ref, pin, typ))
        if typ not in _DC_EDGE_TYPES:
            continue
        uniq: list[str] = []
        for _, n in pins:
            if n and n not in uniq:
                uniq.append(n)
        if len(uniq) >= 2:
            edges.append((ref, tuple(uniq)))
    return {"att": att, "edges": edges, "comps": comps, "nets": nets}


def _net_is_dc_source(net: str, graph: dict[str, Any]) -> bool:
    if not net:
        return False
    if net in GROUND_NETS:
        return True
    nets = graph.get("nets") or {}
    if _closed_rail_v(nets.get(net) or {}) is not None:
        return True
    comps = graph.get("comps") or {}
    for ref, pin, typ in (graph.get("att") or {}).get(net) or []:
        if _pin_role(ref, pin, typ, comps) in _I0_BLOCK_ROLES:
            return True
    return False


def _net_reaches_dc_source(net: str, graph: dict[str, Any], skip_ref: str) -> bool:
    if not net:
        return False
    seen: set[str] = set()
    q: deque[str] = deque([net])
    while q:
        n = q.popleft()
        if n in seen:
            continue
        seen.add(n)
        if _net_is_dc_source(n, graph):
            return True
        for ref, uniq in graph.get("edges") or []:
            if ref == skip_ref or n not in uniq:
                continue
            for m in uniq:
                if m not in seen:
                    q.append(m)
    return False


def _dc_open_cite(net: str, graph: dict[str, Any], skip_ref: str) -> str:
    comps = graph.get("comps") or {}
    hz: list[str] = []
    caps: list[str] = []
    other: list[str] = []
    for ref, pin, typ in (graph.get("att") or {}).get(net) or []:
        if ref == skip_ref:
            continue
        if typ == "capacitor":
            caps.append(ref)
        elif typ == "ic" and _is_high_z_ic_pin(ref, pin, comps):
            c = comps.get(ref) or {}
            mpn = str(c.get("mpn") or c.get("value") or ref)
            fam = mpn.split("/")[0]
            hz.append(f"{ref}.{pin} 为 {fam} 输入（高阻）")
        else:
            other.append(f"{ref}.{pin}" if pin else ref)
    if hz and not other:
        extra = f"；仅电容 {','.join(caps[:4])}" if caps else ""
        return f"{net}：" + "、".join(hz[:4]) + extra
    if not hz and not other and caps:
        return f"{net} 仅电容"
    bits = hz + [f"{x}电容" for x in caps[:3]] + other[:4]
    if bits:
        return f"{net} 去掉 {skip_ref} 后无直流通路（{'、'.join(bits)}）"
    return f"{net} 去掉 {skip_ref} 后无直流通路"


def two_terminal_dc_open(
    ref: str,
    c: dict[str, Any],
    kg: dict[str, Any],
    graph: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """I_dc=0 when same-net pins or a dangling end cannot reach a DC source without this part."""
    comps = kg.get("components") or {}
    nets = kg.get("nets") or {}
    g = graph or _build_dc_graph(comps, nets)
    uniq = _unique_pin_nets(c)
    if not uniq:
        pins = _pin_nets(c)
        n_a = pins[0][1] if pins else ""
        n_b = pins[1][1] if len(pins) > 1 else ""
        if not n_a or not n_b:
            return None
        uniq = [n_a] if n_a == n_b else [n_a, n_b]
    if len(uniq) == 1:
        n = uniq[0]
        return {
            "reason": "same_net",
            "n_a": n,
            "n_b": n,
            "cite": f"{ref} 两脚同网 {n}",
        }
    if len(uniq) != 2:
        return None
    n_a, n_b = uniq[0], uniq[1]
    dangling = [
        n
        for n in (n_a, n_b)
        if n and not _net_reaches_dc_source(n, g, skip_ref=ref)
    ]
    if not dangling:
        return None
    cites = [_dc_open_cite(n, g, skip_ref=ref) for n in dangling]
    return {
        "reason": "dangling",
        "n_a": n_a,
        "n_b": n_b,
        "cite": "；".join(cites),
        "dangling": dangling,
    }


def _dc_open_stress(
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float,
    info: dict[str, Any],
    nets: dict[str, Any],
) -> dict[str, Any]:
    cite = str(info.get("cite") or "一端无直流通路")
    same = info.get("reason") == "same_net"
    name = "两脚同网" if same else "无直流通路"
    case = {
        "name_zh": name,
        "v_across": 0.0,
        "p_w": 0.0,
        "va": None,
        "vb": None,
        "i_dc": 0,
        "dc_open_cite": cite,
        "note": f"I=0：{cite}",
        "driver_mask": 0,
    }
    before = {}
    for n in (n_a, n_b):
        if not n:
            continue
        rail = (nets.get(n) or {}).get("rail") or {}
        before[n] = {
            "status": rail.get("status"),
            "value": rail.get("value"),
            "candidate_v": rail.get("candidate_v"),
            "source": rail.get("source"),
        }
    state = {
        "name": name,
        "v_across": 0.0,
        "va": None,
        "vb": None,
        "p_w": 0.0,
        "i_dc": 0,
        "dc_open_cite": cite,
        "note": case["note"],
        "driver_mask": 0,
    }
    return {
        "nets": {"a": n_a, "b": n_b},
        "before": before,
        "v": 0.0,
        "ohm": ohm,
        "stress_kind": "operating_point",
        "v_source": {"kind": "dc_open", "note": cite, "net_a": n_a, "net_b": n_b},
        "states": [state],
        "cases": [case],
        "drivers": [],
        "drive_kind": "无直流",
        "note": cite,
        "fanout": {},
        "assumed_zero_drops": [],
        "path_missing_fields": [],
    }


def _net_is_passive_interior(
    net: str,
    comps: dict[str, Any],
    *,
    ignore_oc: bool = False,
    ignore_bridges: bool = False,
) -> bool:
    """Unknown node allowed in DC solve.

    When ignore_bridges: LED/diode/switch/inductor pins are ignored here because their
    connectivity is injected as explicit bridge edges for the active state.
    dc_unknown_load (connector/header) does not inject on-board current and does not
    block the node.
    """
    for ref, pin, typ in _net_attachments(net, comps):
        role = _pin_role(ref, pin, typ, comps)
        if role == ROLE_DC_UNKNOWN_LOAD:
            continue
        if role == ROLE_DC_HIGH_Z:
            continue
        if typ in {"resistor", "fuse"}:
            continue
        if ignore_bridges and typ in DIODE_TYPES | SWITCH_TYPES | {"inductor"}:
            continue
        if typ == "inductor":
            continue
        if ignore_oc and typ == "ic" and _is_oc_output_pin(ref, pin, comps):
            continue
        return False
    return True


def _is_digital_or_cmos_input_net(net: str, comps: dict[str, Any]) -> bool:
    if _schmitt_input_net(net, comps):
        return True
    for ref, _pin, typ in _net_attachments(net, comps):
        if typ != "ic":
            continue
        c = comps.get(ref) or {}
        mpn = str(c.get("mpn") or "")
        if re.match(r"^CD40|^74HC|^74HCT|^74LS|^SN74", mpn, re.I):
            return True
    return False


def _schmitt_input_net(net: str, comps: dict[str, Any]) -> bool:
    for ref, pin, typ in _net_attachments(net, comps):
        if typ != "ic":
            continue
        c = comps.get(ref) or {}
        mpn = str(c.get("mpn") or "")
        if re.match(r"^MC14584", mpn, re.I):
            if str(pin) in {"1", "3", "5", "9", "11", "13"}:
                return True
    return False


def _gaussian(a: list[list[float]], b: list[float]) -> list[float] | None:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-18:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        div = m[col][col]
        for j in range(col, n + 1):
            m[col][j] /= div
        for r in range(n):
            if r == col:
                continue
            factor = m[r][col]
            for j in range(col, n + 1):
                m[r][j] -= factor * m[col][j]
    return [m[i][n] for i in range(n)]


def _collect_edges(comps: dict[str, Any]) -> list[tuple[str, float, str, str]]:
    edges: list[tuple[str, float, str, str]] = []
    for ref, c in comps.items():
        if _type_of(ref, c) != "resistor":
            continue
        ohm = _parse_res_ohm(str(c.get("value") or ""))
        if ohm is None or ohm <= 0:
            continue
        uniq = _unique_pin_nets(c)
        if len(uniq) != 2:
            continue
        edges.append((ref, ohm, uniq[0], uniq[1]))
    return edges


def _vf_of(c: dict[str, Any]) -> float | None:
    p = _params(c)
    for k in ("vf_v", "vf", "v_f", "forward_v"):
        if p.get(k) is not None:
            try:
                return float(p[k])
            except (TypeError, ValueError):
                pass
    return None


def _vce_sat_of(c: dict[str, Any]) -> float | None:
    p = _params(c)
    for k in ("vce_sat_v", "vce_sat", "vds_on_v"):
        if p.get(k) is not None:
            try:
                return abs(float(p[k]))
            except (TypeError, ValueError):
                pass
    return None


def _vbe_of(c: dict[str, Any]) -> float | None:
    p = _params(c)
    for k in ("vbe_v", "vbe_sat_v", "vbe"):
        if p.get(k) is not None:
            try:
                return abs(float(p[k]))
            except (TypeError, ValueError):
                pass
    return None


def _num_v(x: float) -> str:
    from kernel.solution import num

    return num(x)


def _trace_origin(net: str, v: float, *, kind: str, cite: str) -> dict[str, Any]:
    return {
        "net": net,
        "v": float(v),
        "origin": net,
        "origin_v": float(v),
        "origin_kind": kind,
        "origin_cite": cite,
        "expr": f"V({net})",
        "subs": _num_v(v),
        "cite": cite,
        "drops": [],
    }


def _trace_from_parent(
    parent: dict[str, Any],
    child_net: str,
    *,
    sym: str,
    val: float,
    sign: int,
    cite: str,
    ref: str,
) -> dict[str, Any]:
    pv = float(parent.get("v") or 0.0)
    v = pv + int(sign) * float(val)
    drops = list(parent.get("drops") or []) + [
        {"sym": sym, "val": float(val), "sign": int(sign), "cite": cite, "ref": ref}
    ]
    origin = str(parent.get("origin") or parent.get("net") or "")
    origin_v = parent.get("origin_v")
    if origin_v is None:
        origin_v = pv
    expr = f"V({origin})"
    subs = _num_v(float(origin_v))
    for d in drops:
        if int(d.get("sign") or 0) < 0:
            expr += f" − {d.get('sym')}"
            subs += f" − {_num_v(float(d.get('val') or 0))}"
        else:
            expr += f" + {d.get('sym')}"
            subs += f" + {_num_v(float(d.get('val') or 0))}"
    return {
        "net": child_net,
        "v": v,
        "origin": origin,
        "origin_v": float(origin_v),
        "origin_kind": parent.get("origin_kind"),
        "origin_cite": parent.get("origin_cite") or parent.get("cite") or "",
        "expr": expr,
        "subs": subs,
        "cite": cite,
        "drops": drops,
        "parent_net": parent.get("net"),
    }


def _ensure_trace(entry: dict[str, Any], net: str) -> dict[str, Any]:
    t = entry.get("trace")
    if isinstance(t, dict) and t.get("v") is not None:
        return t
    v = float(entry.get("v") or 0.0)
    kind = str(entry.get("kind") or "")
    src = entry.get("source")
    cite = ""
    if isinstance(src, dict):
        cite = str(src.get("note") or src.get("kind") or "")
    elif src == "ground" or kind == "ground" or net in GROUND_NETS:
        cite = "地"
        kind = "ground"
    if kind == "closed_rail" and not cite:
        cite = "电源网名闭合"
    if kind == "ground" or net in GROUND_NETS:
        t = _trace_origin(net, 0.0, kind="ground", cite=cite or "地")
    elif kind == "closed_rail":
        t = _trace_origin(net, v, kind="closed_rail", cite=cite or "电源网名闭合")
    elif kind == "divider_solve":
        t = _trace_origin(net, v, kind="divider_solve", cite=cite or "DC 电阻网节点方程")
    else:
        t = _trace_origin(net, v, kind=kind or "forced", cite=cite or kind)
    entry["trace"] = t
    return t


def _bridge_drop_meta(br: dict[str, Any], comps: dict[str, Any]) -> tuple[str, str]:
    if br.get("drop_sym"):
        return str(br["drop_sym"]), str(br.get("drop_cite") or "")
    ref = str(br.get("ref") or "?")
    kind = str(br.get("kind") or "")
    c = comps.get(ref) or {}
    mpn = str(c.get("mpn") or "")
    p = _params(c)
    if kind in {"led", "diode"}:
        page = p.get("vf_page")
        if br.get("assumed_zero"):
            return f"Vf({ref})", f"{ref} 缺 Vf，上界按 0"
        cite = f"{mpn} 规格书第{page}页 正向电压典型值" if page else (f"{mpn} 抽出 Vf" if mpn else f"{ref} Vf")
        return f"Vf({ref})", cite
    if kind == "switch":
        page = p.get("vce_sat_page")
        typ = _type_of(ref, c)
        if typ in {"transistor", "bjt"}:
            sym = f"Vce_sat({ref})"
        else:
            sym = f"Vds_on({ref})"
        if br.get("assumed_zero"):
            return sym, f"{ref} 缺饱和压降，上界按 0"
        cite = f"{mpn} 规格书第{page}页" if page else (f"{mpn} 抽出饱和压降" if mpn else f"{ref} 饱和压降")
        return sym, cite
    if kind == "be_clamp":
        page = p.get("vbe_page")
        if br.get("assumed_zero"):
            return f"Vbe({ref})", f"{ref} 缺 Vbe，不准按 0 短路 BE"
        cite = f"{mpn} 规格书第{page}页 VBE" if page else (f"{mpn} 抽出 Vbe" if mpn else f"{ref} Vbe")
        return f"Vbe({ref})", cite
    if kind == "inductor":
        return f"V_L({ref})", "电感直流短接 0 V"
    return f"Vdrop({ref})", str(br.get("missing") or ref)


def _attach_bridge_meta(br: dict[str, Any], comps: dict[str, Any]) -> dict[str, Any]:
    sym, cite = _bridge_drop_meta(br, comps)
    br["drop_sym"] = sym
    br["drop_cite"] = cite
    return br


def _copy_traces(dst: dict[str, Any], src: dict[str, Any]) -> None:
    if src.get("va_trace") is not None:
        dst["va_trace"] = src.get("va_trace")
    if src.get("vb_trace") is not None:
        dst["vb_trace"] = src.get("vb_trace")


def _switch_pin_roles(c: dict[str, Any]) -> dict[str, str]:
    """Return role -> net for BJT/FET pins (c/e/b)."""
    pins = _pin_nets(c)
    by_pin = {p: n for p, n in pins if p and n}
    role: dict[str, str] = {}
    for p in c.get("pins") or []:
        if not isinstance(p, dict):
            continue
        pin = str(p.get("pin") or "")
        if "." in pin:
            pin = pin.split(".")[-1]
        name = str(p.get("name") or p.get("pin_name") or p.get("pin_role") or "").upper()
        net = str(p.get("net") or "")
        if not net:
            continue
        if name in {"C", "COLLECTOR", "D", "DRAIN"}:
            role["c"] = net
        elif name in {"E", "EMITTER", "S", "SOURCE"}:
            role["e"] = net
        elif name in {"B", "BASE", "G", "GATE"}:
            role["b"] = net
    if role.get("c") and role.get("e") and role.get("b"):
        return role
    # SOT-23-3 common: 1=B, 2=E, 3=C
    if "1" in by_pin and "2" in by_pin and "3" in by_pin:
        role.setdefault("b", by_pin["1"])
        role.setdefault("e", by_pin["2"])
        role.setdefault("c", by_pin["3"])
    return role


def _switch_ce_nets(ref: str, c: dict[str, Any]) -> tuple[str, str] | None:
    """Return (collector_or_drain, emitter_or_source) nets."""
    role = _switch_pin_roles(c)
    if role.get("c") and role.get("e"):
        return role["c"], role["e"]
    pins = _pin_nets(c)
    gnd = [n for _, n in pins if n in GROUND_NETS]
    others = [n for _, n in pins if n and n not in GROUND_NETS]
    if gnd and others:
        return others[-1], gnd[0]
    if len(others) >= 2:
        return others[-1], others[0]
    return None


def _switch_be_nets(ref: str, c: dict[str, Any]) -> tuple[str, str] | None:
    """Return (base_or_gate, emitter_or_source) for upper-bound base-resistor stress."""
    _ = ref
    role = _switch_pin_roles(c)
    if role.get("b") and role.get("e"):
        return role["b"], role["e"]
    return None


def _local_be_bridges(n_a: str, n_b: str, comps: dict[str, Any]) -> list[dict[str, Any]]:
    """BE clamp only when datasheet Vbe exists. Missing Vbe must not short B–E (that lowers P)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n in (n_a, n_b):
        if not n:
            continue
        for ref, _pin, typ in _net_attachments(n, comps):
            if typ not in {"transistor", "bjt"}:
                continue
            be = _switch_be_nets(ref, comps.get(ref) or {})
            if not be or be[0] != n:
                continue
            if ref in seen:
                continue
            vbe = _vbe_of(comps.get(ref) or {})
            if vbe is None:
                continue
            seen.add(ref)
            out.append(
                _attach_bridge_meta(
                    {
                        "ref": ref,
                        "kind": "be_clamp",
                        "net_a": be[0],
                        "net_b": be[1],
                        "vdrop": float(vbe),
                        "switchable": False,
                        "missing": None,
                        "assumed_zero": False,
                    },
                    comps,
                )
            )
    return out


def _rail_pull_to_zero_cases(
    n_a: str,
    n_b: str,
    pot: dict[str, dict[str, Any]],
    comps: dict[str, Any],
    ohm: float,
) -> list[dict[str, Any]] | None:
    """If one end is a rail and the other is digital/Schmitt/OC, emit 稳态+对端≈0."""
    pa, pb = pot.get(n_a), pot.get(n_b)
    if pa is None and pb is None:
        return None

    def is_soft(net: str) -> bool:
        return (
            _is_digital_or_cmos_input_net(net, comps)
            or _schmitt_input_net(net, comps)
            or _is_oc_pull_down_net(net, comps)
        )

    rail_net = other = None
    rail_v = None
    if pa is not None and pb is not None:
        va, vb = float(pa["v"]), float(pb["v"])
        if abs(va - vb) >= CASE_V_TOL:
            return None
        # Both equal: pick closed_rail side as rail if other is soft
        if pa.get("kind") == "closed_rail" and is_soft(n_b):
            rail_net, other, rail_v = n_a, n_b, va
        elif pb.get("kind") == "closed_rail" and is_soft(n_a):
            rail_net, other, rail_v = n_b, n_a, vb
        elif abs(va) > CASE_V_TOL and is_soft(n_b) and not is_soft(n_a):
            rail_net, other, rail_v = n_a, n_b, va
        elif abs(vb) > CASE_V_TOL and is_soft(n_a) and not is_soft(n_b):
            rail_net, other, rail_v = n_b, n_a, vb
        else:
            return None
    elif pa is not None and pb is None and is_soft(n_b):
        rail_net, other, rail_v = n_a, n_b, float(pa["v"])
    elif pb is not None and pa is None and is_soft(n_a):
        rail_net, other, rail_v = n_b, n_a, float(pb["v"])
    else:
        return None
    if rail_v is None or abs(rail_v) < CASE_V_TOL:
        return None
    v_hi = abs(float(rail_v))
    p_hi = (v_hi * v_hi) / ohm if ohm else 0.0
    rail_trace = None
    if rail_net and pot.get(rail_net):
        rail_trace = _ensure_trace(pot[rail_net], rail_net)
    gnd_trace = _trace_origin(other or "对端", 0.0, kind="ground", cite=f"{other}≈0 包络")
    hi = {
        "name_zh": f"{other}≈0/对端拉低",
        "v_across": v_hi,
        "p_w": p_hi,
        "va": v_hi if rail_net == n_a else 0.0,
        "vb": 0.0 if rail_net == n_a else v_hi,
        "note": f"{other}≈0：V={v_hi:.6g}V → P={p_hi:.6g}W（P_max包络）",
        "driver_mask": 1,
    }
    lo = {
        "name_zh": "常通/未拉低",
        "v_across": 0.0,
        "p_w": 0.0,
        "va": float(rail_v),
        "vb": float(rail_v),
        "note": "常通/未拉低：V≈0V → P≈0W",
        "driver_mask": 0,
    }
    if rail_net == n_a:
        hi["va_trace"] = rail_trace
        hi["vb_trace"] = gnd_trace
        lo["va_trace"] = rail_trace
        lo["vb_trace"] = rail_trace
    else:
        hi["va_trace"] = gnd_trace
        hi["vb_trace"] = rail_trace
        lo["va_trace"] = rail_trace
        lo["vb_trace"] = rail_trace
    return [hi, lo]


def _diode_pairs(
    ref: str, c: dict[str, Any], nets: dict[str, Any], known: dict[str, Any]
) -> list[tuple[str, str]]:
    """Anode→cathode net pairs for LED/diode (common-anode to rail supported)."""
    pins = [(p, n) for p, n in _pin_nets(c) if n]
    if len(pins) < 2:
        return []
    typ = _type_of(ref, c)
    if typ == "diode" and len(pins) == 2:
        return [(pins[0][1], pins[1][1])]
    rail_nets = []
    other_nets = []
    for _p, n in pins:
        if n in known or n in GROUND_NETS or _closed_rail_v(nets.get(n) or {}) is not None:
            rail_nets.append(n)
        else:
            other_nets.append(n)
    pairs: list[tuple[str, str]] = []
    if rail_nets and other_nets:
        anode = rail_nets[0]
        for cath in other_nets:
            if cath != anode:
                pairs.append((anode, cath))
        return pairs
    uniq: list[str] = []
    for _p, n in pins:
        if n not in uniq:
            uniq.append(n)
    if len(uniq) >= 2:
        pairs.append((uniq[0], uniq[1]))
    return pairs


def _net_is_closed_rail(net: str, nets: dict[str, Any], known: dict[str, Any]) -> bool:
    if not net:
        return False
    if net in GROUND_NETS or net in known:
        return True
    return _closed_rail_v(nets.get(net) or {}) is not None


def _series_rail_diode_pair(
    c: dict[str, Any], nets: dict[str, Any], known: dict[str, Any]
) -> tuple[str, str] | None:
    """(rail_net, other_net) when exactly one pin is a closed rail/GND. Clamp/TVS → None."""
    uniq = _unique_pin_nets(c)
    if len(uniq) != 2:
        return None
    a, b = uniq[0], uniq[1]
    ra, rb = _net_is_closed_rail(a, nets, known), _net_is_closed_rail(b, nets, known)
    if ra and not rb:
        return a, b
    if rb and not ra:
        return b, a
    return None


def _tripole_driver_diode_pairs(
    c: dict[str, Any], comps: dict[str, Any]
) -> list[tuple[str, str]]:
    """3-net diode: common↔each other pin when any pin is dc_driver. Never short the two signal pins."""
    uniq = _unique_pin_nets(c)
    if len(uniq) != 3:
        return []
    if not any(_net_has_dc_driver(n, comps) for n in uniq):
        return []
    pins = [(str(p), n) for p, n in _pin_nets(c) if n]
    hub: str | None = None
    by_pin = {p: n for p, n in pins}
    for key in ("3", "COM", "COMMON", "CC", "CA", "MID"):
        if key in by_pin:
            hub = by_pin[key]
            break
        for p, n in pins:
            if p.upper() == key:
                hub = n
                break
        if hub:
            break
    if hub is None:
        for n in uniq:
            if _net_has_dc_driver(n, comps):
                hub = n
                break
    if not hub:
        return []
    return [(hub, o) for o in uniq if o != hub]


def _unused_fanout_dc_hops(
    fanout: dict[str, Any],
    used_refs: set[str],
    comps: dict[str, Any],
    known: dict[str, Any],
    pin_nets: tuple[str, str] | None = None,
) -> list[str]:
    """Direct hops from a floating pin-net through 2-pin diode/LED/L to a closed rail.

    Ignores diodes already sitting on the resistor's rail pin (D2 on +24V) and 3-pin
    clamps. fanout is unused; pin_nets come from the resistor under test.
    """
    _ = fanout
    n_a, n_b = pin_nets or ("", "")
    out: list[str] = []
    seen: set[str] = set()
    for n in (n_a, n_b):
        if not n or _net_is_closed_rail(n, {}, known):
            continue
        for ref, _pin, typ in _net_attachments(n, comps):
            if ref in used_refs or ref in seen:
                continue
            if typ not in DIODE_TYPES | {"inductor"}:
                continue
            uniq = _unique_pin_nets(comps.get(ref) or {})
            if typ in DIODE_TYPES and len(uniq) != 2:
                continue
            rails = [x for x in uniq if x != n and _net_is_closed_rail(x, {}, known)]
            if not rails:
                continue
            seen.add(ref)
            out.append(f"{ref}→{rails[0]}")
    return out


def _be_zero_parallels_other_resistor(
    ref: str,
    n_a: str,
    n_b: str,
    be_bridges: list[dict[str, Any]],
    comps: dict[str, Any],
) -> str | None:
    """Vbe=0 ties base to emitter; another R from the far pin to emitter parallels this R."""
    pins = {n_a, n_b}
    for b in be_bridges:
        base = str(b.get("net_a") or "")
        emit = str(b.get("net_b") or "")
        if base not in pins:
            continue
        other = n_b if n_a == base else n_a
        if not other or other == emit:
            continue
        for r2, _pin, typ in _net_attachments(other, comps):
            if typ != "resistor" or r2 == ref:
                continue
            uniq = _unique_pin_nets(comps.get(r2) or {})
            if emit in uniq:
                qref = str(b.get("ref") or "?")
                return f"Vbe({qref})=0 把 {ref} 与 {r2} 并联，跨压被写成 0"
    return None


def _known_abs_v(known: dict[str, Any], net: str) -> float | None:
    row = known.get(net)
    if row is None:
        return None
    raw = row.get("v") if isinstance(row, dict) else row
    if raw is None:
        return None
    try:
        return abs(float(raw))
    except (TypeError, ValueError):
        return None


def _net_is_stiff_rail(net: str, nets: dict[str, Any], known: dict[str, Any]) -> bool:
    """Named closed rail or GND: cannot be assumed 0 in an upper-bound envelope."""
    if not net:
        return False
    if net in GROUND_NETS:
        return True
    if _closed_rail_v(nets.get(net) or {}) is not None:
        return True
    row = known.get(net)
    if isinstance(row, dict) and str(row.get("kind") or "") in {"closed_rail", "ground"}:
        return True
    return False


def _switch_base_hits(net: str, comps: dict[str, Any]) -> list[tuple[str, str, dict[str, str]]]:
    """(ref, type, pin roles) whose B/G is this net."""
    out: list[tuple[str, str, dict[str, str]]] = []
    if not net:
        return out
    for ref, _pin, typ in _net_attachments(net, comps):
        if typ not in SWITCH_TYPES:
            continue
        role = _switch_pin_roles(comps.get(ref) or {})
        if role.get("b") == net:
            out.append((ref, typ, role))
    return out


def _net_vmax(
    net: str,
    comps: dict[str, Any],
    nets: dict[str, Any],
    known: dict[str, Any],
    *,
    ignore_ref: str = "",
    _seen: frozenset[str] | None = None,
) -> float | None:
    """Conservative high voltage on a net: closed rail / CMOS VDD / hop via R·diode."""
    if not net:
        return None
    if net in GROUND_NETS:
        return 0.0
    seen = _seen or frozenset()
    if net in seen or len(seen) > 8:
        return None
    got = _known_abs_v(known, net)
    if got is not None:
        return got
    cv = _closed_rail_v(nets.get(net) or {})
    if cv is not None:
        return abs(float(cv))
    best: float | None = None
    cmos = _cmos_drive_on_net(net, comps, nets, known)
    if cmos and cmos.get("vdd_v") is not None:
        best = abs(float(cmos["vdd_v"]))
    for ref2, pin2, typ2 in _net_attachments(net, comps):
        if typ2 != "ic":
            continue
        c2 = comps.get(ref2) or {}
        mpn2 = str(c2.get("mpn") or c2.get("value") or "")
        ins: set[str] | None = None
        for pat, pins in _IC_INPUT_PINS:
            if pat.search(mpn2):
                ins = pins
                break
        if not ins or str(pin2) not in ins:
            continue
        vdd_net = _ic_vdd_net(ref2, c2)
        if not vdd_net:
            continue
        vv = _known_abs_v(known, vdd_net)
        if vv is None:
            rail_v = _closed_rail_v(nets.get(vdd_net) or {})
            vv = abs(float(rail_v)) if rail_v is not None else None
        if vv is not None:
            best = vv if best is None else max(best, vv)
    nxt = seen | {net}
    for r2, _pin, typ in _net_attachments(net, comps):
        if r2 == ignore_ref:
            continue
        if typ not in {"resistor"} | DIODE_TYPES:
            continue
        for o in _unique_pin_nets(comps.get(r2) or {}):
            if o == net:
                continue
            v = _net_vmax(o, comps, nets, known, ignore_ref=ignore_ref, _seen=nxt)
            if v is None:
                continue
            best = v if best is None else max(best, v)
    return best


def _pin_pair_envelope_cases(
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float | None,
    comps: dict[str, Any],
    nets: dict[str, Any],
    known: dict[str, Any],
    *,
    through_self: bool,
) -> tuple[list[dict[str, Any]], list[str]] | None:
    """Upper-bound |Va−Vb| from per-net vmax. Stiff rails stay high; other nets may be 0.

    through_self: the part itself is a DC path (resistor) so a dead-end pin may copy the
    other pin's vmax. Capacitor is DC-open → False.
    """
    va = _net_vmax(n_a, comps, nets, known, ignore_ref=ref)
    vb = _net_vmax(n_b, comps, nets, known, ignore_ref=ref)
    if through_self:
        if va is None and vb is not None:
            va = vb
        elif vb is None and va is not None:
            vb = va
    if va is None or vb is None:
        return None
    a_lo = va if _net_is_stiff_rail(n_a, nets, known) else 0.0
    b_lo = vb if _net_is_stiff_rail(n_b, nets, known) else 0.0
    v_env = max(abs(va - b_lo), abs(vb - a_lo), abs(va - vb), abs(a_lo - b_lo))
    qrefs: list[str] = []
    for q, _t, _r in _switch_base_hits(n_a, comps) + _switch_base_hits(n_b, comps):
        if q != ref and q not in qrefs:
            qrefs.append(q)
    qtxt = ",".join(qrefs) if qrefs else "软网"
    tag = "基极包络" if qrefs else "电位包络"
    cite = f"{tag} · {qtxt}：非电源网可到 0，跨压上界 {v_env:.6g} V"
    p_w = (v_env * v_env) / ohm if ohm else 0.0
    pairs = [(va, b_lo), (a_lo, vb), (va, vb), (a_lo, b_lo)]
    va_s, vb_s = max(pairs, key=lambda p: abs(p[0] - p[1]))
    origin = "base_drive" if qrefs else "pin_envelope"
    cases = [
        {
            "name_zh": f"{tag}低",
            "v_across": 0.0,
            "va": min(va, vb),
            "vb": min(va, vb),
            "p_w": 0.0,
            "note": f"{tag}低：跨压 0 · {qtxt}",
            "va_trace": _trace_origin(n_a, min(va, vb), kind=origin, cite=cite),
            "vb_trace": _trace_origin(n_b, min(va, vb), kind=origin, cite=cite),
        },
        {
            "name_zh": f"{tag}高",
            "v_across": v_env,
            "va": va_s,
            "vb": vb_s,
            "p_w": p_w,
            "note": f"{cite} → P={p_w:.6g}W" if ohm else cite,
            "va_trace": _trace_origin(n_a, va_s, kind=origin, cite=cite),
            "vb_trace": _trace_origin(n_b, vb_s, kind=origin, cite=cite),
        },
    ]
    return cases, qrefs


def _base_drive_envelope_cases(
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float,
    comps: dict[str, Any],
    nets: dict[str, Any],
    known: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]] | None:
    """P_max envelope for a resistor on BJT/MOS B/G."""
    hits = _switch_base_hits(n_a, comps) + _switch_base_hits(n_b, comps)
    hits = [(q, t, r) for q, t, r in hits if q != ref]
    if not hits:
        return None
    return _pin_pair_envelope_cases(
        ref, n_a, n_b, ohm, comps, nets, known, through_self=True
    )


FALSE_CLOSE_V = 0.2  # below real Vbe; catches D3+Vbe=0 residual millivolts


def _false_close_reason(
    *,
    ref: str,
    n_a: str,
    n_b: str,
    cases: list[dict[str, Any]],
    fanout: dict[str, Any],
    used_refs: set[str],
    be_bridges: list[dict[str, Any]],
    comps: dict[str, Any],
    known: dict[str, Any],
) -> str | None:
    if not cases:
        return None
    pin_nets = {n_a, n_b}
    for n in (n_a, n_b):
        if not n:
            continue
        for r2, _pin, typ in _net_attachments(n, comps):
            if r2 == ref or typ not in SWITCH_TYPES:
                continue
            ce = _switch_ce_nets(r2, comps.get(r2) or {})
            if not ce or (ce[0] not in pin_nets and ce[1] not in pin_nets):
                return f"类型不准入 · {r2}({typ})"
    live = [c for c in cases if isinstance(c, dict) and c.get("i_dc") != 0]
    if not live:
        return None
    try:
        vmax = max(float(c.get("v_across") or 0.0) for c in live)
    except (TypeError, ValueError):
        return None
    unused = _unused_fanout_dc_hops(fanout, used_refs, comps, known, pin_nets=(n_a, n_b))
    if unused and vmax < FALSE_CLOSE_V:
        return "路径有 " + "、".join(unused[:4]) + "，未列入方程"
    if vmax < FALSE_CLOSE_V:
        return _be_zero_parallels_other_resistor(ref, n_a, n_b, be_bridges, comps)
    return None


def _series_unknown_load_net(
    ref: str,
    n_a: str,
    n_b: str,
    comps: dict[str, Any],
    nets: dict[str, Any],
) -> str | None:
    """Pin-net that, without this resistor, is only dc_unknown_load (series to socket)."""
    for n in (n_a, n_b):
        if not n or n in GROUND_NETS:
            continue
        if _closed_rail_v(nets.get(n) or {}) is not None:
            continue
        roles: list[str] = []
        for r2, pin, typ in _net_attachments(n, comps):
            if r2 == ref:
                continue
            roles.append(_pin_role(r2, pin, typ, comps))
        if roles and all(r == ROLE_DC_UNKNOWN_LOAD for r in roles):
            return n
    return None


def resistor_series_to_unknown_load(
    ref: str, c: dict[str, Any], comps: dict[str, Any], nets: dict[str, Any]
) -> str | None:
    uniq = _unique_pin_nets(c)
    if len(uniq) != 2:
        return None
    return _series_unknown_load_net(ref, uniq[0], uniq[1], comps, nets)


def _states_from_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": c.get("name_zh"),
            "v_across": c.get("v_across"),
            "va": c.get("va"),
            "vb": c.get("vb"),
            "p_w": c.get("p_w"),
            "note": c.get("note"),
            "driver_mask": c.get("driver_mask"),
            "i_dc": c.get("i_dc"),
            "dc_open_cite": c.get("dc_open_cite"),
        }
        for c in cases
    ]


def _socket_envelope_named(cases: list[dict[str, Any]]) -> bool:
    names = " ".join(str(c.get("name_zh") or "") for c in cases)
    return "开路" in names and "对地" in names


def _series_socket_envelope_cases(
    n_a: str,
    n_b: str,
    series_net: str,
    ohm: float,
    samples: list[tuple[int, float, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """开路 P=0 and 对地 P=Vboard²/R. Board pin voltage must already be closed — never invent 24 V."""
    if not samples:
        return []
    board = n_b if series_net == n_a else n_a
    best = max(samples, key=lambda s: abs(s[1]))
    mask0, v_rep, tr_rep = best
    open_case: dict[str, Any] = {
        "name_zh": "开路/板外未接",
        "v_across": 0.0,
        "p_w": 0.0,
        "va": float(v_rep),
        "vb": float(v_rep),
        "note": "开路：I=0 → V=0 → P=0",
        "driver_mask": mask0,
        "i_dc": 0,
        "socket_state": "open",
    }
    if isinstance(tr_rep, dict):
        open_case["va_trace"] = dict(tr_rep)
        open_case["vb_trace"] = dict(tr_rep)
    gnd_trace = _trace_origin(series_net, 0.0, kind="ground", cite=f"{series_net} 对地包络")
    out = [open_case]
    seen: list[float] = []
    for mask, vboard, tr in sorted(samples, key=lambda s: -abs(s[1])):
        v = abs(float(vboard))
        if any(abs(v - x) < CASE_V_TOL for x in seen):
            continue
        seen.append(v)
        p = (v * v) / ohm if ohm else 0.0
        tag = f"{v:.6g}V" if v >= CASE_V_TOL else "0V"
        g: dict[str, Any] = {
            "name_zh": f"对地/板外接地（{tag}）",
            "v_across": v,
            "p_w": p,
            "note": f"对地：V=|{tag}| → P={p:.6g}W",
            "driver_mask": mask,
            "socket_state": "gnd",
        }
        if board == n_a:
            g["va"] = float(vboard)
            g["vb"] = 0.0
            if isinstance(tr, dict):
                g["va_trace"] = tr
            g["vb_trace"] = gnd_trace
        else:
            g["va"] = 0.0
            g["vb"] = float(vboard)
            g["va_trace"] = gnd_trace
            if isinstance(tr, dict):
                g["vb_trace"] = tr
        out.append(g)
    return out


def _board_v_samples_from_pot(
    board: str,
    pot: dict[str, dict[str, Any]] | None,
    known: dict[str, dict[str, Any]],
) -> list[tuple[int, float, dict[str, Any]]]:
    if not board:
        return []
    e = (pot or {}).get(board) or known.get(board)
    if not e or e.get("v") is None:
        return []
    return [(0, float(e["v"]), _ensure_trace(e, board))]


def _diode_hop_to_driver(n_a: str, n_b: str, comps: dict[str, Any]) -> list[str]:
    """Pin-net → one diode/LED → dc_driver (CMOS/opamp/CE)."""
    hits: list[str] = []
    seen: set[str] = set()
    for n in (n_a, n_b):
        if not n:
            continue
        for ref, _pin, typ in _net_attachments(n, comps):
            if typ not in DIODE_TYPES or ref in seen:
                continue
            for nn in _unique_pin_nets(comps.get(ref) or {}):
                if not nn or nn == n:
                    continue
                for r2, p2, t2 in _net_attachments(nn, comps):
                    if r2 == ref:
                        continue
                    if _pin_role(r2, p2, t2, comps) == ROLE_DC_DRIVER:
                        seen.add(ref)
                        hits.append(f"{ref}→{r2}.{p2}")
                        break
                if ref in seen:
                    break
    return hits


def _false_open_reason(
    *,
    ref: str,
    n_a: str,
    n_b: str,
    cases: list[dict[str, Any]],
    comps: dict[str, Any],
    nets: dict[str, Any],
    known: dict[str, Any],
    last_pot: dict[str, dict[str, Any]] | None = None,
) -> str | None:
    series = _series_unknown_load_net(ref, n_a, n_b, comps, nets)
    if series:
        board = n_b if series == n_a else n_a
        board_closed = bool(_board_v_samples_from_pot(board, last_pot, known))
        if not board_closed:
            for c in cases:
                v = c.get("va") if board == n_a else c.get("vb")
                if v is not None:
                    board_closed = True
                    break
        if board_closed and not _socket_envelope_named(cases):
            return f"{series} 板内脚已有电位，可开路/对地未写出"
        return None
    if cases:
        return None
    hops = _diode_hop_to_driver(n_a, n_b, comps)
    if hops:
        return "隔二极管已为驱动 " + "、".join(hops[:4]) + "，未列入方程"
    for n, other in ((n_a, n_b), (n_b, n_a)):
        if not _net_is_closed_rail(other, nets, known):
            continue
        has_load = False
        has_driver = False
        has_active = False
        for r2, pin, typ in _net_attachments(n, comps):
            if r2 == ref:
                continue
            role = _pin_role(r2, pin, typ, comps)
            if role == ROLE_DC_UNKNOWN_LOAD:
                has_load = True
            elif role == ROLE_DC_DRIVER:
                has_driver = True
            elif role == ROLE_DC_UNKNOWN_ACTIVE:
                has_active = True
        if has_load and has_driver and not has_active:
            return f"{n} 有连接器但未按板内驱动枚举"
    return None


def _apply_post_solve_gates(
    payload: dict[str, Any],
    *,
    ref: str,
    n_a: str,
    n_b: str,
    cases: list[dict[str, Any]],
    fanout: dict[str, Any],
    used_refs: set[str],
    be_bridges: list[dict[str, Any]],
    comps: dict[str, Any],
    nets: dict[str, Any],
    known: dict[str, Any],
    last_pot: dict[str, dict[str, Any]] | None = None,
    ohm: float | None = None,
) -> dict[str, Any]:
    series = _series_unknown_load_net(ref, n_a, n_b, comps, nets)
    if series:
        work = list(payload.get("cases") or cases or [])
        if not _socket_envelope_named(work):
            board = n_b if series == n_a else n_a
            samples: list[tuple[int, float, dict[str, Any]]] = []
            for c in work:
                raw = c.get("va") if board == n_a else c.get("vb")
                if raw is None:
                    continue
                tr = c.get("va_trace") if board == n_a else c.get("vb_trace")
                samples.append((int(c.get("driver_mask") or 0), float(raw), tr if isinstance(tr, dict) else {}))
            if not samples:
                samples = _board_v_samples_from_pot(board, last_pot, known)
            if samples and ohm:
                work = _series_socket_envelope_cases(n_a, n_b, series, float(ohm), samples)
                payload["cases"] = work
                payload["states"] = _states_from_cases(work)
                payload["drive_kind"] = "开路/对地"
                cases = work
            elif samples:
                gap = f"{series} 板内脚已有电位，可开路/对地未写出"
                payload["v"] = None
                payload["false_close"] = gap
                payload["block_class"] = "rule_gap"
                payload["note"] = gap
                payload["stress_kind"] = None
                payload["v_source"] = {"kind": "rule_gap", "note": gap, "net_a": n_a, "net_b": n_b}
                return payload
            else:
                note = f"{series} 去掉 {ref} 后仅连接器，板内脚电位未闭合"
                payload["v"] = None
                payload["cases"] = []
                payload["states"] = []
                payload["block_class"] = "topology_open"
                payload["note"] = note
                payload["stress_kind"] = None
                payload["v_source"] = {
                    "kind": "topology_open",
                    "note": note,
                    "net": series,
                    "net_a": n_a,
                    "net_b": n_b,
                }
                return payload
        else:
            cases = work
            payload["cases"] = work
            payload["states"] = _states_from_cases(work)
            payload["drive_kind"] = payload.get("drive_kind") or "开路/对地"
    gap = _false_close_reason(
        ref=ref,
        n_a=n_a,
        n_b=n_b,
        cases=cases,
        fanout=fanout,
        used_refs=used_refs,
        be_bridges=be_bridges,
        comps=comps,
        known=known,
    )
    work_cases = list(payload.get("cases") or cases or [])
    want_base = bool(ohm) and (
        _switch_base_hits(n_a, comps) or _switch_base_hits(n_b, comps)
    )
    if want_base and (
        (gap and str(gap).startswith("类型不准入")) or not work_cases
    ):
        env = _base_drive_envelope_cases(ref, n_a, n_b, float(ohm), comps, nets, known)
        if env:
            env_cases, qrefs = env
            payload["cases"] = env_cases
            payload["states"] = _states_from_cases(env_cases)
            payload["drive_kind"] = "基极"
            payload["note"] = f"基极包络取代 {gap}" if gap else "基极包络"
            payload["false_close"] = None
            payload["block_class"] = None
            payload["stress_kind"] = "p_max_envelope"
            payload["v"] = max(float(c.get("v_across") or 0.0) for c in env_cases)
            payload["v_source"] = {
                "kind": "base_drive",
                "note": payload["note"],
                "net_a": n_a,
                "net_b": n_b,
            }
            extra = [
                {
                    "kind": "base",
                    "id": f"base:{q}",
                    "ref": q,
                    "net": n_a if _switch_base_hits(n_a, comps) else n_b,
                }
                for q in qrefs
            ]
            payload["drivers"] = list(payload.get("drivers") or []) + extra
            return payload
    if gap:
        payload["v"] = None
        payload["false_close"] = gap
        payload["block_class"] = "rule_gap"
        payload["note"] = gap
        payload["stress_kind"] = None
        payload["v_source"] = {"kind": "rule_gap", "note": gap, "net_a": n_a, "net_b": n_b}
        return payload
    opened = _false_open_reason(
        ref=ref,
        n_a=n_a,
        n_b=n_b,
        cases=list(payload.get("cases") or cases or []),
        comps=comps,
        nets=nets,
        known=known,
        last_pot=last_pot,
    )
    if opened:
        payload["v"] = None
        payload["false_close"] = opened
        payload["block_class"] = "rule_gap"
        payload["note"] = opened
        payload["stress_kind"] = None
        payload["v_source"] = {"kind": "rule_gap", "note": opened, "net_a": n_a, "net_b": n_b}
    return payload


def _collect_bridges(
    comps: dict[str, Any], nets: dict[str, Any], known: dict[str, Any]
) -> list[dict[str, Any]]:
    """LED/diode/switch/inductor bridges for N-hop DC."""
    bridges: list[dict[str, Any]] = []
    for ref, c in comps.items():
        typ = _type_of(ref, c)
        if typ == "inductor":
            pins = [n for _, n in _pin_nets(c) if n]
            if len(pins) >= 2 and pins[0] != pins[1]:
                bridges.append(
                    _attach_bridge_meta(
                        {
                            "ref": ref,
                            "kind": "inductor",
                            "net_a": pins[0],
                            "net_b": pins[1],
                            "vdrop": 0.0,
                            "switchable": False,
                            "missing": None,
                            "assumed_zero": False,
                        },
                        comps,
                    )
                )
            continue
        if typ == "led":
            vf = _vf_of(c)
            missing = None if vf is not None else f"{ref} 缺 Vf"
            for a, b in _diode_pairs(ref, c, nets, known):
                bridges.append(
                    _attach_bridge_meta(
                        {
                            "ref": ref,
                            "kind": "led",
                            "net_a": a,
                            "net_b": b,
                            "vdrop": float(vf) if vf is not None else 0.0,
                            "switchable": False,
                            "missing": missing,
                            "assumed_zero": vf is None,
                        },
                        comps,
                    )
                )
            continue
        if typ == "diode":
            vf = _vf_of(c)
            missing = None if vf is not None else f"{ref} 缺 Vf"
            pair = _series_rail_diode_pair(c, nets, known)
            pairs: list[tuple[str, str]] = []
            if pair:
                pairs.append(pair)
            else:
                pairs.extend(_tripole_driver_diode_pairs(c, comps))
            for a, b in pairs:
                bridges.append(
                    _attach_bridge_meta(
                        {
                            "ref": ref,
                            "kind": "diode",
                            "net_a": a,
                            "net_b": b,
                            "vdrop": float(vf) if vf is not None else 0.0,
                            "switchable": False,
                            "missing": missing,
                            "assumed_zero": vf is None,
                        },
                        comps,
                    )
                )
            continue
        if typ in SWITCH_TYPES:
            ce = _switch_ce_nets(ref, c)
            if not ce:
                continue
            c_net, e_net = ce
            vsat = _vce_sat_of(c)
            miss = None
            if vsat is None:
                miss = (
                    f"{ref} 缺 Vce_sat"
                    if typ in {"transistor", "bjt"}
                    else f"{ref} 缺 Rds_on/Vds_on"
                )
            bridges.append(
                _attach_bridge_meta(
                    {
                        "ref": ref,
                        "kind": "switch",
                        "net_a": c_net,
                        "net_b": e_net,
                        "vdrop": float(vsat) if vsat is not None else 0.0,
                        "switchable": True,
                        "missing": miss,
                        "assumed_zero": vsat is None,
                    },
                    comps,
                )
            )
    return bridges


def _adjacency_for_fanout(
    comps: dict[str, Any], nets: dict[str, Any], known: dict[str, Any]
) -> dict[str, list[tuple[str, str, str]]]:
    """net -> list of (neighbor_net, via_ref, via_kind).

    For display N-hop: fully connect all pins of LED/diode/switch so base-side
    nets (e.g. OUT_N via R8→Q2.B) appear. Solve still only shorts CE when ON.
    """
    adj: dict[str, list[tuple[str, str, str]]] = {}

    def link(a: str, b: str, ref: str, kind: str) -> None:
        if not a or not b or a == b:
            return
        adj.setdefault(a, []).append((b, ref, kind))
        adj.setdefault(b, []).append((a, ref, kind))

    def star(pin_nets: list[str], ref: str, kind: str) -> None:
        uniq = []
        for n in pin_nets:
            if n and n not in uniq:
                uniq.append(n)
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                link(uniq[i], uniq[j], ref, kind)

    for ref, ohm, a, b in _collect_edges(comps):
        link(a, b, ref, "resistor")
    for ref, c in comps.items():
        typ = _type_of(ref, c)
        pins = [n for _, n in _pin_nets(c) if n]
        if typ in DIODE_TYPES | SWITCH_TYPES:
            star(pins, ref, typ if typ in DIODE_TYPES else "switch")
        elif typ == "inductor" and len(pins) >= 2:
            link(pins[0], pins[1], ref, "inductor")
    return adj


def build_resistor_fanout(
    ref: str,
    kg: dict[str, Any],
    *,
    max_hops: int = FANOUT_MAX_HOPS,
    max_devs: int = FANOUT_MAX_DEVS,
) -> dict[str, Any]:
    """Bounded N-hop connectivity from a resistor's two nets."""
    comps = kg.get("components") or {}
    nets = kg.get("nets") or {}
    known = _known_dc(nets, comps)
    c = comps.get(ref) or {}
    pins = _pin_nets(c)
    n_a = pins[0][1] if pins else ""
    n_b = pins[1][1] if len(pins) > 1 else ""
    empty = {
        "hops": 0,
        "device_n": 0,
        "paths": [],
        "closed_rails": [],
        "open_ends": [],
        "missing_fields": [],
        "devices": [],
        "nets": [x for x in (n_a, n_b) if x],
    }
    if not n_a and not n_b:
        return empty

    adj = _adjacency_for_fanout(comps, nets, known)
    bridges = _collect_bridges(comps, nets, known)
    miss_by_ref = {b["ref"]: b["missing"] for b in bridges if b.get("missing")}

    start = [n for n in (n_a, n_b) if n]
    visited_net: set[str] = set(start)
    devices: list[str] = []
    paths: list[str] = []
    closed_rails: list[str] = []
    open_ends: list[str] = []
    missing: list[str] = []
    max_hop_seen = 0

    for sn in start:
        if sn in known:
            lab = f"{sn}={known[sn]['v']}V"
            if lab not in closed_rails:
                closed_rails.append(lab)
        elif sn in GROUND_NETS and "GND=0" not in closed_rails:
            closed_rails.append("GND=0")

    q: deque[tuple[str, int, list[str]]] = deque()
    for sn in start:
        q.append((sn, 0, [sn]))

    while q:
        net, hops, path = q.popleft()
        max_hop_seen = max(max_hop_seen, hops)
        is_closed = net in known or net in GROUND_NETS
        if is_closed and hops > 0:
            lab = f"{net}={known[net]['v']}V" if net in known else f"{net}=0"
            if lab not in closed_rails:
                closed_rails.append(lab)
            paths.append(" → ".join(path))
            continue
        if hops >= max_hops:
            if net not in open_ends and not is_closed:
                open_ends.append(net)
            continue
        neighbors = adj.get(net) or []
        if not neighbors and not is_closed:
            if net not in open_ends:
                open_ends.append(net)
            continue
        for nxt, via, kind in neighbors:
            if len(devices) >= max_devs and via not in devices and via != ref:
                continue
            if via not in devices and via != ref:
                devices.append(via)
                if via in miss_by_ref and miss_by_ref[via] not in missing:
                    missing.append(miss_by_ref[via])
            new_path = path + [via, nxt]
            if nxt in visited_net and nxt not in known and nxt not in GROUND_NETS:
                if hops + 1 <= max_hops and len(paths) < 12:
                    paths.append(" → ".join(new_path))
                continue
            if nxt not in visited_net:
                visited_net.add(nxt)
            q.append((nxt, hops + 1, new_path))

    uniq_paths: list[str] = []
    for p in paths:
        if p not in uniq_paths:
            uniq_paths.append(p)
        if len(uniq_paths) >= 12:
            break

    for n in visited_net:
        if n in known:
            lab = f"{n}={known[n]['v']}V"
            if lab not in closed_rails:
                closed_rails.append(lab)
        elif n in GROUND_NETS and "GND=0" not in closed_rails:
            closed_rails.append("GND=0")

    # Unclosed visited nets that are not on a closed path
    for n in sorted(visited_net):
        if n in known or n in GROUND_NETS:
            continue
        if n not in open_ends:
            # keep signal endpoints useful for display (OUT_N etc.)
            atts = _net_attachments(n, comps)
            if any(t in SWITCH_TYPES | DIODE_TYPES | {"ic", "connector"} for _, _, t in atts):
                open_ends.append(n)

    return {
        "hops": max_hop_seen,
        "device_n": len(devices),
        "paths": uniq_paths,
        "closed_rails": closed_rails,
        "open_ends": open_ends[:20],
        "missing_fields": missing,
        "devices": devices,
        "nets": sorted(visited_net),
    }


def _base_known(nets: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, nd in nets.items():
        if name in GROUND_NETS:
            rec = {"v": 0.0, "source": "ground", "kind": "ground"}
            rec["trace"] = _trace_origin(name, 0.0, kind="ground", cite="地")
            result[name] = rec
            continue
        cv = _closed_rail_v(nd)
        if cv is not None:
            rail = nd.get("rail") or {}
            src = rail.get("source")
            cite = "电源网名闭合"
            if isinstance(src, dict) and src.get("kind") not in {"netname_voltage", "netname"}:
                if src.get("note"):
                    cite = str(src.get("note"))
            rec = {
                "v": cv,
                "source": src,
                "kind": "closed_rail",
            }
            rec["trace"] = _trace_origin(name, cv, kind="closed_rail", cite=cite)
            if isinstance(src, dict) and src.get("kind") == "zener_clamp":
                rec["trace"]["calc"] = _zener_calc(name, float(cv), src)
            result[name] = rec
    if "GND" not in result:
        rec = {"v": 0.0, "source": "ground", "kind": "ground"}
        rec["trace"] = _trace_origin("GND", 0.0, kind="ground", cite="地")
        result["GND"] = rec
    return result


def _apply_follower_knowns(
    known: dict[str, dict[str, Any]],
    comps: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """OUT net == same-channel IN− → V(OUT)=V(IN+) when IN+ already known. No CMOS."""
    result = {k: dict(v) for k, v in known.items()}
    for _ in range(8):
        changed = False
        for ref, c in comps.items():
            if _type_of(ref, c) != "ic" or _comp_dnp(c):
                continue
            ch = _opamp_channels(str(c.get("mpn") or c.get("value") or ""))
            if not ch:
                continue
            for out_pin in ch:
                info = follower_channel_on_ic(c, out_pin)
                if not info:
                    continue
                plus = info["in_plus_net"]
                out_net = info["out_net"]
                if plus not in result or result[plus].get("v") is None:
                    continue
                if out_net in result and result[out_net].get("v") is not None:
                    continue
                pv = float(result[plus]["v"])
                cite = f"跟随器抄 IN+ {plus}（{ref}.{out_pin}={ref}.{info['in_minus']}）"
                parent = _ensure_trace(result[plus], plus)
                rec = {
                    "v": pv,
                    "source": {
                        "kind": "follower",
                        "ref": ref,
                        "out_pin": out_pin,
                        "in_plus_net": plus,
                        "note": cite,
                    },
                    "kind": "follower",
                    "trace": _trace_origin(out_net, pv, kind="follower", cite=cite),
                }
                plus_cite = str(parent.get("cite") or parent.get("origin_cite") or "")
                plus_known: dict[str, Any] = {
                    "sym": f"V({plus})",
                    "val": f"{_num_v(pv)} V",
                    "cite": plus_cite,
                }
                if isinstance(parent.get("calc"), dict):
                    plus_known["calc"] = parent["calc"]
                rec["trace"]["calc"] = _pack_calc(
                    kind="follower",
                    eq=f"V({out_net})=V({plus})",
                    subs=f"{_num_v(pv)}={_num_v(pv)}",
                    result=f"{_num_v(pv)} V",
                    knowns=[plus_known],
                    steps=[
                        f"{ref}.{out_pin} 与 {ref}.{info['in_minus']} 同网 {out_net}",
                        f"V({out_net})=V({plus})={_num_v(pv)} V",
                        plus_cite,
                    ],
                )
                result[out_net] = rec
                changed = True
        if not changed:
            break
    return result


def _known_dc(nets: dict[str, Any], comps: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return _apply_follower_knowns(_base_known(nets), comps)


def _apply_vdrop_forced(
    known: dict[str, dict[str, Any]],
    bridges_on: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Propagate voltage drops from known rails across ON bridges (fixed-point)."""
    result = {k: dict(v) for k, v in known.items()}
    changed = True
    guard = 0
    while changed and guard < 32:
        guard += 1
        changed = False
        for br in bridges_on:
            a, b = br["net_a"], br["net_b"]
            vd = float(br.get("vdrop") or 0.0)
            sym = str(br.get("drop_sym") or f"Vdrop({br.get('ref')})")
            cite = str(br.get("drop_cite") or "")
            ref = str(br.get("ref") or "")
            # V(a) ≈ V(b) + vd  (LED anode = cathode + Vf; switch C = E + Vsat)
            if a in result and b not in result:
                parent = _ensure_trace(result[a], a)
                if abs(vd) < 1e-15:
                    child_t = dict(parent)
                    child_t["net"] = b
                else:
                    child_t = _trace_from_parent(
                        parent, b, sym=sym, val=vd, sign=-1, cite=cite, ref=ref
                    )
                result[b] = {
                    "v": float(child_t["v"]),
                    "source": {
                        "kind": "bridge_vdrop",
                        "ref": br["ref"],
                        "note": f"{br['ref']} Vdrop={vd}V → {b}",
                    },
                    "kind": "bridge_vdrop",
                    "trace": child_t,
                }
                changed = True
            elif b in result and a not in result:
                parent = _ensure_trace(result[b], b)
                if abs(vd) < 1e-15:
                    child_t = dict(parent)
                    child_t["net"] = a
                else:
                    child_t = _trace_from_parent(
                        parent, a, sym=sym, val=vd, sign=1, cite=cite, ref=ref
                    )
                result[a] = {
                    "v": float(child_t["v"]),
                    "source": {
                        "kind": "bridge_vdrop",
                        "ref": br["ref"],
                        "note": f"{br['ref']} Vdrop={vd}V → {a}",
                    },
                    "kind": "bridge_vdrop",
                    "trace": child_t,
                }
                changed = True
    return result


_SHORT_R = SHORT_OHM * 10  # ≤10 mΩ 视为短接，不列入分压臂
_NODAL_MEMO: dict[tuple, dict[str, dict[str, Any]]] = {}


def _nodal_memo_clear() -> None:
    _NODAL_MEMO.clear()


def _ohm_txt(r: float) -> str:
    from kernel.solution import fmt_ohm

    return fmt_ohm(r)


def _volt_txt(v: float | None) -> str:
    from kernel.solution import fmt_volt

    return fmt_volt(v)


def _r_sym(ref: str) -> str:
    return f"R({ref})"


def _is_short_r(r: float) -> bool:
    return float(r) <= _SHORT_R


def _plus_expr(parts: list[str]) -> str:
    if not parts:
        return "0"
    if len(parts) == 1:
        return parts[0]
    return "(" + "+".join(parts) + ")"


def _known_v_sym(net: str, rec: dict[str, Any] | None) -> tuple[str, float, str]:
    """Return (V(origin_or_net), voltage, cite) for a known node."""
    rec = rec if isinstance(rec, dict) else {}
    t = rec.get("trace") if isinstance(rec.get("trace"), dict) else {}
    origin = str(t.get("origin") or net)
    cite = str(t.get("origin_cite") or t.get("cite") or "")
    if not cite:
        src = rec.get("source")
        if isinstance(src, dict):
            cite = str(src.get("note") or src.get("kind") or "")
        elif isinstance(src, str) and src not in {"none", ""}:
            cite = src
    v = t.get("origin_v")
    if v is None:
        v = rec.get("v")
    try:
        fv = float(v)
    except (TypeError, ValueError):
        fv = 0.0
    if t.get("origin_kind") in {"ground", "cmos_low", "oc"} and origin in GROUND_NETS:
        cite = cite or "地"
    elif t.get("origin_kind") == "closed_rail":
        cite = cite or "电源网名闭合"
    return f"V({origin})", fv, cite


def _res_cite(ref: str, comps: dict[str, Any], r: float) -> str:
    c = comps.get(ref) or {}
    raw = str(c.get("value") or "").strip()
    if raw:
        return f"图纸/BOM value={raw}"
    return f"图纸/BOM {_ohm_txt(r)}"


def _short_note(ref: str, a: str, b: str, comps: dict[str, Any]) -> str:
    c = comps.get(ref) or {}
    typ = _type_of(ref, c)
    if typ in DIODE_TYPES:
        if _vf_of(c) is None:
            return f"{ref} 将 {a} 与 {b} 直流短接（缺 Vf，上界按 0）"
        return f"{ref} 将 {a} 与 {b} 直流短接"
    if typ in SWITCH_TYPES:
        if _vce_sat_of(c) is None:
            return f"{ref} 导通将 {a} 与 {b} 直流短接（缺饱和压降，上界按 0）"
        return f"{ref} 导通将 {a} 与 {b} 直流短接"
    if typ == "inductor":
        return f"{ref} 电感直流短接 {a} 与 {b}"
    return f"{ref} 将 {a} 与 {b} 直流短接"


def _zener_calc(net: str, vz: float, src: dict[str, Any]) -> dict[str, Any]:
    """Cathode-to-ground shunt: V(net) = Vz_max. Numbers stay on the rail source."""
    ref = str(src.get("ref") or "")
    mpn = str(src.get("mpn") or "")
    anode = str(src.get("anode") or "")
    who = ref or mpn or "稳压管"
    steps: list[str] = []
    if ref and anode:
        steps.append(f"{ref} 阳极接 {anode}，阴极接 {net}")
    nom = src.get("vz_nom_v")
    vmin = src.get("vz_min_v")
    if mpn and nom is not None and vmin is not None:
        steps.append(
            f"{mpn} 标称 {float(nom):g} V，范围 {float(vmin):g}–{float(vz):g} V，取上限"
        )
    steps.append(f"V({net}) = Vz_max({who}) = {_num_v(vz)} V")
    return _pack_calc(
        kind="zener",
        eq=f"V({net})=Vz_max({who})",
        subs=_num_v(vz),
        result=f"{_num_v(vz)} V",
        knowns=[],
        steps=steps,
    )


def _pack_calc(
    *,
    kind: str,
    eq: str,
    subs: str,
    result: str,
    knowns: list[dict[str, str]],
    steps: list[str],
) -> dict[str, Any]:
    seen: set[tuple[str, str]] = set()
    uniq: list[dict[str, str]] = []
    for k in knowns:
        if not isinstance(k, dict):
            continue
        key = (str(k.get("sym") or ""), str(k.get("val") or ""))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        item: dict[str, Any] = {"sym": key[0], "val": key[1], "cite": str(k.get("cite") or "")}
        if isinstance(k.get("calc"), dict):
            item["calc"] = k["calc"]
        uniq.append(item)
    return {
        "kind": kind,
        "eq": eq,
        "subs": subs,
        "result": result,
        "knowns": uniq,
        "steps": [str(s) for s in steps if s],
    }


def _nodal_calc_packs(
    unknown: list[str],
    sol: list[float],
    known_result: dict[str, dict[str, Any]],
    sub: list[tuple[str, float, str, str]],
    comps: dict[str, Any],
    members: list[str],
) -> dict[str, dict[str, Any]]:
    """Per-unknown reuse pack: knowns + 分压/节点方程 + 代入 + 结果。不改求解数值。"""
    memset = set(members)
    solved = {n: float(v) for n, v in zip(unknown, sol)}
    uf = {n: n for n in members}

    def find(x: str) -> str:
        while uf[x] != x:
            uf[x] = uf[uf[x]]
            x = uf[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            uf[rb] = ra

    shorts: list[tuple[str, str, str]] = []
    res_edges: list[tuple[str, float, str, str]] = []
    for ref, r, a, b in sub:
        if a not in memset or b not in memset:
            continue
        if _is_short_r(float(r)):
            shorts.append((ref, a, b))
            if a in known_result and b in known_result:
                continue
            union(a, b)
        else:
            res_edges.append((ref, float(r), a, b))

    clusters: dict[str, list[str]] = {}
    for n in members:
        clusters.setdefault(find(n), []).append(n)

    def super_of(n: str) -> str:
        return find(n)

    def is_known_super(root: str) -> bool:
        return any(n in known_result for n in clusters.get(root, []))

    def known_pick(root: str) -> tuple[str, float, str]:
        nets = clusters.get(root) or []
        for n in nets:
            if n in known_result:
                return _known_v_sym(n, known_result[n])
        n0 = nets[0] if nets else root
        return f"V({n0})", 0.0, ""

    adj: dict[str, list[tuple[str, str, float]]] = {find(n): [] for n in members}
    for ref, r, a, b in res_edges:
        ra, rb = super_of(a), super_of(b)
        if ra == rb:
            continue
        adj.setdefault(ra, []).append((rb, ref, r))
        adj.setdefault(rb, []).append((ra, ref, r))

    def local_series(root: str) -> list[tuple[str, str | None, float | None]] | None:
        nbs = adj.get(root) or []
        if len(nbs) != 2:
            return None

        def walk(prev: str, cur: str, ref: str, r: float) -> tuple[list[tuple[str, str, float]], bool]:
            seq: list[tuple[str, str, float]] = [(cur, ref, r)]
            p, c = prev, cur
            for _ in range(32):
                if is_known_super(c):
                    return seq, True
                others = [(nbr, rf, rr) for nbr, rf, rr in (adj.get(c) or []) if nbr != p]
                if len(others) != 1:
                    return seq, False
                p = c
                c, rf, rr = others[0]
                seq.append((c, rf, rr))
            return seq, False

        (n0, r0, ohm0), (n1, r1, ohm1) = nbs[0], nbs[1]
        left, ok_l = walk(root, n0, r0, ohm0)
        right, ok_r = walk(root, n1, r1, ohm1)
        if not (ok_l and ok_r):
            return None
        left_k, right_k = left[-1][0], right[-1][0]
        if left_k == right_k:
            return None
        if known_pick(left_k)[1] < known_pick(right_k)[1]:
            left, right = right, left
        hi_side = list(reversed(left))
        path_supers = [s for s, _, _ in hi_side] + [root] + [s for s, _, _ in right]
        walk2: list[tuple[str, str | None, float | None]] = [(path_supers[0], None, None)]
        for i in range(1, len(path_supers)):
            prev_s, cur_s = path_supers[i - 1], path_supers[i]
            edge = next(((rf, rr) for nbr, rf, rr in (adj.get(cur_s) or []) if nbr == prev_s), None)
            if edge is None:
                edge = next(((rf, rr) for nbr, rf, rr in (adj.get(prev_s) or []) if nbr == cur_s), None)
            if edge is None:
                return None
            walk2.append((cur_s, edge[0], edge[1]))
        return walk2

    def shorts_on(nets: set[str]) -> list[str]:
        out: list[str] = []
        for ref, a, b in shorts:
            if a in nets or b in nets:
                out.append(_short_note(ref, a, b, comps))
        return out

    def knowns_from(pairs: list[tuple[str, float, str]], rlist: list[tuple[str, float]]) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for sym, v, cite in pairs:
            rows.append({"sym": sym, "val": _volt_txt(v), "cite": cite})
        for ref, r in rlist:
            rows.append({"sym": _r_sym(ref), "val": _ohm_txt(r), "cite": _res_cite(ref, comps, r)})
        return rows

    def pack_divider(n: str, v: float, series_path: list[tuple[str, str | None, float | None]]) -> dict[str, Any]:
        hi_s = series_path[0][0]
        lo_s = series_path[-1][0]
        hi_sym, hi_v, hi_cite = known_pick(hi_s)
        lo_sym, lo_v, lo_cite = known_pick(lo_s)
        segs: list[tuple[str, float]] = []
        for s, ref, r in series_path[1:]:
            if ref is None or r is None:
                continue
            segs.append((ref, float(r)))
        prefix: list[list[tuple[str, float]]] = []
        acc: list[tuple[str, float]] = []
        for i, (s, ref, r) in enumerate(series_path):
            if i == 0:
                continue
            if ref is not None and r is not None:
                acc.append((ref, float(r)))
            prefix.append(list(acc))
        root = super_of(n)
        idx = next((i for i, (s, _, _) in enumerate(series_path) if s == root), None)
        if idx is None or idx < 1:
            idx = 1
        above = prefix[idx - 1]
        below = segs[idx:]
        result_s = _volt_txt(v)
        hs, hv, ls, lv = hi_sym, hi_v, lo_sym, lo_v
        lo_nets = set(clusters.get(lo_s) or [lo_s])
        loc_shorts = shorts_on(lo_nets)
        series_knowns = knowns_from([(hi_sym, hi_v, hi_cite), (lo_sym, lo_v, lo_cite)], segs)
        chain_bits: list[str] = []
        for i, (s, ref, r) in enumerate(series_path):
            if i == 0:
                chain_bits.append(hi_sym[2:-1] if hi_sym.startswith("V(") else s)
            else:
                if ref:
                    chain_bits.append(str(ref))
                if is_known_super(s):
                    label = lo_sym[2:-1] if lo_sym.startswith("V(") else s
                else:
                    nets = clusters.get(s) or [s]
                    label = n if n in nets else (nets[0] if len(nets) == 1 else "/".join(nets[:2]))
                chain_bits.append(label)
        chain_line = "链：" + " — ".join(chain_bits)
        rvals = [x for _, x in segs]
        equal = bool(rvals) and all(abs(x - rvals[0]) < 1e-9 for x in rvals)
        nseg = len(segs)
        k_hi = len(above)
        if equal and nseg > 0:
            r0 = rvals[0]
            eq = f"V({n}) = {hs} − {k_hi}/{nseg} × ({hs} − {ls})"
            drop = (k_hi / nseg) * (hv - lv)
            subs = f"{_num_v(hv)} − {k_hi}/{nseg} × ({_num_v(hv)} − {_num_v(lv)})"
            steps = list(loc_shorts)
            steps.append(chain_line)
            steps.append(f"各臂电阻相等 {_ohm_txt(r0)}，共 {nseg} 段")
            steps.append(eq)
            steps.append(f"代入：{subs}")
            steps.append(f"      = {_num_v(hv)} − {_num_v(drop)}")
            steps.append(f"      = {result_s}")
            return _pack_calc(kind="divider", eq=eq, subs=subs, result=result_s, knowns=series_knowns, steps=steps)
        a_syms = [_r_sym(ref) for ref, _ in below] or ["0"]
        b_syms = [_r_sym(ref) for ref, _ in above] or ["0"]
        a_nums = [_num_v(x) for _, x in below] or ["0"]
        b_nums = [_num_v(x) for _, x in above] or ["0"]
        all_syms = [_r_sym(ref) for ref, _ in segs]
        all_nums = [_num_v(x) for _, x in segs]
        lo_sum = sum(x for _, x in below)
        hi_sum = sum(x for _, x in above)
        tot = sum(x for _, x in segs) or 1.0
        eq = (
            f"V({n}) = [{hs}·{_plus_expr(a_syms)} + {ls}·{_plus_expr(b_syms)}]"
            f" / {_plus_expr(all_syms)}"
        )
        subs = (
            f"[{_num_v(hv)}·{_plus_expr(a_nums)} + {_num_v(lv)}·{_plus_expr(b_nums)}]"
            f" / {_plus_expr(all_nums)}"
        )
        nume = hv * lo_sum + lv * hi_sum
        steps = list(loc_shorts)
        steps.append(chain_line)
        steps.append(eq)
        steps.append(f"代入：{subs}")
        steps.append(f"      = {_num_v(nume)} / {_num_v(tot)}")
        steps.append(f"      = {result_s}")
        return _pack_calc(kind="divider", eq=eq, subs=subs, result=result_s, knowns=series_knowns, steps=steps)

    packs: dict[str, dict[str, Any]] = {}

    # remaining unknowns: local series / short-to-known / star / 本节点 KCL
    for n, v in zip(unknown, sol):
        if n in packs:
            continue
        result_s = _volt_txt(v)
        root = super_of(n)
        if not is_known_super(root):
            sp = local_series(root)
            if sp and len(sp) >= 3:
                packs[n] = pack_divider(n, float(v), sp)
                continue
        if is_known_super(root):
            sym, kv, cite = known_pick(root)
            notes = shorts_on(set(clusters.get(root) or [root]))
            eq = f"V({n}) = {sym}"
            packs[n] = _pack_calc(
                kind="short",
                eq=eq,
                subs=_num_v(kv),
                result=result_s,
                knowns=[{"sym": sym, "val": _volt_txt(kv), "cite": cite}],
                steps=notes + [eq, f"代入：{sym} = {_volt_txt(kv)}", f"V({n}) = {result_s}"],
            )
            continue

        loc_nets: set[str] = set(clusters.get(root) or [root])
        for nbr, _ref, _r in adj.get(root) or []:
            loc_nets.update(clusters.get(nbr) or [nbr])
        loc_shorts = shorts_on(loc_nets)

        # incident resistor edges of this supernode
        inc: list[tuple[str, str, float, bool]] = []  # ref, nbr_label, r, nbr_known
        known_pairs: list[tuple[str, float, str]] = []
        rlist: list[tuple[str, float]] = []
        for nbr, ref, r in adj.get(root) or []:
            rlist.append((ref, r))
            if is_known_super(nbr):
                sym, kv, cite = known_pick(nbr)
                known_pairs.append((sym, kv, cite))
                inc.append((ref, sym, r, True))
            else:
                nets = clusters.get(nbr) or [nbr]
                label = nets[0]
                for cand in nets:
                    if cand in solved:
                        label = cand
                        break
                inc.append((ref, f"V({label})", r, False))

        all_nbr_known = bool(inc) and all(k for _ref, _lb, _r, k in inc)
        if all_nbr_known and inc:
            nume_s = "+".join(f"{lb}/{_r_sym(ref)}" for ref, lb, r, _k in inc)
            den_s = "+".join(f"1/{_r_sym(ref)}" for ref, _lb, r, _k in inc)
            eq = f"V({n}) = ({nume_s}) / ({den_s})"
            num_terms = []
            den_terms = []
            for ref, lb, r, _k in inc:
                vv = next((kv for sym, kv, _c in known_pairs if sym == lb), None)
                if vv is None:
                    vv = 0.0
                num_terms.append(f"{_num_v(vv)}/{_num_v(r)}")
                den_terms.append(f"1/{_num_v(r)}")
            subs = f"({'+'.join(num_terms)}) / ({'+'.join(den_terms)})"
            kcl_terms = [f"(V({n}) − {lb})/{_r_sym(ref)}" for ref, lb, r, _k in inc]
            steps = list(loc_shorts)
            steps.append("KCL：" + " + ".join(kcl_terms) + " = 0")
            steps.append(eq)
            steps.append(f"代入：{subs}")
            steps.append(f"V({n}) = {result_s}")
            packs[n] = _pack_calc(
                kind="node_eq",
                eq=eq,
                subs=subs,
                result=result_s,
                knowns=knowns_from(known_pairs, rlist),
                steps=steps,
            )
            continue

        kcl_terms = []
        kcl_subs = []
        group_knowns: list[tuple[str, float, str]] = []
        group_rs: list[tuple[str, float]] = []
        unk_n = 0
        for nbr, ref, r in adj.get(root) or []:
            if is_known_super(nbr):
                sym, kv, cite = known_pick(nbr)
                kcl_terms.append(f"(V({n}) − {sym})/{_r_sym(ref)}")
                kcl_subs.append(f"(V({n}) − {_num_v(kv)})/{_num_v(r)}")
                group_knowns.append((sym, kv, cite))
            else:
                unk_n += 1
                nets = clusters.get(nbr) or [nbr]
                label = nets[0]
                for cand in nets:
                    if cand in solved:
                        label = cand
                        break
                kcl_terms.append(f"(V({n}) − V({label}))/{_r_sym(ref)}")
                kcl_subs.append(f"(V({n}) − V({label}))/{_num_v(r)}")
            group_rs.append((ref, r))
        steps = list(loc_shorts)
        if kcl_terms:
            steps.append(f"KCL at {n}：" + " + ".join(kcl_terms) + " = 0")
            steps.append("代入已知电位/电阻：" + " + ".join(kcl_subs) + " = 0")
        if unk_n:
            steps.append("邻点电位亦由同一电阻网联立求得")
        steps.append(f"V({n}) = {result_s}")
        eq = " + ".join(kcl_terms) + " = 0" if kcl_terms else f"V({n})"
        packs[n] = _pack_calc(
            kind="node_eq",
            eq=eq,
            subs=" + ".join(kcl_subs) + " = 0" if kcl_subs else "",
            result=result_s,
            knowns=knowns_from(group_knowns, group_rs),
            steps=steps,
        )
    return packs


def _nodal_memo_key(
    known: dict[str, dict[str, Any]],
    ignore_oc_on: set[str],
    extra_edges: list[tuple[str, float, str, str]] | None,
    ignore_bridges: bool,
) -> tuple:
    kv = tuple(
        sorted(
            (n, round(float(e.get("v") or 0.0), 12), str(e.get("kind") or ""))
            for n, e in known.items()
            if isinstance(e, dict)
        )
    )
    oc = tuple(sorted(ignore_oc_on))
    ex = tuple(
        sorted((str(r), float(ohm), a, b) for r, ohm, a, b in (extra_edges or []))
    )
    return (kv, oc, ex, bool(ignore_bridges))


def _nodal_solve(
    known: dict[str, dict[str, Any]],
    edges: list[tuple[str, float, str, str]],
    comps: dict[str, Any],
    *,
    ignore_oc_on: set[str] | None = None,
    extra_edges: list[tuple[str, float, str, str]] | None = None,
    ignore_bridges: bool = False,
) -> dict[str, dict[str, Any]]:
    """Solve R (+ optional short bridges) network."""
    ignore_oc_on = ignore_oc_on or set()
    key = _nodal_memo_key(known, ignore_oc_on, extra_edges, ignore_bridges)
    hit = _NODAL_MEMO.get(key)
    if hit is not None:
        return hit
    result = {k: dict(v) for k, v in known.items()}
    all_edges = list(edges) + list(extra_edges or [])

    all_nets: set[str] = set()
    for _, _, a, b in all_edges:
        all_nets.add(a)
        all_nets.add(b)

    eligible: set[str] = set(result.keys())
    for n in all_nets:
        if n in result:
            continue
        if _net_is_passive_interior(
            n, comps, ignore_oc=(n in ignore_oc_on), ignore_bridges=ignore_bridges
        ):
            eligible.add(n)

    sub = [(ref, r, a, b) for ref, r, a, b in all_edges if a in eligible and b in eligible]
    if not sub:
        _NODAL_MEMO[key] = result
        return result

    parent: dict[str, str] = {n: n for n in eligible}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for _, _, a, b in sub:
        union(a, b)

    groups: dict[str, list[str]] = {}
    for n in eligible:
        groups.setdefault(find(n), []).append(n)

    for members in groups.values():
        unknown = [n for n in members if n not in result]
        known_m = [n for n in members if n in result]
        if not unknown or not known_m:
            continue
        idx = {n: i for i, n in enumerate(unknown)}
        nu = len(unknown)
        memset = set(members)
        gmat = [[0.0] * nu for _ in range(nu)]
        rhs = [0.0] * nu
        for _, r, a, b in sub:
            if a not in memset or b not in memset:
                continue
            cond = 1.0 / r
            a_k, b_k = a in result, b in result
            if not a_k and not b_k:
                i, j = idx[a], idx[b]
                gmat[i][i] += cond
                gmat[j][j] += cond
                gmat[i][j] -= cond
                gmat[j][i] -= cond
            elif a_k and not b_k:
                j = idx[b]
                gmat[j][j] += cond
                rhs[j] += cond * float(result[a]["v"])
            elif b_k and not a_k:
                i = idx[a]
                gmat[i][i] += cond
                rhs[i] += cond * float(result[b]["v"])
        sol = _gaussian(gmat, rhs)
        if sol is None:
            continue
        packs = _nodal_calc_packs(unknown, sol, result, sub, comps, members)
        for n, v in zip(unknown, sol):
            t = _trace_origin(
                n, float(v), kind="divider_solve", cite="DC 电阻网节点方程"
            )
            pack = packs.get(n)
            if pack:
                t["calc"] = pack
            result[n] = {
                "v": float(v),
                "source": {
                    "kind": "divider_solve",
                    "note": "DC 电阻网节点方程",
                },
                "kind": "divider_solve",
                "trace": t,
            }
    _NODAL_MEMO[key] = result
    return result


def _oc_nets_on_edges(
    edges: list[tuple[str, float, str, str]],
    comps: dict[str, Any],
    known: dict[str, dict[str, Any]],
) -> list[str]:
    names: set[str] = set()
    for _, _, a, b in edges:
        for n in (a, b):
            if n in known or n in GROUND_NETS:
                continue
            if _is_oc_pull_down_net(n, comps):
                names.add(n)
    return sorted(names)


def _oc_state_masks(k: int) -> list[int]:
    if k <= 0:
        return [0]
    if k <= 3:
        return list(range(1 << k))
    masks = {0, (1 << k) - 1}
    for i in range(k):
        masks.add(1 << i)
    return sorted(masks)


def _state_label(
    oc_nets: list[str],
    mask: int,
    sw_refs: list[str] | None = None,
    sw_mask: int = 0,
) -> str:
    parts: list[str] = []
    if oc_nets:
        on = [oc_nets[i] for i in range(len(oc_nets)) if mask & (1 << i)]
        if not on:
            parts.append("OC_OFF")
        elif len(on) == len(oc_nets):
            parts.append("OC_ALL_ON")
        else:
            parts.append("OC_ON:" + "+".join(on))
    if sw_refs:
        on_sw = [sw_refs[i] for i in range(len(sw_refs)) if sw_mask & (1 << i)]
        if not on_sw:
            parts.append("SW_OFF")
        elif len(on_sw) == len(sw_refs):
            parts.append("SW_ALL_ON")
        else:
            parts.append("SW_ON:" + "+".join(on_sw))
    return "+".join(parts) if parts else "base"


def solve_resistor_potentials(kg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """OFF-state potentials (all OC open, switches off)."""
    _nodal_memo_clear()
    comps = kg.get("components") or {}
    nets = kg.get("nets") or {}
    known = _known_dc(nets, comps)
    edges = _collect_edges(comps)
    oc_nets = _oc_nets_on_edges(edges, comps, known)
    bridges = _collect_bridges(comps, nets, known)
    always = [b for b in bridges if not b.get("switchable")]
    forced = _apply_vdrop_forced(known, always)
    extra = [(b["ref"], SHORT_OHM, b["net_a"], b["net_b"]) for b in always]
    return _nodal_solve(
        forced,
        edges,
        comps,
        ignore_oc_on=set(oc_nets),
        extra_edges=extra,
        ignore_bridges=True,
    )


def compute_resistor_stresses(kg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-resistor stress with local-driver multi-state envelope + cases[].

    Enumerates only drivers that change THIS resistor's |Va−Vb| (OC / CMOS / switch),
    clusters by voltage into engineer-facing cases (点亮/熄灭, 驱动高/低, 常通).
    """
    _nodal_memo_clear()
    comps = kg.get("components") or {}
    nets = kg.get("nets") or {}
    known0 = _known_dc(nets, comps)
    edges = _collect_edges(comps)
    bridges = _collect_bridges(comps, nets, known0)
    always_bridges = [b for b in bridges if not b.get("switchable")]
    switch_bridges = [b for b in bridges if b.get("switchable")]
    sw_by_ref: dict[str, dict[str, Any]] = {}
    for b in switch_bridges:
        sw_by_ref[b["ref"]] = b

    # Baseline: all OC open, switches off, CMOS low (0) when CMOS is a driver — per resistor
    pot_off = _nodal_solve(
        _apply_vdrop_forced(known0, always_bridges),
        edges,
        comps,
        ignore_oc_on=set(),
        extra_edges=[(b["ref"], SHORT_OHM, b["net_a"], b["net_b"]) for b in always_bridges],
        ignore_bridges=True,
    )
    # Also ignore global OC nets in baseline eligibility
    oc_all = _oc_nets_on_edges(edges, comps, known0)
    if oc_all:
        pot_off = _nodal_solve(
            _apply_vdrop_forced(known0, always_bridges),
            edges,
            comps,
            ignore_oc_on=set(oc_all),
            extra_edges=[(b["ref"], SHORT_OHM, b["net_a"], b["net_b"]) for b in always_bridges],
            ignore_bridges=True,
        )

    dc_graph = _build_dc_graph(comps, nets)
    by_ref: dict[str, dict[str, Any]] = {}
    for ref, c in comps.items():
        if _type_of(ref, c) != "resistor" or _comp_dnp(c):
            continue
        ohm = _parse_res_ohm(str(c.get("value") or ""))
        if ohm is None or ohm <= 0:
            continue
        info = two_terminal_dc_open(ref, c, kg, graph=dc_graph)
        if not info:
            continue
        n_a = str(info.get("n_a") or "")
        n_b = str(info.get("n_b") or "")
        if _series_unknown_load_net(ref, n_a, n_b, comps, nets):
            # Socket 开路 is only one state; do not close as I=0 是.
            continue
        by_ref[ref] = _dc_open_stress(ref, n_a, n_b, ohm, info, nets)

    for ref, ohm, n_a, n_b in edges:
        if ref in by_ref:
            continue
        fanout = build_resistor_fanout(ref, kg)
        before = {}
        for n in (n_a, n_b):
            nd = nets.get(n) or {}
            rail = nd.get("rail") or {}
            pot = pot_off.get(n)
            before[n] = {
                "status": (pot.get("kind") if pot else rail.get("status")),
                "value": (pot.get("v") if pot else rail.get("value")),
                "candidate_v": rail.get("candidate_v"),
                "source": (pot.get("source") if pot else rail.get("source")),
            }

        drivers = _local_drivers_for_resistor(
            ref, n_a, n_b, comps, nets, known0, sw_by_ref
        )
        for n in fanout.get("nets") or []:
            if not n or n in known0 or n in GROUND_NETS:
                continue
            if _is_oc_pull_down_net(str(n), comps) and not any(
                d.get("id") == f"oc:{n}" for d in drivers
            ):
                drivers.append({"kind": "oc", "net": str(n), "id": f"oc:{n}", "ref": str(n)})
        led_path = _led_path_resistor(n_a, n_b, comps, drivers)
        be_bridges = _local_be_bridges(n_a, n_b, comps)
        series_net = _series_unknown_load_net(ref, n_a, n_b, comps, nets)
        k = len(drivers)
        masks = list(range(1 << k)) if k <= 4 else _oc_state_masks(k)

        # Assumed-zero drops on pin-net devices
        assumed_flags: set[str] = set()
        local_devs: set[str] = set()
        for n in (n_a, n_b):
            for r2, _p, _t in _net_attachments(n, comps):
                local_devs.add(r2)
        local_devs.discard(ref)
        fan_devs = set(fanout.get("devices") or [])
        for b in bridges:
            if b["ref"] not in fan_devs or b["ref"] not in local_devs:
                continue
            if b.get("assumed_zero") and b.get("missing"):
                assumed_flags.add(b["missing"])
        for b in be_bridges:
            if b.get("assumed_zero") and b.get("missing"):
                assumed_flags.add(b["missing"])
        path_missing = sorted(assumed_flags) or list(fanout.get("missing_fields") or [])[:6]

        raw_states: list[dict[str, Any]] = []
        last_pot: dict[str, dict[str, Any]] = {}
        board_vs: list[tuple[int, float, dict[str, Any]]] = []
        for mask in masks:
            forced = dict(known0)
            ignore_oc: set[str] = set()
            on_bridges = list(always_bridges) + list(be_bridges)
            # Default: all OC on board open for eligibility on unrelated nets
            ignore_oc |= set(oc_all)

            for i, d in enumerate(drivers):
                on = bool(mask & (1 << i))
                if d["kind"] == "oc":
                    n = d["net"]
                    if on:
                        forced[n] = {
                            "v": 0.0,
                            "source": {"kind": "oc_pull_down", "net": n, "note": f"{n} OC导通≈0V"},
                            "kind": "closed_rail",
                            "trace": _trace_origin(n, 0.0, kind="oc", cite=f"{n} OC导通≈0V"),
                        }
                        ignore_oc.discard(n)
                    else:
                        ignore_oc.add(n)
                elif d["kind"] == "cmos":
                    n = d["net"]
                    if on:
                        forced[n] = {
                            "v": float(d["vdd_v"]),
                            "source": {
                                "kind": "cmos_high",
                                "ref": d.get("ref"),
                                "net": n,
                                "note": f"{n} CMOS高≈{d['vdd_v']}V",
                            },
                            "kind": "closed_rail",
                            "trace": _trace_origin(
                                n,
                                float(d["vdd_v"]),
                                kind="cmos_high",
                                cite=f"{n} CMOS高≈{d['vdd_v']}V",
                            ),
                        }
                    else:
                        forced[n] = {
                            "v": 0.0,
                            "source": {
                                "kind": "cmos_low",
                                "ref": d.get("ref"),
                                "net": n,
                                "note": f"{n} CMOS低≈0V",
                            },
                            "kind": "closed_rail",
                            "trace": _trace_origin(n, 0.0, kind="cmos_low", cite=f"{n} CMOS低≈0V"),
                        }
                elif d["kind"] == "switch" and on:
                    on_bridges.append(d["bridge"])

            forced = _apply_vdrop_forced(forced, on_bridges)
            extra = [(b["ref"], SHORT_OHM, b["net_a"], b["net_b"]) for b in on_bridges]
            pot = _nodal_solve(
                forced,
                edges,
                comps,
                ignore_oc_on=ignore_oc,
                extra_edges=extra,
                ignore_bridges=True,
            )
            last_pot = pot
            if series_net:
                board = n_b if series_net == n_a else n_a
                pe = pot.get(board)
                if pe is not None and pe.get("v") is not None:
                    board_vs.append((mask, float(pe["v"]), _ensure_trace(pe, board)))
            pa, pb = pot.get(n_a), pot.get(n_b)
            if pa is None or pb is None:
                continue
            va, vb = float(pa["v"]), float(pb["v"])
            v_ab = abs(va - vb)
            p_w = (v_ab * v_ab) / ohm if ohm else 0.0
            raw_states.append(
                {
                    "driver_mask": mask,
                    "v_across": v_ab,
                    "va": va,
                    "vb": vb,
                    "p_w": p_w,
                    "va_trace": _ensure_trace(pa, n_a),
                    "vb_trace": _ensure_trace(pb, n_b),
                    "note": f"mask={mask}: |V({n_a})-V({n_b})|={v_ab:.6g}V → P={p_w:.6g}W",
                }
            )

        cases = _cluster_cases(raw_states, drivers, led_path=led_path)
        if series_net:
            env = _series_socket_envelope_cases(n_a, n_b, series_net, ohm, board_vs)
            if env:
                cases = env
        # Rail→soft-net pull-to-zero envelope (e.g. R45 delay pull-up)
        elif (not drivers) and (
            not cases
            or (
                len(cases) == 1
                and float(cases[0].get("v_across") or 0.0) < CASE_V_TOL
            )
        ):
            pull = _rail_pull_to_zero_cases(n_a, n_b, last_pot or pot_off, comps, ohm)
            if pull:
                cases = pull

        # Keep compact states = cases for report compatibility
        states = [
            {
                "name": c["name_zh"],
                "v_across": c["v_across"],
                "va": c.get("va"),
                "vb": c.get("vb"),
                "p_w": c.get("p_w"),
                "note": c.get("note"),
                "driver_mask": c.get("driver_mask"),
            }
            for c in cases
        ]

        fanout = dict(fanout)
        fanout["missing_fields"] = path_missing
        if any(d["kind"] == "cmos" for d in drivers):
            drive_kind = "CMOS"
        elif any(d["kind"] == "oc" for d in drivers):
            drive_kind = "OC"
        elif any(d["kind"] == "switch" for d in drivers):
            drive_kind = "开关"
        elif cases and any("对地" in str(c.get("name_zh") or "") or "开路" in str(c.get("name_zh") or "") for c in cases):
            drive_kind = "开路/对地"
        elif cases and any("对端拉低" in str(c.get("name_zh") or "") for c in cases):
            drive_kind = "包络"
        else:
            drive_kind = "常通"

        out: dict[str, Any] = {
            "nets": {"a": n_a, "b": n_b},
            "before": before,
            "v": None,
            "ohm": ohm,
            "stress_kind": None,
            "v_source": None,
            "states": states,
            "cases": cases,
            "drivers": [{"kind": d["kind"], "id": d["id"], "ref": d.get("ref"), "net": d.get("net")} for d in drivers],
            "drive_kind": drive_kind,
            "note": "",
            "fanout": fanout,
            "assumed_zero_drops": sorted(assumed_flags),
            "path_missing_fields": path_missing,
        }
        if not cases:
            legacy = resistor_stress(ref, comps.get(ref) or {}, kg, pot_off)
            legacy["ohm"] = ohm
            # Promote rail_to_possible_0 into explicit cases
            vs = legacy.get("v_source") or {}
            if isinstance(vs, dict) and vs.get("kind") == "rail_to_possible_0" and legacy.get("v") is not None:
                v_hi = float(legacy["v"])
                p_hi = (v_hi * v_hi) / ohm if ohm else 0.0
                other = str(vs.get("other_net") or "对端")
                cases = [
                    {
                        "name_zh": f"{other}≈0/对端拉低",
                        "v_across": v_hi,
                        "p_w": p_hi,
                        "va": v_hi,
                        "vb": 0.0,
                        "note": f"{other}≈0：V={v_hi:.6g}V → P={p_hi:.6g}W（P_max包络）",
                        "driver_mask": 1,
                    },
                    {
                        "name_zh": "常通/未拉低",
                        "v_across": 0.0,
                        "p_w": 0.0,
                        "va": v_hi,
                        "vb": v_hi,
                        "note": "常通/未拉低：V≈0V → P≈0W",
                        "driver_mask": 0,
                    },
                ]
                legacy["cases"] = cases
                legacy["states"] = [
                    {
                        "name": c["name_zh"],
                        "v_across": c["v_across"],
                        "va": c.get("va"),
                        "vb": c.get("vb"),
                        "p_w": c.get("p_w"),
                        "note": c.get("note"),
                        "driver_mask": c.get("driver_mask"),
                    }
                    for c in cases
                ]
                legacy["drive_kind"] = "包络"
            else:
                legacy["states"] = []
                legacy["cases"] = []
                legacy["drive_kind"] = drive_kind
            legacy["drivers"] = out["drivers"]
            legacy["fanout"] = fanout
            legacy["assumed_zero_drops"] = sorted(assumed_flags)
            legacy["path_missing_fields"] = path_missing
            used_refs = (
                {b["ref"] for b in always_bridges}
                | {b["ref"] for b in be_bridges}
                | set(sw_by_ref)
            )
            _apply_post_solve_gates(
                legacy,
                ref=ref,
                n_a=n_a,
                n_b=n_b,
                cases=list(legacy.get("cases") or []),
                fanout=fanout,
                used_refs=used_refs,
                be_bridges=be_bridges,
                comps=comps,
                nets=nets,
                known=known0,
                last_pot=last_pot or pot_off,
                ohm=ohm,
            )
            by_ref[ref] = legacy
            continue

        used_refs = (
            {b["ref"] for b in always_bridges}
            | {b["ref"] for b in be_bridges}
            | set(sw_by_ref)
        )
        _apply_post_solve_gates(
            out,
            ref=ref,
            n_a=n_a,
            n_b=n_b,
            cases=cases,
            fanout=fanout,
            used_refs=used_refs,
            be_bridges=be_bridges,
            comps=comps,
            nets=nets,
            known=known0,
            last_pot=last_pot or pot_off,
            ohm=ohm,
        )
        if out.get("block_class") in {"topology_open", "rule_gap"}:
            by_ref[ref] = out
            continue
        cases = list(out.get("cases") or cases)
        out["cases"] = cases
        out["states"] = _states_from_cases(cases)
        v_env = max(float(c["v_across"]) for c in cases)
        v_min = min(float(c["v_across"]) for c in cases)
        multi = len(cases) > 1 and (v_env - v_min) > CASE_V_TOL
        kind = "p_max_envelope" if multi else "operating_point"
        best = max(cases, key=lambda c: float(c["v_across"]))
        note_parts = [c.get("note") or "" for c in cases]
        if multi:
            note_parts.append(f"P_max用跨压max={v_env:.6g}V")
        if assumed_flags:
            note_parts.append("上界假设压降=0：" + ",".join(sorted(assumed_flags)))
        out["v"] = v_env
        out["stress_kind"] = kind
        out["v_source"] = {
            "kind": "local_multistate" if drivers else "divider_solve",
            "va": best.get("va"),
            "vb": best.get("vb"),
            "net_a": n_a,
            "net_b": n_b,
            "envelope": multi,
            "note": "; ".join(x for x in note_parts if x),
        }
        by_ref[ref] = out

    return by_ref


def compute_capacitor_voltage_envelope(kg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-capacitor |Va−Vb| envelope using same local-driver DC states as resistors.

    Returns ref -> {nets, cases, v_max, drive_kind, before}.
    """
    _nodal_memo_clear()
    comps = kg.get("components") or {}
    nets = kg.get("nets") or {}
    known0 = _known_dc(nets, comps)
    edges = _collect_edges(comps)
    bridges = _collect_bridges(comps, nets, known0)
    always_bridges = [b for b in bridges if not b.get("switchable")]
    sw_by_ref = {b["ref"]: b for b in bridges if b.get("switchable")}
    oc_all = _oc_nets_on_edges(edges, comps, known0)

    out: dict[str, dict[str, Any]] = {}
    for ref, c in comps.items():
        if _type_of(ref, c) != "capacitor":
            continue
        pins = _pin_nets(c)
        if len(pins) < 2:
            continue
        n_a, n_b = pins[0][1], pins[1][1]
        if not n_a or not n_b:
            continue
        if n_a == n_b:
            out[ref] = {
                "nets": {"a": n_a, "b": n_b},
                "cases": [
                    {
                        "name_zh": "两脚同网",
                        "v_across": 0.0,
                        "va": 0.0,
                        "vb": 0.0,
                        "note": f"两脚同网 {n_a}：|Va−Vb|=0V",
                        "driver_mask": 0,
                    }
                ],
                "v_max": 0.0,
                "drivers": [],
                "drive_kind": "常通",
                "before": {
                    n_a: {
                        "status": (nets.get(n_a) or {}).get("rail", {}).get("status"),
                        "value": _closed_rail_v(nets.get(n_a) or {}),
                    }
                },
            }
            continue
        drivers = _local_drivers_for_resistor(ref, n_a, n_b, comps, nets, known0, sw_by_ref)
        # Also treat pin nets that are OC/CMOS even if not on resistor edges
        for n in (n_a, n_b):
            if n in known0 or n in GROUND_NETS:
                continue
            if _is_oc_pull_down_net(n, comps) and not any(
                d.get("id") == f"oc:{n}" for d in drivers
            ):
                drivers.append({"kind": "oc", "net": n, "id": f"oc:{n}", "ref": n})
            cmos = _cmos_drive_on_net(n, comps, nets, known0)
            if cmos and not any(d.get("id") == cmos["id"] for d in drivers):
                drivers.append(cmos)

        k = len(drivers)
        masks = list(range(1 << k)) if k <= 4 else _oc_state_masks(k)
        raw: list[dict[str, Any]] = []
        for mask in masks:
            forced = dict(known0)
            ignore_oc: set[str] = set(oc_all)
            on_bridges = list(always_bridges)
            for i, d in enumerate(drivers):
                on = bool(mask & (1 << i))
                if d["kind"] == "oc":
                    n = d["net"]
                    if on:
                        forced[n] = {
                            "v": 0.0,
                            "source": {"kind": "oc_pull_down", "net": n},
                            "kind": "closed_rail",
                            "trace": _trace_origin(n, 0.0, kind="oc", cite=f"{n} OC导通≈0V"),
                        }
                        ignore_oc.discard(n)
                    else:
                        ignore_oc.add(n)
                elif d["kind"] == "cmos":
                    n = d["net"]
                    forced[n] = {
                        "v": float(d["vdd_v"]) if on else 0.0,
                        "source": {"kind": "cmos_high" if on else "cmos_low", "net": n},
                        "kind": "closed_rail",
                        "trace": _trace_origin(
                            n,
                            float(d["vdd_v"]) if on else 0.0,
                            kind="cmos_high" if on else "cmos_low",
                            cite=f"{n} CMOS{'高' if on else '低'}≈{float(d['vdd_v']) if on else 0}V",
                        ),
                    }
                elif d["kind"] == "switch" and on and d.get("bridge"):
                    on_bridges.append(d["bridge"])
            forced = _apply_vdrop_forced(forced, on_bridges)
            extra = [(b["ref"], SHORT_OHM, b["net_a"], b["net_b"]) for b in on_bridges]
            pot = _nodal_solve(
                forced,
                edges,
                comps,
                ignore_oc_on=ignore_oc,
                extra_edges=extra,
                ignore_bridges=True,
            )
            # Also accept direct known / ground without solve
            def _entry(net: str) -> dict[str, Any] | None:
                if net in GROUND_NETS:
                    return {
                        "v": 0.0,
                        "kind": "ground",
                        "trace": _trace_origin(net, 0.0, kind="ground", cite="地"),
                    }
                if net in pot:
                    return pot[net]
                if net in forced:
                    return forced[net]
                cv = _closed_rail_v(nets.get(net) or {})
                if cv is None:
                    return None
                rec = {"v": cv, "kind": "closed_rail"}
                rec["trace"] = _trace_origin(net, cv, kind="closed_rail", cite="电源网名闭合")
                return rec

            ea, eb = _entry(n_a), _entry(n_b)
            if ea is None or eb is None:
                continue
            va, vb = float(ea["v"]), float(eb["v"])
            v_ab = abs(va - vb)
            raw.append(
                {
                    "driver_mask": mask,
                    "v_across": v_ab,
                    "va": va,
                    "vb": vb,
                    "p_w": v_ab,
                    "va_trace": _ensure_trace(ea, n_a),
                    "vb_trace": _ensure_trace(eb, n_b),
                }
            )
        led_path = _led_path_resistor(n_a, n_b, comps, drivers)
        cases = _cluster_cases(raw, drivers, led_path=led_path)
        if not cases:
            env = _pin_pair_envelope_cases(
                ref, n_a, n_b, 1.0, comps, nets, known0, through_self=False
            )
            if env:
                cases, _qrefs = env
                extra_cmos = _cmos_drive_on_net(n_a, comps, nets, known0) or _cmos_drive_on_net(
                    n_b, comps, nets, known0
                )
                if extra_cmos and not any(d.get("id") == extra_cmos.get("id") for d in drivers):
                    drivers.append(extra_cmos)
        # Rename note for voltage not power
        for c in cases:
            c["note"] = f"{c['name_zh']}：|Va−Vb|={c['v_across']:.6g}V"
            c.pop("p_w", None)
        v_max = max((float(c["v_across"]) for c in cases), default=None)
        if any(d.get("kind") == "switch" for d in drivers):
            drive_kind = "开关"
        elif any(d.get("kind") == "oc" for d in drivers):
            drive_kind = "OC"
        elif any(d.get("kind") == "cmos" for d in drivers):
            drive_kind = "CMOS"
        elif any(str((c.get("va_trace") or {}).get("origin_kind") or "") in {"pin_envelope", "base_drive"} for c in cases):
            drive_kind = "包络"
        else:
            drive_kind = "常通"
        before = {
            n_a: {
                "status": (nets.get(n_a) or {}).get("rail", {}).get("status"),
                "value": _closed_rail_v(nets.get(n_a) or {}),
            },
            n_b: {
                "status": (nets.get(n_b) or {}).get("rail", {}).get("status"),
                "value": _closed_rail_v(nets.get(n_b) or {}),
            },
        }
        out[ref] = {
            "nets": {"a": n_a, "b": n_b},
            "cases": cases,
            "v_max": v_max,
            "drivers": drivers,
            "drive_kind": drive_kind,
            "before": before,
        }
    return out


def resistor_stress(
    ref: str,
    c: dict[str, Any],
    kg: dict[str, Any],
    potentials: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Legacy single-potential stress (used as fallback). Prefer compute_resistor_stresses."""
    nets = kg.get("nets") or {}
    comps = kg.get("components") or {}
    pins = _pin_nets(c)
    n_a = pins[0][1] if pins else ""
    n_b = pins[1][1] if len(pins) > 1 else ""
    before = {}
    for n in (n_a, n_b):
        if not n:
            continue
        nd = nets.get(n) or {}
        rail = nd.get("rail") or {}
        pot = potentials.get(n)
        before[n] = {
            "status": (pot.get("kind") if pot else rail.get("status")),
            "value": (pot.get("v") if pot else rail.get("value")),
            "candidate_v": rail.get("candidate_v"),
            "source": (pot.get("source") if pot else rail.get("source")),
        }

    out: dict[str, Any] = {
        "nets": {"a": n_a, "b": n_b},
        "before": before,
        "v": None,
        "stress_kind": None,
        "v_source": None,
        "note": "",
        "states": [],
        "fanout": build_resistor_fanout(ref, kg),
        "assumed_zero_drops": [],
        "path_missing_fields": [],
    }
    if not n_a or not n_b:
        out["note"] = "脚网缺失"
        return out

    pa = potentials.get(n_a)
    pb = potentials.get(n_b)
    if pa is not None and pb is not None:
        va, vb = float(pa["v"]), float(pb["v"])
        out["v"] = abs(va - vb)
        kinds = {pa.get("kind"), pb.get("kind")}
        if "divider_solve" in kinds:
            out["stress_kind"] = "operating_point"
            out["v_source"] = {
                "kind": "divider_solve",
                "va": va,
                "vb": vb,
                "net_a": n_a,
                "net_b": n_b,
                "note": f"|V({n_a})-V({n_b})|={out['v']:.6g}V",
            }
        else:
            src_a = pa.get("source")
            src_b = pb.get("source")
            envelope_src = False
            for src in (src_a, src_b):
                if isinstance(src, dict) and src.get("kind") in {
                    "zener_clamp",
                    "always_on_switch",
                    "oc_pull_down",
                    "bridge_vdrop",
                }:
                    envelope_src = True
            out["stress_kind"] = "p_max_envelope" if envelope_src else "operating_point"
            out["v_source"] = {
                "kind": "two_node",
                "va": va,
                "vb": vb,
                "net_a": n_a,
                "net_b": n_b,
                "envelope": out["stress_kind"] == "p_max_envelope",
                "note": f"|V({n_a})-V({n_b})|={out['v']:.6g}V"
                + (" (P_max包络)" if envelope_src else ""),
            }
        return out

    known_net = other_net = None
    if pa is not None and pb is None:
        known_net, other_net = n_a, n_b
    elif pb is not None and pa is None:
        known_net, other_net = n_b, n_a
    if known_net and other_net:
        pk = potentials[known_net]
        if other_net in GROUND_NETS:
            out["v"] = abs(float(pk["v"]))
            out["stress_kind"] = "operating_point"
            out["v_source"] = pk.get("source")
            return out
        if (
            _is_digital_or_cmos_input_net(other_net, comps)
            or _schmitt_input_net(other_net, comps)
            or _is_oc_pull_down_net(other_net, comps)
        ):
            if float(pk["v"]) != 0.0 and pk.get("kind") in {
                "closed_rail",
                "divider_solve",
                "bridge_vdrop",
            }:
                out["v"] = abs(float(pk["v"]))
                out["stress_kind"] = "p_max_envelope"
                out["v_source"] = {
                    "kind": "rail_to_possible_0",
                    "rail_net": known_net,
                    "other_net": other_net,
                    "rail_v": float(pk["v"]),
                    "note": f"P_max包络: {other_net} 可被拉至≈0，跨压≤|{pk['v']}|V（非工作点）",
                }
                return out

    out["note"] = "两端对地电位未同时闭合"
    return out
