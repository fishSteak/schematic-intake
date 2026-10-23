"""One-object direct HTTP dispatch through actual schemas and business validators."""

import copy
from dataclasses import replace
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import httpx

from agent_review import llm_dispatch, llm_tasks
from agent_review.llm import LLMClient, Profile, LLMError
from agent_review.review_contract import validate_review
from agent_review.validate_job import validate_judge_card
from kernel.util import ROOT, read_json, write_json


class LLMDispatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.board = Path(temporary.name)
        self.out = self.board / 'review_out'
        self.profile = Profile(base_url='http://localhost:11434/v1', model='configured', retry_backoff=0)
        self.requests = []
        self.events = []
        llm_tasks.configure(emit=lambda kind, value: self.events.append((kind, value)))
        self.addCleanup(llm_tasks.configure)
        self.analysis = '依据当前工作台与原始材料，核对对象和关联支路的连接、适用条件与约束。按已确认来源求解并区分实际见证和未确认候选，保留影响判定的缺口。'

    def native_fixture(self):
        card = read_json(ROOT / 'docs/samples/review-contract-v1/R4.review.example.json')
        old_steps = card.pop('reasoning_steps')
        card.update(analysis_contract='analysis.v1', analysis_steps=[{
            'text': '；'.join(old_steps) + '；若外部条件尚未确认，按既有模型限定推导。',
            'premise_refs': card['display']['premise_refs'],
        }])
        context = {'analysis_contract': 'analysis.v1', 'contract_task': {
            **{key: copy.deepcopy(card[key]) for key in ('schema_version', 'target', 'criterion')},
            'analysis_contract': 'analysis.v1', 'fixed_evidence': [], 'fixed_inputs': [],
        }}
        comps, nets = {}, {}
        for scenario in card['scenarios']:
            for page in (scenario.get('route') or {}).get('pages', []):
                for branch in page['branches']:
                    for token in branch['path']:
                        if re.fullmatch(r'[A-Z]+\d+(?:\.[^.]+)?', token):
                            ref, _, pin = token.partition('.')
                            comp = comps.setdefault(ref, {'pins': []})
                            if pin and not any(row['pin'] == pin for row in comp['pins']):
                                comp['pins'].append({'pin': pin})
                        else:
                            nets[token] = {}
        for evidence in card['evidence']:
            if evidence['kind'] == 'rule':
                evidence['path'] = 'rules/review_contract_rules.json'
                continue
            path = self.board / evidence['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}' if path.suffix == '.json' else 'unit test source', encoding='utf-8')
        write_json(self.out / 'board_kg.json', {'components': comps, 'nets': nets})
        write_json(self.out / 'agent_jobs/P11-A01/R4.context.json', context)
        self.assertEqual(validate_review(card, board=self.board, rule_id='P11-A01', obj='R4'), [])
        self.card = card
        self.base = self.out / 'agent_jobs/P11-A01/R4'
        self.job = {'job_id': 'P11-A01::R4::v1', 'rule_id': 'P11-A01', 'objects': ['R4'],
                    'contract_version': 'review.v1', 'self_check': True, 'execution_hash': 'test-policy'}
        return card

    def legacy_fixture(self):
        self.card = {'rule_id': 'P05-A01', 'object': 'R1', 'verdict_zh': '待确认',
                     'phenomenon': 'R1 的材料没有给出精度标注。', 'calc': '核对原始材料的精度标注字段。',
                     'stuck': '精度字段尚未确认。', 'conclusion': '待确认：需确认精度标注。',
                     'summary': 'R1 精度标注尚未确认。', 'action': '补充原始精度标注。',
                     'location_one_liner': 'R1 所在原图页面。',
                     'analysis_contract': 'analysis.v1',
                     'analysis_steps': [{'text': '核对 R1 的原始器件字段，精度标注尚未确认。', 'assumption_refs': []}],
                     'key_assumptions': []}
        self.base = self.out / 'agent_jobs/P05-A01/R1'
        write_json(self.base.with_suffix('.context.json'), {'analysis_contract': 'analysis.v1'})
        self.assertEqual(validate_judge_card(self.card, rule_id='P05-A01', obj='R1', board=self.board), [])
        self.job = {'job_id': 'P05-A01::R1', 'rule_id': 'P05-A01', 'objects': ['R1'], 'self_check': True}

    def dispatch(self, response_factory, *, profile=None):
        write_json(self.out / 'judge_jobs.json', {'attempt_id': 'current-attempt', 'jobs': [self.job]})
        def handler(request):
            body = json.loads(request.content)
            self.requests.append(body)
            value = response_factory(body)
            if isinstance(value,httpx.Response):return value
            is_tool = isinstance(value, list)
            return httpx.Response(200, json={
                'id': 'response-' + str(len(self.requests)), 'model': 'actual-model',
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
                'choices': [{'finish_reason': 'tool_calls' if is_tool else 'stop', 'message': {
                    'role': 'assistant', 'content': '' if is_tool else json.dumps(value, ensure_ascii=False),
                    'tool_calls': value if is_tool else [],
                }}],
            })
        transport = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(transport.close)
        client = LLMClient(profile or self.profile, transport)
        with patch.object(llm_dispatch, 'LLMClient', return_value=client), \
             patch.object(llm_dispatch, 'load_profile', return_value=client.profile), \
             patch('subprocess.run', side_effect=AssertionError('No CLI subprocess permitted')):
            return llm_dispatch.dispatch(self.board), client

    def final_or_analysis(self, body, card=None):
        schema = body['response_format']['json_schema']['schema']
        return {'card': copy.deepcopy(card or self.card)} if 'card' in schema['properties'] else {'analysis': self.analysis}

    def test_native_multiphase_tool_protocol_schema_and_real_usage(self):
        self.native_fixture()
        source = 'review_out/design_task.json'
        def output(body):
            if len(self.requests) == 1:
                return [{'id': 'read-source', 'type': 'function', 'function': {
                    'name': 'read_material', 'arguments': json.dumps({'path': source}),
                }}]
            return self.final_or_analysis(body)
        result, client = self.dispatch(output)
        self.assertEqual(result['failed'], 0, result)
        self.assertEqual(client.request_count, 4)
        self.assertEqual(read_json(self.base.with_suffix('.json')), self.card)
        metadata = read_json(self.base.with_suffix('.llm.json'))
        self.assertEqual(metadata['model'], 'actual-model')
        self.assertEqual(metadata['usage']['total_tokens'], 60)
        self.assertEqual(len(metadata['response_ids']), 4)
        second_request = self.requests[1]['messages']
        tool_reply = next(message for message in second_request if message['role'] == 'tool')
        self.assertEqual(json.loads(tool_reply['content'])['path'], source)
        phases = [json.loads(line).get('phase') for line in self.base.with_suffix('.run.jsonl').read_text().splitlines()]
        self.assertIn('analysis_draft', phases)
        self.assertIn('decisive_check', phases)
        self.assertIn('contract', phases)

    def test_native_immutable_criterion_failure_never_overwrites_prior_card(self):
        self.native_fixture()
        write_json(self.base.with_suffix('.json'), self.card)
        original = self.base.with_suffix('.json').read_bytes()
        invalid = copy.deepcopy(self.card)
        invalid['criterion']['limit']['value'] *= 2
        result, client = self.dispatch(lambda body: self.final_or_analysis(body, invalid))
        self.assertEqual(result['failed'], 1)
        self.assertEqual(client.request_count, 5)
        self.assertIn('immutable criterion', result['results'][0]['error'])
        self.assertEqual(self.base.with_suffix('.json').read_bytes(), original)
        self.assertIn('immutable criterion', json.dumps(read_json(self.base.with_suffix('.invalid.json'))))
        self.assertIn('immutable criterion', self.requests[-1]['messages'][-1]['content'])
        self.assertTrue(result['results'][0]['repairable'])
        self.assertEqual(result['results'][0]['error_kind'],'output_validation')
        llm_dispatch.raise_for_execution_failures(result)

    def test_budget_is_shared_across_analysis_check_and_finalization(self):
        self.legacy_fixture()
        result, client = self.dispatch(self.final_or_analysis, profile=replace(self.profile, max_requests=2))
        self.assertEqual(result['failed'], 1)
        self.assertEqual(client.request_count, 2)
        self.assertIn('budget exhausted', result['results'][0]['error'])
        self.assertEqual(result['results'][0]['error_kind'],'budget')
        self.assertFalse(result['results'][0]['repairable'])
        self.assertFalse(self.base.with_suffix('.json').exists())
        with self.assertRaisesRegex(LLMError,'budget'):
            llm_dispatch.raise_for_execution_failures(result)
        # The real repair entry point rejects this dispatch before any new client
        # or budget is created, including direct CLI/followup repair callers.
        from agent_review.repair_cards import repair
        with patch.object(llm_dispatch,'dispatch') as retry:
            with self.assertRaisesRegex(LLMError,'budget'):
                repair(self.board,{}, {'by_rule':{'P05-A01':['R1']}})
        retry.assert_not_called()

    def test_transport_and_http_failures_preserve_category_and_cannot_enter_repair(self):
        self.legacy_fixture()
        def timeout(body):raise httpx.ReadTimeout('SECRET_URL should not escape')
        for output,kind,status in ((timeout,'timeout',None),(lambda body:httpx.Response(401,text='SECRET_BODY'),'http',401)):
            with self.subTest(kind=kind):
                result,client=self.dispatch(output,profile=replace(self.profile,max_retries=1))
                row=result['results'][0]
                self.assertEqual(row['error_kind'],kind)
                self.assertFalse(row['repairable'])
                self.assertEqual(row.get('status_code'),status)
                self.assertEqual(client.request_count,2 if kind=='timeout' else 1)
                failed=read_json(self.base.with_suffix('.failed.json'))
                self.assertEqual(failed['error_kind'],kind)
                self.assertNotIn('SECRET_',json.dumps(result)+json.dumps(failed))
                with self.assertRaisesRegex(LLMError,kind):llm_dispatch.raise_for_execution_failures(result)
                self.assertFalse(self.base.with_suffix('.json').exists())

    def test_model_cannot_mark_real_review_as_dry_run_or_supply_provenance(self):
        self.legacy_fixture()
        invalid = copy.deepcopy(self.card)
        invalid['meta'] = {'dry_run': True}
        invalid['_execution'] = {'fingerprint': 'model-claims-valid'}
        result, _ = self.dispatch(lambda body: self.final_or_analysis(body, invalid))
        self.assertEqual(result['failed'], 1)
        self.assertIn('controller provenance', result['results'][0]['error'])
        self.assertIn('dry run', result['results'][0]['error'])
        self.assertFalse(self.base.with_suffix('.json').exists())

    def test_resume_checks_analysis_hash_and_reuses_completed_phases(self):
        self.native_fixture()
        first, _ = self.dispatch(self.final_or_analysis)
        self.assertEqual(first['failed'], 0)
        self.requests.clear()
        self.job['resume_stage'] = 'self_check'
        result, client = self.dispatch(self.final_or_analysis)
        self.assertEqual(result['failed'], 0, result)
        self.assertEqual(client.request_count, 1)
        self.requests.clear()
        checkpoint = self.board / 'history/execution-checkpoints/P11-A01/R4/test-policy'
        conversation=read_json(checkpoint/'conversation.json')
        conversation['messages'].append({'role':'user','content':'Altered conversation must not be reused.'})
        write_json(checkpoint/'conversation.json',conversation)
        result,client=self.dispatch(self.final_or_analysis)
        self.assertEqual(result['failed'],0,result)
        self.assertEqual(client.request_count,3)
        self.requests.clear()
        (checkpoint/'conversation.json').write_text('{invalid JSON')
        result,client=self.dispatch(self.final_or_analysis)
        self.assertEqual(result['failed'],0,result)
        self.assertEqual(client.request_count,3)
        self.requests.clear()
        (checkpoint / 'review_out/agent_jobs/P11-A01/R4.analysis.md').write_text('tampered analysis')
        result, client = self.dispatch(self.final_or_analysis)
        self.assertEqual(result['failed'], 0, result)
        self.assertEqual(client.request_count, 3)


if __name__ == '__main__':
    unittest.main()
