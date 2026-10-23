"""Opt-in review.v1: validate canonical model output and derive legacy/UI views.

No object names, topology, expected results or documentation examples belong here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from kernel.util import ROOT, read_json
from agent_review.analysis_contract import VERSION as ANALYSIS_VERSION, validate_links

VERSION = "review.v1"
SCHEMA_PATH = ROOT / "agent_review/schemas/review.v1.schema.json"
ZH = {"pass": "是", "fail": "否", "pending": "待确认", "not_applicable": "不适用"}


def rule_evidence_base(board, rel):
    """Accept canonical rules or a byte-identical configured material alias."""
    rel=Path(rel);board=Path(board).resolve()
    if not rel.is_absolute() and rel.parts and rel.parts[0]=='rules':return ROOT,True
    candidate=(board/rel).resolve()
    if rel.is_absolute() or not candidate.is_relative_to(board) or not candidate.is_file():return ROOT,False
    try:
        configured=read_json(board/'materials.json').get('rules')
        if not configured:return ROOT,False
        original=Path(configured)
        if not original.is_absolute():original=board/original
        if original.is_file() and hashlib.sha256(original.read_bytes()).digest()==hashlib.sha256(candidate.read_bytes()).digest():
            return board,True
    except (OSError,ValueError,TypeError):pass
    return ROOT,False


def analysis_texts(data):
    return [s['text'] for s in data['analysis_steps']] if 'analysis_steps' in data else data.get('reasoning_steps', [])


def is_v1(card):
    return card.get("schema_version") == VERSION


def core(card):
    return {k: v for k, v in card.items() if k != "_meta"}


def business_node_alias_errors(data,known_names):
    """Reject undeclared node shorthand in business prose, not internal IDs or units."""
    keys={'title','label','reason','route_summary','statement','explanation','coverage_argument','selection_reason','applicability'}
    explicit=re.compile(r'(?:节点|电位)\s*([A-Z])(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])([A-Z])(?:节点|电位|点|端)')
    causal=re.compile(r'(?:抬高|压低|降低|放宽|使)\s*([A-Z])(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])([A-Z])(?=不超过|处需求)')
    errors=[]
    def walk(value,path=''):
        if isinstance(value,dict):
            for key,text in value.items():
                current=path+'.'+key
                if key in keys and isinstance(text,str):
                    for pattern in (explicit,causal):
                        for hit in pattern.findall(text):
                            alias=next(x for x in hit if x)
                            if alias not in known_names and not (pattern is causal and alias in {'I','U','V','P','R','A','W'}):
                                errors.append(f'business field {current} contains undeclared node alias {alias}; use material net names or a clear node description')
                elif isinstance(text,(dict,list)):walk(text,current)
        elif isinstance(value,list):
            for i,item in enumerate(value):walk(item,path+f'[{i}]')
    walk(data)
    return list(dict.fromkeys(errors))


def fingerprint():
    paths = ["agent_review/review_contract.py", "agent_review/schemas/review.v1.schema.json",
             "rules/review_contract_rules.json", "agent_review/prompts/judge/review_v1.common.md",
             "agent_review/prompts/judge/review_v1.power.md", "agent_review/prompts/judge/review_v1.self_check.md",
             "agent_review/prompts/judge/P11-A01.analysis.md", "agent_review/orchestrator.mjs",
             "agent_review/local_workspace.mjs", "agent_review/codex_agent.mjs"]
    h = hashlib.sha256()
    for name in paths:
        h.update(name.encode()); h.update((ROOT / name).read_bytes())
    return h.hexdigest()


def source_hash(card):
    return hashlib.sha256(json.dumps(core(card),ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def evidence_hashes(card, board):
    return {e['path']:hashlib.sha256(((rule_evidence_base(board,e['path'])[0] if e['kind']=='rule' else Path(board))/e['path']).read_bytes()).hexdigest()
            for e in card['evidence']}


def task_header(workbench):
    rid = workbench["rule_id"]
    cfg = read_json(ROOT / "rules/review_contract_rules.json")[rid]
    rated = next((p["value"] for p in workbench["mpn_excerpt"]["params"]
                  if p["key"] == cfg["rated_key"] and p.get("status") == "present"), None)
    if type(rated) not in (int, float) or rated <= 0:
        raise ValueError("review.v1 requires a material-supported rating")
    target = {"rule_id": rid, "object_id": workbench["object"], "object_type": "component"}
    criterion = {k: cfg[k] for k in ("title", "metric_key", "unit", "aggregation", "operator")}
    criterion["limit"] = {"value": rated * cfg["derating_ratio"], "evidence_refs": ["controller_limit"]}
    evidence = {"id": "controller_limit", "kind": "facts",
                "path": f"review_out/agent_jobs/{rid}/{workbench['object']}.context.json",
                "locator": "contract_task.criterion / identity / mpn_excerpt.params",
                "claim": "对象阻值与额定来自工作台材料；限额由材料额定与不可变规则比例计算。", "qualifiers": []}
    from solve_prog.netclass import parse_ohm
    resistance = parse_ohm(str(workbench['identity'].get('value') or ''))
    fixed_inputs = [{'id':'controller_rated', 'label':'对象额定功率', 'value':rated, 'unit':'W', 'kind':'material_fact', 'evidence_refs':['controller_limit']}]
    if resistance:
        fixed_inputs.append({'id':'controller_resistance', 'label':'对象阻值', 'value':resistance, 'unit':'ohm', 'kind':'material_fact', 'evidence_refs':['controller_limit']})
    return {"schema_version": VERSION, "target": target, "criterion": criterion,
            "analysis_contract": ANALYSIS_VERSION, "fixed_evidence": [evidence], "fixed_inputs":fixed_inputs, "rated_value": rated,
            "derating_ratio": cfg["derating_ratio"]}


def metric(scenario, key):
    return next((m for m in scenario["metrics"] if m["key"] == key), None)


def violates(value, criterion):
    return value > criterion["limit"]["value"] if criterion["operator"] == "le" else value < criterion["limit"]["value"]


def _close(a, b):
    return a is not None and b is not None and math.isclose(a, b, rel_tol=2e-5, abs_tol=1e-10)


def validate_review(card, *, board=None, rule_id=None, obj=None, expected=None):
    data = core(card)
    errors = [f"{'.'.join(map(str, e.absolute_path))}: {e.message}" for e in
              Draft202012Validator(read_json(SCHEMA_PATH)).iter_errors(data)]
    if errors:
        return errors
    if 'analysis_steps' in data:
        errors.extend(validate_links(data['analysis_steps'],data['premises'],data['display']['premise_refs'],ref_name='premise_refs'))
    quantity = r'(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\s*(?:mW|W|mA|A|mV|V|ohm|Ω|%|℃|degC|毫瓦|瓦|伏|安|欧姆)'
    for step in analysis_texts(data):
        known_rails=set(re.findall(r'(?<![A-Za-z0-9_])[+\-]\d+(?:\.\d+)?V(?![A-Za-z0-9_])',json.dumps(data['premises'],ensure_ascii=False)))
        checked=step
        for rail in known_rails:checked=checked.replace(rail,'[材料网名]')
        if re.search(quantity,checked):errors.append('reasoning_steps must describe process without numeric quantities')
    fact_text=' '.join(p['text'] for p in data['premises'])+' '+data['target']['object_id']
    anchors=set(re.findall(r'(?<![A-Za-z0-9_])[A-Z]{1,4}\d+(?![A-Za-z0-9_])',fact_text))
    reasoning=' '.join(analysis_texts(data))
    def mentions(text,ref):return re.search(r'(?<![A-Za-z0-9_])'+re.escape(ref)+r'(?![A-Za-z0-9_])',text) is not None
    if analysis_texts(data) and (not mentions(reasoning,data['target']['object_id']) or not any(mentions(reasoning,ref) for ref in anchors-{data['target']['object_id']})):
        errors.append('reasoning_steps must ground the target and related components in material facts')
    for scenario in data['scenarios']:
        if not scenario['route_summary'].strip():errors.append('route_summary must be model-authored and non-empty')
    target = data["target"]
    if rule_id and target["rule_id"] != rule_id: errors.append("target.rule_id mismatch")
    if obj and target["object_id"] != obj: errors.append("target.object_id mismatch")
    if board and expected is None:
        path = Path(board) / "review_out/agent_jobs" / target["rule_id"] / f"{target['object_id']}.context.json"
        if path.is_file(): expected = read_json(path).get("contract_task")
        if not expected: errors.append("missing controller contract_task")
    if expected and expected.get('analysis_contract')==ANALYSIS_VERSION and data.get('analysis_contract')!=ANALYSIS_VERSION:
        errors.append('analysis_contract required by current workbench')
    if expected:
        for key in ("target", "criterion", "schema_version"):
            if data[key] != expected[key]: errors.append(f"immutable {key} differs from controller")
        fixed = {e["id"]: e for e in data["evidence"]}
        for e in expected["fixed_evidence"]:
            if fixed.get(e["id"]) != e: errors.append("controller evidence changed")
        fixed_values = {x['id']:x for x in data['inputs']}
        for value in expected.get('fixed_inputs',[]):
            if fixed_values.get(value['id']) != value: errors.append('controller material input changed')
    collections = {"input": data["inputs"], "premise": data["premises"], "scenario": data["scenarios"],
                   "evidence": data["evidence"], "calculation": [c for s in data["scenarios"] for c in s["calculations"]]}
    maps = {name: {x["id"]: x for x in rows} for name, rows in collections.items()}
    all_ids = [x["id"] for rows in collections.values() for x in rows]
    if len(set(all_ids)) != len(all_ids): errors.append("IDs must be globally unique")
    refs = {"input_refs": "input", "premise_refs": "premise", "scenario_refs": "scenario",
            "evidence_refs": "evidence", "step_refs": "calculation"}
    singles = {"scenario_ref": "scenario", "primary_scenario_ref": "scenario",
               "calculation_ref": "calculation", "current_ref": "calculation"}
    def walk(value, location="review"):
        if isinstance(value, dict):
            for key, v in value.items():
                if key in refs and any(x not in maps[refs[key]] for x in v): errors.append(f"{location}.{key}: unresolved reference")
                if key in singles and v is not None and v not in maps[singles[key]]: errors.append(f"{location}.{key}: unresolved reference")
                if key == "value_ref" and v is not None and v not in maps['input'] and v not in maps['calculation']: errors.append(f"{location}.value_ref: unresolved reference")
                if type(v) is float and not math.isfinite(v): errors.append(f"{location}.{key}: nonfinite value")
                walk(v, location+"."+key)
        elif isinstance(value, list):
            for v in value: walk(v, location)
    walk(data)
    if errors: return errors
    for calc in collections["calculation"]:
        visiting, visited = set(), set()
        def visit(cid):
            if cid in visiting: return True
            if cid in visited: return False
            visiting.add(cid)
            cycle = any(visit(x) for x in maps['calculation'][cid]['step_refs'])
            visiting.remove(cid); visited.add(cid)
            return cycle
        if visit(calc['id']): errors.append("cyclic calculation references")
    c = data["criterion"]; key = c["metric_key"]
    if c['unit'] != 'W' or key != 'power' or target['rule_id'] != 'P11-A01':
        errors.append("pilot supports P11-A01 power only")
    scenarios = maps["scenario"]; ex = data["extreme"]; decision = data["decision"]
    basis = decision["basis"]; verdict = decision["verdict"]
    units = {"power": "W", "current": "A", "voltage_span": "V"}
    eligible_failure = set()
    for s in data["scenarios"]:
        if len({m['key'] for m in s['metrics']}) != len(s['metrics']): errors.append("duplicate metric key")
        calc_ids = {x['id'] for x in s['calculations']}
        for calc in s['calculations']:
            if any(x not in calc_ids for x in calc['step_refs']): errors.append("cross-scenario calculation reference")
        for m in s['metrics']:
            if m['key'] in units and m['unit'] != units[m['key']]: errors.append("metric unit mismatch")
            if m['value'] is not None and m['value'] < 0: errors.append("negative steady power/current/span")
            if m['calculation_ref']:
                source = maps['calculation'][m['calculation_ref']]
                if source['id'] not in calc_ids or source['unit'] != m['unit'] or not ((source['value'] is None and m['value'] is None) or _close(source['value'], m['value'])): errors.append("metric/calculation value or unit mismatch")
        p = metric(s, key)
        if s['applicability']['status'] not in {'unmapped', 'excluded'} and (not p or p['value'] is None): errors.append("computed scenario requires criterion metric")
        if p and p['value'] is not None and p['unit'] != c['unit']: errors.append("criterion metric unit mismatch")
        if p and p['value'] is not None and s['applicability']['status'] == 'supported' and violates(p['value'], c) and p['nature'] not in {'unknown','upper_bound'}:
            eligible_failure.add(s['id'])
        i, v = metric(s,'current'), metric(s,'voltage_span')
        if p and i and v and all(m['value'] is not None for m in (p,i,v)) and p['nature'] == 'nominal_model':
            if not _close(p['value'], i['value']*v['value']): errors.append("power differs from I times object voltage span")
        if s['route']:
            if s['id'] != ex['primary_scenario_ref']: errors.append("only primary scenario may have route")
            for page in s['route']['pages']:
                for branch in page['branches']:
                    if branch['current_ref'] and branch['current_ref'] not in calc_ids: errors.append("route current references other scenario")
                for marker in page['markers']:
                    if marker['value_ref'] in maps['calculation'] and marker['value_ref'] not in calc_ids: errors.append("marker references other scenario")
    rows = ex['comparisons']
    if sorted(x['scenario_ref'] for x in rows) != sorted(scenarios): errors.append("extreme comparisons must cover every scenario once")
    primaries = [x['scenario_ref'] for x in rows if x['role']=='primary']
    if primaries != ([ex['primary_scenario_ref']] if ex['primary_scenario_ref'] else []): errors.append("exactly one explicit primary required")
    primary = scenarios.get(ex['primary_scenario_ref'])
    if primary:
        status = primary['applicability']['status']
        if ex['primary_status']=='confirmed' and status!='supported': errors.append("conditional primary cannot be confirmed")
        if ex['primary_status']=='conditional' and status!='conditional': errors.append("primary status mismatch")
        if primary['kind']=='model_boundary': errors.append("model boundary cannot replace main extreme operating case")
        if not primary['route']: errors.append("pilot primary requires route")
        p = metric(primary,key)
        if p and p['value'] is not None:
            roles = {x['scenario_ref']:x['role'] for x in rows}
            for other in data['scenarios']:
                om=metric(other,key)
                if other['kind']=='operating_case' and other['applicability']['status'] not in {'excluded','unmapped'} and roles.get(other['id'])!='unranked' and om and om['value'] is not None:
                    worse = om['value']>p['value'] if c['aggregation']=='maximum' else om['value']<p['value']
                    if worse: errors.append('primary omitted a more adverse comparable operating case')
    if ex['global_value'] is not None:
        if not ex['ranking_complete'] or data['scope']['coverage']!='complete' or ex['primary_status']!='confirmed': errors.append("unclosed scope/candidate cannot provide global extreme")
        if not primary or not _close(ex['global_value'], metric(primary,key)['value']): errors.append("global value differs from confirmed primary")
    if verdict=='fail':
        if basis['kind'] not in {'supported_case','supported_model_boundary','proven_bound'} or not basis['scenario_refs'] or not all(x in eligible_failure for x in basis['scenario_refs']): errors.append("fail requires supported adverse witness/bound, not conditional or upper bound")
        for sid in basis['scenario_refs']:
            if scenarios[sid]['kind']=='model_boundary' and basis['kind']!='supported_model_boundary': errors.append("model boundary needs explicit boundary basis")
        if not basis['evidence_refs'] or not basis['applicability'] or not basis['coverage_argument']: errors.append("failure needs evidence/applicability/coverage")
    blocking = [g for g in data['gaps'] if g['effect']=='decision_changing']
    if verdict=='pending' and (eligible_failure or not blocking or basis['kind']!='insufficient'): errors.append("pending contradicts sufficient failure or lacks a verdict-changing countercase")
    if verdict=='pass' and (data['scope']['coverage']!='complete' or not ex['ranking_complete'] or blocking or eligible_failure or basis['kind'] not in {'envelope_bound','proven_bound'}): errors.append("pass requires complete supported envelope")
    if verdict=='pass':
        for sid in basis['scenario_refs']:
            m=metric(scenarios[sid],key)
            if not m or m['value'] is None or violates(m['value'],c) or m['nature'] not in {'upper_bound','exact'}: errors.append("pass needs correctly directed adequate envelope bound")
        if not basis['scenario_refs']: errors.append("pass requires envelope evidence")
    if verdict=='not_applicable' and (basis['kind']!='not_applicable' or not basis['evidence_refs']): errors.append("not_applicable requires evidence")
    if verdict!='not_applicable' and not analysis_texts(data): errors.append("reasoning_steps required")
    if verdict in {'fail','pending'} and not data['actions']: errors.append("action required")
    if not basis['evidence_refs']: errors.append("decision evidence required")
    if board:
        for evidence in data['evidence']:
            rel = Path(evidence['path'])
            base,valid_rule = rule_evidence_base(board,rel) if evidence['kind']=='rule' else (Path(board),True)
            resolved = (base / rel).resolve()
            if rel.is_absolute() or not resolved.is_relative_to(base.resolve()) or not resolved.is_file(): errors.append(f"evidence path missing/outside materials: {evidence['id']}")
            parts = {p.lower() for p in rel.parts}
            if parts & {'history','docs','archive'} or rel.name in {'verdicts.json','expert_review.html','engineer_review.html'}:
                errors.append(f"historical/report evidence forbidden: {evidence['id']}")
            if evidence['kind']=='rule' and not valid_rule:
                errors.append(f"rule evidence must come from rule configuration: {evidence['id']}")
            if 'agent_jobs' in rel.parts and rel.name not in {f"{target['object_id']}.context.json",f"{target['object_id']}.analysis.md"}:
                errors.append(f"other review/schema is not source evidence: {evidence['id']}")
        kg_path=Path(board)/'review_out/board_kg.json'
        if kg_path.is_file():
            kg=read_json(kg_path);comps=kg.get('components',{});nets=kg.get('nets',{})
            errors.extend(business_node_alias_errors(data,set(comps)|set(nets)))
            from solve_prog.netclass import resistor_ohms
            resistance = (resistor_ohms(kg).get(target['object_id']) or (None,None,None))[2]
            for s in data['scenarios']:
                p=metric(s,'power');i=metric(s,'current');v=metric(s,'voltage_span')
                if s['applicability']['status'] not in {'unmapped','excluded'}:
                    if not all(m and m['value'] is not None for m in (p,i,v)): errors.append('power pilot requires current, voltage_span and power')
                    elif resistance and not (_close(v['value'],i['value']*resistance) and _close(p['value'],i['value']**2*resistance)): errors.append('object metrics disagree with sealed resistance')
                for page in (s['route'] or {}).get('pages',[]):
                    for branch in page['branches']:
                        for token in branch['path']:
                            name,_,pin=token.partition('.')
                            if name not in comps and token not in nets: errors.append(f"unknown route token: {token}")
                            elif pin and name in comps and not any(str(p.get('pin'))==pin for p in comps[name].get('pins',[])): errors.append(f"unknown pin: {token}")
    return list(dict.fromkeys(errors))


def value_text(value, unit):
    if value is None: return "尚未确定"
    if unit in {'W','A'}: return f"{value*1000:.2f} {'mW' if unit=='W' else 'mA'}"
    return f"{value:g} {({'ohm':'Ω','degC':'℃'}.get(unit,unit)) if unit!='1' else ''}".strip()


def display_calculations(card, scenario):
    """Resolve internal IDs to numeric substitutions; keep only metric dependencies."""
    values={x['id']:x for x in card['inputs']}
    values.update({c['id']:c for c in scenario['calculations']})
    calculations={c['id']:c for c in scenario['calculations']}
    selected=set()
    def include(cid):
        if cid in selected or cid not in calculations:return
        selected.add(cid)
        for dep in calculations[cid]['step_refs']:include(dep)
    for m in scenario['metrics']:
        if m['calculation_ref']:include(m['calculation_ref'])
    result=[]
    for calc in scenario['calculations']:
        if calc['id'] not in selected:continue
        parts=calc['expression'].split('=')
        expr=(parts[-2] if len(parts)>2 else parts[0]).strip()
        for token in sorted(values,key=len,reverse=True):
            value=values[token]['value']
            substitution=f'{value:.8g}' if type(value) in (int,float) else ('未知' if value is None else str(value))
            expr=re.sub(r'(?<![\w])'+re.escape(token)+r'(?![\w])',lambda _:substitution,expr)
        expr=expr.replace('*','×').replace('^2','²')
        result.append({**calc,'expression':f"{calc['label']}：{expr} = {value_text(calc['value'],calc['unit'])}"})
    return result


def project(card):
    """One compatibility boundary. The canonical card remains unchanged."""
    if not is_v1(card): return card
    d=core(card);c=d['criterion'];key=c['metric_key'];ex=d['extreme'];ev={x['id']:x for x in d['evidence']}
    vals={x['id']:x for x in d['inputs']}
    vals.update({x['id']:x for s in d['scenarios'] for x in s['calculations']})
    scenarios={x['id']:x for x in d['scenarios']};primary=scenarios.get(ex['primary_scenario_ref'])
    primary_value=metric(primary,key)['value'] if primary else None
    label='条件极值候选' if ex['primary_status']=='conditional' else '已确认极值'
    verdict={'pass':'符合','fail':'不符合','pending':'待确认','not_applicable':'不适用'}[d['decision']['verdict']]
    headline=f"{d['target']['object_id']}：最大功耗{verdict}要求，限额 {value_text(c['limit']['value'],c['unit'])}。"
    if primary: headline+=f"{label} {value_text(primary_value,c['unit'])}。"
    rows=[]
    for s in d['scenarios']:
        measurements={m['key']:m['value'] for m in s['metrics']};role='primary' if s['id']==ex['primary_scenario_ref'] else ('witness' if s['kind']=='model_boundary' else 'comparison')
        lines=[{'expr':x['expression'],'src':'；'.join(ev[e]['claim'] for e in x['evidence_refs'])} for x in display_calculations(d,s)]
        state={'case_id':s['id'],'name_zh':s['title'],'role':role,'status':'conditional' if s['applicability']['status']=='conditional' else 'modeled',
               'P':measurements.get('power'),'V':measurements.get('voltage_span'),'I':measurements.get('current'),
               'stress_kind':'modeled','why_worst':s['comparison_note'],'conditions':'；'.join(x['text'] for x in d['premises'] if x['id'] in s['premise_refs']),
               'formula_lines':lines,'结论':('按当前假设计算超限' if measurements.get(key) is not None and violates(measurements[key],c) else '按当前假设计算未超限'),
               'review_v1_scenario':s['id']}
        if s['route']:
            pages=[]
            for p in s['route']['pages']:
                marks=[]
                for m in p['markers']:
                    v=vals.get(m['value_ref']); title=m['label']+((' = '+value_text(v['value'],v['unit'])) if v else '')
                    marks.append({'on':m['on'],'title':title,'kind':'volt' if m['kind']=='voltage' else '', 'body':m['note']})
                pages.append({'pdf_page':p['pdf_page'],'label':p.get('label',''),'route_summary':p.get('route_summary',''),'paths':[b['path'] for b in p['branches']],
                              'through_body':p['through_body'],'markers':marks,'notes':p['notes'],
                              'unknown_direction_paths':[b['path'] for b in p['branches'] if b['direction_status']=='unknown']})
            state['figure']={'pages':pages}
        rows.append(state)
    return {'rule_id':d['target']['rule_id'],'object':d['target']['object_id'],'verdict_zh':ZH[d['decision']['verdict']],
            'headline_zh':headline,'summary':d['decision']['explanation'],'conclusion':headline,'phenomenon':primary['title'] if primary else verdict,
            'calc':'；'.join(x['expr'] for s in rows for x in s['formula_lines']), 'stuck':'；'.join(g['issue'] for g in d['gaps']) or '无',
            'action':'；'.join(x['text'] for x in d['actions']),'assumptions':[p['text'] for p in d['premises']],
            'limit':c['limit']['value'], 'computed':{'P_max_w':ex['global_value']},
            'reasoning_steps':analysis_texts(d),'evidence_refs':[{'path':e['path'],'note':e['claim'],'description':e['claim']} for e in d['evidence']],
            'electrical':{'P_rated':vals.get('controller_rated',{}).get('value'),'P_limit':c['limit']['value'],'P_max':ex['global_value']},'states':rows,'review_v1':copy.deepcopy(card)}


def detail_view(card, out=None):
    """Versioned business modules; both HTML and Vue consume this same payload."""
    d=core(card); c=d['criterion'];key=c['metric_key'];ex=d['extreme'];inputs={x['id']:x for x in d['inputs']};premises={x['id']:x for x in d['premises']}
    projected=project(card);rows=[]
    roles={r['scenario_ref']:r['role'] for r in ex['comparisons']}
    for s in d['scenarios']:
        m=metric(s,key);value=m['value'] if m else None
        figure=None; figure_pages=[]
        if out and s['id']==ex['primary_scenario_ref']:
            p=Path(out)/'stress_paths'/d['target']['rule_id']/d['target']['object_id']/s['id']/'meta.json'
            if p.is_file():
                meta=read_json(p)
                if meta.get('routing_complete') and meta.get('status')=='ok' and meta.get('review_source_hash')==source_hash(card):
                    figure=f"stress_paths/{d['target']['rule_id']}/{d['target']['object_id']}/{s['id']}/flow.png"
                    figure_pages=[{'src':str(Path(figure).parent/page['panel']).replace('\\','/'),
                                   'label':f"第{page['pdf_page']}页"} for page in meta.get('pages',[]) if page.get('panel')]
        rows.append({'id':s['id'],'title':s['title'],'kind':s['kind'],'status':s['applicability']['status'],
                     'role':roles[s['id']], 'route_summary':s['route_summary'],
                     'value':value,'value_text':value_text(value,c['unit']),'comparison':'未计算' if value is None else ('按当前假设计算超限' if violates(value,c) else '按当前假设计算未超限'),
                     'conditions':[premises[p]['text'].rstrip('。；') for p in s['premise_refs'] if premises[p]['status']=='unresolved'] or [premises[p]['text'].rstrip('。；') for p in s['premise_refs'] if premises[p]['status']=='model_assumption'], 'reason':s['comparison_note'],
                     'calculations':display_calculations(d,s),'figure':figure,'figure_pages':figure_pages,'route_expected':s['route'] is not None})
    primary=next((s for s in rows if s['id']==ex['primary_scenario_ref']),None)
    return {'version':VERSION,'headline':projected['headline_zh'],'verdict':d['decision']['verdict'],
            'explanation':d['decision']['explanation'],'limit_text':value_text(c['limit']['value'],c['unit']),
            'primary_label':'条件极值候选' if ex['primary_status']=='conditional' else '已确认极值',
            'primary_value_text':primary['value_text'] if primary else '尚未确定',
            'global_value_text':value_text(ex['global_value'],c['unit']),
            'materials':[{'label':inputs[i]['label'],'text':value_text(inputs[i]['value'],inputs[i]['unit']),
                          'kind':inputs[i]['kind']} for i in d['display']['input_refs']],
            'premises':[premises[i] for i in d['display']['premise_refs']],
            'reasoning_steps':analysis_texts(d),'selection_reason':ex['selection_reason'],
            'primary':primary,'comparisons':[s for s in rows if s['role'] in {'comparison','decision_boundary'}],
            'audit_scenarios':[s for s in rows if s['role'] in {'excluded','unranked'}],
            'actions':[a['text'] for a in d['actions']],'gaps':d['gaps'],'evidence':d['evidence'],
            'basis':d['decision']['basis']}
