"""Decision-bound primary analysis; no electrical topology or expected answers."""
from __future__ import annotations
import copy
import math
from pathlib import Path
from kernel.util import ROOT, read_json

VERSION='basis-main.v1'
SCHEMA_PATH=ROOT/'agent_review/schemas/main_analysis.v1.schema.json'


def pilot_enabled(board, rule_id, obj):
    path=Path(board)/'main_analysis_contracts.json'
    return path.is_file() and (read_json(path).get(rule_id) or {}).get(obj)==VERSION


def selected_index(card):
    main=card.get('main_analysis')
    if not isinstance(main,dict) or validate(card):return None
    return main['state_index']


def validate(card, *, required=False):
    from jsonschema import Draft202012Validator
    main=card.get('main_analysis')
    if main is None:return ['decision-bound main_analysis required'] if required else []
    errors=['main_analysis: '+e.message for e in Draft202012Validator(read_json(SCHEMA_PATH)).iter_errors(main)]
    if errors:return errors
    basis=card.get('decision_basis') or {}
    kind=basis.get('kind');index=main['state_index'];states=card.get('states') or []
    if main['basis_ref']!='/decision_basis':errors.append('main analysis must reference the formal decision basis')
    if index!=basis.get('state_index'):errors.append('main analysis and decision basis state differ')
    state=states[index] if type(index) is int and 0<=index<len(states) else None
    if main['kind']!='incomplete_proof' and state is None:errors.append('main analysis needs a valid calculation state')
    if main['kind']=='cross_case_bound':
        if kind!='envelope_upper_bound':errors.append('cross-case main requires an envelope decision basis')
        if state and state.get('stress_kind')!='upper_bound':errors.append('bound analysis cannot display an operating candidate')
    elif main['kind']=='operating_case':
        if kind in {'envelope_upper_bound','insufficient'}:errors.append('an envelope/incomplete proof cannot be displayed as an operating case')
    elif kind!='insufficient' or card.get('verdict_zh')!='待确认':errors.append('incomplete proof requires an insufficient, pending decision')
    verified=main['proof_status']=='verified'
    if main['kind']=='incomplete_proof' and verified:errors.append('incomplete proof cannot be verified')
    if card.get('verdict_zh') in {'是','否'} and (not verified or any(c['status']!='supported' for c in main['proof_checks'])):
        errors.append('a final pass/fail needs completed decision-basis proof checks')
    if state:
        value=basis.get('value_w');actual=state.get('P')
        if verified and (type(value) not in (int,float) or type(actual) not in (int,float) or not math.isfinite(value) or not math.isclose(value,actual,rel_tol=1e-6,abs_tol=1e-12)):
            errors.append('formal basis value and main calculation differ')
        if not state.get('formula_lines'):errors.append('main calculation must contain source-bound derivation')
    known={e.get('id') or e.get('path') for e in card.get('evidence_refs',[]) if isinstance(e,dict)}
    for check in main['proof_checks']:
        if check['status']=='supported' and not check['evidence_refs']:errors.append('supported proof check needs evidence')
        if any(ref not in known for ref in check['evidence_refs']):errors.append('proof evidence reference is not in the card')
    assumptions={x['id']:x for x in card.get('key_assumptions',[])}
    for ref in main['assumption_refs']:
        if ref not in assumptions:errors.append('unknown main-analysis assumption')
        elif verified and assumptions[ref]['status']=='unresolved':errors.append('verified basis cannot depend on an unresolved assumption')
    figure=main['figure']
    if verified and figure is None:errors.append('verified main analysis requires its own figure specification')
    if figure and main['kind']=='cross_case_bound' and figure['kind']!='cross_case_relations':errors.append('bound figure must be an energy/relationship diagram')
    for i,state_row in enumerate(states):
        if state_row.get('figure'):errors.append('main-analysis figures must have one owner, not separate candidate figures')
    return errors


def drawing_states(card):
    """Controller projection only: draw the formal-basis state, never a ranked substitute."""
    states=copy.deepcopy(card.get('states') or [])
    main=card.get('main_analysis')
    if not main:return states
    if validate(card):return []
    index=main['state_index']
    if index is None:return []
    for state in states:state.pop('figure',None)
    state=states[index]
    state.update(name_zh=main['title'],route_summary=main['route_summary'],role='primary',figure=copy.deepcopy(main['figure']))
    state['_analysis_kind']=main['kind']
    if main['figure'] and main['figure']['kind']=='cross_case_relations':
        for page in state['figure']['pages']:
            paths=page.get('paths') or ([page['path']] if page.get('path') else [])
            page['unknown_direction_paths']=paths
            for marker in page.get('markers',[]):
                ref=marker.get('quantity_ref')
                if ref in {'/decision_basis/value_w','/electrical/P_limit'}:
                    bucket,key=ref.strip('/').split('/')
                    value=card.get(bucket,{}).get(key)
                    if type(value) in (int,float):marker['title']=marker.get('title','')+f'：{value*1000:.3f} mW'
                    else:marker['title']=marker.get('title','')+'：尚未证明'
    return states
