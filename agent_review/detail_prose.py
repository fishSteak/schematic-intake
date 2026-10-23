"""Source-bound prose only. Original review cards are never outputs of this worker."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from agent_review.batching import safe_object_name
from agent_review.detail_view import digest, source_hash, scenario_rows, explicit_primary, native, QUANTITY
from kernel.runtime import node_executable
from kernel.util import ROOT, read_json, write_json, resolve_board_path

from agent_review.analysis_contract import VERSION as ANALYSIS_VERSION, assumptions_for, required_assumptions, step_schema, validate_links, has_direct_analysis
PROMPT=ROOT/'agent_review/prompts/judge/detail_prose_v2.md'
LEGACY_PROMPT=ROOT/'agent_review/prompts/judge/detail_prose_legacy_v1.md'
LEGACY_SCHEMA=ROOT/'agent_review/schemas/detail-prose.v1.schema.json'
WORKER=ROOT/'agent_review/detail_prose_agent.mjs'

def policy_hash():
    return digest([p.read_text(encoding='utf-8') for p in (PROMPT,WORKER,Path(__file__),ROOT/'agent_review/analysis_contract.py')])

def path_for(out,card):
    return Path(out)/'detail_prose'/safe_object_name(card['rule_id'])/(safe_object_name(card['object'])+'.json')

def context(out,card):
    analysis=Path(out)/'agent_jobs'/card['rule_id']/(safe_object_name(card['object'])+'.analysis.md')
    a=analysis.read_text(encoding='utf-8') if analysis.exists() else ''
    return {'key':card['rule_id']+'::'+card['object'],'source_hash':digest([source_hash(card),a]),
            'card':card,'analysis':a,'key_assumptions':assumptions_for(card),'required_assumption_refs':required_assumptions(card),'scenarios':[{'id':r['id'],'title':r['title']} for r in scenario_rows(card)],
            'explicit_primary':explicit_primary(card)}

def mask_source_identifiers(step,ctx):
    card=ctx['card'];identifiers={card['object']}
    # Signed, compact voltage rail names are material identifiers, even when
    # mentioned as an adjacent/absent rail rather than an object pin network.
    source_text=json.dumps(card,ensure_ascii=False)+'\n'+ctx.get('analysis','')
    identifiers.update(re.findall(r'(?<![A-Za-z0-9_])[+\-]\d+(?:\.\d+)?V(?![A-Za-z0-9_])',source_text))
    nets=card.get('nets') or {}
    if isinstance(nets,dict):identifiers.update(x for x in nets.values() if isinstance(x,str) and x)
    for key in card.get('context_keys',[]):
        if isinstance(key,str) and key.startswith('net:'):identifiers.add(key[4:])
    for key in ('net','node'):
        value=(card.get('electrical') or {}).get(key)
        if isinstance(value,str) and re.fullmatch(r'[A-Za-z_+\-][\w+.\-]*',value):identifiers.add(value)
    for state in (card.get('solution') or {}).get('states',[]):
        for line in state.get('steps',[]):
            if isinstance(line,str):
                found=re.match(r'^\s*网\s*=\s*(\S+)',line)
                if found:identifiers.add(found[1])
    for identifier in sorted(identifiers,key=len,reverse=True):
        step=re.sub(r'(?<![A-Za-z0-9_])'+re.escape(identifier)+r'(?![A-Za-z0-9_])','[来源标识]',step)
    return step


def validate(data,ctx,*,legacy=False):
    from jsonschema import Draft202012Validator
    errors=[e.message for e in Draft202012Validator((read_json(LEGACY_SCHEMA) if legacy else schema())['properties']['records']['items']).iter_errors({k:v for k,v in data.items() if k!='_meta'})]
    if errors:return errors
    if data.get('key')!=ctx['key'] or data.get('source_hash')!=ctx['source_hash']:errors.append('source mismatch')
    if data.get('status') not in {'ready','source_insufficient','source_conflict'}:errors.append('invalid status')
    steps=data.get('reasoning_steps',[]) if legacy else [s['text'] for s in data.get('analysis_steps',[])]
    if not legacy:
        errors.extend(validate_links(data.get('analysis_steps',[]),ctx['key_assumptions'],ctx['required_assumption_refs'] if data.get('status')=='ready' else ()))
    if not isinstance(steps,list) or len(steps)>3 or any(not isinstance(s,str) or not s.strip() or re.search(QUANTITY,mask_source_identifiers(s,ctx)) for s in steps):errors.append('invalid thinking steps')
    if data.get('status')=='ready' and (not steps or (ctx['card']['object']!='(none)' and ctx['card']['object'] not in ' '.join(steps))):errors.append('missing specific object thinking')
    ids={s['id'] for s in ctx['scenarios']}
    if data.get('primary_ref') and data['primary_ref'] not in ids:errors.append('unknown primary reference')
    if data.get('status')=='ready' and ctx['explicit_primary'] and data.get('primary_ref')!=ctx['explicit_primary']:errors.append('explicit primary changed')
    if data.get('status')=='ready' and data.get('primary_ref') and not data.get('route_summary'):errors.append('missing route summary')
    def leaves(v):
        if isinstance(v,str):return [v]
        if isinstance(v,dict):return [s for x in v.values() for s in leaves(x)]
        if isinstance(v,list):return [s for x in v for s in leaves(x)]
        return []
    sources={'card':json.dumps(ctx['card'],ensure_ascii=False)+'\n'+'\n'.join(leaves(ctx['card'])),'analysis':ctx['analysis']}
    citations=data.get('citations',[])
    if data.get('status')=='ready' and not citations:errors.append('missing citations')
    for c in citations:
        if not c.get('quote') or c.get('quote') not in sources.get(c.get('source'),''):errors.append('citation not in supplied source')
    if data.get('status')!='ready' and not data.get('note'):errors.append('missing insufficiency note')
    return errors

def load(out,card):
    p=path_for(out,card)
    if not p.exists():return None
    d=read_json(p);m=d.get('_meta',{});payload={k:v for k,v in d.items() if k!='_meta'}
    if m.get('output_hash')!=digest(payload) or not m.get('run_id'):return None
    ctx=context(out,card)
    legacy='reasoning_steps' in d and 'analysis_steps' not in d
    if legacy:
        # Explicit historical compatibility: no assumptions need merging, sources
        # unchanged, and real output attested to the frozen v1 prompt/schema.
        if ctx['key_assumptions']:return None
        if m.get('prompt_hash')!=digest(LEGACY_PROMPT.read_text(encoding='utf-8')) or m.get('schema_hash')!=digest(read_json(LEGACY_SCHEMA)):return None
        if validate(d,ctx,legacy=True):return None
    elif m.get('policy_hash')!=policy_hash() or validate(d,ctx):return None
    from agent_review.detail_view import verify_prose_provenance
    verify_prose_provenance(out,d)
    return d


def schema():
    string={'type':'string'}
    props={k:string for k in ['key','source_hash','route_summary','note']}
    props.update(analysis_contract={'const':ANALYSIS_VERSION},status={'enum':['ready','source_insufficient','source_conflict']},primary_ref={'type':['string','null']},
                 analysis_steps=step_schema(),citations={'type':'array','items':{'type':'object','required':['source','quote'],'additionalProperties':False,'properties':{'source':{'enum':['card','analysis']},'quote':string}}})
    return {'type':'object','additionalProperties':False,'required':['records'],'properties':{'records':{'type':'array','items':{'type':'object','additionalProperties':False,'required':list(props),'properties':props}}}}

def generate_batch(board,cards):
    out=board/'review_out';contexts=[context(out,c) for c in cards]
    key=digest([c['source_hash'] for c in contexts])[:20]
    path=out/'detail_prose'/'runs'/(key+'-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.json')
    write_json(path.with_suffix('.context.json'),{'records':contexts})
    write_json(path.with_suffix('.schema.json'),schema())
    env=os.environ.copy();env.setdefault('SCHEMATIC_PYTHON',sys.executable)
    if not env.get('SCHEMATIC_CODEX'):
        candidates=list((Path(os.environ['LOCALAPPDATA'])/'OpenAI/Codex/bin').glob('*/codex.exe'))
        if not candidates:raise RuntimeError('Codex CLI executable missing')
        env['SCHEMATIC_CODEX']=str(max(candidates,key=lambda p:p.stat().st_mtime))
    for attempt in range(3):
        result=subprocess.run([node_executable(),str(WORKER),str(board),str(path),str(PROMPT),str(path.with_suffix('.run.json'))],cwd=ROOT,env=env)
        if result.returncode:continue
        data=read_json(path)
        write_json(path.with_suffix(f'.attempt{attempt+1}.json'),data)
        write_json(path.with_suffix(f'.attempt{attempt+1}.run.json'),read_json(path.with_suffix('.run.json')))
        write_json(path.with_suffix(f'.attempt{attempt+1}.context.json'),read_json(path.with_suffix('.context.json')))
        write_json(path.with_suffix(f'.attempt{attempt+1}.schema.json'),schema())
        records=data.get('records',[])
        bykey={r.get('key'):r for r in records};errors=[]
        if len(records)!=len(contexts) or len(bykey)!=len(contexts):errors.append('batch count mismatch')
        for ctx in contexts:errors.extend(ctx['key']+': '+e for e in validate(bykey.get(ctx['key'],{}),ctx))
        write_json(path.with_suffix('.validation.json'),{'errors':errors,'attempt':attempt+1})
        if errors:
            write_json(path.with_suffix('.context.json'),{'records':contexts,'validation_feedback':errors,'instruction':'修正上一轮校验错误；分析思路不得列已知材料参数值或计算结果；合并条件与推导，按 key_assumptions 引用并保留条件性。保留当前对象原有标识（器件编号、网名等），标识中的数字不属于计算数值；不要用本条对象等通用称呼替代当前对象标识。仅写核查和推导过程。'})
            print('DETAIL_RETRY',key,errors,flush=True);continue
        run=read_json(path.with_suffix('.run.json'))
        for card,ctx in zip(cards,contexts):
            if context(out,card)['source_hash']!=ctx['source_hash']:raise RuntimeError('source changed during prose generation')
            row=bykey[ctx['key']]
            row['_meta']={'policy_hash':policy_hash(),'output_hash':digest(row),'run_id':run['id'],'model':'gpt-6.1-sol',
                          'prompt_hash':digest(PROMPT.read_text(encoding='utf-8')),'schema_hash':digest(schema()),'batch_output':str(path.relative_to(out)),'generated_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
            write_json(path_for(out,card),row)
        print('DETAIL_BATCH_READY',key,len(cards),flush=True);return
    raise RuntimeError(f'detail prose failed: {key}; publication not started')

def ensure(board,verdicts,objects=None):
    cards=[dict(c,rule_id=r['rule_id']) for r in verdicts['results'] for c in r.get('object_cards',[])]
    todo=[c for c in cards if not has_direct_analysis(c) and (objects is None or c['object'] in objects) and not load(board/'review_out',c)]
    print('DETAIL_PROSE_PENDING',len(todo),flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(generate_batch,board,todo[i:i+16]) for i in range(0,len(todo),16)]
        for f in as_completed(futures):f.result()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--board',required=True);ap.add_argument('--objects')
    args=ap.parse_args();board=resolve_board_path(args.board)
    ensure(board,read_json(board/'review_out/verdicts.json'),set(args.objects.split(',')) if args.objects else None)

if __name__=='__main__':main()
