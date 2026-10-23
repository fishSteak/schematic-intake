"""Real CLI recovery probe confined to an isolated history benchmark board."""
from pathlib import Path
import argparse
import datetime
import json
import shutil
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kernel.util import ROOT,read_json,write_json,resolve_board_path
from agent_review.stages import judge
from agent_review.execution_policy import material_hash,execution_hash,checkpoint
from agent_review.batching import safe_object_name

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--board',required=True);ap.add_argument('--rule',required=True);ap.add_argument('--object',required=True);args=ap.parse_args()
    board=resolve_board_path(args.board);resolved=board.resolve()
    if not resolved.is_relative_to((ROOT/'boards').resolve()) or 'history' not in resolved.parts:
        raise RuntimeError('Fault injection is restricted to a history benchmark, never formal review_out')
    o=board/'review_out';rid,obj=args.rule,args.object;path=o/'agent_jobs'/rid/(safe_object_name(obj)+'.json')
    before=read_json(path);wb=read_json(path.with_suffix('.context.json'));key=execution_hash(rid,wb,material_hash(board))
    if before.get('_execution',{}).get('fingerprint')!=key:
        raise RuntimeError('Generate a current validated benchmark card before probing resume')
    saved=checkpoint(board,rid,obj,key)
    if not saved:raise RuntimeError('No valid completed phase checkpoint')
    enumeration=read_json(o/'agent_enumeration.json')
    enumeration['rule_ids']=[rid];enumeration['by_rule']={rid:[obj]}
    kg=read_json(o/'board_kg.json')
    warm=judge.run(board,kg,enumeration,resume=True,concurrency=1)
    if warm['dispatched'] or warm['reused']!=1:raise RuntimeError('Warm resume unexpectedly dispatched a model')
    archive=board/'history'/('resume-fault-probe-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    archive.mkdir(parents=True)
    # Preserve the actual model output; simulate delivery loss without changing any verdict.
    if not path.resolve().is_relative_to(resolved) or not archive.resolve().is_relative_to(resolved):raise RuntimeError('Probe paths escape benchmark')
    shutil.move(str(path),str(archive/path.name))
    start=time.monotonic();attempts=[]
    for attempt in range(2):
        result=judge.run(board,kg,enumeration,resume=True,concurrency=1);attempts.append(result)
        if not result['failed']:break
    if result['failed']:raise RuntimeError('Recovery model call failed; original output remains in benchmark history')
    jobs=read_json(o/'judge_jobs.json')['jobs'];runs=read_json(o/'sdk_judge_result.json')['results']
    run_id=runs[0]['run'];events=[json.loads(s) for s in path.with_suffix('.run.jsonl').read_text(encoding='utf8').splitlines()]
    phases=[e['phase'] for e in events if e.get('type')=='phase_completed' and e.get('run_id')==run_id]
    after=read_json(path)
    checks={'warm_model_calls_zero':warm['dispatched']==0,'restored_analysis':jobs[0].get('resume_stage')=='analysis',
            'only_card_phase':phases==['card'],'same_verdict':before['verdict_zh']==after['verdict_zh']}
    record={'passed':all(checks.values()),'checks':checks,'elapsed_s':time.monotonic()-start,'warm':warm,'recovery_attempts':attempts,'run_id':run_id,'original_preserved':str(archive/path.name)}
    write_json(o/'resume_probe.json',record);print(record)
    return 0 if record['passed'] else 2

if __name__=='__main__':raise SystemExit(main())
