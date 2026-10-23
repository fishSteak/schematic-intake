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
from agent_review.llm import (LLMCancelled, LLMClient, load_profile, LLMError,
    LLMTransportError, LLMHTTPError, BudgetExceeded, ConfigurationError,
    OutputValidationError, ProtocolError)
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

def failure_category(error):
    """Only model output/contract failures can be fixed by semantic repair."""
    if isinstance(error,OutputValidationError):return {'error_kind':'output_validation','repairable':True}
    if isinstance(error,LLMTransportError):return {'error_kind':error.kind,'repairable':False}
    if isinstance(error,LLMHTTPError):return {'error_kind':'http','status_code':error.status_code,'repairable':False}
    if isinstance(error,BudgetExceeded):return {'error_kind':'budget','repairable':False}
    if isinstance(error,ConfigurationError):return {'error_kind':'configuration','repairable':False}
    if isinstance(error,ProtocolError):return {'error_kind':'protocol','repairable':False}
    return {'error_kind':'execution','repairable':False}

def raise_for_execution_failures(result):
    failures=[row for row in result.get('results',[]) if not row.get('ok') and row.get('repairable') is False]
    if failures:
        row=failures[0]
        raise LLMError('Review execution failed ('+row['error_kind']+'): '+row['error'])

def _legacy_schema(rid,obj,context):
    """Describe the legacy delivery contract, retaining semantic checks in validators.

    The field catalogue in rules/object_card_schema.json is documentation, not a
    JSON Schema. In particular, it cannot describe current power decision audits
    or the distinction between a bound and a ranked operating point.
    """
    from agent_review.validate_job import _DEEP, _P11
    from agent_review.main_analysis import SCHEMA_PATH, VERSION as MAIN_VERSION
    text={'type':'string','minLength':1}
    number={'type':['number','null'],'minimum':0}
    index={'type':['integer','null'],'minimum':0}
    references={'type':'array','items':copy.deepcopy(text),'uniqueItems':True}
    explanation={'anyOf':[copy.deepcopy(text),{'type':'array','minItems':1,'items':copy.deepcopy(text)}]}
    def object_schema(properties, required=()):
        return {'type':'object','properties':properties,'required':list(required)}

    properties={'rule_id':{'const':rid},'object':{'const':obj},
                'verdict_zh':{'enum':['是','否','待确认','不适用']}}
    for key in ('phenomenon','calc','stuck','conclusion','location_one_liner'):
        properties[key]=copy.deepcopy(text)
    required=list(properties)
    properties.update(
        headline_zh={**text,'maxLength':90,'description':'对象评审摘要：位号：场景、数字来源、结果与限额或缺口。'},
        summary=copy.deepcopy(text),action=copy.deepcopy(text),
        evidence_refs={'type':'array','description':'实际核查的原始出处。假设和证明引用这里的 id 或 path；不得编造引文。',
            'items':object_schema({'id':copy.deepcopy(text),'path':copy.deepcopy(text),
                'url':copy.deepcopy(text),'locator':copy.deepcopy(text),'quote':copy.deepcopy(text),
                'pdf_page':{'type':'integer','minimum':1},'sha256':{'type':'string'}},())})
    verdicts=[]
    for value in ('是','否','待确认','不适用'):
        branch={'properties':{'verdict_zh':{'const':value}},'required':['verdict_zh']}
        if value=='是':
            branch['required'].append('evidence_refs')
            branch['properties']['evidence_refs']={'minItems':1}
        if value in ('否','待确认'):branch['required']+=['summary','action']
        if rid in _DEEP and value!='不适用':branch['required'].append('headline_zh')
        verdicts.append(branch)
    if context.get('analysis_contract')=='analysis.v1':
        from agent_review.analysis_contract import step_schema
        properties.update(analysis_contract={'const':'analysis.v1'},analysis_steps=step_schema(),
            key_assumptions={'type':'array','items':{'type':'object',
                'required':['id','text','status','evidence_refs'],
                'properties':{'id':copy.deepcopy(text),'text':copy.deepcopy(text),
                    'status':{'enum':['supported','model_assumption','unresolved']},
                    'evidence_refs':copy.deepcopy(references)}}})
        required+=['analysis_contract','analysis_steps','key_assumptions']
    else:
        properties['reasoning_steps']={'type':'array','minItems':3 if rid in _DEEP else 1,
                                      'items':copy.deepcopy(text)}
        required.append('reasoning_steps')

    path={'type':'array','minItems':2,'items':copy.deepcopy(text)}
    marker=object_schema({'on':copy.deepcopy(text),'title':copy.deepcopy(text),'body':{'type':'string'},
                          'quantity_ref':{'enum':['/decision_basis/value_w','/electrical/P_limit']}},('on','title'))
    page=object_schema({'pdf_page':{'type':'integer','minimum':1},'label':copy.deepcopy(text),
        'route_summary':copy.deepcopy(text),'path':copy.deepcopy(path),
        'paths':{'type':'array','minItems':1,'items':copy.deepcopy(path),
                 'description':'每条路径直接是名字数组，如 [["NET","R.1","R.2","GND"]]；不是含 path 的对象。'},
        'markers':{'type':'array','items':marker},
        'through_body':{'type':'array','items':object_schema({k:copy.deepcopy(text) for k in ('ref','from','to','note')},('ref','from','to','note'))},
        'notes':{'anyOf':[{'type':'string'},{'type':'array','items':copy.deepcopy(text)}]}},('pdf_page','markers'))
    page['anyOf']=[{'required':['path']},{'required':['paths']}]
    figure=object_schema({'kind':{'enum':['operating_path','cross_case_relations']},
                          'pages':{'type':'array','minItems':1,'items':page}},('pages',))
    formula=object_schema({'expr':{**text,'description':'带实际代入和单位的等式或上下界不等式；≤/≥保留其真实含义。'},
                           'src':{**text,'description':'本式使用的原始材料、定位或已完成推导，不能写虚构出处。'}},('expr','src'))
    state=object_schema({'name_zh':{**text,'description':'具体通路、工作条件或边界，不用纯分类词。'},
        'case_id':copy.deepcopy(text),'status':{**text,'description':'modeled/confirmed 为已支持模型；conditional 为前提待确认；unmapped 为未完成建模。'},
        'role':{**text,'description':'primary 为正式依据；comparison 为对照；worst/worst_candidate 只标可排名工作点；边界不冒称工作点。'},
        'stress_kind':{'enum':['modeled','lower_bound','upper_bound','exact',None],
                       'description':'区分模型点值和上下界；未建模可为 null，不能为通过校验把上界改为点值。'},
        'P':copy.deepcopy(number),'V':copy.deepcopy(number),'conditions':copy.deepcopy(text),
        'route_summary':copy.deepcopy(text),'conclusion':copy.deepcopy(text),'结论':copy.deepcopy(text),
        'why_worst':copy.deepcopy(text),'formula_lines':{'type':'array','items':formula},
        'figure':{'anyOf':[{'type':'null'},figure]}},('name_zh',))
    properties['states']={'type':'array','items':state}
    if rid in _P11:
        properties['states']['minItems']=1
        required+=['states','electrical']
        quantity='P' if rid=='P11-A01' else 'V'
        electrical={quantity+suffix:copy.deepcopy(number) for suffix in ('_rated','_limit','_max')}
        electrical[quantity+'_max']['description']='只有已证明全局工作包络最大值时填写；上界或未知精确最大值使用 null。'
        electrical['cite']={'anyOf':[{'type':'string'},{'type':'array','items':copy.deepcopy(text)}],
                            'description':'额定值及适用范围的准确厂家资料出处，可用本卡证据 ID 数组；缺资料时留空，不编造。'}
        properties['electrical']=object_schema(electrical,tuple(electrical))
        verdicts[0]['properties']['electrical']={'properties':{
            quantity+'_rated':{'type':'number','minimum':0},quantity+'_limit':{'type':'number','minimum':0},
            'cite':copy.deepcopy(explanation)}}
    if rid=='P11-A01':
        state['required']+=['P','formula_lines']
        basis=object_schema({'kind':{'enum':['reachable_state','proven_lower_bound','normal_design_model','envelope_upper_bound','insufficient']},
            'state_index':{**index,'description':'正式依据在 states 中的索引。envelope_upper_bound 必须指向非 conditional/unmapped 的 upper_bound；其他充分判据也不得采用未确认条件候选。'},
            'value_w':{**number,'description':'与 state_index 指向状态的 P 数值一致，单位 W。'},'limit_w':copy.deepcopy(number),
            'evidence':copy.deepcopy(explanation),'conditions':copy.deepcopy(explanation),
            'model_scope':{'const':'nominal_design'},'applicability':copy.deepcopy(text),
            'coverage_argument':copy.deepcopy(text)},('kind',))
        complete={'required':['kind','state_index','value_w','limit_w','evidence','conditions'],
                  'properties':{'kind':{'enum':['reachable_state','proven_lower_bound','envelope_upper_bound']},
                    'state_index':{'type':'integer','minimum':0},'value_w':{'type':'number','minimum':0},'limit_w':{'type':'number','minimum':0}}}
        modeled=copy.deepcopy(complete)
        modeled['properties']['kind']={'const':'normal_design_model'}
        modeled['required']+=['model_scope','applicability','coverage_argument']
        basis['anyOf']=[{'properties':{'kind':{'const':'insufficient'}},'required':['kind']},complete,modeled]
        properties['decision_basis']=basis
        ranking={**index,'description':'只指向数值可比较的 modeled/exact 工作点。upper_bound/lower_bound 不参与排名；无合格点则 null。confirmed 排除 conditional，conditional 只选 conditional；选中项 role 分别为 worst/worst_candidate。'}
        properties['worst_case']=object_schema({'confirmed_state_index':copy.deepcopy(ranking),
            'conditional_state_index':copy.deepcopy(ranking),
            'comparison':{'type':'array','items':object_schema({'state_index':{'type':'integer','minimum':0},
                'reason':copy.deepcopy(text)},('state_index','reason')),'description':'每个 states 索引恰好一条，解释比较或排除的原因。'},
            'unresolved':{'type':'array','items':copy.deepcopy(text)}},
            ('confirmed_state_index','conditional_state_index','comparison','unresolved'))
        gap=object_schema({'issue':copy.deepcopy(text),'effect':{'enum':['maximum_only','precision_only','can_change_verdict']},
            'reason':copy.deepcopy(text),
            'countercase':object_schema({'description':copy.deepcopy(text),'evidence':copy.deepcopy(explanation),
                'decision_effect':copy.deepcopy(text)},('description','evidence','decision_effect')),
            'proof_gap':object_schema({'failed_claim':copy.deepcopy(text),'reason':copy.deepcopy(text),
                'evidence':copy.deepcopy(explanation),'decision_effect':copy.deepcopy(text)},
                ('failed_claim','reason','evidence','decision_effect'))},('issue','effect','reason'))
        gap['anyOf']=[{'properties':{'effect':{'enum':['maximum_only','precision_only']}},'required':['effect']},
                      {'properties':{'effect':{'const':'can_change_verdict'}},'required':['effect'],
                       'anyOf':[{'required':['countercase']},{'required':['proof_gap']}]}]
        properties['decision_audit']=object_schema({'version':{'const':2},'normal_design_search':copy.deepcopy(text),
            'state_reviews':{'type':'array','items':object_schema({'state_index':{'type':'integer','minimum':0},
                'admissibility':{'enum':['normal_design','conditional','unmapped','out_of_scope']},
                'reason':copy.deepcopy(text)},('state_index','admissibility','reason')),
                'description':'逐个审计所有 states，每个索引恰好一条。'},
            'gaps':{'type':'array','items':gap}},('version','normal_design_search','state_reviews','gaps'))
        required+=['decision_basis','worst_case','decision_audit']
        properties['main_analysis']=_embedded(read_json(SCHEMA_PATH),'main_analysis')
        if context.get('main_analysis_contract')==MAIN_VERSION:
            required.append('main_analysis')
            state['properties']['figure']={'type':'null','description':'启用 main_analysis 时图只有一个归属。'}
        # Even when optional, main_analysis has one diagram owner. It never adds
        # a second prose block beside the unified analysis_steps.
    return {'type':'object','properties':properties,'required':required,'anyOf':verdicts}


