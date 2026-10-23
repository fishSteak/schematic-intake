import hashlib
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from kernel.util import write_json,read_json
from kernel.util import ROOT
from agent_review.applicability import exclusions
from agent_review.execution_policy import checkpoint,execution_hash,two_turn
from agent_review.enumerate import enumerate_board
from agent_review.review_contract import business_node_alias_errors

class OptimizationTests(unittest.TestCase):
    def test_business_nodes_use_material_names_but_internal_ids_and_units_are_allowed(self):
        data={'decision':{'explanation':'释放接口吸流会抬高X，放宽Y处需求仍较重。'}}
        self.assertTrue(business_node_alias_errors(data,{'N_SUPPLY','N_LOAD'}))
        self.assertFalse(business_node_alias_errors(data,{'X','Y'}))
        self.assertFalse(business_node_alias_errors({'label':'N_LOAD节点电位','explanation':'降低P，I²R功耗与2 A电流核算。','calculations':[{'id':'X'}]},{'N_LOAD'}))

    def test_native_prompt_changes_do_not_invalidate_unrelated_legacy_checks(self):
        legacy=execution_hash('P11-A05',{},'same')
        native_wb={'contract_task':{'schema_version':'review.v1'}}
        native=execution_hash('P11-A01',native_wb,'same')
        original=Path.read_bytes;target=ROOT/'agent_review/prompts/judge/review_v1.common.md'
        def changed(p):
            return original(p)+(b'changed native contract' if p==target else b'')
        with patch.object(Path,'read_bytes',changed):
            self.assertEqual(legacy,execution_hash('P11-A05',{},'same'))
            self.assertNotEqual(native,execution_hash('P11-A01',native_wb,'same'))

    @unittest.skipUnless(os.name=='nt','Windows long path regression')
    def test_completed_stage_can_be_read_beyond_windows_path_limit(self):
        with tempfile.TemporaryDirectory() as td:
            board=Path(td)/('a'*70)/('b'*70)
            root=board/'history/execution-checkpoints/P11-A05/C1'/('c'*64)
            extended=Path('\\\\?\\'+str(root))
            p=extended/'review_out/agent_jobs/P11-A05/C1.analysis.md'
            p.parent.mkdir(parents=True);p.write_text('complete',encoding='utf8')
            write_json(extended/'stage.json',{'stage':'self_check','execution_hash':'c'*64,'analysis_hash':hashlib.sha256(p.read_bytes()).hexdigest()})
            try:
                self.assertEqual(checkpoint(board,'P11-A05','C1','c'*64)['stage'],'self_check')
            finally:
                assert str(root).startswith(str(Path(td))+os.sep)
                shutil.rmtree(extended)

    def test_every_enabled_check_has_report_metadata(self):
        enabled=set(read_json(ROOT/'rules/product_center_15.json')['rule_ids'])
        displayed={r['rule_id'] for r in read_json(ROOT/'rules/xlsx_detail_15.json')['items']}
        self.assertEqual(enabled,displayed)

    def test_missing_or_conflicting_material_does_not_skip(self):
        kg={'components':{'C1':{'type':'capacitor','value':'TBD','footprint':'C0805'},
                          'C2':{'type':'capacitor','value':'X7R tantalum'},
                          'C3':{'type':'capacitor','value':'1uF X7R'},
                          'C4':{'type':'capacitor','description':'铝电解'}}}
        self.assertEqual(set(exclusions(kg,'P11-A04',list(kg['components']))),{'C3','C4'})
        e=enumerate_board(kg,rule_ids=['P11-A04'])
        self.assertEqual(set(e['by_rule']['P11-A04']),{'C1','C2'})
        self.assertEqual(set(e['applicability_exclusions']['P11-A04']),{'C3','C4'})

    def test_no_low_voltage_or_internal_interface_guessing(self):
        kg={'components':{'R1':{'value':'10k','pins':[{'net':'+24V'}]},'CN1':{'external_interface':False}}}
        self.assertFalse(exclusions(kg,'P11-A02',['R1']))
        self.assertFalse(exclusions(kg,'P12-A08',['CN1']))
        kg['components']['CN1']['external_interface_source']='signed interface specification'
        self.assertIn('CN1',exclusions(kg,'P12-A08',['CN1']))

    def test_signatures_change_with_material_workbench_or_prompt(self):
        a=execution_hash('P11-A05',{'fact':1},'materials1')
        self.assertNotEqual(a,execution_hash('P11-A05',{'fact':2},'materials1'))
        self.assertNotEqual(a,execution_hash('P11-A05',{'fact':1},'materials2'))
        self.assertTrue(two_turn('P11-A05'));self.assertFalse(two_turn('P11-A01'))
        self.assertFalse(two_turn('P11-A04'))

    def test_checkpoint_rejects_tampering_and_incomplete_stage(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'history/execution-checkpoints/P11-A05/C1/hash'
            p=root/'review_out/agent_jobs/P11-A05/C1.analysis.md';p.parent.mkdir(parents=True)
            p.write_text('completed analysis',encoding='utf8')
            meta={'stage':'analysis','execution_hash':'hash','analysis_hash':hashlib.sha256(p.read_bytes()).hexdigest()}
            write_json(root/'stage.json',meta)
            self.assertEqual(checkpoint(Path(td),'P11-A05','C1','hash')['stage'],'analysis')
            p.write_text('incomplete overwrite',encoding='utf8')
            self.assertIsNone(checkpoint(Path(td),'P11-A05','C1','hash'))
            self.assertIsNone(checkpoint(Path(td),'P11-A05','C1','other'))
