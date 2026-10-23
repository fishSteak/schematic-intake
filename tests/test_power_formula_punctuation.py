import copy
import unittest

from agent_review.validate_job import _normalize_power_formula_text, _power_bound_formulas, validate_judge_card
from tests.test_power_bound_validation import example


class PowerFormulaPunctuationTests(unittest.TestCase):
    def test_only_explicit_fullwidth_operators_and_terminal_stop_are_normalized(self):
        source = "P≤(2 V)²／200 Ω＝0.02 W。  "
        self.assertEqual(_normalize_power_formula_text(source), "P≤(2 V)²/200 Ω=0.02 W")
        self.assertEqual(_normalize_power_formula_text("P＝2²。中间说明；温度25℃。"), "P=2²。中间说明；温度25℃")
        self.assertEqual(_normalize_power_formula_text("Ｐ＝２²。"), "Ｐ=２²")

    def test_valid_dimensioned_formula_and_conversion_accept_fullwidth_equals(self):
        lines = ["器件耗散≤(24 V)²/45925.11 Ω＝0.012542158309473837 W＝12.542158309473837 mW。"]
        self.assertTrue(_power_bound_formulas(lines, 24**2 / 45925.11, "upper_bound"))
        self.assertTrue(_power_bound_formulas(["P≤（1 V＋1 V）＾2／200 Ω＝0.02 W。"], .02, "upper_bound"))
        self.assertTrue(_power_bound_formulas(["P≥(2 V)²/200 Ω＝0.02 W≥0.019 W。"], .019, "lower_bound"))

    def test_card_validation_does_not_modify_original_formula_or_sources(self):
        card = example()
        for state in card['states']:
            for row in state['formula_lines']:
                row['expr'] = row['expr'].replace('=', '＝') + '。'
                row['src'] = '来源原文＝保留。'
        original = copy.deepcopy(card)
        self.assertEqual(validate_judge_card(card, rule_id='P11-A01', obj='RX'), [])
        self.assertEqual(card, original)

    def test_numeric_unit_and_direction_errors_still_fail(self):
        for formula in ("P≤(2 V)²/200 Ω＝0.03 W。", "P≤(2 A)²/200 Ω＝0.02 W。",
                        "P≤(2 V)²/200 Ω＝20 W。", "P≤(2 V)²/200 Ω＝0.02 W≥0.019 W。",
                        "P≤(2 V)²/200 Ω＝0.02 W＜20 mW。", "P≤1／0≤(2 V)²/200 Ω＝0.02 W。"):
            with self.subTest(formula=formula):
                self.assertFalse(_power_bound_formulas([formula], .02, 'upper_bound'))

    def test_midline_sentences_and_conflicting_proofs_are_not_removed(self):
        for formula in ("P≤(2 V)²/200 Ω＝0.02 W。另一个值＝0.03 W。",
                        "P≤(2 V)²/200 Ω＝0.03 W；P≤(2 V)²/200 Ω＝0.02 W。"):
            self.assertFalse(_power_bound_formulas([formula], .02, 'upper_bound'))

    def test_constant_operating_points_inside_the_bound_are_annotations(self):
        cases = [('upper_bound', 'P≤(2V)²/200Ω＝0.02W。', ('P＝0 W。', 'P=0.01W=10mW', '端压为0V时器件耗散＝0 W。')),
                 ('lower_bound', 'P≥(2V)²/200Ω＝0.02W。', ('P=0.02W', 'P=0.03W=30mW'))]
        for kind, proof, notes in cases:
            for note in notes:
                with self.subTest(kind=kind, note=note):
                    self.assertTrue(_power_bound_formulas([proof, note], .02, kind))
                    self.assertFalse(_power_bound_formulas([note], .02, kind))

    def test_outside_points_and_wrong_unit_conversions_still_fail(self):
        cases = [('upper_bound', 'P≤(2V)²/200Ω=0.02W', ('P=0.03W', 'P=0.01W=10W')),
                 ('lower_bound', 'P≥(2V)²/200Ω=0.02W', ('P=0W', 'P=0.03W=30uW'))]
        for kind, proof, notes in cases:
            for note in notes:
                with self.subTest(kind=kind, note=note):
                    self.assertFalse(_power_bound_formulas([proof, note], .02, kind))

    def test_explicit_bound_and_numeric_derivations_are_not_reclassified(self):
        proof = 'P≤(2V)²/200Ω=0.02W'
        for note in ('P≤0 W', 'P_upper=0 W', 'P=0W<0.02W', 'P=1²/100=0.01W',
                     'P=1²/100=0.02W', 'P≈0W'):
            with self.subTest(note=note):
                self.assertFalse(_power_bound_formulas([proof, note], .02, 'upper_bound'))
        self.assertFalse(_power_bound_formulas(['P≤2²/200=0.03W', 'P=0W'], .02, 'upper_bound'))

    def test_semicolon_clauses_are_all_checked_without_removing_middle_content(self):
        proof = 'P≤(2V)²/200Ω=0.02W'
        self.assertTrue(_power_bound_formulas([proof, '0.02W＝20mW；器件限额＝0.100W×50%＝0.050W。'], .02, 'upper_bound'))
        for compound in ('0.02W＝20W；器件限额＝0.05W。',
                         '0.02W＝20mW；P≤(2V)²/200Ω＝0.03W。',
                         'P＝0W；P≤2²/200＝0.03W。',
                         'P≤2²/200＝0.03W;P≤2²/200＝0.02W。'):
            with self.subTest(compound=compound):
                self.assertFalse(_power_bound_formulas([proof, compound], .02, 'upper_bound'))
        text = '0.02W＝20mW；器件限额＝0.05W。'
        self.assertEqual(_normalize_power_formula_text(text), '0.02W=20mW；器件限额=0.05W')

    def test_clause_splitting_preserves_original_formula_length_limit(self):
        proof = 'P≤(2V)²/200Ω=0.02W'
        self.assertFalse(_power_bound_formulas(['P=0W;' * 400 + proof], .02, 'upper_bound'))

    def test_auxiliary_rated_limit_and_margin_math_is_verified_without_becoming_proof(self):
        proof = 'P≤(2V)²/200Ω=0.02W'
        correct = ('P_limit=0.100W×50%=0.050W=50mW', 'P_rated=.1W=100mW',
                   '功率限额=.1W*50%=.05W', '额定功率=0.1W',
                   '本器件限额＝0.100W×50%＝0.050W。', '余量=.05W-.02W=.03W=30mW',
                   'P_limit=.1W*.5e2%=.05W')
        for auxiliary in correct:
            with self.subTest(auxiliary=auxiliary):
                self.assertTrue(_power_bound_formulas([proof, auxiliary], .02, 'upper_bound'))
                self.assertFalse(_power_bound_formulas([auxiliary], .02, 'upper_bound'))
        self.assertFalse(_power_bound_formulas([proof, correct[0]], .05, 'upper_bound'))

    def test_wrong_auxiliary_arithmetic_units_and_percentages_fail(self):
        proof = 'P≤(2V)²/200Ω=0.02W'
        wrong = ('器件限额=.100W×50%=.060W', 'P_limit=.1W*50%=.05mW',
                 '功率限额=.1A*50%=.05W', 'P_rated=.1W=10mW',
                 '额定功率=.1W=100W', '余量=.05W-.02W=.3W',
                 'P_limit=.1W*50%%=.05W', 'P_limit=.1W*50%foo=.05W',
                 'P_limit=.1W*(50+0)%=.05W', 'P_limit=P_rated*50%=.05W',
                 'P_limit=.1W/0=.05W', 'P_limit=.1W*50%=.05W*2=.1W',
                 'P_limit=(2V)/(4V)=.5W')
        for auxiliary in wrong:
            with self.subTest(auxiliary=auxiliary):
                self.assertFalse(_power_bound_formulas([proof, '0.02W=20mW；' + auxiliary], .02, 'upper_bound'))
        self.assertFalse(_power_bound_formulas([proof, 'P_limit=.1W*50%=.05W；P_limit=.1W*50%=.06W'], .02, 'upper_bound'))

    def test_auxiliary_labels_cannot_swallow_another_power_declaration(self):
        proof = 'P≤(2V)²/200Ω=0.02W'
        wrong = ('P≤2²/200=.03W，P_limit=.05W', 'P=.5W，P_limit=.05W',
                 '.02W=200mW，P_limit=.05W', '限额=.1W*50%=.06W，P≤2²/200=.02W',
                 'P_limit=.1W*50%=.05W，P≤2²/200=.03W')
        for auxiliary in wrong:
            with self.subTest(auxiliary=auxiliary):
                self.assertFalse(_power_bound_formulas([proof, auxiliary], .02, 'upper_bound'))

    def test_only_explicit_power_margin_can_be_negative(self):
        proof = 'P≤(2V)²/200Ω=0.02W'
        self.assertTrue(_power_bound_formulas([proof, '余量=.05W-.06W=-.01W=-10mW'], .02, 'upper_bound'))
        for auxiliary in ('余量=.05W-.06W=-.02W', '余量=.05A-.06A=-.01W',
                          'P_limit=-.05W', 'P_rated=-.1W=-100mW', '功率限额=.05W-.06W=-.01W',
                          'P=-.01W', '-.01W=-10mW'):
            with self.subTest(auxiliary=auxiliary):
                self.assertFalse(_power_bound_formulas([proof, auxiliary], .02, 'upper_bound'))
        self.assertFalse(_power_bound_formulas([proof], -.02, 'upper_bound'))

    def test_diagnostics_report_static_cause_and_location_without_formula_text(self):
        proof = 'P≤(2V)²/200Ω=0.02W'
        reasons = []
        self.assertFalse(_power_bound_formulas([proof, '0.02W=20mW；机密型号器件限额=.1W*50%=.06W'],
                                               .02, 'upper_bound', reasons=reasons))
        self.assertEqual(reasons, ['第2行第2段：额定、限额或余量的算术或单位换算不一致'])
        self.assertNotIn('机密', str(reasons))
        reasons.clear()
        self.assertFalse(_power_bound_formulas(['P=0W'], .02, 'upper_bound', reasons=reasons))
        self.assertEqual(reasons, ['缺少独立、完整的功耗数值推导'])
        card = example()
        card['states'][0]['formula_lines'].append({'expr': 'P_limit=.1W*50%=.06W', 'src': 'source'})
        errors = validate_judge_card(card, rule_id='P11-A01', obj='RX')
        self.assertTrue(any('第2行第1段：额定、限额或余量' in error for error in errors), errors)


if __name__ == '__main__':
    unittest.main()
