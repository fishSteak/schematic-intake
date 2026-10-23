"""Recover individually valid records from completed model batches, without rewriting prose."""
import argparse
import datetime
from pathlib import Path
from kernel.util import read_json, write_json, resolve_board_path
from agent_review import detail_prose as dp
from agent_review.detail_view import digest, verify_prose_provenance


def recover(board,verdicts=None):
    out=board/'review_out';v=verdicts or read_json(out/'verdicts.json')
    cards={r['rule_id']+'::'+c['object']:dict(c,rule_id=r['rule_id']) for r in v['results'] for c in r.get('object_cards',[])}
    # Retain the actual generic prompt as well as its hash for future audits.
    prompt=dp.PROMPT.read_text(encoding='utf-8');prompt_hash=digest(prompt)
    policy_dir=out/'detail_prose/policies'/prompt_hash
    policy_dir.mkdir(parents=True,exist_ok=True)
    (policy_dir/'prompt.md').write_text(prompt,encoding='utf-8')
    for card in cards.values():
        data=dp.load(out,card)
        if not data:continue
        verify_prose_provenance(out,data)
        batch=out/data['_meta']['batch_output'];ctxpath=batch.with_suffix('.context.json')
        run=read_json(batch.with_suffix('.run.json'))
        if run.get('policy_hash')!=dp.policy_hash():continue
        if read_json(batch.with_suffix('.schema.json'))!=dp.schema():continue
        meta=data['_meta']
        # Early pilot outputs already have real run/source evidence; add explicit
        # prompt/schema attestations without touching their model-authored payload.
        meta.setdefault('prompt_hash',prompt_hash)
        meta.setdefault('schema_hash',digest(dp.schema()))
        meta['prompt_snapshot']=str((policy_dir/'prompt.md').relative_to(out))
        write_json(dp.path_for(out,card),data)
    recovered=[]
    for batch in sorted((out/'detail_prose/runs').glob('*.json')):
        ctxpath=batch.with_suffix('.context.json');runpath=batch.with_suffix('.run.json')
        if not ctxpath.exists() or not runpath.exists():continue
        # A completed old provider/prompt/transport batch cannot acquire a new
        # execution policy simply because its source card still matches.
        schema_path=batch.with_suffix('.schema.json')
        if not schema_path.is_file() or read_json(schema_path)!=dp.schema():continue
        run=read_json(runpath)
        if run.get('status')!='completed' or not run.get('id'):continue
        if run.get('policy_hash')!=dp.policy_hash():continue
        supplied={r['key']:r for r in read_json(ctxpath)['records']}
        for row in read_json(batch).get('records',[]):
            card=cards.get(row.get('key'))
            if not card or dp.load(out,card):continue
            current=dp.context(out,card)
            if row.get('key') not in supplied or supplied[row['key']]['source_hash']!=current['source_hash']:continue
            if dp.validate(row,current):continue
            data=dict(row)
            data['_meta']={'policy_hash':dp.policy_hash(),'profile_hash':run.get('profile_hash'),'output_hash':digest(row),'run_id':run['id'],
                           'model':run.get('model'),'provider':run.get('provider'),
                           'prompt_hash':digest(dp.PROMPT.read_text(encoding='utf-8')),'schema_hash':digest(dp.schema()),
                           'batch_output':str(batch.relative_to(out)),'recovered_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                           'recovery':'Individual source-bound record validated against completed original batch; model prose unchanged'}
            verify_prose_provenance(out,data)
            write_json(dp.path_for(out,card),data);recovered.append(data['key'])
    print('DETAIL_PROSE_RECOVERED',len(recovered),flush=True)
    return recovered


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--board',required=True)
    recover(resolve_board_path(ap.parse_args().board))
