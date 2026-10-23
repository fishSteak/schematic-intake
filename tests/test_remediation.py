import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent_review.remediation import VERSION,artifact_path,fingerprint,load,validate
from agent_review.review_contract import source_hash
from kernel.util import write_json


class RemediationTests(unittest.TestCase):
    def setUp(self):
        self.card={'target':{'rule_id':'P11-A01','object_id':'R901','object_type':'component'},
                   'inputs':[{'id':'controller_rated','value':2,'unit':'W'},{'id':'controller_resistance','value':2200,'unit':'ohm'}],
                   'criterion':{'limit':{'value':1}},'scenarios':[{'id':'actual_case','metrics':[{'key':'power','value':0.6}]}]}
        self.result={'schema_version':VERSION,'target':self.card['target'],'review_source_hash':source_hash(self.card),
                     'summary':'保持阻值，更换为建议额定规格并匹配焊盘。按最坏持续工况和温度降额验收。',
                     'specific_action':'保持阻值并更换功率规格及相应封装焊盘。',
                     'reference_spec':{'keep_input_refs':['controller_resistance'],'basis_scenario_ref':'actual_case','minimum_rating_w':1.2,'proposed_rating_w':2,'qualification':'当前候选下的参考方案，待实物验收。'},
                     'acceptance_criteria':'最坏持续工况耗散须满足实际温度下的使用比例，且功能正常。'}

    def test_minimum_is_computed_from_actual_review_parameters(self):
        self.assertEqual(validate(self.result,self.card),[])
        self.card['inputs'][0]['value']=4
        self.result['review_source_hash']=source_hash(self.card)
        self.assertTrue(any('minimum rating differs' in e for e in validate(self.result,self.card)))
        self.result['reference_spec']['minimum_rating_w']=2.4
        self.result['reference_spec']['proposed_rating_w']=3
        self.assertEqual(validate(self.result,self.card),[])

    def test_unknown_or_missing_references_do_not_generate_ratings(self):
        self.result['reference_spec']['basis_scenario_ref']='made_up'
        self.assertIn('unknown reference scenario',validate(self.result,self.card))
        self.result['reference_spec']['basis_scenario_ref']='actual_case'
        self.card['scenarios'][0]['metrics'][0]['value']=None
        self.result['review_source_hash']=source_hash(self.card)
        self.assertIn('unknown stress cannot produce a numeric rating',validate(self.result,self.card))

    def test_insufficient_proposal_and_long_summary_are_rejected(self):
        self.result['reference_spec']['proposed_rating_w']=1
        self.assertIn('proposed rating below reference minimum',validate(self.result,self.card))
        self.result['summary']='第一句。第二句。第三句。'
        self.assertIn('summary requires one or two sentences',validate(self.result,self.card))

    def test_source_or_prompt_change_invalidates_cached_model_summary(self):
        before=copy.deepcopy(self.card)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);rid,obj=self.card['target']['rule_id'],self.card['target']['object_id']
            self.assertIsNone(load(out,self.card))
            write_json(out/'remediation_config.json',{rid:{obj:VERSION}})
            data=copy.deepcopy(self.result)
            data['_meta']={'contract_hash':fingerprint(),'validation_status':'valid','output_hash':hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()}
            write_json(artifact_path(out,rid,obj),data)
            self.assertEqual(load(out,self.card)['summary'],self.result['summary'])
            with patch('agent_review.remediation.fingerprint',return_value='changed'):
                with self.assertRaisesRegex(RuntimeError,'prompt/contract changed'):load(out,self.card)
            data['summary']='篡改建议。';write_json(artifact_path(out,rid,obj),data)
            with self.assertRaisesRegex(RuntimeError,'content changed'):load(out,self.card)
        self.assertEqual(self.card,before)
