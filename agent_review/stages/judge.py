# -*- coding: utf-8 -*-
"""Judge stage: fine batches via Codex orchestrator, or dry-run pending cards."""

from __future__ import annotations

from kernel.runtime import node_executable

import subprocess
from pathlib import Path
from typing import Any

from agent_review.batching import build_judge_jobs, safe_object_name
from agent_review.context_store import ContextStore, planned_key_id
from agent_review.defaults import DEFAULT_CONCURRENCY
from agent_review.rule_modes import is_deep_three_turn, load_agent_rule_ids
from agent_review.validate_job import validate_judge_card
from agent_review.decision_contract import validate_decision_audit, power_contract_fingerprint
from agent_review.workbench import build_judge_workbench
from kernel.util import ROOT, out_dir, read_json, write_json

_P11_STRESS = frozenset({"P11-A01", "P11-A03"})  # 有 program_stress 干跑卡


def _deps_for_object(kg: dict[str, Any], obj: str, rule_id: str) -> list[str]:
    keys: list[str] = []
    comps = kg.get("components") or {}
    if rule_id in {"P04-A01", "P04-A03", "P07-A05", "P19-A03", "P12-A02", "P21-A01", "P16-A05"}:
        keys.append(planned_key_id("net", obj))
        return keys
    keys.append(planned_key_id("object", obj))
    c = comps.get(obj) or {}
    mpn = (c.get("mpn") or "").strip()
    if mpn and mpn.upper() not in {"TBD", "CAP", "RES", "NC", "-"}:
        keys.append(planned_key_id("mpn", mpn))
    for p in c.get("pins") or []:
        if isinstance(p, dict) and p.get("net"):
            keys.append(planned_key_id("net", str(p["net"])))
    return keys


def _loc(kg: dict[str, Any], obj: str, rule_id: str) -> str:
    comps = kg.get("components") or {}
    c = comps.get(obj) or {}
    locs = c.get("location") or []
    if isinstance(locs, list) and locs:
        page = locs[0].get("pageName") or locs[0].get("pdf_page")
        return f"{page} · {obj}"
    if rule_id.startswith("P04") or rule_id in {"P07-A05", "P19-A03", "P21-A01"}:
        return f"网络 {obj}"
    return obj


def _fmt_mw(p: Any) -> str:
    if p is None:
        return "?"
    try:
        return f"{float(p) * 1000:.1f} mW"
    except (TypeError, ValueError):
        return "?"


