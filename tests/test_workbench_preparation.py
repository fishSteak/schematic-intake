"""Completed auxiliary stages resume only with unchanged inputs and real outputs."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_review.llm import LLMCancelled
from agent_review.llm_tasks import configure
from kernel.util import ROOT, read_json, write_json
from web_workbench.execution import case_preparation_signature, prepare_cases


class PreparationResumeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.board=Path(temporary.name);self.out=self.board/'review_out'
        self.material=self.board/'material_inputs/input.txt'
        self.material.parent.mkdir();self.material.write_text('source material',encoding='utf-8')
        write_json(self.board/'materials.json',{'netlist':'material_inputs/input.txt'})
        self.kg={'components':{'R7':{'value':'47k'}},'nets':{}}
        write_json(self.out/'board_kg.json',self.kg)
        self.scope={'by_rule':{'P11-A01':['R7']}}
        self.events=[];self.marker=self.board/'history/preparation-checkpoints/cases-calc.json'
        configure();self.addCleanup(configure)
        environment=patch.dict(os.environ,{'BOARD_PATHS_SKIP_AGENT':''})
        environment.start();self.addCleanup(environment.stop)
        model=patch('agent_review.llm.profile_fingerprint',return_value='model-policy-a')
        self.model=model.start();self.addCleanup(model.stop)

    def cases(self,board,kg,**kwargs):
        write_json(self.out/'board_paths/path_draft_jobs.json',{'jobs':[
            {'job_id':'path::domain','out_path':'review_out/board_paths/draft/domain.json'}]})
        write_json(self.out/'board_paths/draft/domain.json',{'domain_id':'domain','paths':[]})
        write_json(self.out/'board_paths/sdk_path_draft_result.json',{'results':[
            {'job_id':'path::domain','ok':True,'wrote':True,
             'metadata_path':'review_out/llm_runs/path_draft/domain.run.json'}]})
        write_json(self.out/'llm_runs/path_draft/domain.run.json',{'status':'completed','request_count':2})
        for name in ('index','projection_stats','assist'):
            write_json(self.out/'board_paths'/f'{name}.json',{})
        write_json(self.out/'p11_cases_result.json',{'cases':{}})

    def calc(self,board,kg):
        for folder in ('operating_cases','program_stress'):
            document={'ref':'R7','cases':[],'completeness':'incomplete'}
            write_json(self.out/folder/'P11-A01/R7.json',document)
            if folder=='operating_cases':document['_private_recipe']='not in public object'
            write_json(self.out/folder/'_bundle.json',{'P11-A01':{'R7':document}})
        write_json(self.out/'p11_stress_result.json',{'stress':{}})

    def run_preparation(self,resume):
        prepare_cases(self.board,self.kg,self.scope,resume=resume,
                      emit=lambda kind,data:self.events.append((kind,data)))

    def stages(self):
        first=patch('agent_review.stages.cases_stage.run',side_effect=self.cases)
        second=patch('agent_review.stages.calc_stage.run',side_effect=self.calc)
        a=first.start();b=second.start()
        self.addCleanup(first.stop);self.addCleanup(second.stop)
        return a,b

    def test_resume_skips_only_complete_unchanged_preparation(self):
        cases,calc=self.stages()
        self.run_preparation(False)
        self.assertTrue(self.marker.is_file())
        saved=read_json(self.marker)
        self.assertIn('review_out/llm_runs/path_draft/domain.run.json',saved['outputs'])
        # Later judge artifacts and context do not invalidate finished preparation.
        write_json(self.out/'evidence_store/current.json',{'new':'research'})
        write_json(self.out/'context_store/manifest.json',{'new':'context'})
        self.run_preparation(True)
        self.assertEqual((cases.call_count,calc.call_count),(1,1))
        self.assertEqual(self.events[-1][0],'preparation_reused')
        # A fresh execution intentionally runs the stages even with a valid marker.
        self.run_preparation(False)
        self.assertEqual((cases.call_count,calc.call_count),(2,2))

    def test_changed_or_missing_outputs_rerun_both_stages(self):
        cases,calc=self.stages();self.run_preparation(False)
        draft=self.out/'board_paths/draft/domain.json'
        write_json(draft,{'domain_id':'domain','paths':['changed']})
        self.run_preparation(True)
        self.assertEqual((cases.call_count,calc.call_count),(2,2))
        (self.out/'program_stress/P11-A01/R7.json').unlink()
        self.run_preparation(True)
        self.assertEqual((cases.call_count,calc.call_count),(3,3))
        write_json(self.out/'program_stress/P11-A01/R7.json',{'mismatch':'bundle'})
        self.run_preparation(True)
        self.assertEqual((cases.call_count,calc.call_count),(4,4))

    def test_changed_material_settings_scope_model_or_code_invalidates_signature(self):
        def signature():return case_preparation_signature(self.board,self.kg,self.scope)
        baseline=signature()
        self.material.write_text('new source',encoding='utf-8')
        self.assertNotEqual(signature(),baseline)
        baseline=signature();write_json(self.board/'review_config.json',{'setting':'changed'})
        self.assertNotEqual(signature(),baseline)
        baseline=signature();self.kg['components']['R7']['value']='50k'
        self.assertNotEqual(signature(),baseline)
        baseline=signature();self.scope['by_rule']['P11-A01'].append('R8')
        self.assertNotEqual(signature(),baseline)
        baseline=signature();self.model.return_value='model-policy-b'
        self.assertNotEqual(signature(),baseline)
        baseline=signature();original=Path.read_bytes
        def changed(path):
            value=original(path)
            return value+b'\n# changed implementation\n' if path==ROOT/'agent_review/llm_tools.py' else value
        with patch.object(Path,'read_bytes',changed):
            self.assertNotEqual(signature(),baseline)

    def test_cancelled_or_incomplete_stage_never_produces_reusable_marker(self):
        cases,calc=self.stages();self.run_preparation(False)
        self.material.write_text('changed source',encoding='utf-8')
        calc.side_effect=LLMCancelled('Stop during preparation')
        with self.assertRaises(LLMCancelled):self.run_preparation(True)
        self.assertFalse(self.marker.exists())
        calc.side_effect=self.calc
        self.run_preparation(True)
        self.assertTrue(self.marker.exists())
        def failed_cases(*args,**kwargs):
            self.cases(*args,**kwargs)
            write_json(self.out/'board_paths/sdk_path_draft_result.json',{'results':[
                {'job_id':'path::domain','ok':False,'wrote':False}]})
        cases.side_effect=failed_cases
        self.run_preparation(False)
        self.assertFalse(self.marker.exists())


if __name__=='__main__':unittest.main()
