# -*- coding: utf-8 -*-
"""统一报告展示数据，并同步构建原有 HTML 与 Vue 所读取的证据。"""

from __future__ import annotations

import json
import copy
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent_review.batching import safe_object_name
from agent_review.p11_card_enrich import enrich_p11_card, scrub_forbidden_copy
from agent_review.presentation import business_copy
from agent_review.program_bridge import review_root
from kernel.report_text import humanize_report_text, pretty_evidence_label
from kernel.util import ROOT, read_json, write_json


def _humanize_card_copy(card: dict[str, Any]) -> dict[str, Any]:
    """展示层：摘要/卡点/证据说明去掉系统路径。"""
    out = dict(card)
    for key in ("summary", "conclusion", "calc", "phenomenon", "stuck", "action", "location_one_liner"):
        if out.get(key) is not None:
            out[key] = scrub_forbidden_copy(str(out[key]))
    refs = []
    for r in out.get("evidence_refs") or []:
        if not isinstance(r, dict):
            continue
        rr = dict(r)
        path = str(rr.get("path") or "")
        note = str(rr.get("note") or "")
        rr["label"] = pretty_evidence_label(path, note)
        rr["note"] = humanize_report_text(note)
        rr["path_display"] = humanize_report_text(path)
        refs.append(rr)
    if refs:
        out["evidence_refs"] = refs
    return business_copy(out)

ZH_TO_EN = {
    "是": "PASS",
    "否": "FAIL",
    "不适用": "NOT_APPLICABLE",
    "INSUFFICIENT": "INSUFFICIENT_EVIDENCE",
    "待确认": "INSUFFICIENT_EVIDENCE",
}


def _present_card(card: dict[str, Any]) -> dict[str, Any]:
    """Expert SPA：列表可精简；solution/工况态与 datasheet 必须保留给明细。"""
    out = dict(card)
    rid = str(out.get("rule_id") or "")
    if rid in {"P11-A01", "P11-A03"}:
        # 明细依赖 solution.states；勿裁剪 solution
        sol = out.get("solution")
        states = list(out.get("states") or [])
        appendix = {
            "all_states": states,
            "variants_ref": f"review_out/program_stress/{rid}/{safe_object_name(str(out.get('object') or ''))}.json",
            "cases_ref": f"review_out/operating_cases/{rid}/{safe_object_name(str(out.get('object') or ''))}.json",
        }
        out["appendix"] = appendix
        out["presentation"] = "concise"
        if sol is not None:
            out["solution"] = sol
        calc = str(out.get("calc") or "")
        if len(calc) > 400:
            out["calc"] = calc[:397] + "…"
    return out


