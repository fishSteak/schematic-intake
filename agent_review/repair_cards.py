"""Retry failed deliveries/contracts with real model runs and unchanged source facts."""
from pathlib import Path
import datetime
import os
import shutil
import sys
import uuid
import json
from kernel.util import ROOT,read_json,write_json,out_dir
from agent_review.execution_policy import material_hash,checkpoint
from agent_review.stages.judge import validate_judge_card,finalize_card
from agent_review.decision_contract import validate_decision_audit
from agent_review.review_contract import is_v1
from agent_review.evidence_loop import validate_object_research

def interrupted_analysis(board,rid,obj):
    """Recover only a real, source-identical interrupted analysis snapshot."""
    board=Path(board); rel=Path('review_out/agent_jobs')/rid/(obj+'.analysis.md')
    log=board/'review_out/agent_jobs'/rid/(obj+'.run.jsonl')
    if not log.is_file():return None
    context_rel=rel.with_name(obj+'.context.json')
    current=read_json(board/context_rel)
    history=(board/'history/review-checkpoints').resolve()
    for line in reversed(log.read_text(encoding='utf-8').splitlines()):
        try:event=json.loads(line).get('event',{})
        except ValueError:continue
        message=event.get('message')
        if not isinstance(message,str) or 'analysis checkpoint: ' not in message:continue
        root=Path(message.rsplit('analysis checkpoint: ',1)[1].strip()).resolve()
        if not root.is_relative_to(history):continue
        analysis=root/rel;context=root/context_rel
        if analysis.is_file() and context.is_file() and read_json(context)==current and '\n## ' in analysis.read_text(encoding='utf-8'):
            return str(root)
    return None

def completed_delivery_check(board,rid,obj,*,run_id=None,requires_check=True):
    if not run_id:return False
    log=Path(board)/'review_out/agent_jobs'/rid/(obj+'.run.jsonl')
    if not log.is_file():return False
    phases=set()
    for line in reversed(log.read_text(encoding='utf-8').splitlines()):
        try:row=json.loads(line)
        except ValueError:continue
        if row.get('type')=='phase_completed' and row.get('run_id')==run_id:
            phases.add(row.get('phase'))
    return 'analysis_draft' in phases and (not requires_check or 'decisive_check' in phases)


def recover_source_jobs(board,sources,enumeration):
    """Find an older dispatcher declaration only by the card's exact signature."""
    board=Path(board);missing={}
    for rid,objects in enumeration['by_rule'].items():
        for obj in objects:
            if (rid,obj) in sources:continue
            p=board/'review_out/agent_jobs'/rid/(obj+'.json')
            if not p.is_file():continue
            c=read_json(p);execution=(c.get('_meta') or {}).get('execution',{}) if is_v1(c) else c.get('_execution',{})
            if execution.get('fingerprint'):missing[(rid,obj)]=execution['fingerprint']
    for path in sorted((board/'history').rglob('judge_jobs.json'),key=lambda p:p.stat().st_mtime,reverse=True):
        if not missing:break
        for job in read_json(path).get('jobs',[]):
            for obj in job.get('objects',[]):
                key=(job['rule_id'],obj)
                if key in missing and job.get('execution_hash')==missing[key]:
                    sources[key]=dict(job,objects=[obj],source_dispatch_archive=str(path.relative_to(board)))
                    del missing[key]
    return sources