def _p11_dry_run_card(
    board: Path,
    rule_id: str,
    obj: str,
    kg: dict[str, Any],
    bundle_hashes: dict[str, str],
) -> dict[str, Any]:
    """Build concise card from sealed operating_cases + program_stress (no Agent invent)."""
    o = out_dir(board)
    sn = safe_object_name(obj)
    cases = {}
    stress = {}
    cp = o / "operating_cases" / rule_id / f"{sn}.json"
    sp = o / "program_stress" / rule_id / f"{sn}.json"
    if cp.is_file():
        cases = read_json(cp)
    if sp.is_file():
        stress = read_json(sp)
    path_text = (cases.get("path") or {}).get("text") or stress.get("path_text") or obj
    stuck_parts = [x for x in [cases.get("stuck"), stress.get("stuck")] if x]
    unmapped_cases = [
        c
        for c in (cases.get("cases") or [])
        if isinstance(c, dict) and c.get("status") == "unmapped"
    ]
    unmapped_states = [
        s
        for s in (stress.get("states") or [])
        if isinstance(s, dict) and (s.get("status") == "unmapped" or s.get("role") == "unmapped")
    ]
    has_unmapped = bool(unmapped_cases or unmapped_states or int(stress.get("unmapped_n") or 0))
    incomplete = (
        (cases.get("completeness") or "") == "incomplete"
        or has_unmapped
        or any(s.get("role") == "missing_numeric" for s in (stress.get("states") or []))
    )
    if has_unmapped:
        for c in unmapped_cases:
            miss = c.get("missing") or []
            stuck_parts.append(
                f"未建模 {c.get('case_id')}: {c.get('stuck') or '缺模型'}"
                + (f"（缺：{'/'.join(str(x) for x in miss)}）" if miss else "")
            )

    states_out: list[dict[str, Any]] = []
    calc_lines: list[str] = []
    if rule_id == "P11-A01":
        pr = stress.get("P_rated")
        limit = stress.get("P_limit")
        p_max = stress.get("P_max")
        for s in stress.get("states") or []:
            st_status = s.get("status") or ""
            states_out.append(
                {
                    "case_id": s.get("case_id"),
                    "name_zh": s.get("name_zh"),
                    "P": None if st_status == "unmapped" else s.get("P"),
                    "I": None if st_status == "unmapped" else s.get("I"),
                    "role": s.get("role"),
                    "status": st_status or None,
                    "stuck": s.get("stuck") or "",
                    "missing": list(s.get("missing") or []),
                    "formula": s.get("formula") or "",
                }
            )
            role = s.get("role") or ""
            if st_status == "unmapped" or role == "unmapped":
                miss = s.get("missing") or []
                calc_lines.append(
                    f"{s.get('name_zh')}: 未建模"
                    + (f"（缺：{'/'.join(str(x) for x in miss)}）" if miss else "")
                )
            elif role in {"workpoint", "worst", "active_pull", "idle"}:
                calc_lines.append(f"{s.get('name_zh')}: P={_fmt_mw(s.get('P'))}")
        # keep ≤6 key lines (含 unmapped 展示)
        if len(calc_lines) > 6:
            calc_lines = calc_lines[:6]
        if not calc_lines:
            calc_lines = ["应力包无可用态"]
        # 校验要求 calc 含功耗痕迹；未建模/空态时补一行
        if not any(("P=" in x) or ("功耗" in x) or ("mW" in x) for x in calc_lines):
            if p_max is not None:
                calc_lines.append(f"P_max={_fmt_mw(p_max)}")
            else:
                calc_lines.append("功耗未闭合（程序参考）")
        if pr is None:
            vz = "待确认"
            stuck_parts.append("缺额定")
            summary = f"{obj} 缺 P_rated，待确认"
            action = "补全电阻额定功率后再判定"
            conclusion = "待确认（缺额定）"
        elif has_unmapped:
            vz = "待确认"
            summary = f"{obj} 含未建模工况，不得升绿"
            action = "补齐程序算数模型或闭合输入后重跑"
            conclusion = "待确认（未建模）"
        elif incomplete and p_max is None:
            vz = "待确认"
            summary = f"{obj} 工况未闭合，待确认"
            action = "闭合通路/工况数字后再判定"
            conclusion = "待确认（工况未闭合）"
        else:
            over_limit = p_max is not None and limit is not None and float(p_max) > float(limit)
            over_rated = p_max is not None and pr is not None and float(p_max) > float(pr)
            if over_limit or over_rated:
                vz = "否"
                summary = (
                    f"最坏 {_fmt_mw(p_max)}，限额 {_fmt_mw(limit)}，额定 {_fmt_mw(pr)} → 否"
                )
                action = "提高额定/分压降耗/限制拉低工况后再复核"
                conclusion = "超 0.5×P_rated" if over_limit else "超 P_rated"
            else:
                vz = "是"
                summary = f"最坏 {_fmt_mw(p_max)} ≤ 限额 {_fmt_mw(limit)} → 是"
                action = "保持现设计"
                conclusion = "满足 0.5×P_rated"
        elec = {
            "P_rated": pr,
            "P_limit": limit,
            "P_max": p_max,
            "P_max_case": stress.get("P_max_case"),
            "cite": stress.get("cite"),
        }
    else:
        vr = stress.get("V_rated")
        limit = stress.get("V_limit")
        v_max = stress.get("V_max")
        for s in stress.get("states") or []:
            states_out.append(
                {
                    "case_id": s.get("case_id"),
                    "name_zh": s.get("name_zh"),
                    "V_across": s.get("V_across"),
                    "role": s.get("role"),
                    "formula": s.get("formula") or "",
                }
            )
            if s.get("role") in {"workpoint", "idle"} or len(calc_lines) < 4:
                calc_lines.append(f"{s.get('name_zh')}: V={s.get('V_across')}")
        if len(calc_lines) > 4:
            calc_lines = calc_lines[:4]
        if not calc_lines:
            calc_lines = ["应力包无可用态"]
        if not any(("V=" in x) or ("V_max" in x) or ("电压" in x) or ("耐压" in x) for x in calc_lines):
            if v_max is not None:
                calc_lines.append(f"V_max={v_max} V")
            else:
                calc_lines.append("电压未闭合（程序参考）")
        if vr is None:
            vz = "待确认"
            stuck_parts.append("缺额定")
            summary = f"{obj} 缺 V_rated，待确认"
            action = "补全电容耐压后再判定"
            conclusion = "待确认（缺额定）"
        elif incomplete and v_max is None:
            vz = "待确认"
            summary = f"{obj} 工况未闭合，待确认"
            action = "闭合电容工况后再判定"
            conclusion = "待确认（工况未闭合）"
        else:
            over = v_max is not None and limit is not None and float(v_max) > float(limit)
            if over:
                vz = "否"
                summary = f"V_max={v_max} V，限额 {limit} V → 否"
                action = "提高耐压或降低工作电压后再复核"
                conclusion = "超 0.5×V_rated"
            else:
                vz = "是"
                summary = f"V_max={v_max} V ≤ 限额 {limit} V → 是"
                action = "保持现设计"
                conclusion = "满足 0.5×V_rated"
        elec = {
            "V_rated": vr,
            "V_limit": limit,
            "V_max": v_max,
            "cite": stress.get("cite"),
        }

    # ensure states align to cases if stress missing some
    if not states_out and cases.get("cases"):
        for c in cases["cases"]:
            states_out.append({"case_id": c.get("case_id"), "name_zh": c.get("name_zh"), "role": "missing_numeric"})

    return {
        "rule_id": rule_id,
        "object": obj,
        "verdict_zh": vz,
        "location_one_liner": _loc(kg, obj, rule_id),
        "reasoning_steps": [
            f"dry-run：读取程序参考包 {obj}（非 Agent 审图）",
            "对照 0.5×额定；正式评审应由 Agent 按工作台自列工况",
            "本卡数字仅程序参考，不代表单聊级推理",
        ],
        "summary": summary,
        "action": action,
        "phenomenon": path_text,
        "calc": "；".join(calc_lines),
        "stuck": "；".join(stuck_parts) if stuck_parts else "无",
        "conclusion": conclusion,
        "evidence_refs": [
            {"path": f"review_out/operating_cases/{rule_id}/{sn}.json", "note": "program_reference"},
            {"path": f"review_out/program_stress/{rule_id}/{sn}.json", "note": "program_reference"},
        ],
        "context_keys": list(bundle_hashes.keys()),
        "context_hashes": bundle_hashes,
        "electrical": elec,
        "states": states_out,
        "meta": {"dry_run": True, "from_program_stress": True, "program_reference_only": True},
    }


