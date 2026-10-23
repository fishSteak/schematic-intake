"""Presentation-only adapters. Never infer a verdict or an electrical extreme."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

VERSION='detail.v2'
SCHEMA_PATH=Path(__file__).resolve().parent/'schemas/review_detail.v2.schema.json'
REGISTRY=json.loads((Path(__file__).resolve().parents[1]/'report_ui/src/review-modules.json').read_text(encoding='utf-8'))
MODULES=[m['id'] for m in REGISTRY]
TITLES={m['id']:m['title'] for m in REGISTRY}
QUANTITY=r'(?<![A-Za-z_0-9])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\s*(?:[munpµμkM]?[WAVF]|[kM]?Hz|[kM]?ohm|[kM]?Ω|%|℃|°C|degC|ms|s|mm|毫瓦|瓦|伏|安|欧姆|微法|纳法|皮法)(?![A-Za-z_0-9])'
LABELS={'P_rated':('额定功率','W'),'P_limit':('功耗限额','W'),'P_max':('已记录最大功耗','W'),
        'V_rated':('额定耐压','V'),'V_limit':('电压限额','V'),'V_max':('已记录最大端压','V'),
        'P_upper_bound':('已记录功耗上界','W'),'V_min':('工作电压下端','V'),'limit_v':('评审电压限额','V'),
        'node':('评审节点',''),'net':('关联网络',''),'ratio':('采用比例',''),'note':('参数说明',''),'scope':('覆盖范围',''),
        'approval_source':('批准资料来源',''),'approval_note':('批准说明',''),'approval_review':('批准核查记录',''),
        'output_basis':('输出类型依据',''),'output_node_note':('输出节点说明',''),'external':('外部接口属性',''),'anti_reverse_basis':('反接保护依据',''),
        'is_switching_supply_output':('开关电源输出属性',''),'is_switching_power_output':('开关电源输出属性',''),'is_switching_power_supply_output':('开关电源输出属性',''),
        'R':('阻值','Ω'),'dielectric':('介质',''),'temperature_range':('温度范围',''),
        'is_external':('对外接口',''),'pin_definition':('引脚定义',''),
        'structural_keying':('结构防呆',''),'controlled_pin_definition':('受控接线定义','')}

def digest(data):return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def source_hash(card):return digest({k:v for k,v in card.items() if k not in ('review_detail','appendix','presentation')})

def text(value):
    if value is None:return '未提供'
    if isinstance(value,bool):return '是' if value else '否'
    if isinstance(value,(dict,list)):return json.dumps(value,ensure_ascii=False)
    return str(value)

def value_text(value,unit=''):
    if value is None:return '尚未确定'
    if isinstance(value,(float,int)) and not isinstance(value,bool):
        return ((f'{value*1000:.2f}' if abs(value*1000)>=0.01 or value==0 else f'{value*1000:.6g}')+' mW') if unit=='W' else f'{value:g} {unit}'.strip()
    return text(value)+((' '+unit) if unit else '')

def native(card):return card.get('review_v1') or (card if card.get('schema_version')=='review.v1' else None)

def raw_states(card):
    return card.get('states') or (card.get('solution') or {}).get('states') or (card.get('formula') if isinstance(card.get('formula'),dict) else {}).get('states') or []


def is_checklist_state(state):
    # Legacy program cards encode per-pin/per-condition checks as "states".
    # Their text steps are checks, not operating points or electrical calculations.
    return isinstance(state,dict) and 'steps' in state and not any(k in state for k in ('formula_lines','lines','metric','P','V','V_across','I','voltage_v','conditions','role','figure'))


def scenario_rows(card):
    return [{'id':str(s.get('case_id') or ('state-'+digest(s)[:16])), 'raw':s,
             'title':s.get('name_zh') or s.get('name') or s.get('label') or s.get('case_id') or '已记录工况'} for s in raw_states(card) if isinstance(s,dict) and not is_checklist_state(s)]


def basis_source_hash(card):
    main=card.get('main_analysis') or {};index=main.get('state_index');states=card.get('states') or []
    state=states[index] if type(index) is int and 0<=index<len(states) else None
    return digest({'main':main,'basis':card.get('decision_basis'),'state':state,'assumptions':card.get('key_assumptions'),'electrical':card.get('electrical')})


def explicit_primary(card):
    if card.get('main_analysis') is not None:
        from agent_review.main_analysis import selected_index
        index=selected_index(card)
        states=raw_states(card)
        if index is None or not 0<=index<len(states):return None
        state=states[index]
        return str(state.get('case_id') or ('state-'+digest(state)[:16]))
    rows=scenario_rows(card)
    from agent_review.presentation import primary_state_index
    index=primary_state_index([r['raw'] for r in rows])
    return rows[index]['id'] if index is not None else None


def usable_steps(card):
    steps=card.get('reasoning_steps') or []
    return bool(steps and len(steps)<=3 and all(isinstance(s,str) and s.strip() and not re.search(QUANTITY,s) for s in steps)
                and str(card.get('object') or '') in ' '.join(steps))

def needs_prose(card):
    from agent_review.analysis_contract import has_direct_analysis
    return not has_direct_analysis(card)


def enabled(out):
    p=Path(out)/'detail_presentation.json'
    return not p.exists() or json.loads(p.read_text(encoding='utf-8')).get('enabled') is True

def evidence_rows(items):
    rows=[]
    for i,item in enumerate(items or []):
        e=item if isinstance(item,dict) else {'description':str(item)}
        rows.append(dict(e,id=e.get('id',str(i)),display_label=e.get('label') or e.get('source_ref') or e.get('path') or '来源记录',
                         display_text=e.get('observation') or e.get('description') or e.get('note') or '',display_locator=e.get('locator') or e.get('pdf_page') or ''))
    return rows


def verify_prose_provenance(out, prose):
    """Require the sidecar to be byte-equivalent JSON to a real completed CLI output."""
    meta=prose.get('_meta',{})
    batch=(Path(out)/meta.get('batch_output','')).resolve()
    if not batch.is_relative_to(Path(out).resolve()) or not batch.is_file():
        raise RuntimeError('Presentation prose batch output missing or outside report')
    run=json.loads(batch.with_suffix('.run.json').read_text(encoding='utf-8'))
    if run.get('status')!='completed' or run.get('id')!=meta.get('run_id'):
        raise RuntimeError('Presentation prose completed run does not match')
    records=json.loads(batch.read_text(encoding='utf-8')).get('records',[])
    raw=next((r for r in records if r.get('key')==prose.get('key')),None)
    if raw is None or digest(raw)!=meta.get('output_hash'):
        raise RuntimeError('Presentation prose differs from collected model output')
    ctx=json.loads(batch.with_suffix('.context.json').read_text(encoding='utf-8'))
    if not any(r.get('key')==prose.get('key') and r.get('source_hash')==prose.get('source_hash') for r in ctx['records']):
        raise RuntimeError('Presentation prose source is not in the recorded model workbench')

def unified_analysis(view,card,out,prose=None):
    from agent_review.analysis_contract import VERSION as analysis_version, assumptions_for
    from agent_review.detail_prose import load
    n=native(card)
    assumptions=assumptions_for(card)
    if n and 'analysis_steps' in n:
        steps=[{'text':s['text'],'assumption_refs':s['premise_refs']} for s in n['analysis_steps']]
        origin='native_model';provenance=n.get('_execution',{})
    else:
        prose=prose or (load(out,card) if card.get('rule_id') and card.get('object') else None)
        if prose and 'analysis_steps' in prose:
            steps=prose['analysis_steps'];origin='merged_model';provenance=prose['_meta']
        elif prose and not assumptions:
            steps=[{'text':s,'assumption_refs':[]} for s in prose.get('reasoning_steps',[])]
            origin='legacy_model_reused';provenance=prose['_meta']
        elif card.get('analysis_contract')==analysis_version:
            steps=card['analysis_steps'];origin='native_model';provenance=card.get('_execution',{})
        else:
            steps=[];origin='unavailable';provenance={}
    view.update(analysis_contract=analysis_version,analysis_steps=steps,key_assumptions=assumptions,
                analysis_origin=origin,analysis_provenance=provenance)
    view.pop('premises',None);view.pop('reasoning_steps',None)
    return view


def build(card,out,presented=None):
    """One view for source formats; presented contributes generated media only."""
    out=Path(out);presented=presented or card;v1=native(card)
    if v1:
        from agent_review.review_contract import detail_view
        v=detail_view(v1,out)
        v.update(version=VERSION,module_order=MODULES,module_titles=TITLES,assessment_title='判定要点',
                 assessment_rows=[{'label':'评审限额','text':v['limit_text']},{'label':v['primary_label'],'text':v['primary_value_text']}],
                 assessment_text=v['selection_reason'],assessment_checks=[],prose_status='native',original_reasoning=[],notes=[])
        v['evidence']=evidence_rows(v.get('evidence',[]))
        v.pop('global_value_text',None);v.pop('actions',None)
        v['prose_provenance']=v1.get('_execution',{})
        return unified_analysis(v,card,out)
    from agent_review.detail_prose import load
    prose=load(out,card) if needs_prose(card) else None
    if prose:verify_prose_provenance(out,prose)
    zh=card.get('verdict_zh','待确认');na=zh=='不适用'
    steps=[]
    if prose and prose.get('reasoning_steps') and prose.get('citations'):steps=prose['reasoning_steps']
    notes=[]
    if prose and prose['status']!='ready':notes.append(prose['note'])
    if prose and 'analysis_steps' in prose:steps=[s['text'] for s in prose['analysis_steps']]
    if card.get('analysis_contract')=='analysis.v1':steps=[s['text'] for s in card['analysis_steps']]
    if not steps:notes.append('现有记录不足以整理具体分析思路，需后续复审补齐。')
    rows=scenario_rows(card);primary_ref=explicit_primary(card)
    if not card.get('main_analysis') and prose and prose['status']=='ready' and prose.get('primary_ref'):primary_ref=prose['primary_ref']
    media=[]
    try:
        from kernel.report_workbench import formula_of
        media=formula_of(presented).get('states',[])
    except (TypeError,ValueError,KeyError):pass
    cases=[]
    for r in rows:
        s=r['raw'];m=next((x for x in media if (x.get('name') or x.get('name_zh'))==r['title'] or x.get('case_id')==r['id']),{})
        if r['id']==primary_ref:
            # Every check uses the same generated-media index. Old non-P11
            # cards have no solution adapter and otherwise silently lose images.
            from agent_review.stress_figures import lookup
            generated=lookup(out,str(card.get('rule_id')),str(card.get('object')),raw_states(card).index(s)) or {}
            if generated.get('status')=='ok' and generated.get('flow'):
                m=dict(m,figure=generated['flow'],figure_pages=generated.get('pages',[]),
                       figure_status=generated['status'],figure_reason=generated.get('reason',''))
        unit=s.get('metric_unit','');value=s.get('metric')
        for key,u in [('P','W'),('voltage_v','V'),('V_across','V'),('V','V'),('I','A')]:
            if key in s:value=s[key];unit=u;break
        lines=s.get('formula_lines') or s.get('lines') or []
        conditions=s.get('conditions') or []
        if isinstance(conditions,str):conditions=[conditions]
        summary=s.get('route_summary') or ''
        if prose and prose.get('citations') and r['id']==prose.get('primary_ref') and (prose['status']=='ready' or r['id']==explicit_primary(card)):summary=prose['route_summary'] or summary
        figure=m.get('figure') or (s.get('figure') if isinstance(s.get('figure'),str) else None)
        calculations=[{'id':str(i),'expression':x.get('expr') or x.get('expression') or '', 'source':x.get('src') or ''} for i,x in enumerate(lines) if isinstance(x,dict)]
        cases.append({'id':r['id'],'title':r['title'],'kind':'operating_case','status':s.get('status',''),
                      'role':s.get('role',''),'route_summary':summary,'value':value,'value_text':value_text(value,unit) if unit else '',
                      'comparison':s.get('comparison_zh') or '', 'conditions':conditions,'reason':s.get('why_worst') or s.get('reason') or '',
                      'calculations':calculations,'figure':figure,'figure_pages':m.get('figure_pages') or [],
                      'route_expected':bool(s.get('figure')),'figure_status':m.get('figure_status',''),'figure_reason':m.get('figure_reason','')})
    from agent_review.main_analysis import pilot_enabled
    missing_basis=pilot_enabled(out.parent,str(card.get('rule_id')),str(card.get('object'))) and not card.get('main_analysis')
    if missing_basis:
        primary_ref=None
        notes.append('主分析缺少正式判定依据引用，需回流处理；未以候选代替。')
    primary=next((r for r in cases if r['id']==primary_ref),None) if not na else None
    if rows and not primary and not na:notes.append('当前记录未明确主展示工况；未按数值大小自动选取。')
    fields=[]
    for k,value in (card.get('electrical') or {}).items():
        if k in {'cite','unit','voltage_unit','external_basis','high_temperature_basis'} or k.startswith('_'):continue
        label,unit=LABELS.get(k,(k,''))
        fields.append({'label':label,'text':value_text(value,unit),'kind':'review_record','key':k})
    given=(card.get('solution') or {}).get('given') or []
    material_fields=[f for f in fields if f['key'] not in {'P_limit','P_max','P_upper_bound','V_limit','V_max','V_min','limit_v'}]
    material_fields += [{'label':g.get('sym','参数'),'text':text(g.get('val')),'kind':'review_record','source':g.get('cite','')} for g in given if g.get('val') is not None]
    assumptions=card.get('assumptions') or []
    if isinstance(assumptions,str):assumptions=[assumptions]
    premises=[{'id':str(i),'text':text(p),'status':'recorded'} for i,p in enumerate(assumptions)]
    source=out/'agent_jobs'/str(card.get('rule_id'))/(str(card.get('object'))+'.research.json')
    gaps=[]
    if source.exists():
        receipt=json.loads(source.read_text(encoding='utf-8'))
        gaps=[{'id':r['id'],'issue':r['question'],'reason':r['why_unavailable'],'category':r['category']} for r in receipt.get('requests',[])
              if any(p['rule_id']==card.get('rule_id') and p['object']==card.get('object') for p in r.get('affected_pairs',[]))]
    stuck=str(card.get('stuck') or '').strip()
    if stuck and stuck not in {'无','—','-'} and not gaps:gaps=[{'id':'recorded-boundary','issue':'原记录中的边界说明','reason':stuck,'category':'unclassified'}]
    basis=card.get('decision_basis') or {}
    explanation=card.get('conclusion') or card.get('summary') or ''
    checks=(card.get('solution') or {}).get('gates') or (card.get('formula') if isinstance(card.get('formula'),dict) else {}).get('gates') or []
    checks=list(checks)+[{'name':s.get('name') or s.get('name_zh') or '条件核查','detail':'\n'.join(text(x) for x in s.get('steps',[]))} for s in raw_states(card) if is_checklist_state(s)]
    criteria=(card.get('solution') or {}).get('rule') or (card.get('formula') if isinstance(card.get('formula'),dict) else {}).get('rule') or ''
    assessment_rows=[] if na else [f for f in fields if f['key'] in {'P_limit','P_max','P_upper_bound','V_limit','V_max','V_min','limit_v'}]
    assessment_text=criteria or (explanation if na else '')
    if not na and basis.get('kind')=='envelope_upper_bound' and basis.get('value_w') is not None:
        # A proved bound and an unknown exact maximum are distinct quantities.
        # Show the formal basis once, rather than expanding every legacy field.
        assessment_rows=[f for f in assessment_rows if f['key'] not in {'P_max','P_upper_bound'}]
        assessment_rows.append({'label':'功耗保守上界','text':value_text(basis['value_w'],'W'),'kind':'review_record','key':'P_upper_bound'})
        assessment_text=basis.get('coverage_argument') or assessment_text
        if (card.get('electrical') or {}).get('P_max') is None:
            assessment_text+=' 精确最大功耗尚未确定；此处展示用于判定的保守上界。'
    if not assessment_rows and not checks and not assessment_text:
        assessment_text=card.get('calc') or card.get('phenomenon') or explanation
    view={'version':VERSION,'module_order':MODULES,'module_titles':TITLES,'headline':card.get('headline_zh') or explanation,
            'verdict':{'是':'pass','否':'fail','待确认':'pending','INSUFFICIENT':'pending','不适用':'not_applicable'}.get(zh,'pending'),
            'explanation':explanation,'materials':material_fields,'premises':premises,'reasoning_steps':steps,
            'assessment_title':'不适用依据' if na else '判定要点','assessment_rows':assessment_rows,
            'assessment_text':assessment_text,'assessment_checks':checks,'criterion_text':criteria,
            'primary':primary,'primary_label':'已记录工况','comparisons':[] if na else [r for r in cases if not primary or r['id']!=primary['id']],
            'audit_scenarios':[],'gaps':gaps,'evidence':evidence_rows(card.get('evidence_refs')),'basis':{
                'applicability':basis.get('applicability') or '', 'coverage_argument':basis.get('coverage_argument') or ''},
            'original_reasoning':card.get('reasoning_steps',[]) if not usable_steps(card) else [],
            'prose_provenance':prose.get('_meta',{}) if prose else {},'prose_citations':prose.get('citations',[]) if prose else [],
            'prose_status':prose['status'] if prose else 'original','notes':list(dict.fromkeys(notes))}
    if card.get('main_analysis'):
        from agent_review.main_analysis import validate as validate_main
        main=card['main_analysis'];errors=validate_main(card)
        if errors:
            view['primary']=None
            view['notes'].append('正式判定依据与主分析引用未通过校验：'+'；'.join(errors))
        else:
            kind=main['kind'];basis=card['decision_basis']
            view['main_analysis']={k:v for k,v in main.items() if k!='figure'}
            view['primary_module_title']={'cross_case_bound':'跨工况上界分析','operating_case':'判定依据工况','incomplete_proof':'判定依据：证明待完成'}[kind]
            view['primary_label']=view['primary_module_title']
            view['assessment_rows']=[{'label':'评审限额','text':value_text(basis.get('limit_w') or (card.get('electrical') or {}).get('P_limit'),'W')},
                {'label':'功耗保守上界' if kind=='cross_case_bound' else '依据功耗' if kind=='operating_case' else '必要证明',
                 'text':value_text(basis.get('value_w'),'W') if kind!='incomplete_proof' else '尚未完成'}]
            view['assessment_text']=basis.get('coverage_argument') or main['scope']['included']
            if view['primary']:
                view['primary'].update(title=main['title'],kind=kind,route_summary=main['route_summary'],role='decision_basis',
                    value=basis.get('value_w'),value_text=value_text(basis.get('value_w'),'W'),route_expected=bool(main['figure']))
                view['primary']['conditions']=[x['text'] for x in card.get('key_assumptions',[]) if x['id'] in main['assumption_refs']]
    return unified_analysis(view,card,out,prose)
