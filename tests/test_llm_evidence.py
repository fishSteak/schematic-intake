"""Research repairs keep a shared budget and native pending decisions stay visible."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from agent_review import evidence_followup, evidence_loop, llm_tasks
from agent_review.llm import LLMClient, Profile
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
            with self.assertRaisesRegex(RuntimeError,'prior report retained'):
                evidence_loop.run_research(self.board,self.enumeration,force=True)
        self.assertEqual(len(calls),1)
        self.assertEqual(client.request_count,1)
        self.assertFalse((self.out/'evidence_store/current.json').exists())
        run=read_json(next((self.out/'evidence_proposals').glob('*/run.json')))
        self.assertEqual(run['status'],'error')
        self.assertIn('budget exhausted',run['error'])
        self.assertEqual(run['metadata']['usage']['total_tokens'],15)


if __name__=='__main__':
    unittest.main()