def normalize_evidence(o: Path, verdicts: dict[str, Any]) -> None:
    by_id = {r["rule_id"]: r for r in verdicts.get("results") or []}
    ev = o / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    kg: dict[str, Any] = {}
    kg_path = o / "board_kg.json"
    if kg_path.is_file():
        kg = read_json(kg_path)

    from agent_review.detail_view import enabled
    wb = None
    if enabled(o):
        from kernel.report_workbench import load_workbench, pack_devices
        wb = load_workbench(o, ROOT)
    for rid, r in by_id.items():
        p = ev / f"{rid}.json"
        pack: dict[str, Any] = {}
        if p.is_file():
            try:
                pack = json.loads(p.read_text(encoding="utf-8-sig"))
            except Exception:
                pack = {}
        pack.setdefault("rule_id", rid)
        pack.setdefault("name", r.get("title") or rid)
        zh = r.get("verdict_zh") or pack.get("verdict_zh") or "待确认"
        if str(zh).startswith("INSUFFICIENT"):
            zh = "待确认"
        pack["verdict_zh"] = zh
        pack["verdict"] = ZH_TO_EN.get(str(zh), "INSUFFICIENT_EVIDENCE")
        rule_summary = str(r.get("rule_summary") or pack.get("rule_summary") or "").strip()
        if rule_summary:
            pack["rule_summary"] = rule_summary
        raw_cards = r.get("object_cards") or pack.get("object_cards") or []
        enriched = []
        for c in raw_cards:
            cc = copy.deepcopy(c)
            if not cc.get("rule_id"):
                cc["rule_id"] = rid
            if cc.get('review_v1'):
                from agent_review.review_contract import detail_view
                raw = cc['review_v1']
                obj = cc['object']; comp = (kg.get('components') or {}).get(obj) or {}
                cc['review_detail'] = detail_view(raw, o)
                cc['identity'] = {'mpn':comp.get('mpn'), 'value_pdf':comp.get('value'), 'footprint':comp.get('footprint')}
                pins = list(dict.fromkeys(p.get('net') for p in comp.get('pins',[]) if p.get('net')))
                cc['nets'] = {'a':pins[0] if pins else '', 'b':pins[1] if len(pins)>1 else ''}
                from agent_review.rated_keys import excerpt_for_mpn
                excerpt = excerpt_for_mpn(o, comp.get('mpn') or '', rid)
                cc['datasheet'] = {'mpn':comp.get('mpn'), 'local_path':str(o.parent/excerpt['datasheet_path']) if excerpt.get('datasheet_path') else '',
                                   'extracted':{p['key']:p.get('value') for p in excerpt.get('params',[]) if p.get('status')=='present'}}
            elif rid in {"P11-A01", "P11-A03"}:
                cc = enrich_p11_card(o, cc, kg)
            # Specifications belong to an exact material identity, not one check.
            # Keep excerpts separate from the raw card's adopted review values.
            from agent_review.rated_keys import excerpt_for_mpn
            comp=(kg.get('components') or {}).get(str(cc.get('object'))) or {}
            mpn=(cc.get('identity') or {}).get('mpn') or comp.get('mpn') or ''
            if mpn:
                excerpt=excerpt_for_mpn(o,mpn,rid)
                sheet=dict(cc.get('datasheet') or {})
                sheet.setdefault('mpn',mpn)
                if not sheet.get('local_path') and excerpt.get('datasheet_path'):
                    sheet['local_path']=str(o.parent/excerpt['datasheet_path'])
                params=dict(sheet.get('extracted') or {})
                for param in excerpt.get('params',[]):
                    if param.get('status')=='present':params.setdefault(param['key'],param['value'])
                sheet['extracted']=params
                cc['datasheet']=sheet
                from agent_review.verified_sources import lookup
                verified=lookup(o.parent,mpn,comp.get('manufacturer') or '')
                if verified:
                    sheet['superseded_excerpt_source']=sheet.get('local_path','')
                    sheet['local_path']=str(o.parent/verified['path'])
                    sheet['extracted']={}
                    sheet['extract_note']='已关联核实过的准确厂家规格书；旧缓存摘录未沿用，评审采用值见材料与分析。'
                    sheet['source_registration']=verified
            from agent_review.detail_view import build, enabled
            if enabled(o):
                cc['review_detail'] = build(dict(c, rule_id=rid), o, presented=cc)
                from jsonschema import Draft202012Validator
                Draft202012Validator(read_json(ROOT/'agent_review/schemas/review_detail.v2.schema.json')).validate(cc['review_detail'])
            enriched.append(_present_card(_humanize_card_copy(cc)))
        pack["object_cards"] = enriched
        if wb:
            devices = pack_devices(pack, wb)['devices']
            byobj = {d['ref']:d for d in devices}
            for cc in enriched:
                d = byobj.get(cc['object'], {})
                cc['review_detail']['resources'] = {'pages':[dict(p,pdf=p.get('pdf') or wb.get('schematic_pdf','')) for p in d.get('pages',[])], 'datasheet_pdf':d.get('datasheet',''), 'extract_rows':d.get('extract_rows',[])}
        pack["card_class"] = "stress_calc" if rid.startswith("P11") else pack.get("card_class") or ""
        pack["method"] = r.get("method") or pack.get("method") or ""
        pack.setdefault(
            "pages_checked",
            [
                {
                    "page": "原理图 PDF",
                    "status": pack.get("verdict"),
                    "note": "结合 PDF / 对象卡人工复核图面",
                    "screenshot": "",
                }
            ],
        )
        pack.setdefault("references", [{"path": "materials.json", "role": "材料盘点"}])
        if pack.get("verdict") == "FAIL" and not pack.get("remediation"):
            pack["remediation"] = pack.get("note") or "按判定说明整改后复测。"
        # 项级规格书列表：从对象卡汇总
        sheets = []
        seen = set()
        for c in enriched:
            ds = c.get("datasheet") or {}
            mpn = (c.get("identity") or {}).get("mpn") or ds.get("mpn") or ""
            key = (mpn, ds.get("local_path") or "")
            if not mpn or key in seen:
                continue
            seen.add(key)
            sheets.append(
                {
                    "mpn": mpn,
                    "title": mpn,
                    "local_path": ds.get("local_path") or "",
                    "extracted": ds.get("extracted") or {},
                    "extract_note": ds.get("extract_note") or "",
                }
            )
        if sheets:
            pack["datasheets"] = sheets
        p.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ensure_sidecars(o: Path, board: Path, materials: dict[str, Any] | None) -> None:
    mats = materials or {}
    (o / "facts").mkdir(parents=True, exist_ok=True)
    if not (o / "facts" / "board_facts.json").is_file():
        write_json(o / "facts" / "board_facts.json", {"board": mats.get("board_name") or board.name})
    if not (o / "product_playbook_used.json").is_file():
        write_json(
            o / "product_playbook_used.json",
            {
                "template": "产品中心一期",
                "item_count": len(mats.get("rule_ids") or []),
                "rule_ids": mats.get("rule_ids") or [],
                "skill": "schematic-intake-hybrid",
            },
        )


