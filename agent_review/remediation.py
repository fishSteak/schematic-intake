"""Opt-in, source-bound model remediation summaries; canonical reviews stay immutable."""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import uuid
from jsonschema import Draft202012Validator
from agent_review.batching import safe_object_name
from agent_review.review_contract import core, source_hash, validate_review
from kernel.util import ROOT, read_json, resolve_board_path, write_json
from agent_review.llm import generate_json, load_profile, profile_fingerprint

PROMPT=ROOT/'agent_review/prompts/judge/remediation_v1.md'
SCHEMA=ROOT/'agent_review/schemas/remediation.v1.schema.json'
VERSION='remediation.v1'


def fingerprint(profile=None):
    h=hashlib.sha256()
    for p in (Path(__file__),PROMPT,SCHEMA,*sorted((ROOT/'agent_review/llm').glob('*.py'))):
        h.update(p.name.encode());h.update(p.read_bytes())
    h.update(profile_fingerprint(profile,task='remediation').encode())
    return h.hexdigest()


def configured(out, rid, obj):
    path=Path(out)/'remediation_config.json'
    return path.is_file() and read_json(path).get(rid,{}).get(obj)==VERSION


def artifact_path(out,rid,obj):
    return Path(out)/'remediation'/safe_object_name(rid)/(safe_object_name(obj)+'.json')


def validate(artifact, card):
    data={k:v for k,v in artifact.items() if k!='_meta'}
    errors=[f"{'.'.join(map(str,e.absolute_path))}: {e.message}" for e in Draft202012Validator(read_json(SCHEMA)).iter_errors(data)]
    if errors:return errors
    if data['target']!=card['target']:errors.append('target differs from current review')
    if data['review_source_hash']!=source_hash(card):errors.append('review source changed')
    if not 1<=len([s for s in re.split('[。！？]+',data['summary']) if s.strip()])<=2:
        errors.append('summary requires one or two sentences')
    inputs={i['id']:i for i in card['inputs']}
    ref=data['reference_spec']
    if any(r not in inputs for r in ref['keep_input_refs']):errors.append('unknown retained input reference')
    scenario=next((s for s in card['scenarios'] if s['id']==ref['basis_scenario_ref']),None)
    if scenario is None:return errors+['unknown reference scenario']
    p=next((m['value'] for m in scenario['metrics'] if m['key']=='power'),None)
    rated=inputs.get('controller_rated',{}).get('value')
    limit=card['criterion']['limit']['value']
    required=p/(limit/rated) if p is not None and rated and limit else None
    minimum,proposal=ref['minimum_rating_w'],ref['proposed_rating_w']
    if required is not None and (minimum is None or not math.isclose(minimum,required,rel_tol=1e-6,abs_tol=1e-9)):
        errors.append('minimum rating differs from referenced scenario and criterion usage ratio')
    if required is None and (minimum is not None or proposal is not None):errors.append('unknown stress cannot produce a numeric rating')
    if proposal is not None and minimum is not None and proposal<minimum:errors.append('proposed rating below reference minimum')
    return errors


def load(out,card):
    rid,obj=card['target']['rule_id'],card['target']['object_id']
    if not configured(out,rid,obj):return None
    path=artifact_path(out,rid,obj)
    if not path.is_file():raise RuntimeError(f'model remediation missing: {rid}/{obj}')
    data=read_json(path)
    errors=validate(data,card)
    if data.get('_meta',{}).get('contract_hash')!=fingerprint():errors.append('remediation prompt/contract changed')
    if data.get('_meta',{}).get('validation_status')!='valid':errors.append('remediation not validated')
    if data.get('_meta',{}).get('output_hash')!=hashlib.sha256(json.dumps({k:v for k,v in data.items() if k!='_meta'},ensure_ascii=False,sort_keys=True).encode()).hexdigest():errors.append('model remediation content changed after validation')
    if errors:raise RuntimeError('; '.join(errors))
    return data


