"""Repair actual model/contract failures against frozen materials, then publish.

No pilot acceptance or unit tests; output/evidence validation remains mandatory.
"""
from pathlib import Path
import argparse
import datetime
import os
import shutil
import subprocess
import sys
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kernel.util import ROOT,read_json,write_json,resolve_board_path
from kernel.runtime import node_executable
from agent_review.enumerate import enumerate_board
from agent_review.execution_policy import material_hash,checkpoint
from agent_review.stages.judge import validate_judge_card,finalize_card
from agent_review.decision_contract import validate_decision_audit
from agent_review.review_contract import is_v1
from agent_review.stages import aggregate_stage,gate

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--board',required=True)
    ap.add_argument('--model',required=True);ap.add_argument('--concurrency',type=int,default=2)
    args=ap.parse_args();board=resolve_board_path(args.board);o=board/'review_out'
    state=read_json(o/'selection_review_run.json')
    if state['status']=='running':raise RuntimeError('Wait until the main dispatch exits')
    if state['model']!=args.model or state['concurrency']!=args.concurrency:raise RuntimeError('Run parameters differ from authorized scope')
    os.environ['AGENT_JUDGE_MODEL']=args.model
    scope=state['objects_by_rule'];kg=read_json(o/'board_kg.json')
    initial=read_json(o/'judge_jobs.json')
    sources={(j['rule_id'],obj):j for j in initial['jobs'] for obj in j['objects']}
    state.setdefault('repair_attempts',[])
    frozen=material_hash(board)

    def errors(rid,obj):
        p=o/'agent_jobs'/rid/(obj+'.json')
        if not p.is_file():return ['No model card was delivered'],None
        try:d=read_json(p)
        except Exception as e:return [str(e)],None
        e=validate_judge_card(d,rule_id=rid,obj=obj,board=board)
        if rid=='P11-A01' and not is_v1(d):e.extend(validate_decision_audit(d,required=True))
        if p.with_suffix('.failed.json').exists():e.append('The preceding CLI execution failed; generate a fresh result')
        execution=d.get('_meta',{}).get('execution',{}) if is_v1(d) else d.get('_execution',{})
        expected=sources.get((rid,obj),{}).get('execution_hash')
        if expected and execution.get('fingerprint')!=expected:e.append('Output has no valid provenance for the current material snapshot')
        return e,d

    for turn in range(3):
        pending=[]
        for rid,objects in scope.items():
            for obj in objects:
                e,d=errors(rid,obj)
                if e:pending.append((rid,obj,e,d))
        if not pending:break
        stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        archive=board/'history'/('selection-contract-repair-'+stamp)
        archive.mkdir(parents=True,exist_ok=True)
        for name in ('judge_jobs.json','sdk_judge_result.json','judge_result.json'):
            if (o/name).exists():shutil.copy2(o/name,archive/name)
        jobs=[]
        for rid,obj,e,d in pending:
            source=sources.get((rid,obj))
            if not source:raise RuntimeError(f'Missing original dispatch provenance: {rid}/{obj}')
            j={**source,'objects':[obj],'job_id':source['job_id']+'::repair'+str(turn+1),
               'validation_errors':e,'prior_card':d}
            saved=checkpoint(board,rid,obj,j['execution_hash'])
            j['resume_stage']=saved['stage'] if saved else None
            j['checkpoint_root']=saved['root'] if saved else None
            for suffix in ('.json','.analysis.md','.invalid.json','.failed.json'):
                p=o/'agent_jobs'/rid/(obj+suffix)
                if p.is_file():
                    dest=archive/rid/p.name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
            jobs.append(j)
        attempt=uuid.uuid4().hex
        write_json(o/'judge_jobs.json',{'jobs':jobs,'concurrency':args.concurrency,'attempt_id':attempt,'mode':'contract_repair'})
        print('CONTRACT_REPAIR',len(jobs),flush=True)
        subprocess.run([node_executable(),str(ROOT/'scripts/repair_selection_cards.mjs'),str(board)],cwd=ROOT,check=False)
        if material_hash(board)!=frozen:raise RuntimeError('Materials changed during contract repair')
        ran=read_json(o/'sdk_judge_result.json')
        if ran.get('attempt_id')!=attempt:raise RuntimeError('No current repair run result')
        results={r['job_id']:r for r in ran['results']}
        repaired=[]
        for j in jobs:
            rid=j['rule_id'];obj=j['objects'][0];r=results.get(j['job_id'],{})
            e=finalize_card(board,kg,rid,obj) if r.get('ok') else [r.get('error','repair run failed')]
            p=o/'agent_jobs'/rid/(obj+'.json')
            if e:write_json(p.with_suffix('.invalid.json'),{'errors':e})
            sources[(rid,obj)]=j
            repaired.append({'rule_id':rid,'object':obj,'run':r.get('run'),'errors':e})
        state['repair_attempts'].append({'archive':str(archive),'results':repaired})
        write_json(o/'selection_review_run.json',state)
    remaining=[(rid,obj,e) for rid,objects in scope.items() for obj in objects if (e:=errors(rid,obj)[0])]
    if remaining:raise RuntimeError(f'Unresolved model output failures: {remaining}')
    full=enumerate_board(kg)
    full['by_rule'].update(scope);full['agent_rule_ids']=state['scope']
    full['review_scope']={'mode':'selected_objects','objects_by_rule':scope,'model':args.model,'judge_pairs':sum(map(len,scope.values()))}
    write_json(o/'agent_enumeration.json',full)
    materials=read_json(o/'materials.json')
    context=read_json(o/'context_build_result.json')
    aggregate_stage.run(board,full,materials=materials,context_meta=context,figure_rule_ids=state['scope'])
    quality=gate.run(board,full,materials=materials,context_meta=context,allow_failed=True)
    now=datetime.datetime.now(datetime.timezone.utc)
    state.update(status='completed',completed_at=now.isoformat(),business_gate_passed=quality.get('passed'),
                 elapsed_s=(now-datetime.datetime.fromisoformat(state['started_at'])).total_seconds(),
                 validated_pairs=sum(map(len,scope.values())))
    state.pop('error',None)
    write_json(o/'selection_review_run.json',state)
    print('SELECTION_REVIEW_COMPLETED',state['elapsed_s'],flush=True)

if __name__=='__main__':main()
