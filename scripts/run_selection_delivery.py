"""Run an explicit representative selection through the normal judge/report chain."""
import argparse,datetime,hashlib,os,shutil,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kernel.util import read_json,write_json,resolve_board_path
from agent_review.enumerate import enumerate_board,ENUMERATORS
from agent_review.rule_modes import filter_enumeration_for_agent
from agent_review.stages import context_build,judge

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def apply_no_object_audits(board,config,enumeration,coverage):
    counts={r['rule_id']:r['candidate_count'] for r in coverage}
    for rid,audit in config.get('no_object_audits',{}).items():
        if rid not in counts or counts[rid] or config['by_rule'].get(rid):
            raise ValueError('No-object audit conflicts with source enumeration: '+rid)
        if not audit.get('summary') or not audit.get('analysis'):
            raise ValueError('No-object audit needs a concrete source-bound summary')
        for rel in [audit['analysis'],*audit.get('sources',[])]:
            p=(board/rel).resolve()
            if not p.is_relative_to(board.resolve()) or not p.is_file():
                raise ValueError('No-object audit source is unavailable: '+rel)
        enumeration.setdefault('rule_na_reasons',{})[rid]=audit['summary']
        enumeration.setdefault('rule_no_object_audits',{})[rid]=audit

def prepare(board):
    config=read_json(board/'selection_delivery.json');o=board/'review_out';kg=read_json(o/'board_kg.json')
    archive=board/'history'/config['run_key'];archive.mkdir(parents=True,exist_ok=True)
    for name in ['verdicts.json','findings.json','report_data.json','expert_review.html','agent_enumeration.json']:
        if (o/name).is_file() and not (archive/name).exists():shutil.copy2(o/name,archive/name)
    for rid in config['by_rule']:
        src=o/'agent_jobs'/rid;dest=archive/'agent_jobs'/rid
        if src.exists() and not dest.exists():shutil.copytree(src,dest)
    full=enumerate_board(kg,rule_ids=config['rule_ids']);coverage=[]
    for rid,selected in config['by_rule'].items():
        raw=ENUMERATORS[rid](kg)
        if not set(selected)<=set(raw):raise ValueError('Selected objects outside original material enumeration: '+rid)
        coverage.append({'rule_id':rid,'candidate_count':len(raw),'selected_objects':selected,'selected_count':len(selected),'no_object':not raw})
    # Keep other report sections at their existing enumeration and batch identity.
    merged=read_json(archive/'agent_enumeration.json')
    for rid,selected in config['by_rule'].items():
        merged['by_rule'][rid]=selected
        if rid not in merged['rule_ids']:merged['rule_ids'].append(rid)
        for field in ['rule_na_reasons','rule_gated','applicability_exclusions']:
            merged.setdefault(field,{}).pop(rid,None)
        if not selected:merged['rule_na_reasons'][rid]=full.get('rule_na_reasons',{}).get(rid) or '按原始BOM和网表核对器件分类，未识别到本检查项适用对象。'
    apply_no_object_audits(board,config,merged,coverage)
    write_json(o/'delivery_enumeration.json',merged)
    write_json(o/'delivery_coverage.json',{'run_key':config['run_key'],'model':'gpt-6.1-sol','concurrency':2,'scope':'representative_selection','rules':coverage,'selected_pairs':sum(x['selected_count'] for x in coverage),'other_sections':'既有批次，未在本轮重审'})
    scope=filter_enumeration_for_agent({'rule_ids':config['rule_ids'],'by_rule':config['by_rule']},kg)
    return config,kg,scope

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--board',required=True);args=ap.parse_args();board=resolve_board_path(args.board)
    config,kg,scope=prepare(board)
    os.environ['AGENT_JUDGE_MODEL']='gpt-6.1-sol';os.environ['AGENT_DELIVERY_TURN_TIMEOUT_MS']='600000'
    if not os.environ.get('SCHEMATIC_CODEX'):
        options=list((Path(os.environ['LOCALAPPDATA'])/'OpenAI/Codex/bin').glob('*/codex.exe'))
        os.environ['SCHEMATIC_CODEX']=str(max(options,key=lambda p:p.stat().st_mtime))
    o=board/'review_out';receipt={'run_key':config['run_key'],'status':'context','started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'selected_pairs':55,'model':'gpt-6.1-sol','concurrency':2}
    write_json(o/'selection_delivery_run.json',receipt)
    context=context_build.run(board,kg,scope)
    receipt['status']='judging';write_json(o/'selection_delivery_run.json',receipt)
    result=judge.run(board,kg,scope,concurrency=2,resume=True,research_prepared=True)
    receipt.update(status='needs_repairs' if result.get('failed') else 'ready_for_publication',judge=result,context=context)
    write_json(o/'selection_delivery_run.json',receipt)
    print('DELIVERY_REVIEW_RESULT',result,flush=True)
    # Publication is a separate, explicit stage after independent and figure audits.

if __name__=='__main__':main()