def _dry_run_card(
    rule_id: str,
    obj: str,
    kg: dict[str, Any],
    bundle_hashes: dict[str, str],
    meta: dict[str, Any],
    board: Path | None = None,
) -> dict[str, Any]:
    if rule_id in _P11_STRESS and board is not None:
        return _p11_dry_run_card(board, rule_id, obj, kg, bundle_hashes)
    title = (meta.get(rule_id) or {}).get("name") or rule_id
    return {
        "rule_id": rule_id,
        "object": obj,
        "verdict_zh": "待确认",
        "location_one_liner": _loc(kg, obj, rule_id),
        "reasoning_steps": [
            f"读取密封上下文中的对象/网络 {obj}",
            f"套用检查项 {rule_id}（{title}）判据",
            "程序预审未调用 Agent，结论保持待确认",
        ],
        "summary": f"{obj} 等待 Agent 判定",
        "action": "由 Codex Agent 重跑本对象",
        "phenomenon": f"已枚举对象 {obj}，上下文 hash 已钉死",
        "calc": "未计算（等待 Agent）",
        "stuck": "等待 Agent Judge",
        "conclusion": "待确认",
        "evidence_refs": [{"path": "review_out/context_store/manifest.json", "note": "sealed"}],
        "context_keys": list(bundle_hashes.keys()),
        "context_hashes": bundle_hashes,
        "electrical": {},
        "meta": {"dry_run": True},
    }


def _interrupted_card(rule_id: str, obj: str, kg: dict[str, Any]) -> dict[str, Any]:
    """分析稿在、判定卡不在：进报告为待确认，不编电气数字。"""
    loc = _loc(kg, obj, rule_id)
    rel = f"review_out/agent_jobs/{rule_id}/{safe_object_name(obj)}.run.jsonl"
    stuck = f"深审未完成落卡。过程见 {rel}"
    headline = f"{obj}：本轮未完成落卡，没有可核对的额定对照，不能按通过。"
    if len(headline) > 90:
        headline = f"{obj}：未完成落卡，不能按通过。"
    card: dict[str, Any] = {
        "rule_id": rule_id,
        "object": obj,
        "verdict_zh": "待确认",
        "location_one_liner": loc,
        "headline_zh": headline,
        "reasoning_steps": [
            f"已开始审 {obj}，分析稿已写入",
            "落判定卡之前会话被中断",
            "缺少完成的额定对照，不能判通过",
        ],
        "summary": f"{obj} 深审未完成，分析稿在、判定卡未形成。",
        "action": "按运行日志续跑该对象的深审",
        "phenomenon": f"{obj} 的深审停在落卡之前，通路数字尚未写入判定卡。",
        "calc": "功耗/电压未完成代入",
        "stuck": stuck,
        "conclusion": "待确认",
        "evidence_refs": [{"path": rel, "note": "本轮运行日志"}],
        "electrical": {},
    }
    if rule_id in {"P11-A01", "P11-A03"}:
        card["states"] = [
            {
                "name_zh": f"{obj} 本轮未形成可核对的稳态通路",
                "结论": "待确认",
            }
        ]
        if rule_id == "P11-A01":
            card["calc"] = "功耗未完成代入"
        else:
            card["calc"] = "电压未完成代入"
    return card