def build_expert_html(o: Path) -> Path:
    """Invoke the local vendor builder against this review_out."""
    rev = review_root()
    builder = rev / "vendor" / "expert_html" / "build_expert_html.py"
    if not builder.is_file():
        raise RuntimeError(f"expert builder missing: {builder}")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(rev)
    proc = subprocess.run(
        [sys.executable, str(builder), "--out-dir", str(o)],
        cwd=str(rev),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"build_expert_html failed: {proc.stderr[-2000:]}\n{proc.stdout[-1000:]}"
        )
    dest = o / "expert_review.html"
    if not dest.is_file():
        raise RuntimeError("expert_review.html not written")
    return dest


def _write_expert_deliverables(
    o: Path,
    board: Path,
    verdicts: dict[str, Any],
    *,
    materials: dict[str, Any] | None = None,
    report_data: dict[str, Any] | None = None,
) -> dict[str, str]:
    """写出报告数据，并据此重新生成 expert_review.html。"""
    from agent_review.detail_view import enabled
    _ensure_sidecars(o, board, materials)
    normalize_evidence(o, verdicts)
    payload = dict(report_data or {})
    from agent_review.selection_coverage import refresh
    coverage=refresh(o)
    if coverage:payload["selection_delivery"]=coverage
    payload.setdefault("board", board.name)
    payload.setdefault("summary", verdicts.get("summary"))
    if enabled(o):
        payload['review_details'] = {r['rule_id']: {c['object']: c['review_detail'] for c in read_json(o/'evidence'/(r['rule_id']+'.json')).get('object_cards', [])} for r in verdicts['results']}
    payload["deliverable"] = "report_ui"
    write_json(o / "report_data.json", payload)
    html = build_expert_html(o)
    if enabled(o):
        # Builder may make source paths portable; every entry point receives that final view.
        payload['review_details'] = {r['rule_id']: {c['object']:c['review_detail'] for c in read_json(o/'evidence'/(r['rule_id']+'.json')).get('object_cards',[])} for r in verdicts['results']}
        write_json(o/'report_data.json',payload)
    mirror = o / "engineer_review.html"
    mirror.write_bytes(html.read_bytes())
    print(f"HINT\tview with serve: python -m agent_review.serve --board {board}")
    return {
        "report_ui": str(ROOT / "report_ui"),
        "report_data.json": str(o / "report_data.json"),
        "expert_review.html": str(html),
        "engineer_review.html": str(mirror),
    }


def write_expert_deliverables(o, board, verdicts, *, materials=None, report_data=None, generate_prose=True):
    """Resolve prose before publishing; restore every previously published file on failure."""
    import shutil
    import tempfile
    from agent_review.detail_view import enabled, digest
    source_digest=digest(verdicts)
    if enabled(o) and generate_prose:
        from agent_review.detail_prose_recovery import recover
        from agent_review.detail_prose import ensure
        recover(board,verdicts)
        ensure(board, verdicts)
    names = ['evidence', 'report_data.json', 'expert_review.html', 'engineer_review.html']
    with tempfile.TemporaryDirectory(prefix='schematic-publication-') as tmp:
        backup = Path(tmp)
        existed = {name: (o/name).exists() for name in names}
        for name in names:
            src=o/name
            if src.is_dir():shutil.copytree(src, backup/name)
            elif src.exists():shutil.copy2(src, backup/name)
        try:
            result=_write_expert_deliverables(o, board, verdicts, materials=materials, report_data=report_data)
            if digest(verdicts)!=source_digest:raise RuntimeError('Report generation mutated the original review payload')
            if enabled(o):
                expected={(r['rule_id'],c['object']):c['verdict_zh'] for r in verdicts['results'] for c in r.get('object_cards',[])}
                published={(r['rule_id'],c['object']):c['verdict_zh'] for r in verdicts['results'] for c in read_json(o/'evidence'/(r['rule_id']+'.json')).get('object_cards',[])}
                if expected!=published:raise RuntimeError('Report identities or verdicts changed during publication')
            return result
        except Exception:
            for name in names:
                dest=o/name
                if dest.is_dir():shutil.rmtree(dest)
                else:dest.unlink(missing_ok=True)
                if existed[name]:
                    if (backup/name).is_dir():shutil.copytree(backup/name,dest)
                    else:shutil.copy2(backup/name,dest)
            raise
