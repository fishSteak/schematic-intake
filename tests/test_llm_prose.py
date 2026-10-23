"""API-generated prose keeps source provenance and publication gates intact."""

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from agent_review import detail_prose, detail_prose_recovery, remediation, llm_tasks
from agent_review.detail_view import verify_prose_provenance
from agent_review.execution_policy import execution_hash
from agent_review.llm import LLMCancelled, LLMClient, Profile
from agent_review.review_contract import source_hash
from kernel.util import read_json, write_json


class LLMProseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.board = Path(tmp.name)
        self.out = self.board / 'review_out'
        self.profile = Profile(base_url='http://localhost:11434/v1', model='requested-model', retry_backoff=0)
        profile_path = self.board / 'profile.json'
        write_json(profile_path, {'base_url': self.profile.base_url, 'model': self.profile.model, 'retry_backoff': 0})
        env = patch.dict(os.environ, {'SCHEMATIC_LLM_PROFILE': str(profile_path)}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.card = dict(rule_id='CHECK', object='X3', verdict_zh='待确认',
                         assumptions=['外部吸流能力尚未确认。'], summary='原始摘要')

    def client(self, output):
        def handler(request):
            payload = json.loads(request.content)
            context = json.loads(payload['messages'][1]['content'])
            data = output(context)
            return httpx.Response(200, json={
                'id': 'real-http-response', 'model': 'served-model',
                'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30},
                'choices': [{'message': {'role': 'assistant', 'content': json.dumps(data)}, 'finish_reason': 'stop'}],
            })
        http = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(http.close)
        return LLMClient(self.profile, http)

    def prose_output(self, supplied):
        return {'records': [dict(
            analysis_contract='analysis.v1', key=ctx['key'], source_hash=ctx['source_hash'], status='ready',
            analysis_steps=[{'text': '若外部吸流条件成立，沿 X3 所在支路核对节点约束并推导对象端压。',
                             'assumption_refs': ['legacy-assumption-0']}],
            primary_ref=None, route_summary='', note='',
            citations=[{'source': 'card', 'quote': '外部吸流能力尚未确认。'}],
        ) for ctx in supplied['records']]}

    def generate_prose(self):
        client = self.client(self.prose_output)
        with patch.object(detail_prose, 'LLMClient', return_value=client), \
             patch('subprocess.run', side_effect=AssertionError('No CLI subprocess permitted')):
            detail_prose.generate_batch(self.board, [self.card], profile=self.profile)
        return detail_prose.load(self.out, self.card)

    def test_detail_prose_records_real_api_model_usage_and_untouched_raw_batch(self):
        before = copy.deepcopy(self.card)
        prose = self.generate_prose()
        self.assertIsNotNone(prose)
        self.assertEqual(self.card, before)
        verify_prose_provenance(self.out, prose)
        batch = self.out / prose['_meta']['batch_output']
        self.assertNotIn('_meta', read_json(batch)['records'][0])
        run = read_json(batch.with_suffix('.run.json'))
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(run['model'], 'served-model')
        self.assertEqual(run['metadata']['response_ids'], ['real-http-response'])
        self.assertEqual(run['metadata']['usage']['total_tokens'], 30)
        run['status'] = 'error'
        write_json(batch.with_suffix('.run.json'), run)
        with self.assertRaisesRegex(RuntimeError, 'completed run'):
            detail_prose.load(self.out, self.card)

    def test_recovery_retains_real_model_and_refuses_previous_model_policy(self):
        prose = self.generate_prose()
        path = detail_prose.path_for(self.out, self.card)
        verdicts = {'results': [{'rule_id': 'CHECK', 'object_cards': [self.card]}]}
        path.unlink()
        self.assertEqual(detail_prose_recovery.recover(self.board, verdicts), ['CHECK::X3'])
        self.assertEqual(read_json(path)['_meta']['model'], 'served-model')
        self.assertEqual(read_json(path)['_meta']['run_id'], prose['_meta']['run_id'])
        path.unlink()
        profile_path = Path(os.environ['SCHEMATIC_LLM_PROFILE'])
        write_json(profile_path, {'base_url': self.profile.base_url, 'model': 'different-model', 'retry_backoff': 0})
        self.assertEqual(detail_prose_recovery.recover(self.board, verdicts), [])
        self.assertFalse(path.exists())

    def test_invalid_prose_never_overwrites_previous_valid_output(self):
        self.generate_prose()
        path = detail_prose.path_for(self.out, self.card)
        original = path.read_bytes()
        client = self.client(lambda context: {'records': []})
        with patch.object(detail_prose, 'LLMClient', return_value=client):
            with self.assertRaisesRegex(RuntimeError, 'publication not started'):
                detail_prose.generate_batch(self.board, [self.card], profile=self.profile)
        self.assertEqual(client.request_count, 3)
        self.assertEqual(path.read_bytes(), original)

    def remediation_inputs(self):
        card = {'target': {'rule_id': 'P11-A01', 'object_id': 'R901', 'object_type': 'component'},
                'inputs': [{'id': 'controller_rated', 'value': 2, 'unit': 'W'},
                           {'id': 'controller_resistance', 'value': 2200, 'unit': 'ohm'}],
                'criterion': {'limit': {'value': 1}},
                'scenarios': [{'id': 'actual_case', 'metrics': [{'key': 'power', 'value': 0.6}]}]}
        result = {'schema_version': remediation.VERSION, 'target': card['target'], 'review_source_hash': source_hash(card),
                  'summary': '保持阻值，更换为建议额定规格并匹配焊盘。按最坏持续工况和温度降额验收。',
                  'specific_action': '保持阻值并更换功率规格及相应封装焊盘。',
                  'reference_spec': {'keep_input_refs': ['controller_resistance'], 'basis_scenario_ref': 'actual_case',
                                     'minimum_rating_w': 1.2, 'proposed_rating_w': 2,
                                     'qualification': '当前候选下的参考方案，待实物验收。'},
                  'acceptance_criteria': '最坏持续工况耗散须满足实际温度下的使用比例，且功能正常。'}
        write_json(self.out / 'agent_jobs/P11-A01/R901.json', card)
        write_json(self.out / 'remediation_config.json', {'P11-A01': {'R901': remediation.VERSION}})
        return card, result

    def test_remediation_uses_api_and_keeps_canonical_card_immutable(self):
        card, result = self.remediation_inputs()
        client = self.client(lambda context: result)
        with patch('agent_review.llm.runtime.LLMClient', return_value=client), \
             patch.object(remediation, 'validate_review', return_value=[]), \
             patch('subprocess.run', side_effect=AssertionError('No CLI subprocess permitted')):
            artifact = remediation.generate(self.board, 'P11-A01', 'R901', profile=self.profile)
        self.assertEqual(artifact['_meta']['model'], 'served-model')
        self.assertEqual(remediation.load(self.out, card), artifact)
        self.assertEqual(read_json(self.out / 'agent_jobs/P11-A01/R901.json'), card)
        path = remediation.artifact_path(self.out, 'P11-A01', 'R901')
        run = read_json(path.with_suffix('.run.json'))
        self.assertEqual(run['metadata']['usage']['total_tokens'], 30)
        self.assertNotIn('_meta', read_json(path.with_suffix('.candidate.json')))
        original = path.read_bytes()
        invalid = copy.deepcopy(result)
        invalid['reference_spec']['proposed_rating_w'] = 0.5
        client = self.client(lambda context: invalid)
        with patch('agent_review.llm.runtime.LLMClient', return_value=client), \
             patch.object(remediation, 'validate_review', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'proposed rating below'):
                remediation.generate(self.board, 'P11-A01', 'R901', force=True, profile=self.profile)
        self.assertEqual(path.read_bytes(), original)

    def test_execution_and_prose_policies_change_with_model_but_not_credentials(self):
        def fingerprints():
            return execution_hash('P05-A01', {}, 'materials'), detail_prose.policy_hash(), remediation.fingerprint()
        before = fingerprints()
        with patch.dict(os.environ, {'SCHEMATIC_LLM_API_KEY': 'rotated-secret', 'AGENT_JUDGE_MODEL': 'ignored-legacy-model'}):
            self.assertEqual(fingerprints(), before)
        path = Path(os.environ['SCHEMATIC_LLM_PROFILE'])
        write_json(path, {'base_url': self.profile.base_url, 'model': 'new-model', 'retry_backoff': 0})
        after = fingerprints()
        for old, new in zip(before, after):
            self.assertNotEqual(old, new)

    def test_prose_inherits_worker_events_and_cancellation_without_explicit_arguments(self):
        emitted=[]
        llm_tasks.configure(emit=lambda kind,event: emitted.append((kind,event)))
        self.addCleanup(llm_tasks.configure)
        self.generate_prose()
        self.assertTrue(any(event['type']=='llm.response' for _,event in emitted))
        path=detail_prose.path_for(self.out,self.card)
        previous=path.read_bytes()
        client=self.client(self.prose_output)
        llm_tasks.configure(cancelled=lambda:True)
        with patch.object(detail_prose,'LLMClient',return_value=client),self.assertRaises(LLMCancelled):
            detail_prose.generate_batch(self.board,[self.card],profile=self.profile)
        self.assertEqual(client.request_count,0)
        self.assertEqual(path.read_bytes(),previous)

    def test_remediation_inherits_worker_cancellation_and_explicit_override_wins(self):
        _,result=self.remediation_inputs()
        llm_tasks.configure(cancelled=lambda:True)
        self.addCleanup(llm_tasks.configure)
        client=self.client(lambda context:result)
        with patch('agent_review.llm.runtime.LLMClient',return_value=client), \
             patch.object(remediation,'validate_review',return_value=[]):
            with self.assertRaises(LLMCancelled):
                remediation.generate(self.board,'P11-A01','R901',profile=self.profile)
            self.assertEqual(client.request_count,0)
            artifact=remediation.generate(self.board,'P11-A01','R901',profile=self.profile,cancelled=lambda:False)
        self.assertEqual(artifact['_meta']['model'],'served-model')


if __name__ == '__main__':
    unittest.main()