def _downgrade_invalid_card(card: dict[str, Any], errs: list[str]) -> dict[str, Any]:
    """校验失败的卡不能继续显示为通过。"""
    out = dict(card)
    obj = str(out.get("object") or "")
    out["verdict_zh"] = "待确认"
    note = "；".join(str(e) for e in errs[:4])
    prev = str(out.get("stuck") or "").strip()
    out["stuck"] = (prev + "；" if prev and prev != "无" else "") + f"校验未过：{note}"
    if not str(out.get("summary") or "").strip():
        out["summary"] = f"{obj} 判定卡未通过校验，不能按通过。"
    if not str(out.get("action") or "").strip():
        out["action"] = "按校验错误补齐分析后重跑该对象"
    hl = str(out.get("headline_zh") or "").strip()
    if not hl or ("：" not in hl and ":" not in hl):
        out["headline_zh"] = f"{obj}：判定卡校验未过，不能按通过。"
    if len(str(out.get("headline_zh") or "")) > 90:
        out["headline_zh"] = f"{obj}：校验未过，不能按通过。"
    return out


def _clear_agent_job_dirs(
    o: Path,
    rule_ids: list[str],
    *,
    objects_by_rule: dict[str, list[str]] | None = None,
) -> None:
    """只清失败标记。判定卡留到新卡校验通过后再替换，避免整批失败把旧卡删光。"""
    for rid in rule_ids:
        d = o / "agent_jobs" / rid
        if not d.is_dir():
            continue
        scoped = None
        if objects_by_rule is not None and rid in objects_by_rule:
            scoped = {safe_object_name(x) for x in (objects_by_rule.get(rid) or [])}
        for p in d.iterdir():
            if not p.is_file():
                continue
            if not (p.name.endswith(".failed.json") or p.name.endswith(".invalid.json")):
                continue
            stem = p.name.split(".", 1)[0]
            if scoped is not None and stem not in scoped:
                continue
            try:
                p.unlink()
            except OSError:
                pass


def reusable_card(board: Path, kg: dict[str, Any], store: ContextStore, rid: str, obj: str) -> bool:
    """Resume only a valid card tied to the current sealed facts."""
    p = out_dir(board) / "agent_jobs" / rid / f"{safe_object_name(obj)}.json"
    try:
        if p.with_suffix(".invalid.json").exists() or p.with_suffix(".failed.json").exists():
            return False
        card = read_json(p)
        from agent_review.execution_policy import execution_hash
        wb_path = p.with_suffix('.context.json')
        if not wb_path.is_file() or not getattr(store,'execution_material_hash',None):
            return False
        expected = execution_hash(rid, read_json(wb_path), store.execution_material_hash)
        if (card.get('_meta',{}).get('execution',{}) if card.get('schema_version')=='review.v1' else card.get('_execution',{})).get('fingerprint') != expected:
            return False
        if card.get('schema_version')!='review.v1':
            from agent_review.execution_policy import digest
            if card.get('_execution',{}).get('output_hash')!=digest({k:v for k,v in card.items() if k!='_execution'}):
                return False
        from agent_review.review_contract import is_v1, fingerprint, evidence_hashes
        v1 = is_v1(card)
        registry_path = out_dir(board) / 'review_contracts.json'
        if registry_path.is_file() and read_json(registry_path).get(rid,{}).get(obj)=='review.v1' and not v1:
            return False
        if v1 and (card.get("_meta") or {}).get("contract_hash") != fingerprint():
            return False
        if v1 and (card.get('_meta') or {}).get('evidence_hashes') != evidence_hashes(card,board):
            return False
        if not v1 and rid == "P11-A01" and validate_decision_audit(card, required=True):
            return False
        if not v1 and rid == "P11-A01" and card.get("decision_contract_hash") != power_contract_fingerprint():
            return False
        if any((card.get("meta") or {}).get(k) for k in ("dry_run", "deferred", "batch_skip", "interrupted")):
            return False
        if validate_judge_card(card, rule_id=rid, obj=obj, board=board):
            return False
        execution=card.get('_meta',{}).get('execution',{}) if v1 else card.get('_execution',{})
        if execution.get('evidence_version'):
            from agent_review.evidence_loop import validate_object_research,sha as evidence_sha
            research=p.with_name(safe_object_name(obj)+'.research.json')
            if not research.is_file() or evidence_sha(research)!=execution.get('research_hash') or validate_object_research(board,rid,obj,card):return False
            if any(not (board/rel).is_file() or evidence_sha(board/rel)!=h for rel,h in execution.get('research_sources',{}).items()):return False
        bundle = store.fetch(_deps_for_object(kg, obj, rid), require_sealed=True, require_ready=True)
        hashes = {k: v.get("content_hash") for k, v in (bundle.get("entries") or {}).items()}
        return bool(hashes) and ((card.get("_meta") or {}).get("context_hashes") if v1 else card.get("context_hashes")) == hashes
    except (OSError, ValueError, TypeError, RuntimeError, KeyError):
        return False


