"""Finalize a completed object immediately; preserve execution vs electronic status."""
import sys,datetime
from pathlib import Path
from kernel.util import read_json,write_json
from agent_review.stages.judge import finalize_card

def update(board,job_id):
    board=Path(board);o=board/'review_out';jobs=read_json(o/'judge_jobs.json');results=read_json(o/'sdk_judge_result.json')
    job=next(j for j in jobs['jobs'] if j['job_id']==job_id)
    result=next(r for r in results['results'] if r['job_id']==job_id)
    for obj in job['objects']:
        errors=finalize_card(board,read_json(o/'board_kg.json'),job['rule_id'],obj) if result.get('ok') else result.get('validation_errors') or [result.get('error','execution failed')]
        p=o/'agent_jobs'/job['rule_id']/(obj+'.json')
        if errors:write_json(p.with_suffix('.invalid.json'),{'errors':errors,'card':read_json(p) if p.is_file() else None})
        card=read_json(p) if p.is_file() else {}
        if card.get('schema_version')=='review.v1':
            from agent_review.review_contract import ZH
            verdict=ZH.get((card.get('decision') or {}).get('verdict'))
        else:verdict=card.get('verdict_zh')
        write_json(o/'delivery_progress'/job['rule_id']/(obj+'.json'),{'run_key':read_json(board/'selection_delivery.json')['run_key'],'rule_id':job['rule_id'],'object':obj,'status':'invalid' if errors else 'validated','errors':errors,'verdict':verdict,'run_id':result.get('run'),'elapsed_ms':result.get('elapsed_ms'),'updated_at':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    return errors

if __name__=='__main__':
    errors=update(sys.argv[1],sys.argv[2]);print('OBJECT_FINALIZED',sys.argv[2],errors,flush=True)
