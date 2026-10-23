# -*- coding: utf-8 -*-
"""Validate Judge output cards — 过程契约优先，不强制对齐程序 case_id。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from agent_review.decision_contract import validate_decision_audit
from agent_review.analysis_contract import VERSION as ANALYSIS_VERSION, validate_links, step_schema

VERDICTS = frozenset({"是", "否", "待确认", "不适用"})


def validate_power_decision(card: dict[str, Any]) -> list[str]:
    """Check a decision witness without requiring a fabricated global maximum."""
    import math
    basis = card.get("decision_basis")
    if not isinstance(basis, dict):
        return ["decision_basis must be object"]
    kind = basis.get("kind")
    if kind not in {"reachable_state", "proven_lower_bound", "normal_design_model", "envelope_upper_bound", "insufficient"}:
        return ["decision_basis.kind invalid"]
    if kind == "insufficient":
        return validate_decision_audit(card) + ([] if card.get("verdict_zh") == "待确认" else ["insufficient evidence requires 待确认"])
    errs = validate_decision_audit(card, required=kind == "normal_design_model")
    try:
        value, limit = float(basis["value_w"]), float(basis["limit_w"])
        if not all(math.isfinite(x) and x >= 0 for x in (value, limit)):
            raise ValueError()
        actual_limit = float((card.get("electrical") or {})["P_limit"])
        if not math.isclose(limit, actual_limit, rel_tol=1e-6, abs_tol=1e-12):
            errs.append("decision_basis limit differs from electrical.P_limit")
        if card.get("verdict_zh") == "否" and (kind not in {"reachable_state", "proven_lower_bound", "normal_design_model"} or value <= limit):
            errs.append("否 requires reachable stress or proven lower bound above limit")
        if card.get("verdict_zh") == "是" and (kind != "envelope_upper_bound" or value > limit):
            errs.append("是 requires envelope upper bound within limit")
    except (KeyError, TypeError, ValueError):
        errs.append("decision_basis requires finite nonnegative value_w/limit_w and P_limit")
    if not basis.get("evidence") or not basis.get("conditions"):
        errs.append("decision_basis requires evidence and conditions")
    if kind in {"reachable_state", "proven_lower_bound", "normal_design_model"}:
        idx = basis.get("state_index")
        states = card.get("states") or []
        if type(idx) is not int or not 0 <= idx < len(states):
            errs.append("decision_basis.state_index invalid")
        elif states[idx].get("status") in {"unmapped", "conditional"} or states[idx].get("role") in {"unmapped", "worst_candidate"}:
            errs.append("decision witness cannot be unmapped or conditional")
        elif kind == "proven_lower_bound" and states[idx].get("stress_kind") != "lower_bound":
            errs.append("lower-bound witness requires stress_kind=lower_bound")
        elif states[idx].get("stress_kind") == "upper_bound":
            errs.append("an upper bound cannot establish a reachable failure witness")
        else:
            try:
                if not math.isclose(float(states[idx]["P"]), float(basis["value_w"]), rel_tol=1e-5, abs_tol=1e-9):
                    errs.append("decision witness value differs from state.P")
            except (KeyError, TypeError, ValueError):
                errs.append("decision witness requires numeric state.P")
    return errs


def validate_worst_case(card: dict[str, Any]) -> list[str]:
    """Validate ranking separately from sufficient evidence for a verdict."""
    import math
    worst = card.get("worst_case")
    if worst is None:  # Existing cards remain readable; new prompt requires this field.
        return []
    if not isinstance(worst, dict):
        return ["worst_case must be object"]
    states = card.get("states") or []
    if not isinstance(states, list) or any(not isinstance(state, dict) for state in states):
        return ["worst_case requires a list of state objects"]
    errors = []
    def power(state):
        value = state.get("P")
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
    for key, conditional in (("confirmed_state_index", False), ("conditional_state_index", True)):
        candidates = [i for i, state in enumerate(states)
                      if power(state) is not None and state.get("stress_kind") not in {"upper_bound", "lower_bound"}
                        and state.get("role") not in {"idle", "witness"}
                      and state.get("status") != "unmapped"
                      and (state.get("status") == "conditional") == conditional]
        index = worst.get(key)
        if not candidates:
            if index is not None:
                errors.append(f"worst_case.{key} has no eligible state")
            continue
        if type(index) is not int or index not in candidates:
            errors.append(f"worst_case.{key} must select an eligible state")
        elif power(states[index]) + 1e-10 < max(power(states[i]) for i in candidates):
            errors.append(f"worst_case.{key} omitted a larger comparable power")
        elif states[index].get("role") != ("worst_candidate" if conditional else "worst"):
            errors.append(f"worst_case.{key} role does not match ranking")
    comparison = worst.get("comparison")
    rows = comparison if isinstance(comparison, list) else []
    if sorted(row.get("state_index") for row in rows if isinstance(row, dict) and type(row.get("state_index")) is int) != list(range(len(states))):
        errors.append("worst_case.comparison must cover each state once")
    if any(not isinstance(row, dict) or not row.get("reason") for row in rows):
        errors.append("worst_case.comparison requires reasons")
    if not isinstance(worst.get("unresolved"), list):
        errors.append("worst_case.unresolved must be list")
    for index, state in enumerate(states):
        if state.get("status") == "conditional" and (power(state) is None or not state.get("conditions") or state.get("stress_kind") != "modeled"):
            errors.append(f"conditional states[{index}] requires numeric modeled P and conditions")
    return errors
_P11 = frozenset({"P11-A01", "P11-A03"})
_DEEP = frozenset(
    {
        "P11-A01",
        "P11-A02",
        "P11-A03",
        "P11-A04",
        "P11-A05",
        "P11-A06",
        "P11-A07",
        "P12-A01",
        "P12-A02",
        "P12-A03",
        "P12-A04",
        "P12-A05",
        "P12-A06",
        "P12-A07",
        "P12-A08",
        "P21-A01",
    }
)
_FORBIDDEN_COPY = (
    "本态",
    "· P=",
    "case_id=",
    "dry-run",
    "dry_run",
)
# 仅 headline/summary（对象摘要）禁内部词；stuck 可留工程备忘，展示层会改写/降级
_FORBIDDEN_BLURB = (
    "程序应力包",
    "interface_specs",
    "rail=unknown",
    "rail unknown",
)
_FORBIDDEN_CASE_IDS = (
    "supply_off",
    "clamp_work",
    "switch_pull",
    "port_gnd",
    "dc_work",
)


def validate_judge_card(
    card: dict[str, Any],
    *,
    rule_id: str,
    obj: str,
    board: Path | None = None,
) -> list[str]:
    if card.get("schema_version") == "review.v1":
        from agent_review.review_contract import validate_review
        return validate_review(card, board=board, rule_id=rule_id, obj=obj)
    errs: list[str] = []
    from agent_review.main_analysis import validate as validate_main, pilot_enabled
    errs.extend(validate_main(card,required=bool(board and pilot_enabled(board,rule_id,obj))))
    if card.get("rule_id") != rule_id:
        errs.append(f"rule_id mismatch: {card.get('rule_id')!r} != {rule_id!r}")
    if str(card.get("object") or "") != str(obj):
        errs.append(f"object mismatch: {card.get('object')!r} != {obj!r}")
    vz = card.get("verdict_zh")
    if vz not in VERDICTS:
        errs.append(f"verdict_zh invalid: {vz!r}")
    for k in ("phenomenon", "calc", "stuck", "conclusion"):
        if not str(card.get(k) or "").strip():
            errs.append(f"empty four-grid field: {k}")
    current_analysis = card.get('analysis_contract') == ANALYSIS_VERSION
    if board:
        from kernel.util import read_json
        from agent_review.batching import safe_object_name
        workbench = Path(board)/'review_out/agent_jobs'/rule_id/(safe_object_name(obj)+'.context.json')
        if not (card.get('meta') or {}).get('dry_run') and workbench.exists() and read_json(workbench).get('analysis_contract') == ANALYSIS_VERSION and not current_analysis:
            errs.append('analysis_contract required by current workbench')
    if current_analysis:
        from jsonschema import Draft202012Validator
        analysis = card.get('analysis_steps')
        errs.extend('analysis_steps: '+e.message for e in Draft202012Validator(step_schema()).iter_errors(analysis))
        assumptions = card.get('key_assumptions')
        assumption_schema = {'type':'array','items':{'type':'object','required':['id','text','status','evidence_refs'],
            'properties':{'id':{'type':'string','minLength':1},'text':{'type':'string','minLength':1},
            'status':{'enum':['supported','model_assumption','unresolved']},'evidence_refs':{'type':'array','items':{'type':'string'}}}}}
        assumption_errors = list(Draft202012Validator(assumption_schema).iter_errors(assumptions))
        errs.extend('key_assumptions: '+e.message for e in assumption_errors)
        steps=[x['text'] for x in (analysis if isinstance(analysis,list) else []) if isinstance(x,dict) and isinstance(x.get('text'),str)]
        if not assumption_errors and isinstance(analysis,list):
            errs.extend(validate_links(analysis,assumptions,[a['id'] for a in assumptions]))
            known={e.get('id') or e.get('path') for e in card.get('evidence_refs',[]) if isinstance(e,dict)}
            for assumption in assumptions:
                if any(ref not in known for ref in assumption['evidence_refs']):errs.append('key assumption evidence reference not in evidence_refs')
                if assumption['status']=='supported' and not assumption['evidence_refs']:errs.append('supported key assumption needs evidence')
        if 'reasoning_steps' in card or 'assumptions' in card:errs.append('new analysis contract must not duplicate legacy prose fields')
        from agent_review.detail_prose import mask_source_identifiers
        from agent_review.detail_view import QUANTITY
        if any(re.search(QUANTITY,mask_source_identifiers(step,{'card':card})) for step in steps):errs.append('analysis_steps must describe process without numeric quantities')
        if steps and obj not in ' '.join(steps):errs.append('analysis_steps must mention current object')
        if not steps:errs.append('analysis_steps required non-empty list')
    else:
        steps = card.get("reasoning_steps")
        if not isinstance(steps, list) or not steps:
            errs.append("reasoning_steps required non-empty list")
        elif rule_id in _DEEP and len(steps) < 3:
            errs.append("深审 reasoning_steps 至少 3 步（通路/工况/对照）")
    if not str(card.get("location_one_liner") or "").strip():
        errs.append("location_one_liner required")
    if vz in {"否", "待确认"}:
        if not str(card.get("summary") or "").strip():
            errs.append("summary required for 否/待确认")
        if not str(card.get("action") or "").strip():
            errs.append("action required for 否/待确认")
    if vz == "是":
        refs = card.get("evidence_refs") or []
        if not isinstance(refs, list) or not refs:
            errs.append("是 requires evidence_refs")
    if rule_id in _DEEP and vz in {"是", "否", "待确认"}:
        hl = str(card.get("headline_zh") or "").strip()
        if not hl:
            errs.append(
                "headline_zh required for 深审 是/否/待确认（场景→数字来源→数字→限额/缺口）"
            )
        elif "：" not in hl and ":" not in hl:
            errs.append("headline_zh 须为「位号：…」一句")
        elif len(hl) > 90:
            errs.append(f"headline_zh 过长（{len(hl)}字，建议≤65）")

    if rule_id in _P11:
        states = card.get("states")
        if not isinstance(states, list) or not states:
            errs.append("P11 requires non-empty states[]（Agent 自列工况）")
        else:
            for i, s in enumerate(states):
                if not isinstance(s, dict):
                    errs.append(f"P11 states[{i}] must be object")
                    continue
                name = str(s.get("name_zh") or s.get("name") or "").strip()
                cid = str(s.get("case_id") or "").strip()
                if not name and not cid:
                    errs.append(f"P11 states[{i}] 需要 name_zh 或 case_id")
                # Agent 审图卡：工况名须是通路描述，禁止纯分类词
                meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
                agentish = bool(card.get("key_assumptions") or card.get("assumptions")) and not meta.get("program_reference_only")
                thin = name in {
                    "有源拉低",
                    "直流工作",
                    "正常钳位",
                    "外接接地",
                    "其它工况",
                    "未评",
                }
                if agentish and thin:
                    errs.append(
                        f"P11 states[{i}] name_zh 须写通路/端态连通，禁止仅用分类词: {name!r}"
                    )
                # Agent 卡：每态须有完整 formula_lines 推理链（对人展示主合同）
                if (
                    agentish
                    and rule_id == "P11-A01"
                    and str(s.get("status") or "") != "unmapped"
                    and "未建模" not in name
                ):
                    fl = [
                        x
                        for x in (s.get("formula_lines") or s.get("lines") or [])
                        if isinstance(x, dict) and str(x.get("expr") or "").strip()
                    ]
                    # 降压：有可核对公式即可；单行完整 P= 也接受（避免凑行）
                    if len(fl) < 1:
                        errs.append(f"P11 states[{i}] 须含 formula_lines，当前 0")
                    else:
                        blob = "\n".join(str(x.get("expr") or "") for x in fl)
                        try:
                            p_val = float(
                                s["P"] if s.get("P") is not None else s.get("P_w")
                            )
                        except (TypeError, ValueError, KeyError):
                            p_val = None
                        # P≈0 态允许「可忽略/≈0」，不强制再写 P= 行
                        near_zero = p_val is not None and abs(p_val) < 1e-9
                        has_p = re.search(
                            r"(P\s*[=≈]|P_[A-Za-z0-9]+\s*[=≈]|P\s*[\(（]|(?:功耗|耗散功率|耗散)\s*[=≈]"
                            r"|耗散\s*可忽略|功耗\s*可忽略|≈\s*0\s*W"
                            r"|\d+(?:\.\d+)?\s*mW\b)",
                            blob,
                        )
                        if not has_p and not near_zero:
                            errs.append(f"P11 states[{i}] formula_lines 须含功耗 P= 代入")
                        missing_src = sum(1 for x in fl if not str(x.get("src") or "").strip())
                        if missing_src:
                            errs.append(f"P11 states[{i}] formula_lines 有 {missing_src} 行缺 src")
                    if s.get("P") is None and s.get("P_w") is None:
                        errs.append(f"P11 states[{i}] 须填写 P（W）")
                fig = s.get("figure")
                if isinstance(fig, dict):
                    pages = fig.get("pages") if isinstance(fig.get("pages"), list) else [fig]
                    if not pages:
                        errs.append(f"P11 states[{i}] figure.pages 为空")
                    for pi, page in enumerate(pages):
                        if not isinstance(page, dict):
                            errs.append(f"P11 states[{i}] figure.pages[{pi}] 须为对象")
                            continue
                        paths = ([page["path"]] if page.get("path") else []) + list(page.get("paths") or [])
                        if not paths or any(not isinstance(p, list) or len(p) < 2 for p in paths):
                            errs.append(f"P11 states[{i}] figure.pages[{pi}] 每条 path/paths 至少两个名字")
                        path = [str(x).strip() for p in paths if isinstance(p, list) for x in p if str(x).strip()]
                        for mi, marker in enumerate(page.get("markers") or []):
                            if not isinstance(marker, dict):
                                errs.append(f"P11 states[{i}] figure 圆点[{mi}] 须为对象")
                                continue
                            on = str(marker.get("on") or "").strip()
                            if not on:
                                errs.append(f"P11 states[{i}] figure 圆点[{mi}] 缺 on")
                            # Off-state devices may be annotated without pretending
                            # that current flows through them. Geometry checks location.
                            if not str(marker.get("title") or "").strip():
                                errs.append(f"P11 states[{i}] figure 圆点[{mi}] 缺 title")
                conclusion = str(s.get("结论") or s.get("conclusion") or "").strip()
                if conclusion and ("未建模" not in conclusion):
                    has_arrow = "→" in conclusion or "->" in conclusion
                    has_cmp = any(x in conclusion for x in ("≤", ">=", "≤", "＞", ">", "<", "≦"))
                    has_vz = any(
                        x in conclusion
                        for x in (
                            "是",
                            "否",
                            "待确认",
                            "超标",
                            "达标",
                            "满足",
                            "不满足",
                            "超",
                            "低于",
                            "高于",
                            "超过",
                            "上界",
                        )
                    )
                    has_limit = any(
                        x in conclusion
                        for x in (
                            "P_limit",
                            "V_limit",
                            "限额",
                            "P_max",
                            "V_max",
                            "Pmax",
                            "Vmax",
                        )
                    )
                    # 过程优先：有限额对照或结论词即可；不逼长对照句
                    if not (
                        has_arrow
                        or (has_cmp and has_vz)
                        or (has_limit and has_vz)
                        or (has_cmp and has_limit)
                        or has_limit
                        or has_vz
                    ):
                        errs.append(
                            f"P11 states[{i}] 结论须能对照限额或给出是|否|超|低于等: {conclusion!r}"
                        )
                # Agent 自己标 unmapped 则禁止升绿
                if vz == "是" and (
                    s.get("status") == "unmapped" or str(s.get("role") or "") == "unmapped"
                ):
                    errs.append("P11 states 含 unmapped 时禁止 verdict_zh=是")

        elec = card.get("electrical") or {}
        if not isinstance(elec, dict):
            errs.append("P11 electrical must be object")
            elec = {}
        if rule_id == "P11-A01":
            errs.extend(validate_worst_case(card))
            basis = card.get("decision_basis")
            if basis is not None:
                errs.extend(validate_power_decision(card))
            if elec.get("P_rated") is None and "缺额定" not in str(card.get("stuck") or ""):
                if vz == "是":
                    errs.append("P11-A01 是 requires electrical.P_rated")
            if vz == "是" and not str(elec.get("cite") or "").strip():
                errs.append("P11-A01 是 requires electrical.cite")
            calc = str(card.get("calc") or "")
            if vz != "不适用" and not re.search(
                r"(P\s*=|P_[A-Za-z0-9]+\s*=|P≈|P_max|mW|功耗|\d+(?:\.\d+)?\s*W\b)", calc
            ):
                errs.append("P11-A01 calc 须含功耗/P/mW 代入痕迹")
        if rule_id == "P11-A03":
            if elec.get("V_rated") is None and "缺额定" not in str(card.get("stuck") or ""):
                if vz == "是":
                    errs.append("P11-A03 是 requires electrical.V_rated")
            if vz == "是" and not str(elec.get("cite") or "").strip():
                errs.append("P11-A03 是 requires electrical.cite")
            calc = str(card.get("calc") or "")
            if vz != "不适用" and not re.search(r"(V\s*=|V_max|耐压|电压)", calc):
                errs.append("P11-A03 calc 须含电压代入痕迹")

        if len(str(card.get("summary") or "")) > 600:
            errs.append("summary too long (keep <=600 chars)")

        # 过程痕迹：phenomenon 或 reasoning 应触及通路/工况
        process_blob = "\n".join(
            [
                str(card.get("phenomenon") or ""),
                "\n".join(str(x) for x in (steps or [])),
                str(card.get("assumptions") or ""),
            ]
        )
        if vz != "不适用" and len(process_blob.strip()) < 20:
            errs.append("P11 推理过短：请写通路与工况假设")

        visible = [
            str(card.get(k) or "")
            for k in ("phenomenon", "calc", "stuck", "conclusion", "summary", "action")
        ]
        sol = card.get("solution") if isinstance(card.get("solution"), dict) else {}
        for st in sol.get("states") or []:
            if not isinstance(st, dict):
                continue
            for step in st.get("steps") or []:
                if isinstance(step, dict):
                    visible.append(str(step.get("eq") or ""))
            for row in st.get("lines") or []:
                if isinstance(row, dict):
                    visible.append(str(row.get("src") or ""))
                    visible.append(str(row.get("expr") or ""))
        for st in states if isinstance(states, list) else []:
            if isinstance(st, dict):
                visible.append(str(st.get("结论") or st.get("conclusion") or ""))
                visible.append(str(st.get("name_zh") or ""))
        blob = "\n".join(visible)
        for bad in _FORBIDDEN_COPY:
            if bad in blob:
                errs.append(f"P11 对人可见文案禁词: {bad!r}")
        for en in _FORBIDDEN_CASE_IDS:
            if re.search(rf"\b{en}\b", blob):
                errs.append(f"P11 对人可见文案禁止裸英文工况 id: {en}")
        blurb_blob = "\n".join(
            str(card.get(k) or "") for k in ("headline_zh", "summary")
        )
        for bad in _FORBIDDEN_BLURB:
            if bad in blurb_blob:
                errs.append(f"headline_zh/summary 禁内部词: {bad!r}（请改成任务书/手册白话）")

        for st in sol.get("states") or []:
            if not isinstance(st, dict):
                continue
            sol_steps = st.get("steps") or []
            if not sol_steps:
                continue
            eq = str((sol_steps[-1] or {}).get("eq") or "") if isinstance(sol_steps[-1], dict) else ""
            if eq and ("→" not in eq) and ("未建模" not in eq):
                errs.append(f"P11 solution.states 末行须为对照句(含→): {eq!r}")
    return errs
