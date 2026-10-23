"""Re-review the actual pending pairs with autonomous, source-bound evidence."""
from pathlib import Path
import argparse
import datetime
import os
import shutil
import sys
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kernel.util import ROOT,read_json,write_json,resolve_board_path
from agent_review.rule_modes import filter_enumeration_for_agent
from agent_review.stages import context_build,judge,aggregate_stage,gate
from agent_review.evidence_loop import run_research,business_requests,digest,policy_hash
from agent_review.repair_cards import repair

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--board',required=True)
    ap.add_argument('--model',required=True);ap.add_argument('--concurrency',type=int,default=2)
    ap.add_argument('--resume',action='store_true');args=ap.parse_args()
    board=resolve_board_path(args.board);o=board/'review_out';os.environ['AGENT_JUDGE_MODEL']=args.model
    status=o/'autonomous_review_run.json';plan=o/'autonomous_review_plan.json'
    if not args.resume or not plan.exists():
        report=read_json(o/'report_data.json');previous=read_json(o/'selection_review_run.json')
        full=read_json(o/'agent_enumeration.json');scope={}
        for row in report['rules']:
            rid=row['rule_id']
            if rid not in previous['scope']:continue
            allowed=set(previous['objects_by_rule'].get(rid,[]))
            objects=[c['object'] for c in row.get('objects',[]) if c.get('verdict_zh')=='待确认' and c.get('object') in allowed]
            if objects:scope[rid]=objects
        if not scope:raise RuntimeError('No pending objects in the preceding authorized review')
        archive=board/'history'/('pre-autonomous-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
        archive.mkdir(parents=True)
        for name in ('report_data.json','expert_review.html','verdicts.json','agent_enumeration.json','selection_review_run.json'):
            if (o/name).exists():shutil.copy2(o/name,archive/name)
        for rid,objects in scope.items():
            for obj in objects:
                for suffix in ('.json','.analysis.md','.context.json','.research.json'):
                    p=o/'agent_jobs'/rid/(obj+suffix)
                    if p.exists():
                        dest=archive/'agent_jobs'/rid/p.name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        write_json(plan,{'scope':scope,'publication_enumeration':full,'baseline_archive':str(archive),'model':args.model,'concurrency':args.concurrency})
        state={'status':'running','scope':scope,'model':args.model,'concurrency':args.concurrency,
               'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'rounds':[],
               'unit_tests_requested':False,'pilot_acceptance_requested':False,'policy_hash':policy_hash()}
        write_json(status,state)
    else:
        state=read_json(status);state['status']='running';state.pop('error',None);write_json(status,state)
    cfg=read_json(plan)
    if cfg['model']!=args.model or cfg['concurrency']!=args.concurrency:raise RuntimeError('Resume parameters differ from authorized run')
    kg=read_json(o/'board_kg.json');scope=cfg['scope'];all_pairs={(rid,obj) for rid,objs in scope.items() for obj in objs}
    def enumeration(pairs):
        scoped={rid:[obj for obj in scope[rid] if (rid,obj) in pairs] for rid in scope}
        scoped={rid:objects for rid,objects in scoped.items() if objects}
        return filter_enumeration_for_agent({'rule_ids':list(scoped),'by_rule':scoped},kg)
    targets=enumeration(all_pairs)
    def current_cards():
        return {(rid,obj):read_json(o/'agent_jobs'/rid/(obj+'.json')) for rid,obj in all_pairs}
    try:
        context=context_build.run(board,kg,targets)
        run_research(board,targets)
        to_run={(p['rule_id'],p['object']) for p in state.pop('next_pairs',[])} or all_pairs
        for wave in range(3):
            selected=enumeration(to_run)
            if not selected['by_rule']:break
            print('AUTONOMOUS_REVIEW_WAVE',wave+1,'pairs',len(to_run),flush=True)
            result=judge.run(board,kg,selected,concurrency=args.concurrency,resume=args.resume and wave==0,research_prepared=True)
            repairs=repair(board,kg,selected,concurrency=args.concurrency) if result['failed'] else []
            state['rounds'].append({'pairs':selected['by_rule'],'judge':result,'repairs':repairs})
            write_json(status,state)
            old=read_json(o/'evidence_store/current.json')
            run_research(board,targets,followup=True,force=True)
            fresh=read_json(o/'evidence_store/current.json')
            cards=current_cards();pending={pair for pair,c in cards.items() if c.get('verdict_zh')=='待确认'}
            old_by={f['id']:f for f in old['facts']};new_by={f['id']:f for f in fresh['facts']}
            semantic=lambda f:digest({k:f.get(k) for k in ('subject','predicate','value','unit','conditions')})
            changed=[f for key,f in new_by.items() if key not in old_by or semantic(f)!=semantic(old_by[key])]
            changed += [f for key,f in old_by.items() if key not in new_by]
            impacted=set()
            for fact in changed:
                impact={(p['rule_id'],p['object']) for p in fact.get('affected_pairs',[])}
                impacted |= (impact&all_pairs) if impact else all_pairs
            changed_impacted=set(impacted)
            for req in fresh['requests']:
                if req['category']!='business_fact':
                    impacted|={(p['rule_id'],p['object']) for p in req['affected_pairs']} & pending
            state['last_audit_run']=fresh['_provenance']['run_id']
            state['evidence_versions']=state.get('evidence_versions',[])+[fresh['_provenance']['version_path']]
            write_json(status,state)
            if wave==2 and changed_impacted:
                state['next_pairs']=[{'rule_id':rid,'object':obj} for rid,obj in changed_impacted]
                write_json(status,state)
                raise RuntimeError('New decisive facts need another targeted review before publication; saved next_pairs')
            if not impacted or wave==2:break
            to_run=impacted
        cards=current_cards();pending={pair for pair,c in cards.items() if c.get('verdict_zh')=='待确认'}
        requests=business_requests(board,pending)
        full=cfg['publication_enumeration']
        full['review_scope']={**(full.get('review_scope') or {}),'autonomous_rerun':scope,'model':args.model,'evidence_version':read_json(o/'evidence_store/current.json')['_provenance']['version_path']}
        write_json(o/'agent_enumeration.json',full)
        materials=read_json(o/'materials.json')
        aggregate_stage.run(board,full,materials=materials,context_meta=context,figure_rule_ids=list(scope),figure_objects=sorted({p[1] for p in all_pairs}))
        quality=gate.run(board,full,materials=materials,context_meta=context,allow_failed=True)
        now=datetime.datetime.now(datetime.timezone.utc)
        from collections import Counter
        counts=dict(Counter(c['verdict_zh'] for c in cards.values()))
        state.update(status='completed',completed_at=now.isoformat(),elapsed_s=(now-datetime.datetime.fromisoformat(state['started_at'])).total_seconds(),
                     verdict_counts=counts,reviewed_pairs=len(all_pairs),business_request_count=len(requests['business_requests']),
                     internal_unresolved_count=len(requests['internal_unresolved']),business_gate_passed=quality.get('passed'))
        write_json(status,state)
        print('AUTONOMOUS_REVIEW_COMPLETED',counts,'business_requests',len(requests['business_requests']),flush=True)
    except Exception as e:
        state.update(status='incomplete',error=str(e));write_json(status,state);raise

if __name__=='__main__':main()