def generate(board,rid,obj,force=False,*,profile=None,on_event=None,cancelled=None):
    from agent_review.llm_tasks import runtime_options
    options=runtime_options()
    if on_event is None:on_event=options['on_event']
    if cancelled is None:cancelled=options['cancelled']
    out=board/'review_out';card=read_json(out/'agent_jobs'/rid/(safe_object_name(obj)+'.json'))
    errors=validate_review(card,board=board,rule_id=rid,obj=obj)
    if errors:raise RuntimeError('review must be valid before remediation: '+'; '.join(errors))
    if not force:
        try:
            cached=load(out,card)
            if cached:return cached
        except RuntimeError:pass
    path=artifact_path(out,rid,obj)
    profile=profile or load_profile(task='remediation')
    if path.is_file():
        history=board/'history'/('remediation-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))/rid
        history.mkdir(parents=True,exist_ok=True);shutil.copy2(path,history/path.name)
    context={'schema_version':VERSION,'target':card['target'],'review_source_hash':source_hash(card),'review_card':core(card)}
    write_json(path.with_suffix('.context.json'),context);write_json(path.with_suffix('.schema.json'),read_json(SCHEMA))
    run_path=path.with_suffix('.run.json')
    print(f'REMEDIATION_GENERATE\t{rid}\t{obj}',flush=True)
    run={'id':'llm-remediation-'+uuid.uuid4().hex,'status':'running','provider':profile.provider,
         'model':profile.model,'profile_hash':profile.signature()}
    write_json(run_path,run)
    try:
        data,metadata=generate_json(PROMPT.read_text(encoding='utf-8')+'\n当前通过直接模型API执行。直接返回符合契约的JSON；上下文已经完整提供，不读取或写入本地文件。',
                                    context,read_json(SCHEMA),profile=profile,on_event=on_event,cancelled=cancelled)
    except Exception as error:
        run.update(status='error',error=type(error).__name__);write_json(run_path,run)
        raise
    run.update(status='completed',provider=metadata['provider'],model=metadata['model'],metadata=metadata)
    write_json(run_path,run)
    write_json(path.with_suffix('.candidate.json'),data)
    if source_hash(read_json(out/'agent_jobs'/rid/(safe_object_name(obj)+'.json')))!=context['review_source_hash']:
        raise RuntimeError('review changed during remediation generation')
    errors=validate(data,card)
    if errors:
        write_json(path.with_suffix('.invalid.json'),{'errors':errors})
        raise RuntimeError('model remediation rejected: '+'; '.join(errors))
    data['_meta']={'contract_hash':fingerprint(profile),'profile_hash':profile.signature(),'review_source_hash':source_hash(card),
                   'output_hash':hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
                   'validation_status':'valid','run_id':run['id'],'provider':metadata['provider'],'model':metadata['model'],
                   'validated_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    write_json(path,data);path.with_suffix('.invalid.json').unlink(missing_ok=True)
    return data


def run_enabled(board,rule_ids=None,objects=None):
    out=board/'review_out';config=out/'remediation_config.json'
    if not config.is_file():return
    for rid,items in read_json(config).items():
        if rule_ids and rid not in rule_ids:continue
        for obj,version in items.items():
            if version==VERSION and (not objects or obj in objects):generate(board,rid,obj)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--board',required=True);ap.add_argument('--rule',required=True)
    ap.add_argument('--objects',required=True);ap.add_argument('--enable',action='store_true');ap.add_argument('--force',action='store_true')
    args=ap.parse_args();board=resolve_board_path(args.board);out=board/'review_out';objects=args.objects.split(',')
    if args.enable:
        p=out/'remediation_config.json';config=read_json(p) if p.is_file() else {}
        config.setdefault(args.rule,{}).update({obj:VERSION for obj in objects});write_json(p,config)
    for obj in objects:
        if not configured(out,args.rule,obj):raise RuntimeError('object not opted in; use --enable')
        data=generate(board,args.rule,obj,force=args.force);print(data['summary'])
    return 0


if __name__=='__main__':raise SystemExit(main())
