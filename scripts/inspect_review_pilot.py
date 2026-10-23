"""Produce an audit table; structural agreement alone never approves full review."""
from pathlib import Path
import argparse
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kernel.util import read_json,write_json
from agent_review.validate_job import validate_judge_card
from benchmark_review import SIMPLE,COMPLEX

def inspect(board,rid,obj):
    out=board/'review_out';path=out/'agent_jobs'/rid/(obj+'.json')
    if not path.is_file():return {'ready':False}
    card=read_json(path);errors=validate_judge_card(card,rule_id=rid,obj=obj,board=board)
    for suffix in ('.invalid.json','.failed.json'):
        if path.with_suffix(suffix).exists():errors.append(str(path.with_suffix(suffix)))
    run=card.get('_execution',{}).get('run_id') or card.get('_meta',{}).get('run_id')
    events=[];all_phases={};log=path.with_suffix('.run.jsonl')
    if log.is_file():
        for line in log.read_text(encoding='utf8').splitlines():
            try:
                e=json.loads(line)
                if e.get('type')=='phase_completed':
                    all_phases.setdefault(e.get('run_id'),[]).append(e)
                    if e.get('run_id')==run:events.append(e)
            except ValueError:pass
    validated=bool(card.get('_execution')) or card.get('_meta',{}).get('validation_status')=='valid'
    return {'ready':not errors and validated,'structurally_valid':not errors,'controller_validated':validated,'errors':errors,'verdict':card.get('verdict_zh',card.get('decision',{}).get('verdict')),
            'summary':card.get('summary',card.get('conclusion',card.get('decision',{}))),
            'electrical':card.get('electrical'),'phases':[e['phase'] for e in events],
            'model_elapsed_ms':max((e['elapsed_ms'] for e in events),default=None),'run_id':run,
            'cold_runs':[{'run_id':key,'phases':[e['phase'] for e in values],'elapsed_ms':max(e['elapsed_ms'] for e in values)} for key,values in all_phases.items() if [e['phase'] for e in values] in (['analysis','card'],['analysis','self_check','card'])]}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);ap.add_argument('--overrides');a=ap.parse_args();root=Path(a.root)
    overrides=read_json(Path(a.overrides)) if a.overrides else {}
    rows=[]
    for rid,objs in {**SIMPLE,**COMPLEX}.items():
        for obj in objs:
            baseline=inspect(root/'baseline',rid,obj) if rid in SIMPLE else None
            candidate_board=Path(overrides.get(rid+'::'+obj,str(root/'optimized')))
            candidate=inspect(candidate_board,rid,obj)
            rows.append({'rule':rid,'object':obj,'baseline':baseline,'candidate':candidate,
                         'candidate_board':str(candidate_board),
                         'verdict_agrees':baseline['verdict']==candidate.get('verdict') if baseline and baseline.get('ready') and candidate.get('ready') else None})
    report={'status':'pending_manual_quality_review','full_review_approved':False,'rows':rows}
    write_json(root/'quality_comparison.json',report)
    for row in rows:
        b=row['baseline'] or {};c=row['candidate'];print(row['rule'],row['object'],b.get('verdict'),c.get('verdict'),c.get('ready'),c.get('phases'),c.get('model_elapsed_ms'))
    return 0

if __name__=='__main__':raise SystemExit(main())
