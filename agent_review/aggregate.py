# -*- coding: utf-8 -*-
"""Aggregate judge cards → verdicts.json + evidence + findings + report_data."""

from __future__ import annotations

from agent_review.review_contract import analysis_texts

from pathlib import Path
from typing import Any

from agent_review.batching import safe_object_name
from agent_review.enumerate import rule_meta_map
from agent_review.rule_modes import load_agent_rule_ids
from kernel.util import read_json, write_json
from agent_review.decision_contract import validate_decision_audit


def _agg_verdict(
    cards: list[dict[str, Any]], empty_rule: bool, *, expected: int | None = None
) -> str:
    if empty_rule and not cards:
        return "不适用"
    real_n = len([c for c in cards if str(c.get("object") or "") not in {"", "(none)"}])
    if expected is not None and real_n < expected:
        return "INSUFFICIENT"
    if not cards:
        return "INSUFFICIENT"
    vzs = [c.get("verdict_zh") for c in cards]
    if any(v == "否" for v in vzs):
        return "否"
    if any(v in {"待确认", "INSUFFICIENT"} for v in vzs):
        return "INSUFFICIENT"
    if all(v == "不适用" for v in vzs):
        return "不适用"
    if all(v == "是" for v in vzs):
        return "是"
    return "INSUFFICIENT"


def load_judge_cards(out_dir: Path, rule_id: str, objects: list[str]) -> list[dict[str, Any]]:
    cards = []
    base = out_dir / "agent_jobs" / rule_id
    for obj in objects:
        p = base / f"{safe_object_name(obj)}.json"
        if p.with_suffix(".invalid.json").exists() or p.with_suffix(".failed.json").exists():
            continue
        if p.is_file():
            card = read_json(p)
            if card.get('schema_version') == 'review.v1':
                from agent_review.review_contract import validate_review, project, source_hash
                if (card.get('_meta') or {}).get('validation_status')!='valid' or (card.get('_meta') or {}).get('source_hash')!=source_hash(card):
                    continue
                if validate_review(card, board=out_dir.parent, rule_id=rule_id, obj=obj):
                    continue
                projected=project(card)
                from agent_review.remediation import load as load_remediation
                remediation=load_remediation(out_dir,card)
                if remediation:
                    projected['action']=remediation['summary']
                    projected['remediation_summary']=remediation
                cards.append(projected)
                continue
            if rule_id == "P11-A01" and card.get("decision_audit") is not None and validate_decision_audit(card):
                continue
            cards.append(card)
    return cards


def _na_action(na_reason: str) -> str:
    if "Tamb" in na_reason or "环境温度" in na_reason or "t_amb" in na_reason:
        return "在任务书补齐项目环境温度后重跑本项"
    if "连接器" in na_reason and ("电流" in na_reason or "插拔" in na_reason or "防护" in na_reason):
        return "在任务书补齐连接器电流 / 插拔 / 防护整机需求后重跑本项"
    if "无适用" in na_reason or "无有源" in na_reason or "无连接器" in na_reason:
        return "无需改板"
    return "按任务书补齐前置条件后重跑本项"


def _card_plain_headline(c: dict[str, Any], rule_id: str = "") -> str:
    """Use authored review text; aggregation must not infer electrical conclusions."""
    for key in ("headline_zh", "conclusion", "summary"):
        text = str(c.get(key) or "").strip()
        if text:
            return text
    obj = str(c.get("object") or "").strip() or "对象"
    labels = {"是": "满足判据", "否": "不满足判据", "不适用": "不适用",
              "待确认": "待确认", "INSUFFICIENT": "待确认"}
    return f"{obj}：{labels.get(c.get('verdict_zh'), '缺少评审摘要')}。"


def synthesize_rule_summary(
    rid: str,
    item_v: str,
    cards: list[dict[str, Any]],
    *,
    na_reason: str | None = None,
    expected: int | None = None,
) -> str:
    """检查项头概括。优先门禁原因；对象句直接采用模型摘要，不重新推断电气结论。"""
    if na_reason:
        return str(na_reason).strip()
    real = [c for c in cards if str(c.get("object") or "") not in {"", "(none)"}]
    n = len(real)
    if expected is not None and n < expected:
        return f"已落卡 {n} / 应审 {expected}。"
    if item_v == "不适用":
        return "本项无适用对象。"
    if not real:
        return ""
    if item_v == "是":
        return f"{n} 个对象均满足判据。"

    fails = [c for c in real if c.get("verdict_zh") == "否"]
    waits = [c for c in real if c.get("verdict_zh") in {"待确认", "INSUFFICIENT"}]
    focus = fails or waits
    if not focus:
        return ""
    # 多对象：最多两句，用「；」连接
    lines = [_card_plain_headline(c, rid) for c in focus[:2]]
    text = "；".join(lines)
    more = len(focus) - 2
    if more > 0:
        text += f"；另 {more} 处同类。"
    return text


