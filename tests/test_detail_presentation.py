import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent_review.detail_view import build, explicit_primary, value_text, QUANTITY
from agent_review.detail_prose import context, validate
import re


class DetailPresentationTests(unittest.TestCase):
    def card(self, **extra):
        return dict(rule_id='P-test', object='C2', verdict_zh='待确认', electrical={}, **extra)

    def test_no_object_audit_requires_cited_reasoning_not_literal_placeholder(self):
        c=self.card(summary='No inductors installed');c['object']='(none)'
        with tempfile.TemporaryDirectory() as d:ctx=context(Path(d),c)
        row=dict(key=ctx['key'],source_hash=ctx['source_hash'],status='ready',reasoning_steps=['Verify the installed component classes against the inductor scope.'],primary_ref=None,route_summary='',note='',citations=[{'source':'card','quote':'No inductors installed'}])
        self.assertEqual(validate(row,ctx,legacy=True),[])
        ctx['card']['object']='R99'
        self.assertIn('missing specific object thinking',validate(row,ctx,legacy=True))

    def test_no_numeric_primary_guess(self):
        c=self.card(states=[{'name':'A','P':1},{'name':'B','P':2}])
        self.assertIsNone(explicit_primary(c))
        with tempfile.TemporaryDirectory() as d:
            v=build(c,Path(d))
        self.assertIsNone(v['primary'])
        self.assertTrue(v['notes'])

    def test_qualitative_no_power_boxes_or_formula_premises(self):
        c=self.card(formula='a formula with numbers', solution={'gates':[{'name':'精度','detail':'缺少标注'}]})
        original=copy.deepcopy(c)
        with tempfile.TemporaryDirectory() as d:v=build(c,Path(d))
        self.assertEqual(v['assessment_rows'],[])
        self.assertEqual(v['key_assumptions'],[])
        self.assertEqual(v['analysis_steps'],[])
        self.assertEqual(c,original)

    def test_na_hides_scenarios(self):
        c=self.card(states=[{'name':'A','P':1}]);c['verdict_zh']='不适用'
        with tempfile.TemporaryDirectory() as d:v=build(c,Path(d))
        self.assertIsNone(v['primary']);self.assertEqual(v['comparisons'],[])

    def test_unknown_not_zero_and_results_separate(self):
        c=self.card();c['electrical']={'V_rated':50,'V_max':None,'V_limit':25}
        with tempfile.TemporaryDirectory() as d:v=build(c,Path(d))
        self.assertEqual(len(v['materials']),1)
        self.assertIn('尚未确定',[r['text'] for r in v['assessment_rows']])

    def test_power_display_preserves_bound_precision_zero_and_tiny_values(self):
        for value, expected in ((0.012543, '12.543 mW'), (0.012547, '12.547 mW'),
                                (0, '0 mW'), (-0.0, '0 mW'), (0.05, '50 mW'),
                                (1e-10, '0.0000001 mW'), (1.23456789e-8, '0.0000123456789 mW')):
            with self.subTest(value=value):
                self.assertEqual(value_text(value, 'W'), expected)

    def test_power_precision_keeps_other_units_and_unknown_values(self):
        for value, unit, expected in ((12.3456789, 'V', '12.3457 V'), (47000, 'Ω', '47000 Ω'),
                                      (2.5, '', '2.5'), (None, 'W', '尚未确定'),
                                      (True, 'W', '是 W'), ('原始值', 'W', '原始值 W')):
            with self.subTest(value=value, unit=unit):
                self.assertEqual(value_text(value, unit), expected)

    def test_bound_display_does_not_rewrite_machine_card(self):
        c=self.card(states=[{'name':'正式上界','role':'primary','P':0.012543},
                            {'name':'独立下界','role':'comparison','P':0.012547}],
                    decision_basis={'kind':'envelope_upper_bound','value_w':0.012543})
        c['electrical']={'P_limit':0.05,'P_max':None}
        before=copy.deepcopy(c)
        with tempfile.TemporaryDirectory() as d:v=build(c,Path(d))
        upper=next(row for row in v['assessment_rows'] if row['key']=='P_upper_bound')
        self.assertEqual(upper['text'], '12.543 mW')
        self.assertEqual(v['primary']['value_text'], '12.543 mW')
        self.assertEqual(v['comparisons'][0]['value_text'], '12.547 mW')
        self.assertIn('精确最大功耗尚未确定', v['assessment_text'])
        # The existing adapter may attach presentation-only cause metadata;
        # every original machine field and number must remain untouched.
        self.assertEqual({key:c[key] for key in before}, before)

    def test_prose_must_cite_supplied_source(self):
        c=self.card()
        with tempfile.TemporaryDirectory() as d:ctx=context(Path(d),c)
        row=dict(key=ctx['key'],source_hash=ctx['source_hash'],status='ready',reasoning_steps=['核对 C2 的标注及采用依据。'],primary_ref=None,route_summary='',note='',citations=[{'source':'card','quote':'invented'}])
        self.assertIn('citation not in supplied source',validate(row,ctx,legacy=True))

    def test_quantity_excludes_part_number_but_detects_values(self):
        self.assertIsNone(re.search(QUANTITY,'核对 RES-ADJ-TH_3296W 标注'))
        self.assertIsNotNone(re.search(QUANTITY,'阻值 100kΩ'))

    def test_publication_failure_restores_previous_report(self):
        from agent_review.expert_report import write_expert_deliverables
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);(out/'evidence').mkdir()
            (out/'expert_review.html').write_text('published',encoding='utf-8')
            (out/'evidence/a.json').write_text('original',encoding='utf-8')
            def fail(*args,**kwargs):
                (out/'expert_review.html').write_text('partial',encoding='utf-8')
                (out/'evidence/a.json').write_text('partial',encoding='utf-8')
                raise RuntimeError('failed build')
            with patch('agent_review.detail_view.enabled',return_value=False),patch('agent_review.expert_report._write_expert_deliverables',side_effect=fail):
                with self.assertRaises(RuntimeError):write_expert_deliverables(out,out,{})
            self.assertEqual((out/'expert_review.html').read_text(encoding='utf-8'),'published')
            self.assertEqual((out/'evidence/a.json').read_text(encoding='utf-8'),'original')

    def test_every_view_has_shared_module_registry(self):
        from agent_review.detail_view import MODULES,TITLES
        with tempfile.TemporaryDirectory() as d:v=build(self.card(),Path(d))
        self.assertEqual(v['module_order'],MODULES)
        self.assertEqual(v['module_titles'],TITLES)

    def test_net_identifier_is_not_a_new_calculation_value(self):
        c=self.card();c['object']='+12V'
        with tempfile.TemporaryDirectory() as d:ctx=context(Path(d),c)
        row=dict(key=ctx['key'],source_hash=ctx['source_hash'],status='ready',reasoning_steps=['逐字符核查网名 +12V 是否属于原卡允许的字符范围。'],primary_ref=None,route_summary='',note='',citations=[{'source':'card','quote':'+12V'}])
        self.assertEqual(validate(row,ctx,legacy=True),[])
        row['reasoning_steps']=['逐字符核查网名 +12V，并采用 24 V 计算。']
        self.assertIn('invalid thinking steps',validate(row,ctx,legacy=True))

    def test_insufficient_source_does_not_require_invented_route(self):
        c=self.card(states=[{'name':'Recorded case','role':'primary'}])
        with tempfile.TemporaryDirectory() as d:ctx=context(Path(d),c)
        row=dict(key=ctx['key'],source_hash=ctx['source_hash'],status='source_insufficient',reasoning_steps=[],primary_ref=None,route_summary='',note='原记录没有通路证据，需复审。',citations=[])
        self.assertEqual(validate(row,ctx,legacy=True),[])

    def test_legacy_per_pin_steps_are_checks_not_operating_scenarios(self):
        c=self.card(solution={'states':[{'name':'电源脚','steps':['核查同网电容标注与目标容量。'],'verdict_zh':'否'}]})
        with tempfile.TemporaryDirectory() as d:v=build(c,Path(d))
        self.assertIsNone(v['primary'])
        self.assertEqual(v['comparisons'],[])
        self.assertEqual(v['assessment_checks'][0]['name'],'电源脚')
        self.assertEqual(v['assessment_checks'][0]['detail'],'核查同网电容标注与目标容量。')


if __name__=='__main__':unittest.main()
