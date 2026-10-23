"""Revalidate completed model artifacts after a validator JSON transport failure.

Never recovers a timeout, incomplete model turn, or a real contract error.
The original dispatcher result is archived; all normal gates still execute.
"""
import datetime,hashlib,json,shutil
from pathlib import Path
from kernel.util import read_json,write_json


def completed_phases(log,run_id,*,requires_check):
    if not run_id or not log.is_file():return []
    phases=[]
    for line in log.read_text(encoding='utf-8').splitlines():
        try:row=json.loads(line)
        except ValueError:continue
        if row.get('type')=='phase_completed' and row.get('run_id')==run_id:
            phases.append(row.get('phase'))
    if not phases or 'analysis_draft' not in phases:return []
    if requires_check and 'decisive_check' not in phases:return []
    return phases


def revalidate(board):
    from agent_review.delivery import policy,validate
    from agent_review.validate_job import validate_judge_card
    from agent_review.evidence_loop import validate_object_research
    from agent_review.decision_contract import validate_decision_audit
    from agent_review.delivery_progress import update
    board=Path(board);o=board/'review_out'
    config=read_json(board/'selection_delivery.json')
    for rel,expected in config.get('raw_source_hashes',{}).items():
        p=board/rel
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
            raise RuntimeError('Raw material changed; validation recovery refused: '+rel)
    jobs=read_json(o/'judge_jobs.json');result=read_json(o/'sdk_judge_result.json')
    if not jobs.get('attempt_id') or jobs['attempt_id']!=result.get('attempt_id'):
        raise RuntimeError('Current model attempt does not match its dispatcher receipt')
    by_id={j['job_id']:j for j in jobs['jobs']};records=[];recovered=[]
    for row in result['results']:
        errors=row.get('validation_errors') or [row.get('error','')]
        if row.get('ok') or not errors or not all(str(e).startswith('validation execution failed:') for e in errors):continue
        job=by_id[row['job_id']]
        if not job.get('delivery_version') or len(job['objects'])!=1:continue
        rid=job['rule_id'];obj=job['objects'][0];p=o/'agent_jobs'/rid/(obj+'.json')
        phases=completed_phases(p.with_suffix('.run.jsonl'),row.get('run'),requires_check=job.get('self_check') is not False)
        files=[p,p.with_suffix('.analysis.md'),p.with_suffix('.research.json'),p.with_suffix('.context.json')]
        if not phases or not all(f.is_file() and f.stat().st_size for f in files):continue
        card=read_json(p);wb=read_json(p.with_suffix('.context.json'))
        if wb.get('delivery_policy')!=policy(board,rid,obj):continue
        checked=validate_judge_card(card,rule_id=rid,obj=obj,board=board)+validate(card,wb)+validate_object_research(board,rid,obj,card)
        if rid=='P11-A01' and card.get('schema_version')!='review.v1':checked+=validate_decision_audit(card,required=True)
        record={'rule_id':rid,'object':obj,'run_id':row['run'],'completed_phases':phases,
                'original_worker_errors':errors,'revalidation_errors':checked,
                'artifact_hashes':{str(f.relative_to(board)):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}}
        records.append(record)
        if not checked:recovered.append((row,record))
    if not recovered:return {'recovered':0,'records':records}
    archive=board/'history'/read_json(board/'selection_delivery.json')['run_key']/'validation-transport'/jobs['attempt_id']
    archive.mkdir(parents=True,exist_ok=True)
    for name in ['judge_jobs.json','sdk_judge_result.json']:
        if not (archive/name).exists():shutil.copy2(o/name,archive/name)
    for row,record in recovered:
        row['original_worker_validation']={'error':row.get('error'),'validation_errors':row.get('validation_errors')}
        row.update(ok=True,error=None,validation_errors=[],validation_recovery=record)
    write_json(o/'sdk_judge_result.json',result)
    for row,record in recovered:
        record['finalization_errors']=update(board,row['job_id'])
        if record['finalization_errors']:
            row.update(ok=False,error='Normal finalization rejected revalidated artifacts',validation_errors=record['finalization_errors'])
    write_json(o/'sdk_judge_result.json',result)
    receipt={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'attempt_id':jobs['attempt_id'],
             'original_receipt_archive':str(archive.relative_to(board)),
             'recovered':sum(not r.get('finalization_errors') for _,r in recovered),'records':records}
    write_json(o/'delivery_validation_recovery.json',receipt)
    print('DELIVERY_VALIDATION_RECOVERY',receipt['recovered'],flush=True)
    return receipt