def _legacy_delivery_prompt(rid):
    prompt=_prompt('judge/'+rid+'.md')+'''

应用交付约定：本轮返回卡片 JSON，不写文件。API Schema 已列出本轮基础字段及本检查项扩展字段；
按 Schema、原始材料及已核查分析交付，不依赖分析轮的记忆补猜字段，不读取其他 Schema 文件。
analysis_steps 是唯一业务“分析思路”，整合条件和因果推导；key_assumptions 只作状态与证据追溯，
不重复生成“成立前提/判断思路”两段文案。模型假设和未确认前提在分析中保留限定。
图的 paths 必须是二维名字数组，每条至少两个名字；不使用 {path:[...]} 分支对象。
若输出 main_analysis，图只归 main_analysis.figure，states[].figure 省略或 null。
若交付 research，事实的 evidence 必须直接引用原始材料或独立资料；本轮生成的分析、卡片及研究记录
可以描述推导过程，但不能充当它们自身结论的原始证据。
'''
    if rid=='P11-A01':prompt+='''
功耗的 upper_bound/lower_bound 与 modeled/exact 工作点保持区分，条件上界可以作为 comparison，
但不填 worst_case.confirmed_state_index/conditional_state_index，也不标 worst/worst_candidate。
排名索引只选择符合条件的数值工作点，无合格点才为 null；正式依据仍由 decision_basis 指定。
envelope_upper_bound 的 state_index 必须指向非 conditional/unmapped、role 非 worst_candidate/unmapped 的 upper_bound 状态，
value_w 与该状态的 P 一致；未确认条件下的辅助边界不能冒充已证明通过依据。
上界公式允许使用 ≤，下界允许 ≥，必须保留实际代入、单位和来源，不能改成未经证明可达的等号。
功耗上下界的数值代入请以 W 给出最终结果，并与 states[].P 一致；若另展示 mW，明确列出单位换算。
formula_lines.expr 写可核对的数学关系，解释与适用条件写在 src 或 analysis_steps；
多个独立功率算式分行或用分号分开，普通功耗点说明不能代替完整的上下界数值推导。
'''
    return prompt

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
    if not analysis_prompt: raise ConfigurationError('No review prompt for '+rid)
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
        result,metadata=task(board,common+'\n'+policy+'\n'+('' if native else _legacy_delivery_prompt(rid))+
            '\nAssemble the final card strictly from the inspected and checked analysis. Return card'+
            (' and independent research receipt. Search logs must describe actual tool use; unresolved facts must remain explicit.' if 'research' in properties else '.')+
            '\nFix validation errors using evidence; never change source material or invent engineering facts. '
            'The API supplies the structural output contract; the application also checks sources and engineering consistency. Do not read a schema file. '
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
    raise OutputValidationError('Card validation failed: '+'; '.join(errors[:8]))

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
            failure={'error':str(exc),**failure_category(exc)}
            for obj in job['objects']:
                atomic_json(out_dir(board)/'agent_jobs'/job['rule_id']/(safe_object_name(obj)+'.failed.json'),
                            {**failure,'run_id':ident})
            return {'job_id':job['job_id'],'ok':False,'run':ident,**failure}
    results=[]
    with ThreadPoolExecutor(max_workers=max(1,min(int(concurrency),8))) as pool:
        for future in as_completed([pool.submit(run_job,job) for job in source['jobs']]):
            results.append(future.result())
            atomic_json(out_dir(board)/'sdk_judge_result.json',{'attempt_id':source['attempt_id'],'transport':'llm-api','results':results})
    return {'results':results,'failed':sum(not x['ok'] for x in results)}
