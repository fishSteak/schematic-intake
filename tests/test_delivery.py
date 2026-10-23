import unittest,tempfile
from pathlib import Path
from agent_review.delivery import validate
from agent_review.enumerate import apply_rule_precondition_gates

class DeliveryTests(unittest.TestCase):
    def test_missing_requirements_do_not_remove_real_objects(self):
        by={'P12-A03':['U9'],'P12-A07':['CN9']}
        reasons,gates=apply_rule_precondition_gates(by,{'design_task':{}})
        self.assertEqual(by,{'P12-A03':['U9'],'P12-A07':['CN9']})
        self.assertNotIn('P12-A03',reasons);self.assertNotIn('P12-A07',reasons)
    def test_numeric_missing_diagram_is_not_deliverable(self):
        wb={'delivery_policy':{'requires_state_if_applicable':True}}
        self.assertTrue(validate({'verdict_zh':'待确认','states':[]},wb))
        self.assertEqual(validate({'verdict_zh':'不适用','states':[]},wb),[])
    def test_bound_has_one_explicit_relationship_diagram(self):
        wb={'delivery_policy':{'requires_state_if_applicable':True}}
        state={'role':'primary','P':.01,'stress_kind':'upper_bound','route_summary':'供能与耗散关系','figure':{'kind':'cross_case_relations','pages':[{'pdf_page':1}]}}
        self.assertEqual(validate({'verdict_zh':'是','states':[state],'decision_basis':{'state_index':0}},wb),[])
        state['figure']['kind']='operating_path'
        self.assertTrue(validate({'verdict_zh':'是','states':[state]},wb))

if __name__=='__main__':unittest.main()
