# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_review.aggregate import aggregate
from agent_review.expert_report import write_expert_deliverables
from agent_review.report import build_report_data
from kernel.util import out_dir, read_json


def run(
    board: Path,
    enumeration: dict[str, Any],
    *,
    materials: dict[str, Any] | None = None,
    context_meta: dict[str, Any] | None = None,
    generate_prose: bool = True,
    figure_rule_ids: list[str] | None = None,
    figure_objects: list[str] | None = None,
) -> dict[str, Any]:
    o = out_dir(board)
    # Regenerate only explicitly opted-in summaries when their review or prompt changed.
    from agent_review.remediation import run_enabled
    if generate_prose:
        run_enabled(board,rule_ids=figure_rule_ids,objects=figure_objects)
    # 出图读 agent_jobs，必须在汇总写报告之前；已有图则按指纹跳过。
    from agent_review.stress_figures import run as run_stress_figures

    run_stress_figures(board, rule_ids=figure_rule_ids, objects=figure_objects)
    # A new-contract card cannot publish an unverified or stale route image.
    from agent_review.review_contract import is_v1, source_hash
    for path in (o / 'agent_jobs').glob('*/*.json'):
        if path.name.endswith(('.context.json','.schema.json','.failed.json','.invalid.json')):
            continue
        if figure_objects and path.stem not in figure_objects:
            continue
        card = read_json(path)
        from agent_review.main_analysis import pilot_enabled
        if pilot_enabled(board,str(card.get('rule_id')),str(card.get('object'))) and not card.get('main_analysis'):
            raise RuntimeError('decision-bound pilot has no formal-basis reference')
        if card.get('main_analysis'):
            from agent_review.main_analysis import validate as validate_main
            from agent_review.detail_view import basis_source_hash
            errors=validate_main(card)
            if errors:raise RuntimeError('main-analysis contract invalid: '+'; '.join(errors))
            main=card['main_analysis'];idx=main['state_index']
            if main['figure'] and idx is not None:
                from agent_review.stress_figures import _slug
                obj=card['object'];rid=card['rule_id'];slug=_slug(card['states'][idx],idx)
                meta_path=o/'stress_paths'/rid/obj/slug/'meta.json'
                if not meta_path.exists() or not (meta_path.parent/'flow.png').exists():raise RuntimeError('decision-basis diagram missing')
                meta=read_json(meta_path)
                if meta.get('basis_source_hash')!=basis_source_hash(card) or meta.get('basis_state_index')!=idx or meta.get('basis_ref')!='/decision_basis':
                    raise RuntimeError('decision-basis diagram is stale or references a different basis')
                if main['figure']['kind']=='cross_case_relations' and (not meta.get('relation_diagram') or any(p.get('arrows_enabled',True) for p in meta.get('pages',[]))):
                    raise RuntimeError('bound must not publish an instantaneous operating diagram')
        if is_v1(card):
            primary = card['extreme']['primary_scenario_ref']
            if primary:
                meta_path = o/'stress_paths'/card['target']['rule_id']/card['target']['object_id']/primary/'meta.json'
                if not meta_path.is_file() or not (meta_path.parent/'flow.png').is_file() or not read_json(meta_path).get('routing_complete') or read_json(meta_path).get('review_source_hash')!=source_hash(card):
                    raise RuntimeError(f"review.v1 route failed geometry validation: {path.stem}")
    agg = aggregate(o, enumeration, materials=materials, context_meta=context_meta)
    ds = None
    if (o / "datasheet_miss.json").is_file():
        ds = read_json(o / "datasheet_miss.json")
    data = build_report_data(
        board.name,
        enumeration,
        agg["verdicts"],
        agg["findings"],
        materials=materials,
        context_meta=context_meta,
        gate=None,
        datasheet_miss=ds,
    )
    paths = write_expert_deliverables(
        o,
        board,
        agg["verdicts"],
        materials=materials,
        report_data=data,
        generate_prose=generate_prose,
    )
    print(f"OK\treport_data {paths.get('report_data.json')}")
    return agg
