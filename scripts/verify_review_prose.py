"""Verify model-authored business prose reaches both published report payloads unchanged."""
import argparse
import datetime
import html
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_review.review_contract import detail_view, fingerprint, source_hash, validate_review
from kernel.util import read_json, resolve_board_path, write_json


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--board',required=True)
    ap.add_argument('--rule',required=True)
    ap.add_argument('--object',required=True)
    ap.add_argument('--mode',choices=['full_review','card_revision'],default='full_review')
    args=ap.parse_args()
    board=resolve_board_path(args.board);out=board/'review_out'
    path=out/'agent_jobs'/args.rule/(args.object+'.json')
    card=read_json(path);view=detail_view(card,out)
    primary=next(s for s in card['scenarios'] if s['id']==card['extreme']['primary_scenario_ref'])
    evidence=next(c for c in read_json(out/'evidence'/(args.rule+'.json'))['object_cards'] if c['object']==args.object)['review_detail']
    packs=[json.loads(html.unescape(s)) for s in re.findall(r'<textarea class="js-devpack" hidden>(.*?)</textarea>',(out/'expert_review.html').read_text(encoding='utf-8'),re.S)]
    published=next(d for pack in packs if pack['rule_id']==args.rule for d in pack['devices'] if d['ref']==args.object)['review_detail']
    events=[json.loads(line) for line in path.with_suffix('.run.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    checks={
        'canonical_valid':not validate_review(card,board=board,rule_id=args.rule,obj=args.object),
        'current_contract':card['_meta']['contract_hash']==fingerprint(),
        'source_hash_matches':card['_meta']['source_hash']==source_hash(card),
        'model_run_recorded':bool(card['_meta']['run_id']) and any(e.get('type')=='assistant' for e in events),
        'analysis_available':path.with_suffix('.analysis.md').is_file(),
        'model_reasoning_preserved_in_view':view['reasoning_steps']==card['reasoning_steps'],
        'model_route_sentence_preserved_in_view':view['primary']['route_summary']==primary['route_summary'],
        'evidence_reasoning_matches':evidence['reasoning_steps']==card['reasoning_steps'],
        'evidence_route_sentence_matches':evidence['primary']['route_summary']==primary['route_summary'],
        'html_reasoning_matches':published['reasoning_steps']==card['reasoning_steps'],
        'html_route_sentence_matches':published['primary']['route_summary']==primary['route_summary'],
    }
    record={'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'target':card['target'],'run_id':card['_meta']['run_id'],'model_rerun':True,'generation_mode':args.mode,'source_hash':source_hash(card),'checks':checks,'all_passed':all(checks.values()),'reasoning_steps':card['reasoning_steps'],'route_summary':primary['route_summary'],'verdict':card['decision']['verdict'],'business_acceptance':'pending_user_review'}
    write_json(out/(args.object+'_model_prose_verification.json'),record)
    print(json.dumps(record,ensure_ascii=False,indent=2))
    return 0 if record['all_passed'] else 1


if __name__=='__main__':raise SystemExit(main())
