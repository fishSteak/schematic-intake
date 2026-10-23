"""Generate source-bound presentation prose and publish without rerunning review."""
import argparse
from pathlib import Path
from kernel.util import resolve_board_path, read_json, write_json
from agent_review.detail_view import digest, native
from agent_review.analysis_contract import has_direct_analysis
from agent_review.detail_prose import ensure, load
from agent_review.expert_report import write_expert_deliverables


def publish(board):
    out=board/'review_out'
    verdicts=read_json(out/'verdicts.json')
    original_hash=digest(verdicts)
    raw_hashes={str(p.relative_to(out)):digest(read_json(p)) for p in (out/'agent_jobs').glob('*/*.json')}
    from agent_review.detail_prose_recovery import recover
    recover(board,verdicts)
    ensure(board,verdicts)
    cards=[dict(c,rule_id=r['rule_id']) for r in verdicts['results'] for c in r.get('object_cards',[])]
    for card in cards:
        if not has_direct_analysis(card) and not load(out,card):raise RuntimeError('Missing validated presentation prose')
    config=out/'detail_presentation.json'
    previous=config.read_bytes() if config.exists() else None
    write_json(config,{'enabled':True,'display_version':'detail.v2'})
    try:
        write_expert_deliverables(out,board,verdicts,report_data=read_json(out/'report_data.json'),materials=read_json(out/'materials.json'))
        packs=[read_json(out/'evidence'/(r['rule_id']+'.json')) for r in verdicts['results']]
        after=[c for p in packs for c in p.get('object_cards',[])]
        assert len(after)==len(cards)
        assert all(c.get('review_detail',{}).get('version')=='detail.v2' for c in after)
        assert digest(read_json(out/'verdicts.json'))==original_hash
        assert raw_hashes=={str(p.relative_to(out)):digest(read_json(p)) for p in (out/'agent_jobs').glob('*/*.json')}
        write_json(out/'detail_presentation_receipt.json',{'display_version':'detail.v2','records':len(after),'object_records':sum(c.get('object')!='(none)' for c in after),'applicability_placeholders':sum(c.get('object')=='(none)' for c in after),'verdicts_hash':original_hash,'raw_cards_unchanged':True,'prose_status_counts':{s:sum(c['review_detail']['prose_status']==s for c in after) for s in {'native','ready','source_insufficient','source_conflict','original'}}})
    except Exception:
        if previous is None:config.unlink(missing_ok=True)
        else:config.write_bytes(previous)
        raise


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--board',required=True)
    publish(resolve_board_path(ap.parse_args().board))