def contract_feedback(out: Path, rid: str, obj: str) -> list[str]:
    """Return validation and current-source geometry feedback to model repair."""
    card_path=out/'agent_jobs'/rid/f'{safe_object_name(obj)}.json'
    invalid=card_path.with_suffix('.invalid.json')
    errors=list(read_json(invalid).get('errors',[])) if invalid.is_file() else []
    if not card_path.is_file():return errors
    from agent_review.review_contract import is_v1, source_hash
    card=read_json(card_path)
    if not is_v1(card):return errors
    primary=card.get('extreme',{}).get('primary_scenario_ref')
    if not primary:return errors
    meta_path=out/'stress_paths'/rid/safe_object_name(obj)/primary/'meta.json'
    if meta_path.is_file():
        meta=read_json(meta_path)
        if meta.get('review_source_hash')==source_hash(card) and not meta.get('routing_complete'):
            for page in meta.get('pages',[]):
                for issue in page.get('routing_issues',[]):
                    errors.append(f"route page {page.get('pdf_page')}: {issue}. Correct page/route/marker from the source schematic; do not invent coordinates or bypass geometry validation.")
    return errors


def finalize_card(board: Path, kg: dict[str, Any], rid: str, obj: str) -> list[str]:
    """Validate model output, then attach controller-owned context provenance."""
    p = out_dir(board) / "agent_jobs" / rid / f"{safe_object_name(obj)}.json"
    card = read_json(p)
    errors = validate_judge_card(card, rule_id=rid, obj=obj, board=board)
    from agent_review.review_contract import is_v1, fingerprint, evidence_hashes, source_hash
    if rid == "P11-A01" and not is_v1(card):
        errors.extend(validate_decision_audit(card, required=True))
    if errors:
        return errors
    # Only a completed model run may receive current execution provenance.
    jobs_path = out_dir(board)/'judge_jobs.json'
    run_path = out_dir(board)/'sdk_judge_result.json'
    jobs = read_json(jobs_path).get('jobs',[]) if jobs_path.is_file() else []
    runs = read_json(run_path).get('results',[]) if run_path.is_file() else []
    job = next((j for j in jobs if j['rule_id']==rid and obj in j.get('objects',[])),None)
    current = read_json(jobs_path).get('attempt_id') if jobs_path.is_file() else None
    result_attempt = read_json(run_path).get('attempt_id') if run_path.is_file() else None
    completed = next((r for r in runs if current and current==result_attempt and job and r.get('job_id')==job['job_id'] and r.get('ok')),None)
    if not completed:
        return ['no successful current model run for this object']
    execution={'fingerprint':job['execution_hash'],'run_id':completed.get('run'),
               'self_check':job.get('self_check',True)}
    if job.get('autonomous_evidence'):
        from agent_review.evidence_loop import validate_object_research,sha as evidence_sha
        research=p.with_name(safe_object_name(obj)+'.research.json')
        research_errors=validate_object_research(board,rid,obj,card)
        if research_errors:return research_errors
        receipt=read_json(research)
        execution.update(evidence_version='evidence.v1',research_hash=evidence_sha(research),
                         research_sources={ev['path']:evidence_sha(board/ev['path']) for f in receipt['facts'] for ev in f['evidence']})
    invalid_path = p.with_suffix(".invalid.json")
    if invalid_path.is_file():
        prior = read_json(invalid_path)
        diagnostic = "校验未过：" + "；".join(str(e) for e in (prior.get("errors") or [])[:4])
        stuck = str(card.get("stuck") or "")
        if stuck.endswith(diagnostic):
            # Remove only the obsolete controller suffix, never review evidence.
            card["stuck"] = stuck[:-len(diagnostic)].rstrip("；")
    bundle = ContextStore(out_dir(board)).fetch(_deps_for_object(kg, obj, rid))
    hashes = {key: entry.get("content_hash") for key, entry in bundle["entries"].items()}
    if is_v1(card):
        import datetime
        analysis = p.with_suffix(".analysis.md")
        import hashlib
        card["_meta"] = {"execution":execution,"contract_hash": fingerprint(), "context_hashes": hashes,
                         "source_hash":source_hash(card),
                         "evidence_hashes":evidence_hashes(card,board),
                         "validation_status": "valid", "validated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                         "analysis_sha256": hashlib.sha256(analysis.read_bytes()).hexdigest() if analysis.is_file() else None}
        from agent_review.review_contract import SCHEMA_PATH
        card['_meta']['schema_hash'] = hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()
        card['_meta']['prompt_hashes'] = {str(x.relative_to(ROOT)):hashlib.sha256(x.read_bytes()).hexdigest()
                                        for x in [ROOT/'agent_review/prompts/judge/P11-A01.analysis.md',
                                                  *sorted((ROOT/'agent_review/prompts/judge').glob('review_v1*.md'))]}
        run_path = out_dir(board)/'sdk_judge_result.json'
        runs = read_json(run_path).get('results',[]) if run_path.is_file() else []
        card['_meta']['run_id'] = next((r.get('run') for r in runs if obj in str(r.get('job_id','')).split('::')),None)
        import os
        card['_meta']['model_requested'] = os.environ.get('AGENT_JUDGE_MODEL','auto')
        write_json(p, card)
        for suffix in (".invalid.json", ".failed.json"):
            p.with_suffix(suffix).unlink(missing_ok=True)
        return []
    card["context_hashes"] = hashes
    card['_execution'] = execution
    card["context_keys"] = list(hashes)
    if rid == "P11-A01":
        card["decision_contract_hash"] = power_contract_fingerprint()
    from agent_review.execution_policy import digest
    card['_execution']['output_hash']=digest({k:v for k,v in card.items() if k!='_execution'})
    write_json(p, card)
    for suffix in (".invalid.json", ".failed.json"):
        p.with_suffix(suffix).unlink(missing_ok=True)
    return []


def run(
    board: Path,
    kg: dict[str, Any],
    enumeration: dict[str, Any],
    *,
    dry_run: bool = False,
    concurrency: int = DEFAULT_CONCURRENCY,
    resume: bool = False,
    critique: bool = False,
    benchmark_three_turn: bool = False,
    autonomous_evidence: bool = True,
    research_prepared: bool = False,
) -> dict[str, Any]:
    o = out_dir(board)
    store = ContextStore(o)
    from agent_review.execution_policy import material_hash, execution_hash, checkpoint, two_turn
    jobs = build_judge_jobs(enumeration)
    registry_path = o / "review_contracts.json"
    registry = read_json(registry_path) if registry_path.is_file() else {}
    split_jobs = []
    for job in jobs:
        legacy = [obj for obj in job.get("objects", []) if registry.get(job['rule_id'], {}).get(obj) != 'review.v1']
        if legacy or job.get('empty_rule'):
            split_jobs.append({**job, 'objects': legacy})
        for obj in job.get('objects', []):
            if registry.get(job['rule_id'], {}).get(obj) == 'review.v1':
                split_jobs.append({**job, 'objects': [obj], 'job_id': job['job_id']+'::'+obj+'::v1', 'contract_version': 'review.v1'})
    jobs = split_jobs
    delivery_config=board/'selection_delivery.json'
    if delivery_config.is_file():
        priority=read_json(delivery_config).get('priority_pairs',[])
        ranks={pair:i for i,pair in enumerate(priority)}
        jobs.sort(key=lambda j:ranks.get(j['rule_id']+'::'+(j.get('objects') or [''])[0],len(ranks)))
    reused = set()

    if not dry_run and autonomous_evidence:
        from agent_review.evidence_loop import run_research,scoped_pairs
        if not research_prepared and scoped_pairs(enumeration):
            run_research(board,enumeration)
        print('OK\tautonomous_evidence material lookup is agent-directed; fixed-key enrichment skipped',flush=True)
    if not dry_run and not autonomous_evidence:
        from agent_review.rated_keys import ensure_rule_params

        try:
            filled = ensure_rule_params(board, kg, enumeration)
            print(
                f"OK\trated_params jobs={filled.get('jobs')} merged={filled.get('merged')} "
                f"skipped={filled.get('skipped')}"
            )
        except Exception as e:
            print(f"WARN\trated_params {e}")
    store.execution_material_hash = material_hash(board) if not dry_run else None

    agent_rids = [r for r in (enumeration.get("rule_ids") or []) if r in set(load_agent_rule_ids())]
    by_rule = enumeration.get("by_rule") or {}
    # 全量枚举时仍整目录清空；scoped（by_rule 对象远少于板上全集）只清本批
    enum_full_path = o / "agent_enumeration.json"
    scoped_clear: dict[str, list[str]] | None = None
    if enum_full_path.is_file():
        try:
            full_by = (read_json(enum_full_path).get("by_rule") or {})
            for rid in agent_rids:
                full_n = len(full_by.get(rid) or [])
                cur_n = len(by_rule.get(rid) or [])
                if full_n > 0 and cur_n > 0 and cur_n < full_n:
                    scoped_clear = {r: list(by_rule.get(r) or []) for r in agent_rids}
                    break
        except Exception:
            scoped_clear = None
    # critique=难例续聊：保留已有 analysis/json；resume=缺卡才跑
    if not resume and not critique:
        _clear_agent_job_dirs(o, agent_rids, objects_by_rule=scoped_clear)

    # 编排前写好工作台 / deps，供 Agent 首读
    preflight_fail: set[tuple[str, str]] = set()
    for job in jobs:
        if job.get("empty_rule"):
            continue
        rid = job["rule_id"]
        for obj in job.get("objects") or []:
            out_p = o / "agent_jobs" / rid / f"{safe_object_name(obj)}.json"
            deps = _deps_for_object(kg, obj, rid)
            try:
                bundle = store.fetch(deps, require_sealed=True, require_ready=True)
                hashes = {k: v.get("content_hash") for k, v in (bundle.get("entries") or {}).items()}
            except Exception as e:
                hashes = {}
                if not dry_run:
                    preflight_fail.add((rid, obj))
                    write_json(
                        out_p.with_suffix(".failed.json"),
                        {"object": obj, "rule_id": rid, "error": str(e)},
                    )
                    continue
            slice_path = o / "agent_jobs" / rid / f"{safe_object_name(obj)}.context.json"
            try:
                if is_deep_three_turn(rid):
                    wb = build_judge_workbench(
                        board, kg, obj, rid, deps=deps, hashes=hashes
                    )
                    if wb.get('main_analysis_contract') or wb.get('available_main_analysis_contract'):
                        from agent_review.main_analysis import SCHEMA_PATH as MAIN_SCHEMA
                        write_json(slice_path.with_name(slice_path.name.replace('.context.json','.main.schema.json')),read_json(MAIN_SCHEMA))
                    if job.get('contract_version') == 'review.v1':
                        from agent_review.review_contract import task_header, SCHEMA_PATH
                        wb['contract_task'] = task_header(wb)
                        if critique:
                            feedback=contract_feedback(o,rid,obj)
                            if feedback:wb['contract_validation_errors']=feedback
                        write_json(slice_path.with_name(slice_path.name.replace('.context.json','.schema.json')), read_json(SCHEMA_PATH))
                    if autonomous_evidence and not dry_run:
                        from agent_review.evidence_loop import SCHEMA as EVIDENCE_SCHEMA,sha as evidence_sha
                        evidence_file=o/'evidence_store/current.json'
                        evidence_version=read_json(evidence_file)['_provenance']['version_path']
                        wb['autonomous_evidence']={'path':evidence_version,'sha256':evidence_sha(board/evidence_version)}
                        job['autonomous_evidence']=True
                        write_json(out_p.with_name(safe_object_name(obj)+'.research.schema.json'),read_json(EVIDENCE_SCHEMA))
                    if wb.get("delivery_policy"):
                        job["delivery_version"] = wb["delivery_policy"]["version"]
                    write_json(slice_path, wb)
                    if not dry_run:
                        job['execution_hash'] = execution_hash(rid,wb,store.execution_material_hash)
                        job['self_check'] = benchmark_three_turn or not two_turn(rid)
                        if resume and not critique and reusable_card(board,kg,store,rid,obj):
                            reused.add((rid,obj))
                        if resume and not critique:
                            recovered = checkpoint(board,rid,obj,job['execution_hash'])
                            if recovered:
                                job['checkpoint_root'] = recovered['root']
                                job['resume_stage'] = recovered['stage']
                elif hashes:
                    write_json(slice_path, {"deps": deps, "hashes": hashes})
            except Exception as exc:
                if job.get('contract_version') == 'review.v1':
                    preflight_fail.add((rid,obj))
                    write_json(out_p.with_suffix('.failed.json'), {'error':str(exc), 'phase':'contract_preflight'})

    dispatch = []
    for job in jobs:
        if job.get("empty_rule"):
            continue
        objects = [obj for obj in job.get("objects", [])
                   if (job["rule_id"], obj) not in reused
                   and (job["rule_id"], obj) not in preflight_fail]
        if objects:
            dispatch.append({**job, "objects": objects})
    import uuid
    attempt_id=uuid.uuid4().hex
    write_json(o / "judge_jobs.json", {"jobs": dispatch, "concurrency": concurrency, "reused_n": len(reused),"attempt_id":attempt_id})

    if not dry_run and dispatch:
        import os

        orch = ROOT / "agent_review" / "orchestrator.mjs"
        # AGENT_JUDGE_MODEL overrides the user-configured Codex model.
        model = os.environ.get("AGENT_JUDGE_MODEL", "auto")
        cmd = [
            node_executable(),
            str(orch),
            "--board",
            str(board),
            "--mode",
            "critique" if critique else "judge",
            "--concurrency",
            str(concurrency),
            "--model",
            model,
        ]
        if resume and not critique:
            cmd.append("--resume")

        env = os.environ.copy()
        import sys
        env.setdefault("SCHEMATIC_PYTHON", sys.executable)
        proc = subprocess.run(cmd, cwd=str(ROOT), env=env)
        if proc.returncode not in (0, 2):
            raise RuntimeError(f"orchestrator failed code={proc.returncode}")

    done = 0
    failed = 0
    material_changed = not dry_run and bool(dispatch) and material_hash(board)!=store.execution_material_hash
    meta = enumeration.get("meta") or {}
    for job in jobs:
        if job.get("empty_rule"):
            continue
        rid = job["rule_id"]
        for obj in job.get("objects") or []:
            out_p = o / "agent_jobs" / rid / f"{safe_object_name(obj)}.json"
            if material_changed:
                failed += 1
                write_json(out_p.with_suffix('.failed.json'),{'error':'materials changed during model execution; result requires rerun'})
                continue
            if (rid, obj) in reused:
                done += 1
                continue
            if (rid, obj) in preflight_fail:
                failed += 1
                continue
            deps = _deps_for_object(kg, obj, rid)
            try:
                bundle = store.fetch(deps, require_sealed=True, require_ready=True)
                hashes = {k: v.get("content_hash") for k, v in (bundle.get("entries") or {}).items()}
            except Exception:
                hashes = {}

            if dry_run:
                card = _dry_run_card(rid, obj, kg, hashes, meta, board=board)
                errs = validate_judge_card(card, rule_id=rid, obj=obj, board=board)
                if errs:
                    failed += 1
                    write_json(out_p.with_suffix(".invalid.json"), {"errors": errs, "card": card})
                else:
                    write_json(out_p, card)
                    done += 1
                continue

            if not out_p.is_file():
                failed += 1
                write_json(
                    out_p.with_suffix(".failed.json"),
                    {"object": obj, "rule_id": rid, "error": "orchestrator did not write card"},
                )
                continue
            card = read_json(out_p)
            run_results = read_json(o/'sdk_judge_result.json').get('results',[]) if (o/'sdk_judge_result.json').is_file() else []
            if not (o/'sdk_judge_result.json').is_file() or read_json(o/'sdk_judge_result.json').get('attempt_id')!=attempt_id or not any(r.get('job_id')==job['job_id'] and r.get('ok') for r in run_results):
                failed += 1
                write_json(out_p.with_suffix('.failed.json'),{'error':'current model run failed; prior card is not a fresh result'})
                continue
            if job.get('contract_version') == 'review.v1':
                from agent_review.review_contract import is_v1
                errs = validate_judge_card(card, rule_id=rid, obj=obj, board=board) if is_v1(card) else ['review.v1 output required']
                if errs:
                    failed += 1
                    write_json(out_p.with_suffix('.invalid.json'), {'errors':errs,'card':card})
                else:
                    final_errors = finalize_card(board, kg, rid, obj)
                    if final_errors:
                        failed += 1
                        write_json(out_p.with_suffix('.invalid.json'), {'errors':final_errors,'card':card})
                    else:
                        done += 1
                continue
            # Agent 常漏写 stuck；空串按「无」归一，避免四格校验误杀
            dirty = False
            if not str(card.get("stuck") or "").strip():
                card["stuck"] = "无"
                dirty = True
            # P11-A01：统一 P_w → P，便于校验与 enrich
            if rid == "P11-A01":
                for st in card.get("states") or []:
                    if not isinstance(st, dict):
                        continue
                    if st.get("P") is None and st.get("P_w") is not None:
                        st["P"] = st.get("P_w")
                        dirty = True
            if dirty:
                write_json(out_p, card)
            errs = validate_judge_card(card, rule_id=rid, obj=obj, board=board)
            if rid == "P11-A01":
                errs.extend(validate_decision_audit(card, required=True))
            if errs:
                # A contract error is an execution failure, not a business
                # decision. Preserve the model output and block publication.
                failed += 1
                write_json(out_p.with_suffix(".invalid.json"), {"errors": errs, "card": card})
            else:
                final_errors=finalize_card(board, kg, rid, obj)
                if final_errors:
                    failed+=1;write_json(out_p.with_suffix('.invalid.json'),{'errors':final_errors,'card':card})
                else:done += 1

    result = {"done": done, "failed": failed, "dry_run": dry_run, "jobs": len(jobs),"reused":len(reused),"dispatched":len(dispatch)}
    write_json(o / "judge_result.json", result)
    return result
