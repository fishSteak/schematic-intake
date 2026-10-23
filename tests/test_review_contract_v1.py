import copy
import json
import unittest
import subprocess
from pathlib import Path

from agent_review.review_contract import validate_review, project, detail_view, source_hash, task_header


class UnifiedContractTests(unittest.TestCase):
    def setUp(self):
        self.card = json.loads((Path(__file__).resolve().parents[1] / 'docs/samples/review-contract-v1/R4.review.example.json').read_text(encoding='utf-8'))

    def test_valid_core_has_separate_candidate_and_decision_boundary(self):
        self.assertEqual(validate_review(self.card), [])
        view = detail_view(self.card)
        self.assertNotEqual(view['primary']['id'], view['basis']['scenario_refs'][0])
        self.assertEqual(view['global_value_text'], '尚未确定')
        self.assertTrue(view['primary']['route_expected'])

    def test_projection_is_derived_without_mutating_canonical_card(self):
        before=copy.deepcopy(self.card); derived=project(self.card)
        self.assertEqual(self.card,before)
        self.assertNotIn('states',self.card)
        self.assertEqual(len([s for s in derived['states'] if s.get('figure')]),1)

    def test_reordering_preserves_explicit_primary_and_display_selection(self):
        before=detail_view(self.card);self.card['scenarios'].reverse();self.card['inputs'].reverse()
        self.assertEqual(validate_review(self.card),[])
        after=detail_view(self.card)
        self.assertEqual(before['primary'],after['primary'])
        self.assertEqual(before['materials'],after['materials'])

    def test_conditional_candidate_cannot_replace_failure_evidence(self):
        self.card['decision']['basis']['scenario_refs']=[self.card['extreme']['primary_scenario_ref']]
        self.assertTrue(validate_review(self.card))

    def test_supported_failure_cannot_become_pending_due_to_unknown_maximum(self):
        self.card['decision']['verdict']='pending';self.card['decision']['basis']['kind']='insufficient'
        self.assertTrue(validate_review(self.card))

    def test_model_upper_bound_cannot_prove_failure(self):
        self.card['scenarios'][0]['metrics'][-1]['nature']='upper_bound'
        self.assertTrue(validate_review(self.card))

    def test_cycle_and_dangling_reference_are_rejected(self):
        first=self.card['scenarios'][0]['calculations'][0]
        first['step_refs']=[first['id']]
        self.assertIn('cyclic calculation references',validate_review(self.card))
        first['step_refs']=['missing']
        self.assertTrue(validate_review(self.card))

    def test_metric_and_calculation_must_agree(self):
        self.card['scenarios'][0]['metrics'][-1]['value']*=2
        self.assertTrue(validate_review(self.card))

    def test_only_primary_can_have_route_and_geometry_inputs_are_not_guessed(self):
        self.card['scenarios'][0]['route']=self.card['scenarios'][1]['route']
        self.assertTrue(validate_review(self.card))

    def test_global_extreme_requires_closed_scope(self):
        self.card['extreme']['global_value']=1
        self.assertTrue(validate_review(self.card))

    def test_object_rule_header_is_immutable(self):
        expected={k:copy.deepcopy(self.card[k]) for k in ('target','criterion','schema_version')}
        expected['fixed_evidence']=[]
        self.card['criterion']['limit']['value']*=2
        self.assertTrue(validate_review(self.card,expected=expected))

    def test_larger_comparable_candidate_must_not_be_omitted(self):
        a,b=self.card['scenarios'][1:]
        self.card['extreme']['primary_scenario_ref']=b['id']
        a['route'],b['route']=None,a['route']
        for r in self.card['extreme']['comparisons']:
            if r['scenario_ref']==a['id']:r['role']='comparison'
            if r['scenario_ref']==b['id']:r['role']='primary'
        self.assertTrue(any('more adverse' in e for e in validate_review(self.card)))

    def test_metadata_is_not_a_business_value_and_does_not_change_image_source_hash(self):
        before=source_hash(self.card);self.card['_meta']={'validation_status':'valid'}
        self.assertEqual(before,source_hash(self.card))
        self.assertEqual(validate_review(self.card),[])

    def test_unknown_direction_is_preserved_in_render_contract(self):
        route=self.card['scenarios'][1]['route'];route['pages'][0]['branches'][0]['direction_status']='unknown'
        state=next(s for s in project(self.card)['states'] if s['role']=='primary')
        self.assertTrue(state['figure']['pages'][0]['unknown_direction_paths'])

    def test_calculation_ids_are_resolved_and_unused_node_steps_are_not_business_formulas(self):
        s=self.card['scenarios'][1]
        s['calculations'][2]['expression']='(24 - c_interface_feed) / c_interface_total'
        extra=copy.deepcopy(s['calculations'][0]);extra['id']='unused_node';extra['label']='不应出现在核心计算';s['calculations'].append(extra)
        formulas=detail_view(self.card)['primary']['calculations']
        text=' '.join(c['expression'] for c in formulas)
        self.assertNotIn('c_interface_feed',text)
        self.assertNotIn('c_interface_total',text)
        self.assertNotIn('不应出现在核心计算',text)
        self.assertIn('mA',text)

    def test_controller_header_uses_materials_not_an_object_special_case(self):
        w={'rule_id':'P11-A01','object':'DIFFERENT_DEVICE','identity':{'value':'2K'},
           'mpn_excerpt':{'params':[{'key':'p_rated_w','status':'present','value':2}]}}
        task=task_header(w)
        self.assertEqual(task['criterion']['limit']['value'],1)
        self.assertEqual(task['fixed_inputs'][1]['value'],2000)

    def test_html_modules_render_explicit_inputs_and_one_figure(self):
        from kernel.runtime import node_executable
        root=Path(__file__).resolve().parents[1]
        text=(root/'vendor/expert_html/build_expert_html.py').read_text(encoding='utf-8')
        functions=text[text.index('function renderContractPremises(v){{'):text.index('function renderPremises(d){{')].replace('{{','{').replace('}}','}')
        from agent_review.detail_view import build
        view=build({'review_v1':self.card},root);view['primary']['figure']='verified.png'
        script="const hEsc=s=>String(s).replaceAll('<','&lt;'); const stressViewHtml=s=>'<img src=verified.png>';\n"+functions
        script+='\nconst v='+json.dumps(view,ensure_ascii=False)+';console.log(JSON.stringify({premises:renderContractPremises(v),body:renderContractReview(v)}));'
        result=subprocess.run([node_executable(),'--input-type=module','-e',script],capture_output=True,text=True,encoding='utf-8',check=True)
        output=json.loads(result.stdout)
        self.assertEqual(output['body'].count('<img'),1)
        self.assertNotIn('工况通路图',output['body'])
        self.assertNotIn('整改与补证',output['body'])
        self.assertNotIn('为何选择这一工况',output['body'])
        self.assertEqual(output['body'].count(view['selection_reason'].replace('<','&lt;')),1)
        for heading in ['01 · 判定依据','02 · 覆盖范围与边界','03 · 未闭合事项']:
            self.assertIn(heading,output['body'])
        self.assertIn('rv-route',output['body'])
        self.assertNotIn('模型超限',output['body'])
        self.assertIn('按当前假设计算超限',output['body'])
        self.assertIn('rv-reason-steps',output['premises'])
        self.assertIn('判定边界',output['body'])
        self.assertNotIn('全局极值',output['body'])
        self.assertIn('材料与分析思路',output['premises'])
        self.assertNotIn('成立前提',output['premises'])
        self.assertNotIn('判断思路',output['premises'])

    def test_ui_prose_is_copied_from_model_without_replacement(self):
        card=copy.deepcopy(self.card)
        card['reasoning_steps']=['先核对D8与D9的并联关系，再比较同一范围内的工况。']
        primary=next(s for s in card['scenarios'] if s['id']==card['extreme']['primary_scenario_ref'])
        primary['route_summary']='模型依据材料形成的特定通路说明。'
        before=copy.deepcopy(card);view=detail_view(card)
        self.assertEqual(view['reasoning_steps'],card['reasoning_steps'])
        self.assertEqual(view['primary']['route_summary'],primary['route_summary'])
        self.assertEqual(card,before)

    def test_missing_route_sentence_and_numeric_reasoning_fail_validation(self):
        card=copy.deepcopy(self.card);del card['scenarios'][0]['route_summary']
        self.assertTrue(any('route_summary' in e for e in validate_review(card)))
        card=copy.deepcopy(self.card);card['reasoning_steps']=['计算得到0.282 W，高于0.05 W限额。']
        self.assertTrue(any('reasoning_steps' in e for e in validate_review(card)))

    def test_unknown_metric_can_reference_an_unknown_calculation(self):
        card=copy.deepcopy(self.card)
        s=copy.deepcopy(card['scenarios'][0]);s.update(id='unknown_cycle',route=None,calculations=[],metrics=[{'key':'power','unit':'W','value':None,'nature':'unknown','calculation_ref':None}])
        s['applicability']['status']='unmapped'
        card['scenarios'].append(s)
        card['extreme']['comparisons'].append({'scenario_ref':s['id'],'role':'unranked','reason':'波形未知，保留未知计算引用。'})
        m=s['metrics'][0]
        calc={'id':'unknown_integral','label':'待求周期积分','expression':'尚待波形','value':None,'unit':m['unit'],'input_refs':[],'step_refs':[],'evidence_refs':[]}
        s['calculations'].append(calc);m['value']=None;m['calculation_ref']=calc['id']
        self.assertEqual(validate_review(card),[])
        calc['value']=1
        self.assertTrue(any('metric/calculation value or unit mismatch' in e for e in validate_review(card)))

    def test_generic_method_without_target_facts_is_rejected(self):
        card=copy.deepcopy(self.card)
        card['reasoning_steps']=['核对电路连接，再求解应力并比较极值。']
        self.assertTrue(any('ground the target' in e for e in validate_review(card)))

    def test_geometry_feedback_only_uses_current_model_source(self):
        import tempfile
        from agent_review.stages.judge import contract_feedback
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);card=copy.deepcopy(self.card);rid=card['target']['rule_id'];obj=card['target']['object_id']
            path=out/'agent_jobs'/rid/(obj+'.json');path.parent.mkdir(parents=True);path.write_text(json.dumps(card),encoding='utf-8')
            meta=out/'stress_paths'/rid/obj/card['extreme']['primary_scenario_ref']/'meta.json';meta.parent.mkdir(parents=True)
            data={'review_source_hash':source_hash(card),'routing_complete':False,'pages':[{'pdf_page':3,'routing_issues':['connector not located on page']}]}
            meta.write_text(json.dumps(data),encoding='utf-8')
            self.assertIn('route page 3',contract_feedback(out,rid,obj)[0])
            data['review_source_hash']='stale';meta.write_text(json.dumps(data),encoding='utf-8')
            self.assertEqual(contract_feedback(out,rid,obj),[])
