"""Paired material-only CLI pilot. Never publishes or reads historical verdicts."""
from pathlib import Path
import argparse
import copy
import shutil
import time
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kernel.util import ROOT,read_json,write_json
from agent_review.enumerate import enumerate_board
from agent_review.rule_modes import filter_enumeration_for_agent
from agent_review.stages import context_build,judge

SIMPLE={'P11-A04':['C5','C18'],'P11-A05':['C1'],'P12-A08':['CN1']}
COMPLEX={'P11-A01':['R4'],'P12-A01':['U2']}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--board',required=True);ap.add_argument('--root',required=True)
    ap.add_argument('--profile',choices=['baseline','optimized'],required=True);ap.add_argument('--concurrency',type=int,default=2)
    ap.add_argument('--resume',action='store_true');ap.add_argument('--pairs',help='Optional pilot subset, rule:object comma separated');args=ap.parse_args()
    source=(ROOT/args.board).resolve();root=(ROOT/args.root).resolve();board=root/args.profile;o=board/'review_out'
    if not o.exists():
        for folder in ('cache','review_out/cache','review_out/datasheets','review_out/sch','review_out/context_store'):
            if (source/folder).is_dir():shutil.copytree(source/folder,board/folder)
        for name in ('materials.json','review_out/materials.json','review_out/board_kg.json','review_out/sch_index.json','review_out/design_graph.json','review_out/design_task.json','review_out/operating_conditions.json','review_out/jlc_project.json','review_out/pintable_index.json'):
            if (source/name).is_file():
                (board/name).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/name,board/name)
        config=read_json(source/'materials.json')
        for key,v in config.items():
            if isinstance(v,str) and (source/v).is_file():config[key]=str((source/v).resolve())
        write_json(board/'materials.json',config)
        write_json(o/'review_contracts.json',{'P11-A01':{'R4':'review.v1'}})
    kg=read_json(o/'board_kg.json');selected={**SIMPLE,**(COMPLEX if args.profile=='optimized' else {})}
    if args.pairs:
        selected={}
        for pair in args.pairs.split(','):
            rid,obj=pair.split(':',1);selected.setdefault(rid,[]).append(obj)
    enumeration=enumerate_board(kg,rule_ids=list(selected))
    # Force excluded examples into the baseline only if explicitly selected; the
    # optimized profile follows the material filter. Current sample C5/C18 unknown.
    enumeration['by_rule']={rid:[obj for obj in objs if obj in enumeration['by_rule'][rid]] for rid,objs in selected.items()}
    enumeration['applicability_exclusions']={}
    enumeration=filter_enumeration_for_agent(enumeration,kg)
    write_json(o/'agent_enumeration.json',enumeration)
    context_build.run(board,kg,enumeration)
    started=time.monotonic()
    result=judge.run(board,kg,enumeration,concurrency=args.concurrency,resume=args.resume,benchmark_three_turn=args.profile=='baseline')
    timing=root/(args.profile+'-timing.json')
    prior=read_json(timing) if timing.is_file() else {}
    attempts=prior.get('attempts',[])
    attempts.append({'elapsed_s':time.monotonic()-started,'result':result,'resume':args.resume})
    write_json(timing,{'elapsed_s':sum(a['elapsed_s'] for a in attempts),'result':result,'selected':selected,'attempts':attempts})
    print(result)
    return 0 if not result['failed'] else 2

if __name__=='__main__':raise SystemExit(main())
