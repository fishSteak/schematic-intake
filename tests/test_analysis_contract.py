import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_review.analysis_contract import assumptions_for, required_assumptions, validate_links
from agent_review.detail_prose import context, validate, load
from agent_review.detail_view import unified_analysis
from agent_review.validate_job import validate_judge_card
from agent_review.review_contract import validate_review, task_header
from kernel.util import ROOT, read_json


class AnalysisContractTests(unittest.TestCase):
    def card(self):
        return dict(rule_id='CHECK', object='X3', verdict_zh='待确认',
                    assumptions=['外部吸流能力尚未确认。'], summary='原始摘要')

    def record(self, ctx):
        return dict(analysis_contract='analysis.v1', key=ctx['key'], source_hash=ctx['source_hash'],
                    status='ready', analysis_steps=[{'text':'若外部吸流条件成立，沿 X3 所在支路核对节点约束并推导对象端压。',
                    'assumption_refs':['legacy-assumption-0']}], primary_ref=None, route_summary='', note='',
                    citations=[{'source':'card','quote':'外部吸流能力尚未确认。'}])

    def test_merge_keeps_stable_source_and_model_links(self):
        card=self.card();before=copy.deepcopy(card)
        with tempfile.TemporaryDirectory() as d:
            ctx=context(Path(d),card);row=self.record(ctx);row['_meta']={'run_id':'actual-model-run'}
            self.assertEqual(validate(row,ctx),[])
            with patch('agent_review.detail_prose.load',return_value=row):
                view=unified_analysis({'reasoning_steps':['old'],'premises':[]},card,Path(d))
        self.assertEqual(view['analysis_steps'],row['analysis_steps'])
        self.assertEqual(view['key_assumptions'][0]['source'],{'field':'assumptions','index':0})
        self.assertEqual(view['analysis_provenance']['run_id'],'actual-model-run')
        self.assertNotIn('reasoning_steps',view);self.assertNotIn('premises',view)
        self.assertEqual(card,before)

    def test_unknown_and_missing_required_refs_rejected(self):
        a=assumptions_for(self.card())
        self.assertTrue(validate_links([{'text':'核对 X3','assumption_refs':['invented']}],a,required_assumptions(self.card())))
        self.assertTrue(validate_links([{'text':'核对 X3','assumption_refs':[]}],a,required_assumptions(self.card())))

    def test_uncertainty_cannot_be_promoted_to_fact(self):
        a=[{'id':'boundary','text':'外部吸流未知','status':'unresolved'}]
        self.assertTrue(validate_links([{'text':'X3 的外部接口持续吸流。','assumption_refs':['boundary']}],a))
        self.assertEqual(validate_links([{'text':'若接口持续吸流，X3 的应力随下游电位变化；该能力尚未确认。','assumption_refs':['boundary']}],a),[])

    def test_model_assumption_needs_qualifier(self):
        a=[{'id':'m','text':'理想钳位','status':'model_assumption'}]
        self.assertTrue(validate_links([{'text':'X3 下端接地。','assumption_refs':['m']}],a))
        self.assertEqual(validate_links([{'text':'按理想钳位模型推导 X3 端压。','assumption_refs':['m']}],a),[])

    def test_analysis_does_not_move_calculation_results_into_prose(self):
        with tempfile.TemporaryDirectory() as d:ctx=context(Path(d),self.card())
        row=self.record(ctx);row['analysis_steps'][0]['text']='X3 端压算得 12 V。'
        self.assertIn('invalid thinking steps',validate(row,ctx))

    def test_insufficient_source_does_not_fabricate_assumption_coverage(self):
        with tempfile.TemporaryDirectory() as d:ctx=context(Path(d),self.card())
        row=self.record(ctx);row.update(status='source_insufficient',analysis_steps=[],note='已有资料没有完成对象推导，需复审。')
        self.assertEqual(validate(row,ctx),[])

    def test_adjacent_material_rail_is_not_a_calculated_voltage(self):
        card=self.card();card['summary']='U2 未接 +12V 输出'
        with tempfile.TemporaryDirectory() as d:ctx=context(Path(d),card)
        row=self.record(ctx);row['analysis_steps'][0]['text']='若外部条件成立，核对 X3 与 +12V 供电支路的关系。'
        self.assertEqual(validate(row,ctx),[])
        row['analysis_steps'][0]['text']='若外部条件成立，X3 端压计算为 12 V。'
        self.assertIn('invalid thinking steps',validate(row,ctx))

    def test_new_legacy_judge_schema_rejects_missing_evidence_and_duplicate_prose(self):
        card=self.card();card.pop('assumptions');card.update(analysis_contract='analysis.v1',
            analysis_steps=[{'text':'核对 X3 外部条件。','assumption_refs':['a']}],
            key_assumptions=[{'id':'a','text':'外部条件成立','status':'supported','evidence_refs':[]}],reasoning_steps=['duplicate'])
        errors=validate_judge_card(card,rule_id='CHECK',obj='X3')
        self.assertIn('supported key assumption needs evidence',errors)
        self.assertIn('new analysis contract must not duplicate legacy prose fields',errors)

    def test_native_new_analysis_and_historical_cards_both_validate(self):
        card=read_json(ROOT/'docs/samples/review-contract-v1/R4.review.example.json')
        self.assertEqual(validate_review(card),[])
        new=copy.deepcopy(card);old_steps=new.pop('reasoning_steps')
        refs=new['display']['premise_refs']
        new.update(analysis_contract='analysis.v1',analysis_steps=[{'text':'；'.join(old_steps)+'；若外部条件尚未确认，按既有模型限定推导。','premise_refs':refs}])
        self.assertEqual(validate_review(new),[])
        new['analysis_steps'][0]['premise_refs']=['undefined']
        self.assertTrue(validate_review(new))

    def test_current_native_task_requires_new_analysis_contract(self):
        card=read_json(ROOT/'docs/samples/review-contract-v1/R4.review.example.json')
        expected={k:card[k] for k in ('target','criterion','schema_version')}
        expected.update(analysis_contract='analysis.v1',fixed_evidence=[],fixed_inputs=[])
        self.assertIn('analysis_contract required by current workbench',validate_review(card,expected=expected))


if __name__=='__main__':unittest.main()
