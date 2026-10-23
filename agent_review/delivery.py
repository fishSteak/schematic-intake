"""Opt-in, source-bound representative delivery policy; no electronic answers."""
from pathlib import Path
import hashlib
from kernel.util import read_json

VERSION='delivery.v1'
NUMERIC={'P11-A01','P11-A02','P11-A03','P11-A06','P11-A07','P12-A01','P12-A02','P12-A04','P12-A05','P12-A06','P21-A01'}

def policy(board,rid,obj):
    p=Path(board)/'selection_delivery.json'
    if not p.is_file():return None
    config=read_json(p)
    if obj not in config.get('by_rule',{}).get(rid,[]):return None
    return {'version':VERSION,'run_key':config['run_key'],'requires_state_if_applicable':rid in NUMERIC,
            'raw_material_hash':config.get('raw_material_hash'),
            'independent_documents':config.get('independent_documents',{}).get(rid+'::'+obj,[]),
            'independent_primary_sources':config.get('independent_primary_sources',[]),
            'independent_source_hashes':{rel:hashlib.sha256((Path(board)/rel).read_bytes()).hexdigest() for rel in config.get('independent_documents',{}).get(rid+'::'+obj,[]) if (Path(board)/rel).is_file()}}

def validate(card,workbench):
    p=workbench.get('delivery_policy')
    if not p:return []
    if card.get('schema_version')=='review.v1':
        primary=(card.get('extreme') or {}).get('primary_scenario_ref')
        cases=card.get('scenarios',[])
        if cases and not primary:return ['delivery: operating analysis needs an explicit primary reference']
        return []
    if card.get('verdict_zh')=='不适用':return []
    from agent_review.figure_contract import validate_shape
    figures=[(card.get('main_analysis') or {}).get('figure')]+[s.get('figure') for s in card.get('states') or []]
    shape_errors=[error for figure in figures if figure is not None for error in validate_shape(figure)]
    if shape_errors:return shape_errors
    states=card.get('states') or []
    if not states:
        return ['delivery: applicable numeric/interface review requires an analyzed state and diagram specification'] if p['requires_state_if_applicable'] else []
    # Checklist states describe conditions and are not electrical scenarios.
    states=[s for s in states if not ('steps' in s and not any(k in s for k in ['P','V','metric','formula_lines','figure','role']))]
    if not states:return []
    main=card.get('main_analysis')
    if main:
        figure=main.get('figure')
        if not figure or not figure.get('pages'):return ['delivery: main analysis needs its own diagram, including unfinished proofs']
        return []
    primaries=[s for s in states if s.get('role')=='primary']
    if len(primaries)!=1:return ['delivery: exactly one explicit primary state required']
    state=primaries[0];figure=state.get('figure')
    errors=[]
    if not isinstance(figure,dict) or not figure.get('pages'):errors.append('delivery: primary state must have a source-page diagram specification')
    if not state.get('route_summary'):errors.append('delivery: primary state needs a specific Chinese route/relationship explanation')
    for s in states:
        if s is not state and s.get('figure'):errors.append('delivery: comparison states must be text only')
    basis=card.get('decision_basis') or {}
    if basis.get('state_index') is not None and states.index(state)!=basis['state_index']:errors.append('delivery: primary state must reference the formal decision basis')
    if state.get('stress_kind')=='upper_bound' or state.get('status')=='unmapped':
        if isinstance(figure,dict) and figure.get('kind')!='cross_case_relations':errors.append('delivery: bound/unfinished proof needs a relation diagram, not an invented instantaneous path')
    return errors
