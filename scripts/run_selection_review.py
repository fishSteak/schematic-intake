"""Run the complete selection scope only after a source-bound pilot acceptance."""
from pathlib import Path
import argparse
import datetime
import hashlib
import time
import sys
import os
import shutil
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kernel.util import ROOT,read_json,write_json,resolve_board_path
from agent_review.execution_policy import execution_hash,digest
from agent_review.enumerate import enumerate_board
from agent_review.rule_modes import filter_enumeration_for_agent,load_agent_rule_ids
from agent_review.stages import context_build,judge,aggregate_stage,gate

def pipeline_hash():
    from agent_review.review_contract import fingerprint
    return digest({'rules':{rid:execution_hash(rid,{},'pilot-policy') for rid in load_agent_rule_ids()},
                   'power_v1_contract':fingerprint()})

def source_hash(board):
    """Pilot binds raw materials, not derived manifest timestamps or scheduling scope."""
    files={}
    for name in ('materials.json','review_out/board_kg.json'):
        p=board/name;files[name]=hashlib.sha256(p.read_bytes()).hexdigest()
    for key,value in read_json(board/'materials.json').items():
        if isinstance(value,str):
            p=Path(value) if Path(value).is_absolute() else board/value
            if p.is_file():files['input:'+key]=hashlib.sha256(p.read_bytes()).hexdigest()
    for folder in ('cache','review_out/datasheets'):
        for p in sorted((board/folder).rglob('*')):
            if p.is_file() and (p.suffix.lower()=='.pdf' or p.name=='pintable.json'):
                files[str(p.relative_to(board))]=hashlib.sha256(p.read_bytes()).hexdigest()
    return digest(files)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--board',required=True);ap.add_argument('--acceptance')
    ap.add_argument('--skip-pilot-acceptance',action='store_true',help='Explicit user-authorized waiver; does not bypass output validation')
    ap.add_argument('--scope',help='JSON mapping rule IDs to explicitly selected objects')
    ap.add_argument('--model',default='gpt-6.1-sol')
    ap.add_argument('--concurrency',type=int,default=2);ap.add_argument('--resume',action='store_true')
    args=ap.parse_args();board=resolve_board_path(args.board);o=board/'review_out'
    if not args.skip_pilot_acceptance:
        if not args.acceptance:raise RuntimeError('Provide acceptance or an explicitly authorized waiver')
        acceptance=read_json(Path(args.acceptance));policy=pipeline_hash()
        if not acceptance.get('passed') or acceptance.get('pipeline_hash')!=policy:
            raise RuntimeError('Pilot quality gate missing, failed or stale; full review was not started')
        if acceptance.get('board_input_hash')!=source_hash(board):
            raise RuntimeError('Board material inputs changed since pilot acceptance')
    os.environ['AGENT_JUDGE_MODEL']=args.model
    ids=load_agent_rule_ids()
    expected=[f'P11-A{i:02}' for i in range(1,8)]+[f'P12-A{i:02}' for i in range(1,9)]+['P21-A01']
    if set(ids)!=set(expected):raise RuntimeError('Review scope changed after pilot planning')
    kg=read_json(o/'board_kg.json');full=enumerate_board(kg)
    requested=read_json(Path(args.scope)) if args.scope else None
    if requested is not None:
        if set(requested)!=set(ids):raise RuntimeError('Scoped run must explicitly cover all selection checks')
        for rid,objects in requested.items():
            if not isinstance(objects,list) or len(objects)!=len(set(objects)):raise RuntimeError(f'Invalid scope: {rid}')
            unknown=set(objects)-set(full['by_rule'].get(rid,[]))
            if unknown:raise RuntimeError(f'Objects outside eligible scope: {rid}: {sorted(unknown)}')
    selected=filter_enumeration_for_agent(full,kg)
    if requested is not None:
        selected['by_rule']={rid:list(requested[rid]) for rid in ids}
        selected=filter_enumeration_for_agent(selected,kg)
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup=board/'history'/('pre-selection-'+stamp)
    backup.mkdir(parents=True,exist_ok=True)
    for name in ('expert_review.html','report_data.json','verdicts.json','findings.json','agent_enumeration.json','selection_review_run.json'):
        if (o/name).is_file():shutil.copy2(o/name,backup/name)
    materials=read_json(o/'materials.json');materials['rule_ids']=full['rule_ids'];materials['rule_ids_explicit']=False
    write_json(o/'materials.json',materials)
    write_json(o/'agent_enumeration.json',full)
    state={'scope':ids,'objects_by_rule':selected['by_rule'],'model':args.model,'concurrency':args.concurrency,
           'pilot_acceptance_waived':args.skip_pilot_acceptance,'unit_tests_requested':False,
           'backup':str(backup),'status':'running','started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'attempts':[]}
    status=o/'selection_review_run.json';write_json(status,state);started=time.monotonic()
    try:
        context=context_build.run(board,kg,selected)
        for attempt in range(2):
            result=judge.run(board,kg,selected,concurrency=args.concurrency,resume=args.resume or attempt>0)
            state['attempts'].append(result);state['elapsed_s']=time.monotonic()-started;write_json(status,state)
            if not result['failed']:break
        if result['failed']:
            raise RuntimeError('Some model outputs failed execution/validation after resume; previous published report retained')
        if requested is not None:
            full['by_rule'].update(selected['by_rule'])
            full['review_scope']={'mode':'selected_objects','objects_by_rule':selected['by_rule'],'model':args.model}
            write_json(o/'agent_enumeration.json',full)
        full['agent_rule_ids']=ids
        aggregate_stage.run(board,full,materials=materials,context_meta=context,figure_rule_ids=ids)
        quality=gate.run(board,full,materials=materials,context_meta=context,allow_failed=True)
        state.update(status='completed',business_gate_passed=quality.get('passed'),elapsed_s=time.monotonic()-started)
        write_json(status,state);print('SELECTION_REVIEW_COMPLETED',state['elapsed_s']);return 0
    except Exception as e:
        state.update(status='incomplete',error=str(e),elapsed_s=time.monotonic()-started);write_json(status,state);raise

if __name__=='__main__':raise SystemExit(main())
