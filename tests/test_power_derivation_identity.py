"""A numeric conversion is not evidence of an electrical power bound."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from agent_review.validate_job import (
    _checked_power_formula, _power_bound_formulas, validate_judge_card,
)
from tests.test_llm_legacy_schema import add_main_analysis, power_card


class PowerDerivationIdentityTests(unittest.TestCase):
    proof = "P≤(2V)²/(200Ω)=0.020W"

    def test_rescaling_and_identity_arithmetic_are_only_annotations(self):
        expressions = [
            "20*0.001", "20/1000", "0.020*1", "20*10**(-3)",
            "(20/1000)+0", "20mW*1", "(20mW)^1", "20mW*(1V/1V)",
            "20mW/(1A)*1A", ".020+(0*2²/200)",
        ]
        for expression in expressions:
            for relation, kind in (("≤", "upper_bound"), ("≥", "lower_bound")):
                formula = "P" + relation + expression + "=0.020W"
                with self.subTest(expression=expression, kind=kind):
                    self.assertEqual(_checked_power_formula(formula, .02, kind), 0)
                    self.assertFalse(_power_bound_formulas([formula], .02, kind))

    def test_rough_power_baseline_cannot_be_promoted_by_a_conversion(self):
        card = power_card()
        card["states"][0]["formula_lines"] = [
            {"expr": "P ≤ 20 × 0.001 = 0.020 W",
             "src": "已确认的类别粗估为20mW，按1mW=0.001W换算；不是完整电路物理上界。"},
            {"expr": "P_limit=0.2W*50%=0.1W", "src": "厂家额定与评审比例。"},
        ]
        before = copy.deepcopy(card)
        errors = validate_judge_card(card, rule_id="P11-A01", obj="R7")
        self.assertTrue(any("states[0]" in e and "独立" in e for e in errors), errors)
        self.assertEqual(card, before)

    def test_neutral_squared_factors_cannot_lend_a_conversion_proof_identity(self):
        expressions = [
            "20*.001*(1²)", "20*.001*(1²/1)", "20*.001*(2²/4)",
            "(1²)*20*.001", "(1²/1)*(20/1000)", "(2²/4)*20*.001",
            "(2²/4)/(1000/20)", "20*.001*(1²)*1",
        ]
        for expression in expressions:
            formula = "P≤" + expression + "=.020W"
            with self.subTest(expression=expression):
                self.assertEqual(_checked_power_formula(formula, .02, "upper_bound"), 0)
                self.assertFalse(_power_bound_formulas([formula], .02, "upper_bound"))
                self.assertTrue(_power_bound_formulas([self.proof, formula], .02, "upper_bound"))

    def test_direct_one_volt_square_and_one_watt_output_conversions_are_preserved(self):
        for expression, power in (("1²/20=.05W", .05), ("2²/4=1W", 1),
                                  ("(2²/4)*1000=1000mW", 1), ("1000*(2²/4)=1000mW", 1),
                                  ("(2²/4)/.001=1000mW", 1), ("(1A)²*200Ω=200W", 200)):
            with self.subTest(expression=expression):
                self.assertTrue(_power_bound_formulas(["P≤" + expression], power, "upper_bound"))

    def test_electrical_dimension_combinations_remain_derivations(self):
        expressions = [
            "P≤(2V)*(0.01A)=.020W", "P≤(2V)^2/(200Ω)=.020W",
            "P≤(0.01A)^2*(200Ω)=.020W", "P≤(1V+1V)*10mA=20mW",
            "P≤(24mV)²/(1kΩ)=5.76e-7W",
        ]
        for formula in expressions:
            power = 5.76e-7 if "24mV" in formula else .02
            with self.subTest(formula=formula):
                self.assertTrue(_power_bound_formulas([formula], power, "upper_bound"))

    def test_legacy_resistor_equations_and_output_scales_are_preserved(self):
        cases = [
            ("P≤2²/200=.020W", .02), ("P≤.01²*200=.020W", .02),
            ("P≤(10²/50000)*1000=2mW", .002),
            ("P≤(10²/50000)*10⁶=2000µW", .002),
            ("P≤(10²*1000)/50000=2mW", .002),
            ("P≤2²/200≤20*.001=.020W", .02),
            ("P≤(2V)²/(200Ω)≤20*.001=.020W", .02),
        ]
        for formula, power in cases:
            with self.subTest(formula=formula):
                self.assertTrue(_power_bound_formulas([formula], power, "upper_bound"))

    def test_old_unit_only_example_needs_a_separate_derivation(self):
        conversion = "耗散功率≦(2×10⁻³)×10⁶=2000µW"
        self.assertEqual(_checked_power_formula(conversion, .002, "upper_bound"), 0)
        self.assertFalse(_power_bound_formulas([conversion], .002, "upper_bound"))
        self.assertTrue(_power_bound_formulas(["P≤10²/50000=.002W", conversion], .002, "upper_bound"))

    def test_correct_scaling_can_accompany_but_not_replace_the_proof(self):
        for extra in ("P≤20/1000=.020W", "P_upper=20mW*1=.020W",
                      "P≤.020*1=.020W=20mW<.050W"):
            with self.subTest(extra=extra):
                self.assertTrue(_power_bound_formulas([self.proof, extra], .02, "upper_bound"))
                self.assertFalse(_power_bound_formulas([extra, "P_limit=.1W*50%=.05W"], .02, "upper_bound"))

    def test_invalid_scaling_is_not_discarded_because_another_proof_exists(self):
        invalid = ["P≤20*.001=.030W", "P≤20/1000=.020mW", "P≤20mW*1=.020A",
                   "P≤20mW*(1V/1A)=.020W", "P≤20/0=.020W",
                   "P≤20*.001=.020W=20W", "P≤20*.001=.020W≤.019W",
                   "P≤20*.001=.020W；P_limit=.1W*50%=.06W"]
        for bad in invalid:
            with self.subTest(bad=bad):
                self.assertFalse(_power_bound_formulas([self.proof, bad], .02, "upper_bound"))
        self.assertFalse(_power_bound_formulas([self.proof, "P≤20*.001=.020W"], .05, "upper_bound"))

    def test_conservative_rounding_still_binds_the_actual_bound(self):
        self.assertTrue(_power_bound_formulas(
            ["P≤(24V)²/45925.11Ω=.012542158309473837W≤.012543W=12.543mW"],
            .012543, "upper_bound"))
        self.assertTrue(_power_bound_formulas(
            ["P≥(24V)²/45925.11Ω=.012542158309473837W≥.012542W=12.542mW"],
            .012542, "lower_bound"))
        self.assertFalse(_power_bound_formulas(
            ["P≤(24V)²/45925.11Ω=.012542158309473837W≤.01254W"], .01254, "upper_bound"))


class RequiredMainAnalysisTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.board = Path(temporary.name)
        self.context_path = self.board / "review_out/agent_jobs/P11-A01/R7.context.json"
        self.context_path.parent.mkdir(parents=True)
        self.card = power_card()

    def check(self, card=None):
        return validate_judge_card(self.card if card is None else card,
                                   rule_id="P11-A01", obj="R7", board=self.board)

    def context(self, **values):
        self.context_path.write_text(json.dumps(values), encoding="utf-8")

    def test_actual_workbench_requires_main_without_a_pilot_file(self):
        self.context(main_analysis_contract="basis-main.v1", analysis_contract="analysis.v1")
        self.assertEqual(self.check(), ["decision-bound main_analysis required"])
        self.assertEqual(self.check(add_main_analysis(copy.deepcopy(self.card))), [])

    def test_legacy_and_available_only_contexts_remain_readable(self):
        self.assertEqual(self.check(), [])
        for context in ({}, {"analysis_contract": "analysis.v1"},
                        {"available_main_analysis_contract": "basis-main.v1", "main_analysis_contract": None}):
            with self.subTest(context=context):
                self.context(**context)
                self.assertEqual(self.check(), [])

    def test_existing_pilot_requirement_and_supplied_main_validation_remain(self):
        (self.board / "main_analysis_contracts.json").write_text(
            json.dumps({"P11-A01": {"R7": "basis-main.v1"}}), encoding="utf-8")
        self.assertEqual(self.check(), ["decision-bound main_analysis required"])
        card = add_main_analysis(copy.deepcopy(self.card))
        card["main_analysis"]["proof_status"] = "incomplete"
        self.assertTrue(any("completed decision-basis" in e for e in self.check(card)))

    def test_other_object_context_cannot_require_or_waive_current_contract(self):
        other = self.context_path.with_name("OTHER.context.json")
        other.write_text(json.dumps({"main_analysis_contract": "basis-main.v1"}), encoding="utf-8")
        self.assertEqual(self.check(), [])
        self.context(main_analysis_contract="basis-main.v1")
        other.write_text("{}", encoding="utf-8")
        self.assertEqual(self.check(), ["decision-bound main_analysis required"])


if __name__ == "__main__":
    unittest.main()
