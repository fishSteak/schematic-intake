# -*- coding: utf-8 -*-
"""Judge 单对象工作台：厚上下文切片（材料事实，无判定词）。"""

from __future__ import annotations
from agent_review.main_analysis import VERSION as MAIN_VERSION, pilot_enabled

from pathlib import Path
from typing import Any

from agent_review.batching import safe_object_name
from kernel.util import out_dir, read_json
from agent_review.criteria import criteria_for_rule
from agent_review.design_pin_labels import design_pin_labels
from agent_review.page_evidence import for_object as page_evidence_for_object

# 二跳优先展开的邻接类型（开漏上拉、指示灯、串联电阻等）
_EXPAND_TYPES = frozenset(
    {
        "resistor",
        "led",
        "diode",
        "zener",
        "tvs",
        "ic",
        "transistor",
        "mosfet",
        "bjt",
        "opamp",
        "comparator",
    }
)


def _pin_nets(comp: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for pin in comp.get("pins") or []:
        if isinstance(pin, dict) and pin.get("net"):
            out.append(str(pin["net"]))
    return out


def _neighbor_brief(comps: dict[str, Any], ref: str) -> dict[str, Any]:
    c = comps.get(ref) or {}
    return {
        "ref": ref,
        "type": c.get("type"),
        "value": c.get("value"),
        "mpn": c.get("mpn"),
        "pins": c.get("pins") or [],
        "manufacturer": c.get("manufacturer"),
        "pintable": c.get("pintable") or {},
    }


def _net_brief(nets: dict[str, Any], name: str) -> dict[str, Any]:
    nd = nets.get(name) or {}
    rail = nd.get("rail") if isinstance(nd.get("rail"), dict) else {}
    return {
        "name": name,
        "refs": list(nd.get("refs") or [])[:24],
        "rail": rail or None,
    }


def _type_key(c: dict[str, Any]) -> str:
    return str(c.get("type") or "").strip().lower()


def _mpn_excerpt(o: Path, mpn: str, rule_id: str = "") -> dict[str, Any]:
    from agent_review.rated_keys import excerpt_for_mpn

    return excerpt_for_mpn(o, mpn, rule_id)


def _program_reference_quarantine(o: Path, rule_id: str, sn: str) -> dict[str, Any]:
    """程序包只留路径指针，不注入态名/P_max/completeness，避免污染 Agent 审图。"""
    cases_rel = f"review_out/operating_cases/{rule_id}/{sn}.json"
    stress_rel = f"review_out/program_stress/{rule_id}/{sn}.json"
    pack: dict[str, Any] = {
        "role": "program_reference_quarantine",
        "note": (
            "程序工况/应力包仅后台旁证，禁止抄其态名、P_max/V_max、completeness。"
            "审图以 neighbors/nets/手册为准；程序可能漏支路（如开漏释放+LED）。"
            "不一致时以你自己的通路与代入为准，并可在 stuck 写明。"
        ),
        "forbidden_to_copy": [
            "case_names",
            "state_names",
            "P_max",
            "V_max",
            "completeness",
        ],
    }
    if (o / "operating_cases" / rule_id / f"{sn}.json").is_file():
        pack["operating_cases_path"] = cases_rel
    if (o / "program_stress" / rule_id / f"{sn}.json").is_file():
        pack["program_stress_path"] = stress_rel
    return pack


def _endpoint_excerpts(o: Path, kg: dict[str, Any], obj: str, rule_id: str) -> list[dict[str, Any]]:
    from agent_review.rated_keys import excerpt_for_mpn, mpns_for_object

    comps = kg.get("components") or {}
    if obj in comps:
        return []
    return [excerpt_for_mpn(o, mpn, rule_id) for mpn in mpns_for_object(kg, obj, rule_id)]


def build_judge_workbench(
    board: Path,
    kg: dict[str, Any],
    obj: str,
    rule_id: str,
    *,
    deps: list[str] | None = None,
    hashes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """为 Judge 生成可读工作台（一对象一份）。"""
    o = out_dir(board)
    # These workbenches feed the direct LLM dispatcher. New legacy P11-A01
    # reviews always bind their primary analysis to the decision basis; the
    # opt-in review.v1 route keeps its own schema and proof contract.
    registry_path = o / "review_contracts.json"
    registry = read_json(registry_path) if registry_path.is_file() else {}
    native_review = registry.get(rule_id, {}).get(obj) == "review.v1"
    required_main = rule_id == "P11-A01" and not native_review
    comps = kg.get("components") or {}
    nets = kg.get("nets") or {}
    c = comps.get(obj) or {}
    pin_nets = _pin_nets(c)

    neighbor_refs: list[str] = []
    for n in pin_nets:
        nd = nets.get(n) or {}
        for r in list(nd.get("refs") or []):
            if r != obj and r not in neighbor_refs:
                neighbor_refs.append(str(r))
            if len(neighbor_refs) >= 32:
                break
        if len(neighbor_refs) >= 32:
            break

    second: list[str] = []
    for r in list(neighbor_refs)[:20]:
        nc = comps.get(r) or {}
        for n in _pin_nets(nc)[:6]:
            nd = nets.get(n) or {}
            for r2 in list(nd.get("refs") or [])[:12]:
                if r2 == obj or r2 in neighbor_refs or r2 in second:
                    continue
                c2 = comps.get(r2) or {}
                tk = _type_key(c2)
                if tk in _EXPAND_TYPES or not tk or len(second) < 8:
                    second.append(str(r2))
                if len(second) >= 20:
                    break
            if len(second) >= 20:
                break
        if len(second) >= 20:
            break
    all_nbr = neighbor_refs + second

    sn = safe_object_name(obj)
    locs = c.get("location") or []
    loc0 = locs[0] if isinstance(locs, list) and locs and isinstance(locs[0], dict) else {}

    return {
        "rule_id": rule_id,
        "object": obj,
        "role": "judge_workbench",
        "check_criteria": criteria_for_rule(rule_id),
        "design_pin_labels": design_pin_labels(o,[obj,*all_nbr,*list((nets.get(obj) or {}).get('refs') or [])]),
        "source_page_evidence": page_evidence_for_object(board,rule_id,obj),
        "available_main_analysis_contract": MAIN_VERSION if rule_id=='P11-A01' else None,
        "delivery_policy": __import__("agent_review.delivery", fromlist=["policy"]).policy(board,rule_id,obj),
        "main_analysis_contract": (MAIN_VERSION if required_main or pilot_enabled(board,rule_id,obj) else None),
        "analysis_contract": "analysis.v1",
        "read_first": [
            "check_criteria 原始完整条款；design_pin_labels 原设计脚名；source_page_evidence 原始页面图与来源记录",
            "本文件 identity / neighbors / nets / mpn_excerpt（审图主材料）",
            "context_store 对应 object/mpn/net",
            "program_reference 仅路径旁证：禁止抄其中数字与态名",
        ],
        "identity": {
            "ref": obj,
            "type": c.get("type"),
            "value": c.get("value"),
            "mpn": c.get("mpn"),
            "footprint": c.get("footprint"),
            "manufacturer": c.get("manufacturer"),
            "pins": c.get("pins") or [],
            "page_name": loc0.get("pageName") or loc0.get("page_name"),
            "pdf_page": loc0.get("pdf_page"),
            "module_title": loc0.get("module_title"),
        },
        "nets": [_net_brief(nets, n) for n in pin_nets],
        "neighbors": [_neighbor_brief(comps, r) for r in all_nbr[:48]],
        "mpn_excerpt": _mpn_excerpt(o, str(c.get("mpn") or ""), rule_id),
        "endpoint_mpn_excerpts": _endpoint_excerpts(o, kg, obj, rule_id),
        "program_reference": _program_reference_quarantine(o, rule_id, sn),
        "deps": list(deps or []),
        "hashes": dict(hashes or {}),
    }
