"""Direct-API judge dispatcher. Models propose; application validators publish."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import hashlib
import json
from pathlib import Path
import uuid
from kernel.util import ROOT, read_json, out_dir
from agent_review.batching import safe_object_name
from agent_review.llm import LLMCancelled, LLMClient, load_profile
from agent_review.llm_tasks import task, atomic_json, check_cancelled

def _prompt(*names):
    return '\n\n'.join((ROOT/'agent_review/prompts'/name).read_text(encoding='utf-8')
                       for name in names if (ROOT/'agent_review/prompts'/name).is_file())

def _embedded(schema, name):
    def convert(value):
        if isinstance(value,list): return [convert(v) for v in value]
        if not isinstance(value,dict): return value
        return {k:('#/properties/'+name+v[1:] if k=='$ref' and isinstance(v,str) and v.startswith('#/') else convert(v))
                for k,v in value.items() if k not in ('$id','$schema')}
    return convert(copy.deepcopy(schema))

def _record(path, event):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf-8') as stream: stream.write(json.dumps(event,ensure_ascii=False)+'\n')

def _legacy_schema(rid,obj,context):
    """Expose the current shared contract; rule-specific semantics stay in validators."""
    properties={'rule_id':{'const':rid},'object':{'const':obj},
                'verdict_zh':{'enum':['是','否','待确认','不适用']}}
    for key in ('phenomenon','calc','stuck','conclusion','location_one_liner'):
        properties[key]={'type':'string','minLength':1}
    required=list(properties)
    if context.get('analysis_contract')=='analysis.v1':
        from agent_review.analysis_contract import step_schema
        properties.update(analysis_contract={'const':'analysis.v1'},analysis_steps=step_schema(),
            key_assumptions={'type':'array','items':{'type':'object',
                'required':['id','text','status','evidence_refs'],
                'properties':{'id':{'type':'string','minLength':1},'text':{'type':'string','minLength':1},
                    'status':{'enum':['supported','model_assumption','unresolved']},
                    'evidence_refs':{'type':'array','items':{'type':'string'}}}}})
        required+=['analysis_contract','analysis_steps','key_assumptions']
    return {'type':'object','properties':properties,'required':required}

def _controller_field_errors(card):
    fields={'_execution','_meta','context_hashes','context_keys','decision_contract_hash'} & card.keys()
    errors=['Model output must not supply controller provenance: '+', '.join(sorted(fields))] if fields else []
    meta=card.get('meta') or {}
    if not isinstance(meta,dict):return errors+['meta must be an object']
    if any(meta.get(key) for key in ('dry_run','deferred','batch_skip','interrupted','program_reference_only','from_program_stress')):
        errors.append('Model output must not claim a dry run, skipped task or program reference result')
    return errors

def _object(board,job,obj,run_id):
    client=LLMClient(load_profile())
    try:return _object_impl(board,job,obj,run_id,client)
    finally:client.close()

def _object_impl(board,job,obj,run_id,client):
    from agent_review.validate_job import validate_judge_card
    from agent_review.delivery import validate as validate_delivery
    from agent_review.evidence_loop import SCHEMA, validate_research
    rid=job['rule_id'];name=safe_object_name(obj)
    base=out_dir(board)/'agent_jobs'/rid/name
    context_path=base.with_suffix('.context.json')
    context=read_json(context_path) if context_path.is_file() else {'rule_id':rid,'object':obj}
    inputs=[context_path] if context_path.is_file() else []
    log=base.with_suffix('.run.jsonl')
    native=job.get('contract_version')=='review.v1'
    common=_prompt('judge/review_v1.common.md') if native else _prompt('_common_judge.md')
    policy=_prompt('judge/review_v1.power.md' if native else 'judge/_power_decision.md') if rid=='P11-A01' else ''
    analysis_prompt=_prompt('judge/'+rid+'.analysis.md','judge/'+rid+'.md')
    if not analysis_prompt: raise ValueError('No review prompt for '+rid)
    analysis_schema={'type':'object','properties':{'analysis':{'type':'string','minLength':40}},'required':['analysis'],'additionalProperties':False}
    messages=None;checkpoint=None;recovered_stage=None
    key=job.get('execution_hash')
    if key:
        checkpoint=Path(board)/'history/execution-checkpoints'/safe_object_name(rid)/name/key
        saved=checkpoint/'conversation.json'
        if job.get('resume_stage') and saved.is_file():
            from agent_review.execution_policy import checkpoint as valid_checkpoint
            try:
                saved_bytes=saved.read_bytes();prior=json.loads(saved_bytes)
                valid=valid_checkpoint(board,rid,obj,key)
                stage=read_json(checkpoint/'stage.json') if valid else {}
                conversation_hash=hashlib.sha256(saved_bytes).hexdigest()
                if not isinstance(prior,dict):prior={}
            except (OSError,ValueError,TypeError):
                prior={};valid=None;stage={};conversation_hash=None
            if prior.get('execution_hash')==key and valid and stage.get('conversation_hash')==conversation_hash:
                messages=prior.get('messages')
                recovered_stage=valid['stage'] if messages else None
                if recovered_stage:
                    rel=Path('review_out/agent_jobs')/rid/(name+'.analysis.md')
                    base.with_suffix('.analysis.md').write_bytes((Path(valid['root'])/rel).read_bytes())
                    inputs.append(base.with_suffix('.analysis.md'))
    phase_context={'rule_id':rid,'object':obj,'workbench':context,
                   'validation_errors':job.get('validation_errors',[]),'prior_card':job.get('prior_card'),
                   'available_files':__import__('agent_review.llm_tools',fromlist=['MaterialTools']).MaterialTools(board,inputs=inputs).list_materials()}
    phases=[('analysis_draft',analysis_prompt)]
    if job.get('self_check',True):
        phases.append(('decisive_check',_prompt('judge/review_v1.self_check.md' if native else 'judge/_deep_self_check.md',
                                              'judge/'+rid+'.self_check.md')))
    for phase,prompt in phases:
        check_cancelled()
        if recovered_stage=='self_check' or (recovered_stage=='analysis' and phase=='analysis_draft'):
            _record(log,{'type':'phase_reused','phase':phase,'run_id':run_id,'execution_hash':key})
            continue
        result,metadata=task(board,common+'\n'+policy+'\n'+prompt+
          '\nReturn {analysis: complete source-grounded engineering analysis in Markdown}. Do not deliver the final card yet.',
          phase_context,analysis_schema,inputs=inputs,messages=messages,log_path=log,
          assets_root=f'review_out/research_assets/{rid}/{name}',client=client)
        messages=metadata['messages']
        base.parent.mkdir(parents=True,exist_ok=True)
        base.with_suffix('.analysis.md').write_text(result['analysis'],encoding='utf-8')
        if base.with_suffix('.analysis.md') not in inputs: inputs.append(base.with_suffix('.analysis.md'))
        _record(log,{'type':'phase_completed','phase':phase,'run_id':run_id,'provider':metadata['provider'],'model':metadata['model']})
        if checkpoint:
            rel=Path('review_out/agent_jobs')/rid/(name+'.analysis.md')
            (checkpoint/rel).parent.mkdir(parents=True,exist_ok=True)
            (checkpoint/rel).write_text(result['analysis'],encoding='utf-8')
            atomic_json(checkpoint/'conversation.json',{'execution_hash':key,'messages':messages})
            atomic_json(checkpoint/'stage.json',{'execution_hash':key,'stage':'analysis' if phase=='analysis_draft' else 'self_check',
                'analysis_hash':hashlib.sha256((checkpoint/rel).read_bytes()).hexdigest(),
                'conversation_hash':hashlib.sha256((checkpoint/'conversation.json').read_bytes()).hexdigest()})
    if native:
        from agent_review.review_contract import SCHEMA_PATH
        card_schema=read_json(SCHEMA_PATH)
    else:
        card_schema=_legacy_schema(rid,obj,context)
    properties={'card':_embedded(card_schema,'card')}
    if job.get('autonomous_evidence'): properties['research']=_embedded(read_json(SCHEMA),'research')
    schema={'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
    errors=[]
    for attempt in range(3):
        result,metadata=task(board,common+'\n'+policy+
            '\nAssemble the final card strictly from the inspected and checked analysis. Return card'+
            (' and independent research receipt. Search logs must describe actual tool use; unresolved facts must remain explicit.' if 'research' in properties else '.')+
            '\nFix validation errors using evidence; never change source material or invent engineering facts. '
            'The API supplies the complete output schema. Do not read a schema file. '
            'Do not supply controller provenance, context hashes, _meta, _execution or dry-run flags.',
            {**phase_context,'validation_errors':errors or phase_context['validation_errors']},schema,
            inputs=inputs,messages=messages,log_path=log,assets_root=f'review_out/research_assets/{rid}/{name}',client=client)
        messages=metadata['messages'];card=result['card']
        errors=_controller_field_errors(card)+validate_judge_card(card,rule_id=rid,obj=obj,board=Path(board))+validate_delivery(card,context)
        if rid=='P11-A01' and not native:
            from agent_review.decision_contract import validate_decision_audit
            errors+=validate_decision_audit(card,required=True)
        if 'research' in properties:
            errors+=validate_research(result['research'],Path(board))
            pending=card.get('verdict_zh')=='待确认' or card.get('decision',{}).get('verdict')=='pending'
            if pending and not any(any(p['rule_id']==rid and p['object']==obj for p in r['affected_pairs']) for r in result['research'].get('requests',[])):
                errors.append('Pending review needs an unresolved request scoped to this object')
        atomic_json(base.with_suffix('.llm.json'),metadata)
        if not errors:
            check_cancelled()
            if 'research' in result: atomic_json(base.with_suffix('.research.json'),result['research'])
            atomic_json(base.with_suffix('.json'),card)
            for suffix in ('.failed.json','.invalid.json'):base.with_suffix(suffix).unlink(missing_ok=True)
            _record(log,{'type':'phase_completed','phase':'contract','run_id':run_id})
            return {'object':obj,'model':metadata['model'],'provider':metadata['provider'],'usage':metadata['usage']}
        atomic_json(base.with_suffix('.invalid.json'),{'errors':errors,'card':card,'attempt':attempt+1})
    raise ValueError('Card validation failed: '+'; '.join(errors[:8]))

def dispatch(board, *, concurrency=2):
    board=Path(board);source=read_json(out_dir(board)/'judge_jobs.json')
    def run_job(job):
        ident=uuid.uuid4().hex
        try:
            objects=[_object(board,job,obj,ident) for obj in job['objects']]
            return {'job_id':job['job_id'],'ok':True,'run':ident,'objects':objects,
                    'model':objects[-1]['model'],'provider':objects[-1]['provider']}
        except LLMCancelled: raise
        except Exception as exc:
            for obj in job['objects']:
                atomic_json(out_dir(board)/'agent_jobs'/job['rule_id']/(safe_object_name(obj)+'.failed.json'),
                            {'error':str(exc),'run_id':ident})
            return {'job_id':job['job_id'],'ok':False,'run':ident,'error':str(exc)}
    results=[]
    with ThreadPoolExecutor(max_workers=max(1,min(int(concurrency),8))) as pool:
        for future in as_completed([pool.submit(run_job,job) for job in source['jobs']]):
            results.append(future.result())
            atomic_json(out_dir(board)/'sdk_judge_result.json',{'attempt_id':source['attempt_id'],'transport':'llm-api','results':results})
    return {'results':results,'failed':sum(not x['ok'] for x in results)}