def repair(board,kg,enumeration,*,concurrency=2,max_attempts=3,geometry_feedback=None):
    board=Path(board);o=out_dir(board);initial=read_json(o/'judge_jobs.json')
    sources={(j['rule_id'],obj):j for j in initial['jobs'] for obj in j['objects']}
    sources=recover_source_jobs(board,sources,enumeration)
    dispatched=read_json(o/'sdk_judge_result.json')
    source_runs={r['job_id']:r.get('run') for r in dispatched.get('results',[])} if dispatched.get('attempt_id')==initial.get('attempt_id') else {}
    frozen=material_hash(board);records=[]
    route_repairs={(r['rule_id'],r['object']):r for r in geometry_feedback or []}
    def errors(rid,obj):
        p=o/'agent_jobs'/rid/(obj+'.json')
        if not p.is_file():return ['No card delivered'],None
        try:d=read_json(p)
        except Exception as e:return [str(e)],None
        e=validate_judge_card(d,rule_id=rid,obj=obj,board=board)
        from agent_review.delivery import validate as validate_delivery
        context=p.with_suffix('.context.json')
        if context.is_file():e+=validate_delivery(d,read_json(context))
        if rid=='P11-A01' and not is_v1(d):e+=validate_decision_audit(d,required=True)
        source=sources.get((rid,obj),{})
        if source.get('autonomous_evidence'):e+=validate_object_research(board,rid,obj,d)
        execution=d.get('_meta',{}).get('execution',{}) if is_v1(d) else d.get('_execution',{})
        if source and execution.get('fingerprint')!=source['execution_hash']:e.append('Fresh validated execution provenance required')
        if p.with_suffix('.failed.json').exists():e.append('Preceding LLM delivery failed; generate a fresh card')
        if (rid,obj) in route_repairs:
            e.append('Diagram source-location feedback: '+json.dumps(route_repairs[(rid,obj)],ensure_ascii=False)+'. Correct the declared route/page from original material; disconnected same-net symbols are logical references, never invented copper. Keep any genuinely unresolved location explicit.')
        return e,d
    for turn in range(max_attempts):
        pending=[]
        for rid,objects in enumeration['by_rule'].items():
            for obj in objects:
                e,d=errors(rid,obj)
                if e:pending.append((rid,obj,e,d))
        if not pending:return records
        archive=board/'history'/('autonomous-repair-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
        archive.mkdir(parents=True)
        for name in ('judge_jobs.json','sdk_judge_result.json','judge_result.json'):
            if (o/name).exists():shutil.copy2(o/name,archive/name)
        jobs=[]
        for rid,obj,e,d in pending:
            if (rid,obj) not in sources:raise RuntimeError(f'No current source job: {rid}/{obj}')
            j={**sources[(rid,obj)],'objects':[obj],'job_id':sources[(rid,obj)]['job_id']+'::repair'+str(turn+1),'validation_errors':e,'prior_card':d}
            saved=checkpoint(board,rid,obj,j['execution_hash'])
            j['resume_stage']=saved['stage'] if saved else None;j['checkpoint_root']=saved['root'] if saved else None
            if not saved:
                interrupted=interrupted_analysis(board,rid,obj)
                if interrupted:j['resume_stage']='analysis';j['checkpoint_root']=interrupted
            if j.get('delivery_version') and d and (o/'agent_jobs'/rid/(obj+'.analysis.md')).is_file() and completed_delivery_check(board,rid,obj,run_id=source_runs.get(sources[(rid,obj)]['job_id']),requires_check=j.get('self_check') is not False):
                # Resume the failed contract stage from the saved full draft.
                j['resume_stage']='self_check'
            if e and all(message.startswith('Diagram source-location feedback:') for message in e):
                # The current electronic card already passed all gates. A
                # declared route correction resumes assembly only.
                j['resume_stage']='self_check';j['checkpoint_root']=None
            for suffix in ('.json','.analysis.md','.research.json','.invalid.json','.failed.json'):
                p=o/'agent_jobs'/rid/(obj+suffix)
                if p.exists():
                    dest=archive/rid/p.name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
            jobs.append(j)
        attempt=uuid.uuid4().hex
        write_json(o/'judge_jobs.json',{'jobs':jobs,'concurrency':concurrency,'attempt_id':attempt,'mode':'autonomous_repair'})
        print('AUTONOMOUS_REPAIR',len(jobs),flush=True)
        from agent_review.llm_dispatch import dispatch
        dispatch(board,concurrency=concurrency)
        if material_hash(board)!=frozen:raise RuntimeError('Evidence changed during delivery repair')
        result=read_json(o/'sdk_judge_result.json')
        if result.get('attempt_id')!=attempt:raise RuntimeError('No matching current repair execution')
        runs={r['job_id']:r for r in result['results']}
        rows=[]
        for j in jobs:
            rid=j['rule_id'];obj=j['objects'][0];r=runs.get(j['job_id'],{})
            e=finalize_card(board,kg,rid,obj) if r.get('ok') else [r.get('error','No completed repair')]
            if e:write_json((o/'agent_jobs'/rid/(obj+'.json')).with_suffix('.invalid.json'),{'errors':e})
            if j.get('delivery_version'):
                from agent_review.delivery_progress import update
                e=update(board,j['job_id'])
            if r.get('ok') and not e:route_repairs.pop((rid,obj),None)
            sources[(rid,obj)]=j;rows.append({'rule_id':rid,'object':obj,'run':r.get('run'),'errors':e})
        records.append({'archive':str(archive),'results':rows})
        write_json(o/'autonomous_repair_result.json',{'attempts':records})
    remaining=[(rid,obj,e) for rid,objects in enumeration['by_rule'].items() for obj in objects if (e:=errors(rid,obj)[0])]
    if remaining:raise RuntimeError(f'Unresolved execution/contract failures: {remaining}')
    return records
