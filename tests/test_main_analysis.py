import copy
import tempfile
import unittest
from pathlib import Path
from agent_review.main_analysis import validate, drawing_states, selected_index
from agent_review.detail_view import build, explicit_primary, basis_source_hash
from agent_review.decision_contract import validate_decision_audit


def example():
    return {'rule_id':'P11-A01','object':'X1','verdict_zh':'是','electrical':{'P_limit':1,'P_max':None},
        'analysis_contract':'analysis.v1','analysis_steps':[{'text':'X1 所在支路按材料边界建立储能平衡。','assumption_refs':[]}],
        'key_assumptions':[], 'evidence_refs':[{'path':'materials.pdf'}],
        'states':[{'case_id':'candidate','name_zh':'条件候选','role':'worst_candidate','P':.5},
                  {'case_id':'envelope','name_zh':'保守界推导','stress_kind':'upper_bound','P':.1,
                   'formula_lines':[{'expr':'耗散上界 = 0.1 W','src':'materials.pdf 的边界及已完成的推导'}]}],
        'decision_basis':{'kind':'envelope_upper_bound','state_index':1,'value_w':.1,'limit_w':1},
        'main_analysis':{'version':'basis-main.v1','basis_ref':'/decision_basis','kind':'cross_case_bound',
            'state_index':1,'title':'供能与耗散上界','route_summary':'X1 的功耗由源边界与储能平衡限制，不指定瞬时状态。',
            'proof_status':'verified','assumption_refs':[],
            'scope':{'included':'材料确认的长期范围','excluded':'范围外的外部驱动'},
            'proof_checks':[{'claim':'源与储能边界成立','status':'supported','reason':'由原材料和推导核查','evidence_refs':['materials.pdf']}],
            'figure':{'kind':'cross_case_relations','pages':[{'pdf_page':1,'label':'供能关系','route_summary':'源边界与耗散支路的关系。',
                'paths':[['SUPPLY','X1','RETURN']], 'markers':[{'on':'X1','title':'耗散上界','quantity_ref':'/decision_basis/value_w'}]}]}}}


class MainAnalysisTests(unittest.TestCase):
    def test_primary_references_basis_not_bigger_candidate(self):
        c=example();self.assertEqual(validate(c),[])
        self.assertEqual(selected_index(c),1);self.assertEqual(explicit_primary(c),'envelope')
        with tempfile.TemporaryDirectory() as d:view=build(c,Path(d))
        self.assertEqual(view['primary']['kind'],'cross_case_bound')
        self.assertEqual(view['primary']['value'],c['decision_basis']['value_w'])
        self.assertEqual(view['primary']['calculations'][0]['expression'],c['states'][1]['formula_lines'][0]['expr'])
        self.assertEqual(view['primary']['route_summary'],c['main_analysis']['route_summary'])

    def test_invalid_reference_has_no_silent_rank_fallback(self):
        c=example();c['main_analysis']['state_index']=0
        self.assertTrue(validate(c));self.assertIsNone(selected_index(c));self.assertIsNone(explicit_primary(c))
        with tempfile.TemporaryDirectory() as d:view=build(c,Path(d))
        self.assertIsNone(view['primary']);self.assertTrue(view['notes'])

    def test_bound_cannot_be_rendered_as_reachable_case(self):
        c=example();c['main_analysis']['kind']='operating_case'
        self.assertTrue(validate(c))
        c=example();c['main_analysis']['figure']['kind']='operating_path'
        self.assertTrue(validate(c))

    def test_incomplete_proof_cannot_pass(self):
        c=example();c['main_analysis']['proof_checks'][0]['status']='unresolved'
        self.assertTrue(validate(c))
        c=example();c['main_analysis']['proof_status']='incomplete'
        self.assertTrue(validate(c))

    def test_main_assumptions_and_evidence_are_checked(self):
        c=example();c['main_analysis']['assumption_refs']=['unknown']
        self.assertTrue(validate(c))
        c=example();c['main_analysis']['proof_checks'][0]['evidence_refs']=['other.pdf']
        self.assertTrue(validate(c))

    def test_state_and_basis_numbers_cannot_drift(self):
        c=example();c['states'][1]['P']=.2
        self.assertTrue(validate(c))

    def test_relation_projection_has_no_claimed_direction_and_uses_formal_quantity(self):
        c=example();before=copy.deepcopy(c);states=drawing_states(c);page=states[1]['figure']['pages'][0]
        self.assertEqual(page['unknown_direction_paths'],page['paths'])
        self.assertIn('100.000 mW',page['markers'][0]['title'])
        self.assertEqual(c,before)
        self.assertEqual(states[1]['route_summary'],c['main_analysis']['route_summary'])

    def test_candidate_picture_cannot_be_a_second_owner(self):
        c=example();c['states'][0]['figure']={'pages':[]}
        self.assertTrue(validate(c))

    def test_figure_provenance_tracks_basis_not_unused_candidate(self):
        c=example();sig=basis_source_hash(c);c['states'][0]['P']=.9
        self.assertEqual(sig,basis_source_hash(c))
        c['main_analysis']['scope']['included']='changed scope'
        self.assertNotEqual(sig,basis_source_hash(c))

    def test_an_explicit_unproved_obligation_can_remain_pending_without_fake_counterexample(self):
        c=example();c['verdict_zh']='待确认';c['decision_basis']['kind']='insufficient'
        c['main_analysis'].update(kind='incomplete_proof',proof_status='incomplete')
        c['main_analysis']['proof_checks'][0]['status']='unresolved'
        c['decision_audit']={'version':2,'normal_design_search':'已建立方程但源边界尚未证明',
            'state_reviews':[{'state_index':i,'admissibility':'conditional','reason':'范围尚未闭合'} for i in range(2)],
            'gaps':[{'issue':'必要边界未证明','effect':'can_change_verdict','reason':'无法建立要求的上界',
                     'proof_gap':{'failed_claim':'源范围','reason':'材料与推导尚未支持','evidence':['materials.pdf'],'decision_effect':'无法证明全部不超限'}}]}
        self.assertEqual(validate(c),[]);self.assertEqual(validate_decision_audit(c),[])


if __name__=='__main__':unittest.main()
