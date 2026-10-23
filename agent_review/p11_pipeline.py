# -*- coding: utf-8 -*-
"""P11：先拆工况 → 再逐态计算（subprocess schematic-intake）。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from agent_review.batching import safe_object_name
from agent_review.program_bridge import review_root
from kernel.util import out_dir, write_json

_WORKER = r'''
# -*- coding: utf-8 -*-
import json, re, sys
from pathlib import Path

kg_path = Path(sys.argv[1])
out_cases = Path(sys.argv[2])
out_stress = Path(sys.argv[3])
mode = sys.argv[4]  # cases | calc | all

from kernel.util import read_json
from kernel.situations import (
    find_series_clamp_groups,
    build_clamp_situations,
    cases_for_resistor,
    _comps,
    _nets,
    _resistor_ends,
    _attachments,
    _clamp_devices,
    _half_rated,
    _pin_nets,
    CLAMP_TYPES,
    PORT_TYPES,
    SWITCH_TYPES,
)
from kernel.dc_modes import dc_modes, project_resistor, capacitor_view
from kernel.resistor_network import _parse_res_ohm, _type_of, _comp_dnp, GROUND_NETS, _closed_rail_v

kg = read_json(kg_path)
comps = _comps(kg)
ends = _resistor_ends(comps)
groups = find_series_clamp_groups(kg)
ref_to_group = {}
for g in groups:
    for r in g.get("refs") or []:
        ref_to_group[r] = g


def _p_rated(c):
    params = ((c.get("pintable") or {}).get("params") or {}) if isinstance(c, dict) else {}
    pr = params.get("ptot_w") or params.get("p_rated_w") or c.get("p_rated")
    if pr is None:
        text = params.get("ptot_text") or ""
        m = re.search(r"(\d+(?:\.\d+)?)\s*(mW|W)", str(text), re.I)
        if m:
            pr = float(m.group(1)) / (1000.0 if m.group(2).lower() == "mw" else 1.0)
    try:
        return float(pr) if pr is not None else None
    except (TypeError, ValueError):
        return None


def _cite(c, pr, *, rated_kind: str = "P"):
    """rated_kind: P→功率额定；V→耐压额定（电容等）。"""
    params = ((c.get("pintable") or {}).get("params") or {}) if isinstance(c, dict) else {}
    note = params.get("extract_note")
    if not note and isinstance(c.get("datasheet"), dict):
        note = (c.get("datasheet") or {}).get("extract_note")
    if note:
        return str(note)
    if pr is not None:
        if rated_kind == "V":
            return f"料号参数表 v_rated_v={pr} V"
        return f"料号参数表 p_rated_w={pr} W"
    if rated_kind == "V":
        return "额定耐压 V_rated 未抽出"
    return "额定功率 P_rated 未抽出"


def _datasheet_block(c, pr, *, rated_kind: str = "P"):
    params = ((c.get("pintable") or {}).get("params") or {}) if isinstance(c, dict) else {}
    pt = c.get("pintable") if isinstance(c.get("pintable"), dict) else {}
    pdf = pt.get("pdf") or ""
    local = ""
    if pdf:
        local = str(pdf)
    elif pt.get("path"):
        # 料号参数表旁通常有 datasheet.pdf
        try:
            from pathlib import Path as _P
            cand = _P(str(pt.get("path"))).with_name("datasheet.pdf")
            if cand.is_file():
                local = str(cand)
        except Exception:
            pass
    extracted = {}
    if pr is not None:
        if rated_kind == "V":
            extracted["v_rated_v"] = pr
        else:
            extracted["p_rated_w"] = pr
    if rated_kind != "V":
        if params.get("p_rated_temp_c") is not None:
            extracted["p_rated_temp_c"] = params.get("p_rated_temp_c")
        elif "70" in str(params.get("extract_note") or ""):
            extracted["p_rated_temp_c"] = 70
    note = str(params.get("extract_note") or "")
    if rated_kind == "V" and note and "P_rated" in note and "V_rated" not in note:
        note = note.replace("P_rated", "V_rated")
    return {
        "url": "",
        "local_path": local,
        "extracted": extracted,
        "extract_note": note,
    }


def _classify_name(name: str) -> str:
    n = name or ""
    if "未上电" in n:
        return "supply_off"
    if "外部接" in n or ("接地" in n and "未上电" not in n) or re.search(r"\bCN\d+", n):
        return "port_gnd"
    if "导通" in n or re.search(r"\bQ\d+", n):
        return "switch_pull"
    # 直流优先于裸 Vf，避免 LED Vf 误标成钳位
    if "常通" in n or "直流" in n:
        return "dc_work"
    if "钳位" in n or "稳压" in n or "Vz" in n:
        return "clamp_work"
    if "Vf" in n or "VF" in n:
        return "dc_work"
    return "other"


def _drop_supply_off_majors(majors):
    return [m for m in majors if m.get("case_id") != "supply_off" and "未上电" not in str(m.get("name_zh") or "")]


def _star_path(ref, n_a, n_b):
    """同网星形/分压：写出串联与旁路，而非单电阻两脚。"""
    nets_map = _nets(kg)

    def rail_v(n):
        if not n:
            return None
        return _closed_rail_v(nets_map.get(n) or {})

    def on_net(net):
        return [r for r, (a, b, _) in ends.items() if a == net or b == net]

    def other_end(r, net):
        a, b, _ = ends[r]
        return b if a == net else a

    candidates = [n for n in (n_a, n_b) if n and n not in GROUND_NETS and rail_v(n) is None]
    mid = None
    for n in candidates:
        if len(on_net(n)) >= 2:
            mid = n
            break
    if not mid:
        return {
            "chain_refs": [ref],
            "nodes": [n_a, n_b],
            "supply_net": n_a,
            "exit_net": n_b,
            "text": f"{n_a} — {ref} — {n_b}",
            "group_id": "",
        }
    branches = [(r, other_end(r, mid)) for r in on_net(mid)]
    # GND 的 _closed_rail_v 可能为 0.0，不得当电源轨
    supply_br = [
        (r, o) for r, o in branches if o not in GROUND_NETS and rail_v(o) is not None
    ]
    gnd_br = [(r, o) for r, o in branches if o in GROUND_NETS]
    other_br = [(r, o) for r, o in branches if (r, o) not in supply_br and (r, o) not in gnd_br]
    parts = []
    if supply_br and gnd_br:
        rs, so = supply_br[0]
        rg, go = gnd_br[0]
        parts.append(f"{so} — {rs} — {mid} — {rg} — {go}")
        for r, o in list(supply_br[1:]) + list(gnd_br[1:]) + other_br:
            parts.append(f"旁路 {mid} — {r} — {o}")
    else:
        parts.append(f"{n_a} — {ref} — {n_b}")
        for r, o in branches:
            if r != ref:
                parts.append(f"旁路 {mid} — {r} — {o}")
    ics = [r for r, _p, t in _attachments(mid, comps) if t == "ic"]
    text = "；".join(parts)
    if ics:
        text += f"；{mid} 接 {','.join(ics[:3])}"
    return {
        "chain_refs": [r for r, _ in branches],
        "nodes": [n_a, n_b, mid],
        "supply_net": supply_br[0][1] if supply_br else n_a,
        "exit_net": gnd_br[0][1] if gnd_br else n_b,
        "text": text,
        "group_id": f"star:{mid}",
    }


def _actors_for_group(g, kg):
    comps = _comps(kg)
    exit_net = g["exit_net"]
    actors = []
    for ref, pin, typ in _clamp_devices(exit_net, comps):
        role = "port" if typ in PORT_TYPES else ("switch" if typ in SWITCH_TYPES else "exit_clamp")
        actors.append({"ref": ref, "pin": pin, "type": typ, "role": role, "net": exit_net})
    # 出口经二极管下游的开关/端口也会改变本串电流（如 Q4@SPDL、CN1）
    for ref, _pin, typ in _attachments(exit_net, comps):
        if typ not in CLAMP_TYPES:
            continue
        for _p2, n2 in _pin_nets(comps.get(ref) or {}):
            if not n2 or n2 == exit_net:
                continue
            for r3, pin3, t3 in _attachments(str(n2), comps):
                if t3 in SWITCH_TYPES:
                    actors.append({"ref": r3, "pin": pin3, "type": t3, "role": "switch", "net": str(n2)})
                if t3 in PORT_TYPES:
                    actors.append({"ref": r3, "pin": pin3, "type": t3, "role": "port", "net": str(n2)})
    seen = set()
    out = []
    for a in actors:
        k = (a["ref"], a["role"])
        if k in seen:
            continue
        seen.add(k)
        out.append(a)
    return out


def _major_cases_from_sits(sits):
    """Collapse boundary variants into presentation majors；剔除未上电。"""
    buckets = {"clamp_work": [], "switch_pull": [], "port_gnd": [], "dc_work": [], "other": []}
    for s in sits:
        cid = s.get("id") or s.get("case_id") or ""
        name = s.get("name") or s.get("name_zh") or cid
        cls = _classify_name(str(name))
        if cls == "supply_off" or "未上电" in str(name):
            continue
        buckets.setdefault(cls, []).append({**s, "case_id": cid or cls, "name_zh": name, "class_id": cls})
    majors = []
    order = ["clamp_work", "switch_pull", "port_gnd", "dc_work", "other"]
    name_zh_map = {
        "clamp_work": "正常钳位",
        "switch_pull": "有源拉低",
        "port_gnd": "外接接地",
        "dc_work": "直流工作",
    }
    for cls in order:
        items = buckets.get(cls) or []
        if not items:
            continue
        pick = items[0]
        if cls == "clamp_work":
            pick = None
            for it in items:
                if "动态" in str(it.get("name_zh") or ""):
                    pick = it
                    break
            if pick is None:
                for it in items:
                    n = str(it.get("name_zh") or "")
                    if "标称" in n and "下限" not in n and "上限" not in n:
                        pick = it
                        break
            if pick is None:
                pick = items[0]
        # 直流档：去掉误带的 Vf 展示名
        display = name_zh_map.get(cls, pick.get("name_zh"))
        if cls == "dc_work":
            display = "直流工作"
        note = pick.get("note") or pick.get("name_zh") or cls
        if cls == "dc_work" and ("Vf" in str(note) or "未上电" in str(note)):
            note = "直流稳态工作"
        majors.append(
            {
                "case_id": cls,
                "name_zh": display,
                "what_changes": note,
                "qualitative": pick.get("vc_src") or note,
                "variant_ids": [str(it.get("case_id") or it.get("id") or "") for it in items],
            }
        )
    return majors


def build_cases_for_ref(ref):
    c = comps.get(ref) or {}
    if _type_of(ref, c) != "resistor" or _comp_dnp(c):
        return None
    g = ref_to_group.get(ref)
    stuck = []
    if g:
        path_nodes = list(g.get("nodes") or [])
        chain_bits = []
        for i, node in enumerate(path_nodes):
            chain_bits.append(str(node))
            if i < len(g.get("refs") or []):
                chain_bits.append(g["refs"][i])
        path_text = " — ".join(chain_bits)
        sits = build_clamp_situations(g, kg)
        majors = _drop_supply_off_majors(_major_cases_from_sits(sits))
        have = {m["case_id"] for m in majors}
        completeness = "complete" if have & {"clamp_work", "dc_work", "switch_pull", "port_gnd"} else "incomplete"
        if "switch_pull" not in {m["case_id"] for m in majors} and any(
            a["type"] in SWITCH_TYPES for a in _actors_for_group(g, kg)
        ):
            stuck.append("拓扑含开关但工况未列出有源拉低")
            completeness = "incomplete"
        if "port_gnd" not in {m["case_id"] for m in majors} and any(
            a["type"] in PORT_TYPES for a in _actors_for_group(g, kg)
        ):
            # ports may be optional for closes_verdict; still list if actor exists
            majors.append(
                {
                    "case_id": "port_gnd",
                    "name_zh": "外接接地",
                    "what_changes": "端口把钳位通路拉到地",
                    "qualitative": "由端口 actors 推断，待计算层填数",
                    "variant_ids": [],
                }
            )
        return {
            "ref": ref,
            "rule_id": "P11-A01",
            "path": {
                "chain_refs": list(g.get("refs") or []),
                "nodes": path_nodes,
                "supply_net": g.get("supply_net"),
                "exit_net": g.get("exit_net"),
                "text": path_text,
                "group_id": g.get("group_id"),
            },
            "actors": _actors_for_group(g, kg),
            "cases": majors,
            "completeness": completeness,
            "stuck": "；".join(stuck),
            "kind": "series_clamp",
        }
    # fallback non-clamp
    block = dc_modes(kg, ref)
    modes = list(block.get("modes") or [])
    ends_ab = (block.get("ends") or {}).get(ref) or ends.get(ref)
    if isinstance(ends_ab, tuple):
        n_a, n_b = ends_ab[0], ends_ab[1]
    elif isinstance(ends_ab, (list, tuple)) and len(ends_ab) >= 2:
        n_a, n_b = ends_ab[0], ends_ab[1]
    else:
        n_a = n_b = ""
    sits = [{"id": f"m{i}", "name": m.get("name") or m.get("name_zh") or f"mode{i}", "note": m.get("note") or ""} for i, m in enumerate(modes)]
    if not sits:
        sits = [{"id": "dc_work", "name": "直流工作", "note": str(block.get("note") or "未列出多态")}]
        stuck = ["未登记串联钳位通路，工况退化"]
        completeness = "incomplete"
    else:
        stuck = []
        completeness = "complete" if block.get("in_index") else "incomplete"
        if not block.get("in_index"):
            stuck.append(str(block.get("note") or "通路种类未登记"))
    majors = _drop_supply_off_majors(_major_cases_from_sits(sits))
    if not majors:
        majors = [{"case_id": "dc_work", "name_zh": "直流工作", "what_changes": "直流稳态", "qualitative": "", "variant_ids": []}]
        completeness = "incomplete"
        stuck.append("无有效工作工况")
    path = _star_path(ref, n_a, n_b)
    if block.get("block_id") and not path.get("group_id"):
        path["group_id"] = block.get("block_id")
    kind = block.get("kind") or "other"
    if path.get("group_id", "").startswith("star:"):
        kind = "resistor_star"
    return {
        "ref": ref,
        "rule_id": "P11-A01",
        "path": path,
        "actors": [],
        "cases": majors,
        "completeness": completeness,
        "stuck": "；".join(stuck),
        "kind": kind,
    }


def _case_class(cid):
    s = str(cid or "")
    if "::" in s:
        return s.split("::", 1)[1]
    return s


def build_stress_for_ref(ref, cases_doc):
    c = comps.get(ref) or {}
    ohm = _parse_res_ohm(str(c.get("value") or ""))
    pr = _p_rated(c)
    limit = 0.5 * pr if pr is not None else None
    g = ref_to_group.get(ref)
    variants = []
    if g and ohm:
        sits = build_clamp_situations(g, kg)
        raw_cases = cases_for_resistor(g, sits, ref)
        for sit, rc in zip(sits, raw_cases):
            variants.append(
                {
                    "case_id": sit.get("id"),
                    "class_id": _classify_name(sit.get("name") or ""),
                    "name_zh": sit.get("name"),
                    "V_exit": sit.get("vc"),
                    "I": rc.get("i_dc"),
                    "V_drop": rc.get("v_across"),
                    "P": rc.get("p_w"),
                    "formula_lines": rc.get("lines") or [],
                    "note": rc.get("note") or "",
                }
            )
    else:
        block = dc_modes(kg, ref)
        projected = project_resistor(block, ref, float(ohm or 0.0), limit) if ohm else []
        for i, pc in enumerate(projected):
            variants.append(
                {
                    "case_id": f"m{i}",
                    "class_id": _classify_name(str(pc.get("name_zh") or "")),
                    "name_zh": pc.get("name_zh"),
                    "V_exit": None,
                    "I": pc.get("i_dc"),
                    "V_drop": pc.get("v_across"),
                    "P": pc.get("p_w"),
                    "formula_lines": pc.get("lines") or [],
                    "note": pc.get("note") or "",
                }
            )

    # pick key states aligned to operating case majors (case_id may be path_id::event_id)
    states = []
    by_class = {}
    for v in variants:
        by_class.setdefault(v.get("class_id") or "other", []).append(v)
    unmapped_n = 0
    for maj in cases_doc.get("cases") or []:
        cid = maj.get("case_id") or ""
        cls = _case_class(cid)
        st_status = maj.get("status") or ""
        if st_status == "unmapped":
            unmapped_n += 1
            miss = maj.get("missing") or []
            stuck = maj.get("stuck") or "未建模；缺可闭合应力模型"
            if miss:
                stuck = stuck + "；缺：" + "/".join(str(x) for x in miss)
            states.append(
                {
                    "case_id": cid,
                    "name_zh": maj.get("name_zh"),
                    "role": "unmapped",
                    "status": "unmapped",
                    "P": None,
                    "I": None,
                    "V_drop": None,
                    "formula": "",
                    "stuck": stuck,
                    "missing": list(miss),
                }
            )
            continue
        if cls == "supply_off" or "未上电" in str(maj.get("name_zh") or ""):
            continue
        items = by_class.get(cls) or []
        if not items:
            states.append(
                {
                    "case_id": cid,
                    "name_zh": maj.get("name_zh"),
                    "role": "missing_numeric",
                    "status": st_status or "unmapped",
                    "P": None,
                    "I": None,
                    "V_drop": None,
                    "formula": "",
                    "stuck": maj.get("stuck") or "计算层未给出该工况数字",
                }
            )
            continue
        pick = items[0]
        if cls == "clamp_work":
            pick = None
            for it in items:
                n = str(it.get("name_zh") or "")
                if "动态" in n:
                    pick = it
                    break
            if pick is None:
                for it in items:
                    n = str(it.get("name_zh") or "")
                    if "标称" in n and "下限" not in n and "上限" not in n:
                        pick = it
                        break
            # 仅有 Vz 上下限时取上限侧（更接近标称工作点功耗）
            if pick is None:
                for it in items:
                    if "上限" in str(it.get("name_zh") or ""):
                        pick = it
                        break
            if pick is None:
                ranked = sorted(items, key=lambda x: float(x.get("P") or 0.0))
                pick = ranked[(len(ranked) - 1) // 2]
        elif cls in {"switch_pull", "port_gnd"}:
            pick = max(items, key=lambda x: float(x.get("P") or 0.0))
        lines = pick.get("formula_lines") or []
        formula = "; ".join(str(x.get("expr") or "") for x in lines[-3:] if isinstance(x, dict))
        role = {"supply_off": "idle", "clamp_work": "workpoint", "switch_pull": "active_pull", "port_gnd": "worst", "dc_work": "workpoint"}.get(cls, "other")
        states.append(
            {
                "case_id": cid,
                "name_zh": maj.get("name_zh") or pick.get("name_zh"),
                "role": role,
                "status": "closed",
                "P": pick.get("P"),
                "I": pick.get("I"),
                "V_drop": pick.get("V_drop"),
                "formula": formula,
                "source_variant": pick.get("name_zh"),
            }
        )
    # P_max 仅统计 closed 有数态；排除未上电/idle
    p_vals = [
        float(s["P"])
        for s in states
        if s.get("P") is not None
        and s.get("status") != "unmapped"
        and s.get("case_id") != "supply_off"
        and s.get("role") != "idle"
    ]
    p_max = max(p_vals) if p_vals else None
    p_max_case = None
    if p_max is not None:
        for s in states:
            if s.get("P") is not None and abs(float(s["P"]) - p_max) < 1e-18:
                p_max_case = s.get("case_id")
                break
    completeness = cases_doc.get("completeness")
    if unmapped_n:
        completeness = "incomplete"
    stuck = cases_doc.get("stuck") or ""
    if unmapped_n and "未建模" not in stuck:
        stuck = (stuck + "；" if stuck else "") + f"含 {unmapped_n} 个未建模工况"
    return {
        "ref": ref,
        "rule_id": "P11-A01",
        "ohm": ohm,
        "P_rated": pr,
        "P_limit": limit,
        "cite": _cite(c, pr),
        "datasheet": _datasheet_block(c, pr),
        "states": states,
        "variants": variants,
        "P_max": p_max,
        "P_max_case": p_max_case,
        "path_text": (cases_doc.get("path") or {}).get("text"),
        "completeness": completeness,
        "stuck": stuck,
        "unmapped_n": unmapped_n,
    }


# capacitors P11-A03
def build_cap_cases(ref):
    c = comps.get(ref) or {}
    if _type_of(ref, c) not in {"capacitor", "cap", "mlcc"} or _comp_dnp(c):
        return None
    pins = []
    for p in c.get("pins") or []:
        if isinstance(p, dict) and p.get("net"):
            pins.append(str(p["net"]))
    n_a = pins[0] if pins else ""
    n_b = pins[1] if len(pins) > 1 else ""
    view = capacitor_view(kg, n_a, n_b)
    cases_raw = list(view.get("cases") or [])
    majors = _major_cases_from_sits(
        [{"id": f"c{i}", "name": x.get("name_zh") or f"case{i}", "note": x.get("note") or ""} for i, x in enumerate(cases_raw)]
    )
    majors = _drop_supply_off_majors(majors)
    if not majors:
        majors = [{"case_id": "dc_work", "name_zh": "直流工作", "what_changes": view.get("note") or "", "qualitative": "", "variant_ids": []}]
    return {
        "ref": ref,
        "rule_id": "P11-A03",
        "path": {"chain_refs": [ref], "nodes": [n_a, n_b], "text": f"{n_a} — {ref} — {n_b}", "supply_net": n_a, "exit_net": n_b},
        "actors": [],
        "cases": majors,
        "completeness": "complete" if view.get("status") == "closed" else "incomplete",
        "stuck": "" if view.get("status") == "closed" else str(view.get("note") or "电容工况未闭合"),
        "kind": "capacitor",
        "_view": view,
    }


def build_cap_stress(ref, cases_doc):
    view = cases_doc.pop("_view", None) or capacitor_view(
        kg,
        (cases_doc.get("path") or {}).get("nodes", ["", ""])[0],
        ((cases_doc.get("path") or {}).get("nodes") or ["", ""])[1] if len((cases_doc.get("path") or {}).get("nodes") or []) > 1 else "",
    )
    c = comps.get(ref) or {}
    params = ((c.get("pintable") or {}).get("params") or {})
    vr = (
        params.get("v_rated_v")
        or params.get("rated_voltage_v")
        or params.get("voltage_rating_v")
    )
    try:
        vr = float(vr) if vr is not None else None
    except (TypeError, ValueError):
        vr = None
    limit = 0.5 * vr if vr is not None else None
    variants = []
    for i, case in enumerate(view.get("cases") or []):
        variants.append(
            {
                "case_id": f"c{i}",
                "class_id": _classify_name(str(case.get("name_zh") or "")),
                "name_zh": case.get("name_zh"),
                "V_across": case.get("v_across"),
                "P": None,
                "formula_lines": case.get("lines") or [],
                "note": case.get("note") or "",
            }
        )
    states = []
    for maj in cases_doc.get("cases") or []:
        cls = maj["case_id"]
        if cls == "supply_off" or "未上电" in str(maj.get("name_zh") or ""):
            continue
        items = [v for v in variants if v.get("class_id") == cls] or variants
        pick = max(items, key=lambda x: float(x.get("V_across") or 0.0)) if items else {}
        states.append(
            {
                "case_id": cls,
                "name_zh": maj.get("name_zh") or pick.get("name_zh"),
                "role": "workpoint",
                "V_across": pick.get("V_across"),
                "formula": "; ".join(str(x.get("expr") or "") for x in (pick.get("formula_lines") or [])[-2:]),
            }
        )
    vmax = view.get("v_max")
    return {
        "ref": ref,
        "rule_id": "P11-A03",
        "V_rated": vr,
        "V_limit": limit,
        "cite": _cite(c, vr, rated_kind="V"),
        "datasheet": _datasheet_block(c, vr, rated_kind="V"),
        "states": states,
        "variants": variants,
        "V_max": vmax,
        "path_text": (cases_doc.get("path") or {}).get("text"),
        "completeness": cases_doc.get("completeness"),
        "stuck": cases_doc.get("stuck") or "",
    }


# collect refs
res_refs = sorted(ends.keys())
cap_refs = []
for ref, c in comps.items():
    if _comp_dnp(c):
        continue
    if _type_of(ref, c) in {"capacitor", "cap", "mlcc"}:
        cap_refs.append(ref)

cases_out = {"P11-A01": {}, "P11-A03": {}}
stress_out = {"P11-A01": {}, "P11-A03": {}}

if mode in {"cases", "all"}:
    for ref in res_refs:
        doc = build_cases_for_ref(ref)
        if doc:
            cases_out["P11-A01"][ref] = doc
    for ref in sorted(cap_refs):
        doc = build_cap_cases(ref)
        if doc:
            # keep _view for calc in same process when mode=all
            cases_out["P11-A03"][ref] = doc

if mode == "cases":
    out_cases.write_text(json.dumps(cases_out, ensure_ascii=False), encoding="utf-8")
    out_stress.write_text(json.dumps({"skipped": True}, ensure_ascii=False), encoding="utf-8")
    raise SystemExit(0)

if mode == "calc":
    cases_out = json.loads(out_cases.read_text(encoding="utf-8"))

if mode in {"calc", "all"}:
    for ref, doc in (cases_out.get("P11-A01") or {}).items():
        stress_out["P11-A01"][ref] = build_stress_for_ref(ref, doc)
    for ref, doc in list((cases_out.get("P11-A03") or {}).items()):
        # rebuild view if missing
        d2 = dict(doc)
        stress_out["P11-A03"][ref] = build_cap_stress(ref, d2)

out_cases.write_text(json.dumps(cases_out, ensure_ascii=False), encoding="utf-8")
out_stress.write_text(json.dumps(stress_out, ensure_ascii=False), encoding="utf-8")
'''


def _run_worker(board: Path, kg: dict[str, Any], mode: str) -> tuple[dict[str, Any], dict[str, Any]]:
    o = out_dir(board)
    rev = review_root()
    kg_path = o / "board_kg.json"
    if not kg_path.is_file():
        write_json(kg_path, kg)
    with tempfile.TemporaryDirectory(prefix="p11_pipe_") as td:
        raw_cases = Path(td) / "cases.json"
        raw_stress = Path(td) / "stress.json"
        # for calc-only, seed cases from disk
        if mode == "calc":
            disk = o / "operating_cases" / "_bundle.json"
            if disk.is_file():
                raw_cases.write_text(disk.read_text(encoding="utf-8"), encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(rev)
        proc = subprocess.run(
            [sys.executable, "-c", _WORKER, str(kg_path), str(raw_cases), str(raw_stress), mode],
            cwd=str(rev),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"p11 worker failed mode={mode} code={proc.returncode}\n"
                f"stderr={proc.stderr[-2500:]}\nstdout={proc.stdout[-1000:]}"
            )
        cases = json.loads(raw_cases.read_text(encoding="utf-8"))
        stress = json.loads(raw_stress.read_text(encoding="utf-8"))
    return cases, stress


def _materialize_cases(o: Path, cases: dict[str, Any]) -> dict[str, Any]:
    root = o / "operating_cases"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "_bundle.json", cases)
    stats: dict[str, Any] = {}
    for rid, by_ref in cases.items():
        if not isinstance(by_ref, dict):
            continue
        d = root / rid
        d.mkdir(parents=True, exist_ok=True)
        n_complete = 0
        for ref, doc in by_ref.items():
            # strip private
            clean = {k: v for k, v in doc.items() if not str(k).startswith("_")}
            write_json(d / f"{safe_object_name(ref)}.json", clean)
            if clean.get("completeness") == "complete":
                n_complete += 1
        stats[rid] = {"objects": len(by_ref), "complete": n_complete}
    return stats


def _materialize_stress(o: Path, stress: dict[str, Any]) -> dict[str, Any]:
    root = o / "program_stress"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "_bundle.json", stress)
    stats: dict[str, Any] = {}
    for rid, by_ref in stress.items():
        if not isinstance(by_ref, dict):
            continue
        d = root / rid
        d.mkdir(parents=True, exist_ok=True)
        n_ok = 0
        for ref, doc in by_ref.items():
            write_json(d / f"{safe_object_name(ref)}.json", doc)
            if doc.get("P_max") is not None or doc.get("V_max") is not None:
                n_ok += 1
        stats[rid] = {"objects": len(by_ref), "with_peak": n_ok}
    return stats


def run_cases(
    board: Path,
    kg: dict[str, Any],
    *,
    skip_path_agent: bool = False,
) -> dict[str, Any]:
    """board_paths draft→seal→project，再与程序旧 cases 并集（投影优先）。"""
    o = out_dir(board)
    from agent_review.board_paths.run import run_board_paths

    bp = run_board_paths(board, kg, skip_agent=skip_path_agent)
    cases, _ = _run_worker(board, kg, "cases")
    if not isinstance(cases.get("P11-A01"), dict):
        cases["P11-A01"] = {}
    projected = bp.get("projected") or {}
    for ref, doc in projected.items():
        # board_paths 投影覆盖程序种子；剔除未上电
        if isinstance(doc, dict) and isinstance(doc.get("cases"), list):
            doc = dict(doc)
            doc["cases"] = [
                c
                for c in doc["cases"]
                if str(c.get("event_id") or "") != "supply_off"
                and not str(c.get("case_id") or "").endswith("::supply_off")
                and "未上电" not in str(c.get("name_zh") or "")
            ]
        cases["P11-A01"][ref] = doc
    cstats = _materialize_cases(o, cases)
    write_json(
        o / "p11_cases_result.json",
        {
            "cases": cstats,
            "board_paths": {
                "covered_refs_n": len(bp.get("covered_refs") or []),
                "status_counts": bp.get("status_counts") or {},
                "assist_paths_n": bp.get("assist_paths_n") or 0,
            },
        },
    )
    return {"cases": cstats, "board_paths": bp.get("status_counts") or {}}


def run_calc(board: Path, kg: dict[str, Any]) -> dict[str, Any]:
    """Fill program_stress from sealed operating_cases (or build both if missing)."""
    o = out_dir(board)
    if not (o / "operating_cases" / "_bundle.json").is_file():
        run_cases(board, kg)
    cases, stress = _run_worker(board, kg, "calc")
    _materialize_cases(o, cases)
    sstats = _materialize_stress(o, stress)
    # SPA/门禁可见 unmapped 计数
    unmapped_total = 0
    for rid, by_ref in (stress or {}).items():
        if not isinstance(by_ref, dict):
            continue
        for doc in by_ref.values():
            if isinstance(doc, dict):
                unmapped_total += int(doc.get("unmapped_n") or 0)
    write_json(
        o / "p11_stress_result.json",
        {"stress": sstats, "unmapped_total": unmapped_total},
    )
    return {"stress": sstats, "reused": False, "unmapped_total": unmapped_total}
