"""Research repairs keep a shared budget and native pending decisions stay visible."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from agent_review import evidence_followup, evidence_loop, llm_tasks
from agent_review.llm import LLMClient, Profile, BudgetExceeded, LLMTransportError, LLMHTTPError
from agent_review.llm_tools import MaterialTools
from kernel.util import read_json, write_json


class LLMEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.board=Path(temporary.name)
        write_json(self.board/'materials.json',{})
        self.out=self.board/'review_out'
        self.pair={'rule_id':'P11-A01','object':'R4'}
        self.enumeration={'rule_ids':['P11-A01'],'by_rule':{'P11-A01':['R4']}}
        self.native={'schema_version':'review.v1','decision':{'verdict':'pending'}}
        write_json(self.out/'agent_jobs/P11-A01/R4.json',self.native)
        self.data={'schema_version':'evidence.v1','facts':[],'conflicts':[],'requests':[],
                   'search_log':[{'source':'materials.json','purpose':'Inspect supplied materials','outcome':'No decisive facts supplied'}]}
        llm_tasks.configure();self.addCleanup(llm_tasks.configure)

    def request(self,category='business_fact'):
        return {'id':'request-1','category':category,'question':'Confirm the interface boundary.',
                'decision_impact':'The missing boundary changes whether the candidate is applicable.',
                'why_unavailable':'The supplied material does not specify this value.',
                'attempts':['Inspected supplied material.'],'affected_pairs':[self.pair]}

    def test_material_read_and_quote_validation_share_strict_text_decoding(self):
        path=self.board/'material_inputs/bom.csv';path.parent.mkdir()
        source='位号,厂家,阻值\r\nR4,厚声,47kΩ\r\n'
        tools=MaterialTools(self.board)
        for codec in ('utf-8-sig','utf-16','gbk','gb18030'):
            with self.subTest(codec=codec):
                text=source+('𠀀扩展字符\r\n' if codec=='gb18030' else '')
                path.write_bytes(text.encode(codec))
                returned=tools.read_material('material_inputs/bom.csv')
                self.assertEqual(returned['text'],text)
                data=copy.deepcopy(self.data)
                data['facts']=[{'id':'fact-1','subject':'R4','predicate':'BOM identity',
                    'value':'厚声','conditions':'Provided BOM row',
                    'evidence':[{'path':'material_inputs/bom.csv','locator':'BOM row',
                                 'quote':returned['text']}],
                    'affected_pairs':[self.pair]}]
                self.assertEqual(evidence_loop.validate_research(data,self.board),[])
                data['facts'][0]['evidence'][0]['quote']='这个内容不在原始材料'
                self.assertTrue(evidence_loop.validate_research(data,self.board))
        path.write_bytes(b'\xff')
        self.assertTrue(any('unreadable evidence' in error
                            for error in evidence_loop.validate_research(data,self.board)))

    def test_native_pending_requires_scoped_receipt_and_followup_coverage(self):
        self.assertEqual(evidence_loop.pending_pairs(self.board,[self.pair]),{('P11-A01','R4')})
        write_json(self.out/'agent_jobs/P11-A01/R4.research.json',self.data)
        self.assertTrue(evidence_loop.validate_object_research(self.board,'P11-A01','R4',self.native))
        self.assertTrue(evidence_loop.validate_followup_coverage(self.data,self.board,[self.pair]))
        self.data['requests']=[self.request()]
        write_json(self.out/'agent_jobs/P11-A01/R4.research.json',self.data)
        self.assertEqual(evidence_loop.validate_object_research(self.board,'P11-A01','R4',self.native),[])
        self.assertEqual(evidence_loop.validate_followup_coverage(self.data,self.board,[self.pair]),[])

    def test_native_business_request_is_not_dropped_from_published_request_list(self):
        self.data.update(requests=[self.request()],_provenance={'run_id':'research-run'})
        write_json(self.out/'evidence_store/current.json',self.data)
        with patch.object(evidence_followup,'run_research'):
            result=evidence_followup.run(self.board,{},self.enumeration)
        self.assertEqual(result['business_request_count'],1)
        self.assertEqual(read_json(self.out/'business_material_requests.json')['business_requests'],[self.request()])

    def test_native_reasoning_request_triggers_targeted_review(self):
        self.data.update(requests=[self.request('reasoning')],_provenance={'run_id':'research-run'})
        write_json(self.out/'evidence_store/current.json',self.data)
        with patch.object(evidence_followup,'run_research'), \
             patch.object(evidence_followup,'filter_enumeration_for_agent',side_effect=lambda value,kg:value), \
             patch('agent_review.stages.judge.run',return_value={'failed':0}) as judge:
            result=evidence_followup.run(self.board,{},self.enumeration,max_audits=2)
        judge.assert_called_once()
        self.assertEqual(judge.call_args.args[2]['by_rule'],{'P11-A01':['R4']})
        self.assertEqual(result['internal_unresolved_count'],1)

    def test_research_repairs_share_request_budget_and_keep_real_usage(self):
        bad=copy.deepcopy(self.data)
        bad['facts']=[{'id':'fact-1','subject':'R4','predicate':'rating','value':1,'conditions':'Specified condition',
                       'evidence':[{'path':'missing-source.txt','locator':'rating','quote':'not present'}],
                       'affected_pairs':[self.pair]}]
        calls=[]
        def handler(request):
            calls.append(request)
            return httpx.Response(200,json={'id':'response-1','model':'actual-model',
                'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15},
                'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(bad)}}]})
        profile=Profile(base_url='http://localhost:11434/v1',model='configured',max_requests=1)
        transport=httpx.Client(transport=httpx.MockTransport(handler));self.addCleanup(transport.close)
        client=LLMClient(profile,transport)
        with patch('agent_review.llm.load_profile',return_value=profile), \
             patch('agent_review.llm.LLMClient',return_value=client):
            with self.assertRaisesRegex(BudgetExceeded,'budget exhausted'):
                evidence_loop.run_research(self.board,self.enumeration,force=True)
        self.assertEqual(len(calls),1)
        self.assertEqual(client.request_count,1)
        self.assertFalse((self.out/'evidence_store/current.json').exists())
        run=read_json(next((self.out/'evidence_proposals').glob('*/run.json')))
        self.assertEqual(run['status'],'error')
        self.assertIn('budget exhausted',run['error'])
        self.assertEqual(run['metadata']['usage']['total_tokens'],15)
        self.assertEqual(run['error_kind'],'budget')
        self.assertFalse(run['repairable'])

    def test_research_does_not_retry_exhausted_transport_or_http_as_semantic_repair(self):
        for kind,error in (('timeout',LLMTransportError),('http',LLMHTTPError)):
            with self.subTest(kind=kind):
                calls=[]
                def handler(request):
                    calls.append(request)
                    if kind=='timeout':raise httpx.ReadTimeout('sensitive provider details',request=request)
                    return httpx.Response(401,text='sensitive provider body')
                profile=Profile(base_url='http://localhost:11434/v1',model='test',max_retries=1,retry_backoff=0)
                transport=httpx.Client(transport=httpx.MockTransport(handler));self.addCleanup(transport.close)
                client=LLMClient(profile,transport)
                with patch('agent_review.llm.load_profile',return_value=profile), \
                     patch('agent_review.llm.LLMClient',return_value=client):
                    with self.assertRaises(error):evidence_loop.run_research(self.board,self.enumeration,force=True)
                self.assertEqual(len(calls),2 if kind=='timeout' else 1)
                self.assertFalse((self.out/'evidence_store/current.json').exists())
                logs=[read_json(path) for path in (self.out/'evidence_proposals').glob('*/run.json')]
                self.assertTrue(any(log.get('error_kind')==kind and log.get('repairable') is False for log in logs))
                self.assertNotIn('sensitive provider',json.dumps(logs))

    def test_research_schema_failure_still_gets_semantic_repair(self):
        calls=[]
        def handler(request):
            calls.append(request)
            value={} if len(calls)==1 else self.data
            return httpx.Response(200,json={'id':'response-'+str(len(calls)),'model':'actual-model',
                'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(value)}}]})
        profile=Profile(base_url='http://localhost:11434/v1',model='test')
        transport=httpx.Client(transport=httpx.MockTransport(handler));self.addCleanup(transport.close)
        client=LLMClient(profile,transport)
        with patch('agent_review.llm.load_profile',return_value=profile), \
             patch('agent_review.llm.LLMClient',return_value=client):
            result=evidence_loop.run_research(self.board,self.enumeration,force=True)
        self.assertFalse(result['reused'])
        self.assertEqual(len(calls),2)
        self.assertTrue((self.out/'evidence_store/current.json').is_file())


if __name__=='__main__':
    unittest.main()
