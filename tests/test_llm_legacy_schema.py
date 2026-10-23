"""Legacy delivery schemas and gates through the real HTTP adapter, without a model."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from jsonschema import Draft202012Validator

from agent_review import llm_dispatch, llm_tasks
from agent_review.llm import LLMClient, Profile
from agent_review.validate_job import validate_judge_card
from kernel.util import read_json, write_json


def power_card():
    """Synthetic independent sources and bounds, not an expected board answer."""
    states = [
        {'name_zh': '规定端口包络下的耗散上界', 'status': 'modeled', 'role': 'primary',
         'stress_kind': 'upper_bound', 'P': .02, 'conditions': '已确认的端口包络及阻值下端。',
         'formula_lines': [{'expr': 'R7耗散≤2²/200=0.02W', 'src': 'E1 中的端口包络与阻值下端。'}],
         'figure': {'kind': 'cross_case_relations', 'pages': [{'pdf_page': 1,
             'paths': [['INPUT', 'R7.1', 'R7.2', 'RETURN']],
             'markers': [{'on': 'R7.1', 'title': '耗散上界'}]}]}},
        {'name_zh': '外部保持端压的条件工作点', 'status': 'conditional', 'role': 'worst_candidate',
         'stress_kind': 'modeled', 'P': .01, 'conditions': '外部能够在实际负载下保持端压。',
         'formula_lines': [{'expr': 'P=1²/100=0.01W', 'src': 'E1 及明确列出的外部保持条件。'}]},
        {'name_zh': '扩展范围内的条件耗散上界', 'status': 'conditional', 'role': 'comparison',
         'stress_kind': 'upper_bound', 'P': .05, 'conditions': '辅助供能包络尚未确认，不用于正式判定。',
         'formula_lines': [{'expr': 'R7耗散≤1²/20=0.05W', 'src': '辅助边界及其条件，仅用于比较。'}]},
    ]
    return {'rule_id': 'P11-A01', 'object': 'R7', 'verdict_zh': '是',
        'headline_zh': 'R7：按材料包络计算耗散上界20毫瓦，低于100毫瓦限额。',
        'location_one_liner': '原图中输入端口与回流之间的 R7。',
        'phenomenon': 'R7 跨接已确认的输入端口与回流，控制支路改变分流但不改变已给定的正常端口包络。',
        'calc': 'R7耗散上界≤2²/200=0.02W，低于0.1W限额。',
        'stuck': '无影响规定范围内判定的缺口；扩展范围条件仍未确认。',
        'conclusion': '是：规定范围内共同上界低于限额，不主张等号可达。',
        'summary': 'R7 的共同上界满足限额。', 'action': '保持已核查的设计边界。',
        'analysis_contract': 'analysis.v1',
        'analysis_steps': [{'text': '核对 R7 的原始端口范围与直接连接，用共同耗散上界覆盖控制变化。',
                            'assumption_refs': ['A1']}],
        'key_assumptions': [{'id': 'A1', 'text': '源端口工作范围由原始材料确认。',
                             'status': 'supported', 'evidence_refs': ['E1']}],
        'evidence_refs': [{'id': 'E1', 'path': 'material_inputs/source.txt', 'quote': 'Confirmed review envelope.'}],
        'electrical': {'P_rated': .2, 'P_limit': .1, 'P_max': None, 'cite': ['E1']},
        'states': states,
        'decision_basis': {'kind': 'envelope_upper_bound', 'state_index': 0, 'value_w': .02,
                           'limit_w': .1, 'evidence': ['E1'], 'conditions': '规定正常端口包络。'},
        'worst_case': {'confirmed_state_index': None, 'conditional_state_index': 1,
                      'comparison': [{'state_index': i, 'reason': '工作点参与排名；上下界仅作证明或辅助比较。'} for i in range(3)],
                      'unresolved': ['外部实际保持端压的能力尚未确认。']},
        'decision_audit': {'version': 2, 'normal_design_search': '独立检索正常范围并用共同边界覆盖控制变化。',
            'state_reviews': [{'state_index': i, 'admissibility': 'normal_design' if i == 0 else 'conditional',
                               'reason': '区分已确认范围与额外条件。'} for i in range(3)],
            'gaps': [{'issue': '条件端点可达性未确认。', 'effect': 'maximum_only',
                      'reason': '影响精确最大值，不撤销已有共同上界。'}]}}


def add_main_analysis(card):
    figure = card['states'][0].pop('figure')
    page = figure['pages'][0]
    page.update(label='耗散边界', route_summary='规定源范围与耗散支路的关系。')
    card['main_analysis'] = {'version': 'basis-main.v1', 'basis_ref': '/decision_basis',
        'kind': 'cross_case_bound', 'state_index': 0, 'title': '规定范围内的共同上界',
        'route_summary': 'R7 的端口包络约束耗散，不假设上界等号可达。', 'proof_status': 'verified',
        'assumption_refs': ['A1'], 'scope': {'included': '已确认正常输入包络。', 'excluded': '额外未确认外部驱动。'},
        'proof_checks': [{'claim': '端口包络和阻值下端成立。', 'status': 'supported',
                          'reason': '原始材料支持且已完成边界推导。', 'evidence_refs': ['E1']}],
        'figure': figure}
    return card


class LegacySchemaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.board = Path(temporary.name)
        self.base = self.board / 'review_out/agent_jobs/P11-A01/R7'
        source = self.board / 'material_inputs/source.txt'
        source.parent.mkdir(parents=True)
        source.write_text('Confirmed review envelope.', encoding='utf-8')
        self.context = {'analysis_contract': 'analysis.v1'}
        self.card = power_card()
        self.requests = []
        self.analysis = '核对对象原始材料与端口连接，使用已确认工作范围推导共同上界；独立区分条件工作点与范围外的辅助边界，保留实际证据和不可达等号的限制。'
        llm_tasks.configure()
        self.addCleanup(llm_tasks.configure)

    def schema(self):
        return llm_dispatch._legacy_schema('P11-A01', 'R7', self.context)

    def dispatch(self, wire_api='responses', card=None):
        write_json(self.base.with_suffix('.context.json'), self.context)
        job = {'job_id': 'P11-A01::R7', 'rule_id': 'P11-A01', 'objects': ['R7'], 'self_check': True}
        write_json(self.board / 'review_out/judge_jobs.json', {'attempt_id': 'test', 'jobs': [job]})
        def handler(request):
            body = json.loads(request.content)
            self.requests.append(body)
            schema = body['text']['format']['schema'] if wire_api == 'responses' else body['response_format']['json_schema']['schema']
            value = {'card': copy.deepcopy(card or self.card)} if 'card' in schema['properties'] else {'analysis': self.analysis}
            content = json.dumps(value, ensure_ascii=False)
            if wire_api == 'responses':
                self.assertTrue(str(request.url).endswith('/responses'))
                return httpx.Response(200, json={'id': 'test-response-'+str(len(self.requests)),
                    'model': 'actual-model', 'status': 'completed', 'usage': {'input_tokens': 11, 'output_tokens': 7, 'total_tokens': 18},
                    'output': [{'type': 'message', 'id': 'test-message-'+str(len(self.requests)),
                                'role': 'assistant', 'status': 'completed',
                                'content': [{'type': 'output_text', 'text': content}]}]})
            return httpx.Response(200, json={'id': 'test-response-'+str(len(self.requests)), 'model': 'actual-model',
                'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18},
                'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': content}}]})
        transport = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(transport.close)
        profile = Profile(base_url='http://localhost:11434/v1', model='configured', wire_api=wire_api, retry_backoff=0)
        client = LLMClient(profile, transport)
        with patch.object(llm_dispatch, 'LLMClient', return_value=client), \
             patch.object(llm_dispatch, 'load_profile', return_value=profile), \
             patch('subprocess.run', side_effect=AssertionError('No CLI execution')):
            return llm_dispatch.dispatch(self.board), client

    def test_power_schema_exposes_fields_and_uses_supported_structural_keywords(self):
        schema = self.schema()
        Draft202012Validator.check_schema(schema)
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(self.card)), [])
        fields = ('evidence_refs', 'headline_zh', 'states', 'electrical', 'decision_basis', 'worst_case', 'decision_audit')
        for field in fields:
            with self.subTest(missing=field):
                invalid = copy.deepcopy(self.card)
                del invalid[field]
                self.assertTrue(list(Draft202012Validator(schema).iter_errors(invalid)))
        def inspect(value):
            if isinstance(value, dict):
                self.assertFalse(set(value) & {'if', 'then', 'else', 'allOf', 'not', 'dependentSchemas'})
                for child in value.values(): inspect(child)
            elif isinstance(value, list):
                for child in value: inspect(child)
        inspect(schema)
        invalid = copy.deepcopy(self.card)
        invalid['decision_audit']['version'] = 1
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(invalid)))

    def test_both_wire_protocols_deliver_full_legacy_card_after_analysis_and_self_check(self):
        for protocol in ('chat', 'responses'):
            with self.subTest(protocol=protocol):
                self.requests.clear()
                self.assertEqual(validate_judge_card(self.card, rule_id='P11-A01', obj='R7', board=self.board), [])
                result, client = self.dispatch(protocol)
                self.assertEqual(result['failed'], 0, result)
                self.assertEqual(client.request_count, 3)
                self.assertEqual(read_json(self.base.with_suffix('.json')), self.card)
                metadata = read_json(self.base.with_suffix('.llm.json'))
                self.assertEqual(metadata['wire_api'], protocol)
                self.assertEqual(metadata['usage']['total_tokens'], 54)
                final = self.requests[-1]
                messages = final['input'] if protocol == 'responses' else final['messages']
                prompt = json.dumps(messages[-1], ensure_ascii=False)
                self.assertIn('P11-A01 · 落判定卡', prompt)
                self.assertIn('二维名字数组', prompt)
                self.assertIn('upper_bound/lower_bound', prompt)
                self.assertIn('analysis_steps 是唯一业务', prompt)
                if protocol == 'responses':
                    self.assertFalse(final['store'])
                    self.assertEqual(sum(m.get('type') == 'message' and m.get('role') == 'assistant' for m in messages), 2)

    def test_missing_power_contract_is_rejected_without_overwriting_prior_card(self):
        write_json(self.base.with_suffix('.json'), self.card)
        original = self.base.with_suffix('.json').read_bytes()
        invalid = copy.deepcopy(self.card)
        del invalid['decision_audit']
        result, _ = self.dispatch(card=invalid)
        self.assertEqual(result['failed'], 1)
        self.assertIn('JSON schema validation failed', result['results'][0]['error'])
        self.assertEqual(result['results'][0]['error_kind'], 'output_validation')
        self.assertEqual(self.base.with_suffix('.json').read_bytes(), original)

    def test_schema_valid_wrong_bound_ranking_still_fails_semantic_gate(self):
        invalid = copy.deepcopy(self.card)
        invalid['worst_case']['conditional_state_index'] = 2
        invalid['states'][2]['role'] = 'worst_candidate'
        self.assertEqual(list(Draft202012Validator(self.schema()).iter_errors(invalid)), [])
        result, client = self.dispatch(card=invalid)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(client.request_count, 5)
        self.assertIn('conditional_state_index', result['results'][0]['error'])
        self.assertFalse(self.base.with_suffix('.json').exists())
        self.assertEqual(read_json(self.base.with_suffix('.invalid.json'))['attempt'], 3)

    def test_diagram_branch_objects_are_rejected_by_output_schema(self):
        invalid = copy.deepcopy(self.card)
        page = invalid['states'][0]['figure']['pages'][0]
        page['paths'] = [{'path': page['paths'][0], 'relation_role': 'dissipation'}]
        errors = list(Draft202012Validator(self.schema()).iter_errors(invalid))
        self.assertTrue(errors)
        result, _ = self.dispatch(card=invalid)
        self.assertEqual(result['failed'], 1)
        self.assertFalse(self.base.with_suffix('.json').exists())

    def test_main_analysis_embeds_existing_contract_and_keeps_one_figure_owner(self):
        self.context['main_analysis_contract'] = 'basis-main.v1'
        self.card = add_main_analysis(self.card)
        schema = self.schema()
        self.assertIn('main_analysis', schema['required'])
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(self.card)), [])
        self.assertEqual(validate_judge_card(self.card, rule_id='P11-A01', obj='R7', board=self.board), [])
        result, _ = self.dispatch()
        self.assertEqual(result['failed'], 0, result)
        invalid = copy.deepcopy(self.card)
        invalid['states'][0]['figure'] = copy.deepcopy(invalid['main_analysis']['figure'])
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(invalid)))
        invalid = copy.deepcopy(self.card)
        invalid['main_analysis']['state_index'] = 1
        self.assertTrue(validate_judge_card(invalid, rule_id='P11-A01', obj='R7', board=self.board))

    def test_voltage_review_exposes_voltage_fields_without_power_audit(self):
        schema = llm_dispatch._legacy_schema('P11-A03', 'C7', self.context)
        electrical = schema['properties']['electrical']
        self.assertEqual(set(electrical['required']), {'V_rated', 'V_limit', 'V_max', 'cite'})
        self.assertNotIn('decision_audit', schema['required'])


if __name__ == '__main__':
    unittest.main()
