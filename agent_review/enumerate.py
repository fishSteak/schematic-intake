# -*- coding: utf-8 -*-
"""Enumerate review objects per rule_id. Zero verdicts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from kernel.util import ROOT, read_json

_DNP = re.compile(r"(?<![A-Z0-9])(DNP|DNI|NF)(?![A-Z0-9])", re.I)


def _dnp(c: dict[str, Any]) -> bool:
    blob = " ".join(str(x or "") for x in (c.get("status"), c.get("value"), c.get("mpn"))).upper()
    return bool(_DNP.search(blob))


def _type(c: dict[str, Any], ref: str) -> str:
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
        "Y": "crystal",
        "X": "crystal",
        "CN": "connector",
        "J": "connector",
        "P": "connector",
        "TB": "connector",
        "H": "header",
    }.get(p, "unknown")


def _assembled(kg: dict[str, Any], kind: str) -> list[str]:
    out: list[str] = []
    for ref, c in (kg.get("components") or {}).items():
        if _dnp(c):
            continue
        typ = _type(c, ref)
        if typ == kind or (kind == "connector" and typ in {"connector", "header"}):
            out.append(ref)
    return sorted(out)


def _all_refs(kg: dict[str, Any]) -> list[str]:
    return sorted(
        ref for ref, c in (kg.get("components") or {}).items() if not _dnp(c)
    )


def _all_nets(kg: dict[str, Any]) -> list[str]:
    return sorted(str(n) for n in (kg.get("nets") or {}).keys())


def _ics(kg: dict[str, Any]) -> list[str]:
    return _assembled(kg, "ic")


def _connectors(kg: dict[str, Any]) -> list[str]:
    return _assembled(kg, "connector")


def _crystals(kg: dict[str, Any]) -> list[str]:
    return _assembled(kg, "crystal")


def _inductors(kg: dict[str, Any]) -> list[str]:
    return _assembled(kg, "inductor")


def _mosfets(kg: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for ref, c in (kg.get("components") or {}).items():
        if _dnp(c):
            continue
        typ = _type(c, ref)
        blob = " ".join(
            str(x or "") for x in (c.get("mpn"), c.get("value"), c.get("type"), ref)
        ).upper()
        if typ in {"mosfet", "fet", "jfet"} or "MOSFET" in blob or "MOS FET" in blob:
            out.append(ref)
            continue
        # 常见 Q* 且类型为 transistor：保守纳入，由检查器再收窄
        if typ in {"transistor", "bjt"} and re.match(r"^Q\d", ref or "", re.I):
            if any(k in blob for k in ("MOS", "FET", "BSS", "AO34", "SI23", "2N700")):
                out.append(ref)
    return sorted(out)


def _tvs(kg: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for ref, c in (kg.get("components") or {}).items():
        if _dnp(c):
            continue
        typ = _type(c, ref)
        blob = " ".join(
            str(x or "") for x in (c.get("mpn"), c.get("value"), c.get("type"), c.get("description"))
        ).upper()
        if "TVS" in blob or "ESD" in blob or "TRANSIENT" in blob:
            out.append(ref)
            continue
        if typ == "diode" and any(k in blob for k in ("SMAJ", "SMBJ", "SMCJ", "PESD", "USBLC", "SRV05")):
            out.append(ref)
    return sorted(out)


def _actives(kg: dict[str, Any]) -> list[str]:
    """有源/半导体：IC、晶体管、MOSFET、LED、二极管/TVS（不含无源 RCL/连接器/保险丝）。"""
    out: list[str] = []
    for ref, c in (kg.get("components") or {}).items():
        if _dnp(c):
            continue
        typ = _type(c, ref)
        if typ in {
            "ic",
            "transistor",
            "mosfet",
            "bjt",
            "fet",
            "jfet",
            "led",
            "diode",
            "tvs",
        }:
            out.append(ref)
    return sorted(out)


def design_task_tamb_bounds(kg: dict[str, Any]) -> tuple[float, float] | None:
    """任务书项目环境温度 (Tamb_min, Tamb_max)。缺任一侧 → None（P12-A03 整项不适用）。"""
    dt = kg.get("design_task") if isinstance(kg.get("design_task"), dict) else {}
    lo = dt.get("t_amb_min_c")
    hi = dt.get("t_amb_max_c")
    raw = dt.get("t_amb_c")
    if lo is None and hi is None and isinstance(raw, dict):
        lo = raw.get("min", raw.get("t_min", raw.get("Tamb_min", raw.get("low"))))
        hi = raw.get("max", raw.get("t_max", raw.get("Tamb_max", raw.get("high"))))
    elif lo is None and hi is None and isinstance(raw, (list, tuple)) and len(raw) >= 2:
        lo, hi = raw[0], raw[1]
    if lo is None or hi is None:
        return None
    try:
        return float(lo), float(hi)
    except (TypeError, ValueError):
        return None


def design_task_has_connector_machine_reqs(kg: dict[str, Any]) -> bool:
    """任务书是否签了连接器整机需求（电流 / 插拔 / 防护）任一类。"""
    dt = kg.get("design_task") if isinstance(kg.get("design_task"), dict) else {}
    if dt.get("currents"):
        return True
    for k in (
        "mating_cycles",
        "plug_cycles",
        "n_mate",
        "插拔寿命",
        "ip_rating",
        "ip_class",
        "防护等级",
        "enclosure_ip",
        "connector_requirements",
    ):
        v = dt.get(k)
        if v not in (None, "", [], {}):
            return True
    cr = dt.get("connectors")
    if isinstance(cr, (list, dict)) and cr:
        return True
    return False


def apply_rule_precondition_gates(
    by_rule: dict[str, list[str]],
    kg: dict[str, Any],
) -> tuple[dict[str, str], dict[str, Any]]:
    """缺任务书前置条件时清空对象（不开深审）。返回短 na 原因与 gated 元数据。"""
    na_reasons: dict[str, str] = {}
    gated: dict[str, Any] = {}

    if "P12-A03" in by_rule:
        cands = list(by_rule.get("P12-A03") or [])
        if not cands:
            na_reasons["P12-A03"] = "无有源器件，本项不适用。"
        elif design_task_tamb_bounds(kg) is None:
            # Missing structured requirements require source resolution, not exclusion.
            gated["P12-A03"] = {"candidates": cands, "n": len(cands), "gate": "no_tamb"}
            gated["P12-A03"]["gate"] = "resolve_ambient_from_confirmed_materials"

    if "P12-A07" in by_rule:
        cands = list(by_rule.get("P12-A07") or [])
        if not cands:
            na_reasons["P12-A07"] = "无连接器，本项不适用。"
        elif not design_task_has_connector_machine_reqs(kg):
            gated["P12-A07"] = {
                "candidates": cands,
                "n": len(cands),
                "gate": "no_connector_machine_reqs",
            }
            gated["P12-A07"]["gate"] = "resolve_machine_requirements_or_pending"

    if "P12-A08" in by_rule and not by_rule.get("P12-A08"):
        na_reasons["P12-A08"] = "无连接器，本项不适用。"

    return na_reasons, gated


# 兼容旧名
def apply_p12a03_tamb_gate(
    by_rule: dict[str, list[str]],
    kg: dict[str, Any],
) -> tuple[dict[str, str], dict[str, Any]]:
    return apply_rule_precondition_gates(by_rule, kg)


def _reset_nets(kg: dict[str, Any]) -> list[str]:
    out = []
    for n in _all_nets(kg):
        u = n.upper()
        if "RESET" in u or "NRST" in u or u.endswith("_RST") or "POR" in u:
            out.append(n)
    return out


_GROUND_NETS = {"GND", "AGND", "DGND", "PGND", "VSS", "AVSS"}
_POWER_TOKEN = re.compile(
    r"(VCC|VDD|VEE|VBAT|VIN|VOUT|VREF|AVDD|DVDD|PVDD|VBUS|3V3|5V0|1V8)",
    re.I,
)
_VOLT_NET = re.compile(r"^[+]?\d+(\.\d+)?V\d*$", re.I)


def _is_power_or_gnd_net(name: str, nd: dict[str, Any] | None = None) -> bool:
    n = (name or "").strip()
    if not n or n.upper() in _GROUND_NETS:
        return True
    if n.startswith("N$") or n.upper().startswith("UNCONNECTED"):
        return True
    if _VOLT_NET.match(n) or _POWER_TOKEN.search(n):
        return True
    cls = str((nd or {}).get("class") or (nd or {}).get("net_class") or "").lower()
    if cls in {"power", "ground", "gnd"}:
        return True
    return False


def _signal_nets_multi_ic(kg: dict[str, Any]) -> list[str]:
    """两颗及以上 IC 的信号网（排除电源/地）；供 P21-A01 / P12-A02。"""
    comps = kg.get("components") or {}
    nets = kg.get("nets") or {}
    out = []
    for n, nd in nets.items():
        if _is_power_or_gnd_net(str(n), nd if isinstance(nd, dict) else None):
            continue
        refs = list((nd or {}).get("refs") or [])
        ics = [r for r in refs if _type(comps.get(r) or {}, r) == "ic"]
        if len(ics) >= 2:
            out.append(str(n))
    return sorted(out)


ENUMERATORS: dict[str, Any] = {
    "P05-A01": lambda kg: _assembled(kg, "resistor"),
    "P05-A06": lambda kg: _assembled(kg, "capacitor"),
    "P04-A01": lambda kg: _all_nets(kg),
    "P04-A03": lambda kg: _all_nets(kg),
    "P07-A05": lambda kg: _all_nets(kg),
    "P11-A01": lambda kg: _assembled(kg, "resistor"),
    "P11-A02": lambda kg: _assembled(kg, "resistor"),
    "P11-A03": lambda kg: _assembled(kg, "capacitor"),
    "P11-A04": lambda kg: _assembled(kg, "capacitor"),
    "P11-A05": lambda kg: _assembled(kg, "capacitor"),
    "P11-A06": lambda kg: _inductors(kg),
    "P11-A07": lambda kg: _inductors(kg),
    "P12-A01": lambda kg: _ics(kg),
    "P12-A02": lambda kg: _signal_nets_multi_ic(kg),
    "P12-A03": lambda kg: _actives(kg),
    "P12-A04": lambda kg: _mosfets(kg),
    "P12-A05": lambda kg: _mosfets(kg),
    "P12-A06": lambda kg: _tvs(kg),
    "P12-A07": lambda kg: _connectors(kg),
    "P12-A08": lambda kg: _connectors(kg),
    "P14-A02": lambda kg: _connectors(kg),
    "P14-A05": lambda kg: _connectors(kg),
    "P14-A06": lambda kg: _connectors(kg),
    "P16-A01": lambda kg: _ics(kg),
    "P16-A05": lambda kg: [
        n
        for n in _all_nets(kg)
        if any(x in n.upper() for x in ("AVDD", "AVCC", "VDDA", "ANALOG"))
    ]
    or _assembled(kg, "inductor"),  # ferrite beads often L*
    "P18-A02": lambda kg: _crystals(kg),
    "P19-A03": lambda kg: _reset_nets(kg),
    "P21-A01": lambda kg: _signal_nets_multi_ic(kg),
    "P42-A01": lambda kg: _all_refs(kg),
    "P42-A04": lambda kg: _all_refs(kg),
}


def load_program_rules() -> dict[str, Any]:
    return read_json(ROOT / "rules" / "product_center_15.json")


def rule_meta_map() -> dict[str, dict[str, Any]]:
    prog = load_program_rules()
    return {it["rule_id"]: it for it in (prog.get("items") or []) if it.get("rule_id")}


def enumerate_board(
    kg: dict[str, Any],
    rule_ids: list[str] | None = None,
) -> dict[str, Any]:
    prog = load_program_rules()
    ids = list(rule_ids or prog.get("rule_ids") or [])
    meta = rule_meta_map()
    by_rule: dict[str, list[str]] = {}
    for rid in ids:
        fn = ENUMERATORS.get(rid)
        if not fn:
            by_rule[rid] = []
            continue
        by_rule[rid] = list(fn(kg))

    na_reasons, gated = apply_rule_precondition_gates(by_rule, kg)
    from agent_review.applicability import exclusions
    screened = {}
    for rid, objects in by_rule.items():
        excluded = exclusions(kg, rid, objects)
        if excluded:
            screened[rid] = excluded
            by_rule[rid] = [obj for obj in objects if obj not in excluded]
            if not by_rule[rid]:
                na_reasons[rid] = '材料适用性筛选：所有候选对象均有明确的不适用依据。'

    # context keys: objects ∪ mpns ∪ nets touched by any rule
    object_keys: set[str] = set()
    net_keys: set[str] = set()
    for rid, objs in by_rule.items():
        if rid in (
            "P04-A01",
            "P04-A03",
            "P07-A05",
            "P19-A03",
            "P21-A01",
            "P12-A02",
            "P16-A05",
        ):
            net_keys.update(objs)
        else:
            object_keys.update(objs)

    comps = kg.get("components") or {}
    mpn_keys: set[str] = set()
    for ref in object_keys:
        c = comps.get(ref) or {}
        mpn = (c.get("mpn") or "").strip()
        if mpn and mpn.upper() not in {"TBD", "CAP", "RES", "NC", "-"}:
            mpn_keys.add(mpn)
        for p in c.get("pins") or []:
            n = p.get("net") if isinstance(p, dict) else None
            if n:
                net_keys.add(str(n))

    planned = (
        [{"kind": "object", "key": k} for k in sorted(object_keys)]
        + [{"kind": "mpn", "key": k} for k in sorted(mpn_keys)]
        + [{"kind": "net", "key": k} for k in sorted(net_keys)]
    )

    out: dict[str, Any] = {
        "rule_ids": ids,
        "applicability_exclusions": screened,
        "by_rule": by_rule,
        "meta": {rid: meta.get(rid) or {"rule_id": rid, "name": rid, "severity": "中"} for rid in ids},
        "planned_context": planned,
        "stats": {
            "rules": len(ids),
            "objects": len(object_keys),
            "mpns": len(mpn_keys),
            "nets": len(net_keys),
            "judge_pairs": sum(len(v) for v in by_rule.values()),
        },
    }
    if na_reasons:
        out["rule_na_reasons"] = na_reasons
    if gated:
        out["rule_gated"] = gated
    return out


def write_enumeration(out_dir: Path, data: dict[str, Any]) -> Path:
    from kernel.util import write_json

    path = out_dir / "agent_enumeration.json"
    write_json(path, data)
    return path