def aggregate(
    out_dir: Path,
    enumeration: dict[str, Any],
    *,
    materials: dict[str, Any] | None = None,
    context_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = rule_meta_map()
    results: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    summary = {"是": 0, "否": 0, "INSUFFICIENT": 0, "不适用": 0}

    for rid in enumeration.get("rule_ids") or []:
        objs = list((enumeration.get("by_rule") or {}).get(rid) or [])
        empty = len(objs) == 0
        cards = load_judge_cards(out_dir, rid, objs)
        na_reason = str((enumeration.get("rule_na_reasons") or {}).get(rid) or "").strip() or None
        if empty:
            na_reason = na_reason or "本项无适用对象。"
            gated = (enumeration.get("rule_gated") or {}).get(rid) or {}
            steps = [na_reason, "项结论：不适用"]
            if gated.get("n"):
                steps.insert(1, f"候选 {gated.get('n')} 个未开深审")
            cards = [
                {
                    "rule_id": rid,
                    "object": "(none)",
                    "verdict_zh": "不适用",
                    "location_one_liner": na_reason,
                    "reasoning_steps": steps,
                    "summary": na_reason,
                    "action": _na_action(na_reason),
                    "phenomenon": na_reason,
                    "calc": "无",
                    "stuck": "无",
                    "conclusion": "不适用",
                    "evidence_refs": [],
                }
            ]
        if empty and (enumeration.get('rule_no_object_audits') or {}).get(rid):
            audit=enumeration['rule_no_object_audits'][rid]
            cards[0]['evidence_refs']=[{'path':rel,'note':'器件分类原始来源'} for rel in audit['sources']]+[{'path':audit['analysis'],'note':'独立分类核查记录（推导，非原始事实）'}]
            cards[0]['reasoning_steps'].insert(0,'已对照原始BOM/网表及独立器件分类复核，未发现适用对象。')
        expected = 0 if empty else len(objs)
        item_v = _agg_verdict(cards if not empty else [], empty, expected=expected)
        if empty:
            item_v = "不适用"
        # Screening is a material-based controller result, never an invented model card.
        excluded = (enumeration.get('applicability_exclusions') or {}).get(rid,{})
        if excluded:
            if empty:
                cards = []
            for obj, screening in excluded.items():
                reason = screening['reason']
                cards.append({'rule_id':rid,'object':obj,'verdict_zh':'不适用',
                              'location_one_liner':obj,'summary':reason,'conclusion':reason,
                              'reasoning_steps':[reason],'action':'本项无需整改。',
                              'phenomenon':reason,'calc':'不适用','stuck':'无',
                              'evidence_refs':[{'path':'review_out/board_kg.json','note':str(screening['facts'])}],
                              'meta':{'source':'material_applicability_filter','screening':screening}})
        summary[item_v] = summary.get(item_v, 0) + 1
        rm = meta.get(rid) or enumeration.get("meta", {}).get(rid) or {}
        rule_summary = synthesize_rule_summary(
            rid, item_v, cards, na_reason=na_reason, expected=None if empty else len(objs)+len(excluded)
        )
        # reasoning overview from first few cards
        overview = []
        for c in cards[:5]:
            steps = analysis_texts(c)
            if not steps:
                continue
            bit = steps[0]
            if isinstance(bit, dict):
                bit = bit.get("eq") or bit.get("observation") or bit.get("action") or ""
            bit = str(bit or "").strip()
            if bit:
                overview.append(bit)
        if not overview:
            overview = ["读取密封上下文", "按检查项专属判据比较", "汇总对象结论"]

        agent_set = set(load_agent_rule_ids())
        method = "agent_judge" if rid in agent_set else "program"
        result = {
            "rule_id": rid,
            "title": rm.get("name") or rid,
            "severity": rm.get("severity") or "中",
            "verdict_zh": item_v,
            "coverage_n": len(objs)+len(excluded),
            "screened_n": len(excluded),
            "object_cards": cards,
            "reasoning_overview": overview[:6],
            "rule_summary": rule_summary,
            "intent": rm.get("name") or rid,
            "method": method,
        }
        results.append(result)

        evid = {
            "rule_id": rid,
            "ready": True,
            "agent_id": rid,
            "verdict_zh": {"是": "是", "否": "否", "不适用": "不适用", "INSUFFICIENT": "待确认"}.get(
                item_v, "待确认"
            ),
            "object_cards": cards,
            "rule_summary": rule_summary,
            "meta": {"filled_by": method},
            "method": method,
        }
        write_json(out_dir / "evidence" / f"{rid}.json", evid)

        for c in cards:
            vz = c.get("verdict_zh")
            if vz not in {"否", "待确认"} and not (vz == "是" and False):
                if vz not in {"否", "待确认"}:
                    continue
            findings.append(
                {
                    "finding_id": f"{rid}::{c.get('object')}",
                    "rule_id": rid,
                    "object": c.get("object"),
                    "severity": rm.get("severity") or "中",
                    "verdict_zh": vz,
                    "title": rm.get("name") or rid,
                    "summary": c.get("summary") or c.get("conclusion"),
                    "action": c.get("action") or "",
                    "location_one_liner": c.get("location_one_liner") or "",
                    "reasoning_steps": analysis_texts(c),
                    "electrical": c.get("electrical") or {},
                    "location": c.get("location") or {},
                    "evidence_refs": c.get("evidence_refs") or [],
                    "context_keys": c.get("context_keys") or [],
                    "suggested_fix_hint": c.get("suggested_fix_hint"),
                }
            )

    # severity sort findings
    sev_rank = {"严重": 0, "高": 1, "中": 2, "低": 3}
    findings.sort(key=lambda f: (sev_rank.get(str(f.get("severity")), 9), f.get("rule_id"), f.get("object")))

    verdicts = {
        "rule_ids": enumeration.get("rule_ids"),
        "summary": summary,
        "source": "hybrid_program_agent",
        "agent_rule_ids": list(load_agent_rule_ids()),
        "results": results,
        "context": context_meta or {},
    }
    write_json(out_dir / "verdicts.json", verdicts)
    write_json(out_dir / "findings.json", {"findings": findings, "count": len(findings)})
    return {"verdicts": verdicts, "findings": findings}
