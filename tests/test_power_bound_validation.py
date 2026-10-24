"""Numeric bound substitutions stay separate from reachable-point decisions."""
import copy
import unittest
from unittest.mock import patch

from agent_review.llm_tools import MaterialTools
from agent_review.validate_job import validate_judge_card, validate_power_decision, validate_worst_case, _power_bound_formulas


def example():
    states = [
        {"name_zh": "规定端压范围的共同耗散上界", "status": "modeled", "role": "primary",
         "stress_kind": "upper_bound", "P": .002, "conditions": "材料支持的端压与阻值范围",
         "formula_lines": [{"expr": "耗散≤平均[端压²]/50000≤10²/50000=0.002W", "src": "任务书与阻值来源"}]},
        {"name_zh": "外部可维持规定端点时的比较", "status": "conditional", "role": "worst_candidate",
         "stress_kind": "modeled", "P": .002, "conditions": "外部实际负载能力尚未确认",
         "formula_lines": [{"expr": "耗散=10²/50000=0.002W", "src": "端点条件与标称阻值"}]},
        {"name_zh": "扩展供能条件的辅助上界", "status": "conditional", "role": "comparison",
         "stress_kind": "upper_bound", "P": .0125, "conditions": "扩展供能范围尚未确认",
         "formula_lines": [{"expr": "耗散≤25²/50000=0.0125W", "src": "明确保留未确认的端压条件"}]},
    ]
    return {
        "rule_id": "P11-A01", "object": "RX", "verdict_zh": "是",
        "analysis_contract": "analysis.v1",
        "analysis_steps": [{"text": "RX按任务书支持的端压范围和阻值边界建立共同上界，扩展条件仅作辅助比较。", "assumption_refs": ["scope"]}],
        "key_assumptions": [{"id": "scope", "text": "采用规定正常范围，不承诺范围外条件", "status": "supported", "evidence_refs": ["source"]}],
        "evidence_refs": [{"id": "source", "path": "source.pdf"}],
        "headline_zh": "RX：规定范围的耗散上界低于限额，扩展条件未作为判定依据。",
        "location_one_liner": "RX的规定输入通路", "summary": "采用共同上界，不主张等号可达。",
        "phenomenon": "RX直接跨接规定端压与返回网络，逐点上界覆盖规定范围内的长期耗散。",
        "calc": "功耗上界=0.002W", "stuck": "精确可达最大值未确认", "action": "范围扩大后另行复核",
        "conclusion": "规定范围内上界低于限额", "electrical": {"P_rated": .1, "P_limit": .05, "P_max": None, "cite": "source"},
        "states": states,
        "decision_basis": {"kind": "envelope_upper_bound", "state_index": 0, "value_w": .002, "limit_w": .05,
                           "evidence": ["source"], "conditions": "材料支持的正常范围"},
        "decision_audit": {"version": 2, "normal_design_search": "独立检查正常范围内共同上界与条件范围的区别",
                           "state_reviews": [{"state_index": i, "admissibility": "normal_design" if i == 0 else "conditional", "reason": s["conditions"]} for i, s in enumerate(states)],
                           "gaps": [{"issue": "外部实际能力", "effect": "maximum_only", "reason": "共同上界不依赖等号可达"}]},
        "worst_case": {"confirmed_state_index": None, "conditional_state_index": 1,
                       "comparison": [{"state_index": i, "reason": "区分工作点与上界"} for i in range(3)],
                       "unresolved": ["精确可达最大值"]},
    }


class PowerBoundValidationTests(unittest.TestCase):
    def errors_for_formula(self, expression, *, power=.002, kind="upper_bound"):
        card = example()
        # Change only the auxiliary item so the supported pass basis stays fixed.
        card["states"][2].update(P=power, stress_kind=kind,
                                 formula_lines=[{"expr": expression, "src": "明确列出的数字及条件来源"}])
        return validate_judge_card(card, rule_id="P11-A01", obj="RX")

    def test_symbolic_chain_with_numeric_substitution_and_conditional_bound_is_valid(self):
        card = example()
        before = copy.deepcopy(card)
        self.assertEqual(validate_judge_card(card, rule_id="P11-A01", obj="RX"), [])
        self.assertEqual(card, before)

    def test_supported_notation_units_and_rounding(self):
        expressions = [
            ("P<=10^2/50000=0.002W", .002),
            ("P_RX≤(10²÷50000)×1000=2mW", .002),
            ("耗散功率≦(10²/50000)×10⁶=2000µW", .002),
            ("P(RX) ≤ (1e1)**2/5e4=2e-3W", .002),
            ("耗散≤10²/45925.11=0.002177458W", 100 / 45925.11),
        ]
        for expression, power in expressions:
            with self.subTest(expression=expression):
                self.assertEqual(self.errors_for_formula(expression, power=power), [])
        self.assertEqual(self.errors_for_formula("耗散≥10²/50000=0.002W", kind="lower_bound"), [])

    def test_dimensioned_substitution_conservative_rounding_and_unit_comparison(self):
        card = example()
        card["states"][0].update(P=.012543, formula_lines=[
            {"expr": "P = [V(RX) 两端电压差]² / RX ≤ (24 V)² / 45925.11 Ω = 0.012542158309473837 W ≤ 0.012543 W", "src": "source"},
            {"expr": "P ≤ 0.012543 W = 12.543 mW < 0.050 W", "src": "source"},
        ])
        card["decision_basis"]["value_w"] = .012543
        self.assertEqual(validate_judge_card(card, rule_id="P11-A01", obj="RX"), [])
        second = ["P = 平均[V(SENSOR)² / RX] ≤ (10 V)² / 45925.11 Ω = 0.0021774580398392076 W ≤ 0.002178 W",
                  "P ≤ 0.002178 W = 2.178 mW < 0.050 W"]
        self.assertTrue(_power_bound_formulas(second, .002178, "upper_bound"))
        self.assertTrue(_power_bound_formulas([
            "P ≥ (24 V)² / 45925.11 Ω = 0.012542158309473837 W ≥ 0.012542 W",
            "P ≥ 0.012542 W = 12.542 mW > 0.010 W"], .012542, "lower_bound"))

    def test_strict_comparison_limit_is_not_the_formal_bound(self):
        formula = "P ≤ (24 V)² / 45925.11 Ω = 0.012542158309473837 W ≤ 0.012543 W = 12.543 mW < 0.050 W"
        self.assertTrue(_power_bound_formulas([formula], .012543, "upper_bound"))
        self.assertFalse(_power_bound_formulas([formula], .05, "upper_bound"))
        self.assertFalse(_power_bound_formulas([formula], .01254, "upper_bound"))

    def test_only_conservative_rounding_is_accepted(self):
        cases = [
            ("P≤2²/200=0.02W≤0.01999W", .01999, "upper_bound"),
            ("P≥2²/200=0.02W≥0.02001W", .02001, "lower_bound"),
            ("P≤2²/200=0.02W≥0.02001W", .02001, "upper_bound"),
            ("P≥2²/200=0.02W≤0.01999W", .01999, "lower_bound"),
            # Equality tolerance must not permit a silently nonconservative state.
            ("P≤2²/200=0.01999999W", .01999999, "upper_bound"),
            ("P≥2²/200=0.02000001W", .02000001, "lower_bound"),
        ]
        for formula, value, kind in cases:
            with self.subTest(formula=formula):
                self.assertFalse(_power_bound_formulas([formula], value, kind))

    def test_quantity_units_are_checked_without_erasing_dimensions(self):
        valid = [
            ("P≤(24mV)²/(1kΩ)=5.76e-7W", 5.76e-7),
            ("P≤(10mA)²*200Ω=20mW", .02),
            ("P≤(1V+1V)*10mA=0.02W", .02),
            ("P≤(24V)²/(1MΩ)=0.000576W", .000576),
            # Unit-free arithmetic retains the explicit right-hand output unit.
            ("P≤(10²/50000)*1000=2mW", .002),
        ]
        for formula, value in valid:
            with self.subTest(formula=formula):
                self.assertTrue(_power_bound_formulas([formula], value, "upper_bound"))
        invalid = [
            "P≤(24mV)²/(1kΩ)=0.576W", "P≤(24A)²/45925.11Ω=0.012542158309473837W",
            "P≤(2V)/(4V)=0.5W", "P≤(1V+1A)*1A=2W",
            "P≤(2unknown)²/200=0.02W", "P≤(2V)²/200A=0.02W",
            "P≤(2V)²/200=0.02W", "P≤2²/(200Ω)=0.02W",
        ]
        for formula in invalid:
            with self.subTest(formula=formula):
                self.assertFalse(_power_bound_formulas([formula], .02, "upper_bound"))

    def test_auxiliary_unit_conversion_needs_an_independent_numeric_proof(self):
        proof = "P≤2²/200=0.02W"
        self.assertTrue(_power_bound_formulas([proof, "P≤0.02W=20mW<0.05W"], .02, "upper_bound"))
        for lines in (["P≤0.02W=20mW<0.05W"], ["P_upper=0.02W", "P_limit=0.05W"], ["P_limit=0.05W"]):
            with self.subTest(lines=lines):
                self.assertFalse(_power_bound_formulas(lines, .02, "upper_bound"))
        bad_conversions = ["P≤0.02W=20W<50W", "P≤0.02W=20uW<0.05W",
                           "P≤0.02W<20mW", "P≤0.02W>20mW", "P≤0.02W=20mW<0.01W",
                           "P≤0.02A=20mA"]
        for line in bad_conversions:
            with self.subTest(line=line):
                self.assertFalse(_power_bound_formulas([proof, line], .02, "upper_bound"))
        self.assertTrue(_power_bound_formulas([proof, "0.02 W = 20 mW"], .02, "upper_bound"))
        self.assertFalse(_power_bound_formulas([proof, "0.02 W = 20 W"], .02, "upper_bound"))
        self.assertFalse(_power_bound_formulas([proof, "0.02 W = 20.0001 mW"], .02, "upper_bound"))
        self.assertFalse(_power_bound_formulas(["0.02 W = 20 mW"], .02, "upper_bound"))

    def test_float_unit_scaling_cannot_fabricate_strict_order(self):
        proof = "P≤1*0.000017=0.000017W"
        for comparison in ("P≤0.000017W<0.017000mW", "P≤0.017000mW>0.000017W",
                           "0.000017W<0.017000mW", "0.017000mW>0.000017W"):
            with self.subTest(comparison=comparison):
                self.assertFalse(_power_bound_formulas([proof, comparison], .000017, "upper_bound"))

    def test_dimensioned_wrong_proof_cannot_be_masked_by_another_proof(self):
        for wrong in ("P≤(2V)²/(200Ω)=0.03W", "P≤(2A)²/(200Ω)=0.02W",
                      "P≤(2V)²/(200Ω)=0.02W≤0.019W"):
            self.assertFalse(_power_bound_formulas([wrong, "P≤2²/200=0.02W", "P_limit=0.1W"], .02, "upper_bound"))

    def test_quantity_parser_keeps_numeric_size_and_syntax_limits(self):
        invalid = ["P≤(1e309V)²/200Ω=0.02W", "P≤(2V)**101/200Ω=0.02W",
                   "P≤(2V)**(1e308*1e308)/200Ω=0.02W", "P≤(2V)**0.5/200Ω=0.02W",
                   "P≤__import__('os').getcwd()=0.02W", "P≤q0+(2V)=0.02W",
                   "P≤(2V)[0]/200Ω=0.02W", "P≤" + "-(" * 40 + "2V" + ")" * 40 + "**2/200Ω=0.02W",
                   "P≤" + "1+" * 200 + "1=0.02W"]
        for formula in invalid:
            with self.subTest(formula=formula[:60]):
                self.assertFalse(_power_bound_formulas([formula], .02, "upper_bound"))
        for token in ("NaN", "inf", "Infinity", "-INF", "∞"):
            with self.subTest(mixed_nonfinite=token):
                self.assertFalse(_power_bound_formulas(["P≤2*2=4W", "P≤" + token + " W"], 4, "upper_bound"))

    def test_decidable_numeric_prefixes_cannot_be_skipped_as_symbolic(self):
        for prefix in ("1/0", "2*2", "2W", "1e309", "(2A)²/200Ω", "2unknown"):
            formula = "P≤" + prefix + "≤10²/50000=0.002W"
            with self.subTest(prefix=prefix):
                self.assertFalse(_power_bound_formulas([formula], .002, "upper_bound"))
        self.assertTrue(_power_bound_formulas(["P≤1/1000≤10²/50000=0.002W"], .002, "upper_bound"))
        self.assertTrue(_power_bound_formulas(["P≤平均[端压²]/50000≤10²/50000=0.002W"], .002, "upper_bound"))
        self.assertFalse(_power_bound_formulas(["P≤2*2≤平均[端压²]/50000≤10²/50000=0.002W"], .002, "upper_bound"))

    def test_unverified_or_inconsistent_substitution_is_rejected(self):
        expressions = [
            "耗散≤由条件可知安全", "耗散≤0.002W", "耗散≤0.002=0.002W",
            "耗散≤未知电压²/50000=0.002W", "耗散≤10²/50000=2W",
            "耗散≤10²/50000=0.002mW", "耗散≤10²/50000=0.002",
            "耗散≤10²/50000=0.002kW", "耗散≤10²/0=0.002W",
            "耗散≤10**10000=0.002W", "耗散≤1e309/2=0.002W",
            "耗散≤__import__('os').getcwd()=0.002W",
            "耗散≤(10²/50000)[0]=0.002W", "耗散≤10²/50000=nanW",
            "耗散≤未知情况但0.002mW", "耗散≤平均[端压²]≥10²/50000=0.002W",
        ]
        for expression in expressions:
            with self.subTest(expression=expression):
                errors = self.errors_for_formula(expression)
                self.assertTrue(any("上下界数值推导" in e for e in errors), errors)
        for power in (0, .003, float("inf"), float("nan")):
            with self.subTest(power=power):
                self.assertTrue(self.errors_for_formula("耗散≤10²/50000=0.002W", power=power))

    def test_bound_direction_and_exact_state_semantics_are_preserved(self):
        for expression, kind in (("P≥10²/50000=0.002W", "upper_bound"),
                                 ("P≤10²/50000=0.002W", "lower_bound"),
                                 ("P≤10²/50000=0.002W", "modeled")):
            with self.subTest(kind=kind):
                self.assertTrue(self.errors_for_formula(expression, kind=kind))
        for kind in ({"kind": "upper_bound"}, ["upper_bound"], False, None):
            with self.subTest(malformed_kind=kind):
                self.assertTrue(self.errors_for_formula("P≤10²/50000=0.002W", kind=kind))

    def test_numeric_derivation_still_requires_source(self):
        card = example(); card["states"][0]["formula_lines"][0]["src"] = ""
        self.assertTrue(any("缺 src" in error for error in validate_judge_card(card, rule_id="P11-A01", obj="RX")))

    def test_other_equations_cannot_conceal_an_invalid_numeric_bound(self):
        for companion in ("P_limit=0.1W", "P=10²/50000=0.002W", "P≤10²/50000=0.002W", "P_upper=10²/50000=0.002W"):
            card = example()
            card["states"][0]["formula_lines"] = [{"expr": expr, "src": "source"} for expr in
                ("RX耗散≤10²/50000=0.003W", companion)]
            with self.subTest(companion=companion):
                errors = validate_judge_card(card, rule_id="P11-A01", obj="RX")
                self.assertTrue(any("上下界数值推导" in error for error in errors), errors)
        for expression in ("P≤10²/50000=0.003W；P≤10²/50000=0.002W",
                           "P_upper=10²/50000=0.003W；P_upper=10²/50000=0.002W"):
            with self.subTest(same_line=expression):
                self.assertTrue(self.errors_for_formula(expression))

    def test_symbolic_relation_can_use_separately_named_numeric_bound(self):
        for relation, name, kind in (("≤", "upper", "upper_bound"), ("≥", "lower", "lower_bound")):
            card = example()
            card["states"][2].update(P=.002, stress_kind=kind, formula_lines=[
                {"expr": "P" + relation + "V²/R", "src": "symbolic boundary and conditions"},
                {"expr": "P_" + name + "=10²/50000=0.002W", "src": "explicit numeric substitution"},
                {"expr": "P_limit=0.05W", "src": "limit source"},
            ])
            with self.subTest(kind=kind):
                self.assertEqual(validate_judge_card(card, rule_id="P11-A01", obj="RX"), [])
                card["states"][2]["formula_lines"][1]["expr"] = "P_" + name + "=10²/50000=0.003W"
                self.assertTrue(validate_judge_card(card, rule_id="P11-A01", obj="RX"))

    def test_symbolic_relation_and_limit_alone_are_not_numeric_proof(self):
        for companion in ("P_limit=0.05W", "P_upper=V²/R", "P_lower=10²/50000=0.002W"):
            card = example()
            card["states"][0]["formula_lines"] = [{"expr": expr, "src": "source"} for expr in ("P≤V²/R", companion)]
            with self.subTest(companion=companion):
                self.assertTrue(validate_judge_card(card, rule_id="P11-A01", obj="RX"))

    def test_conditional_bounds_do_not_enter_reachable_point_ranking(self):
        for kind in ("upper_bound", "lower_bound"):
            card = example(); card["states"][2]["stress_kind"] = kind
            self.assertEqual(validate_worst_case(card), [])
            for key in ("confirmed_state_index", "conditional_state_index"):
                changed = copy.deepcopy(card); changed["worst_case"][key] = 2
                self.assertTrue(any(key in error for error in validate_worst_case(changed)))
            card["states"][2]["role"] = "worst_candidate"
            self.assertTrue(any("ranked working-point role" in error for error in validate_worst_case(card)))

    def test_conditional_bounds_need_finite_numbers_and_explicit_conditions(self):
        for kind in ("upper_bound", "lower_bound"):
            for field, value in (("P", None), ("P", True), ("P", float("nan")), ("P", float("inf")), ("P", -.1), ("conditions", ""), ("conditions", "  ")):
                card = example(); card["states"][2].update(stress_kind=kind, **{field: value})
                self.assertTrue(validate_worst_case(card), (kind, field, value))

    def test_unconfirmed_bound_cannot_be_promoted_to_pass_or_fail_evidence(self):
        card = example(); card["decision_basis"].update(state_index=2, value_w=.0125)
        self.assertTrue(any("conditional" in error for error in validate_power_decision(card)))
        card["decision_audit"]["state_reviews"][2]["admissibility"] = "normal_design"
        self.assertTrue(any("conditional/unmapped" in error for error in validate_power_decision(card)))
        for kind in ("reachable_state", "proven_lower_bound"):
            card = example(); card.update(verdict_zh="否")
            card["decision_basis"].update(kind=kind, state_index=2, value_w=.1)
            card["states"][2].update(P=.1, stress_kind="lower_bound" if kind == "proven_lower_bound" else "upper_bound")
            self.assertTrue(validate_power_decision(card))

    def test_envelope_basis_requires_matching_bound_state(self):
        for changes in ({"state_index": None}, {"state_index": 1}, {"value_w": .003}):
            card = example(); card["decision_basis"].update(changes)
            self.assertTrue(validate_power_decision(card), changes)

    def test_valid_bound_does_not_hide_invalid_figure_paths_or_rank_selection(self):
        card = example()
        card["states"][0]["figure"] = {"pages": [{"pdf_page": 1, "paths": [{"path": ["SUPPLY", "RX"]}]}]}
        card["worst_case"]["conditional_state_index"] = 2
        card["states"][2]["role"] = "worst_candidate"
        errors = validate_judge_card(card, rule_id="P11-A01", obj="RX")
        self.assertTrue(any("每条 path/paths" in error for error in errors), errors)
        self.assertTrue(any("conditional_state_index" in error for error in errors), errors)
        self.assertFalse(any("上下界数值推导" in error for error in errors), errors)

    def test_reusable_calculator_keeps_static_and_instance_calls_pure(self):
        # Bypass construction: the calculator neither needs a board nor accesses it.
        instance = object.__new__(MaterialTools)
        with patch("builtins.open", side_effect=AssertionError("unexpected file I/O")), \
             patch("pathlib.Path.open", side_effect=AssertionError("unexpected file I/O")), \
             patch("httpx.Client.request", side_effect=AssertionError("unexpected network I/O")):
            self.assertEqual(MaterialTools.calculate("10**2/50000"), instance.calculate("10**2/50000"))
            self.assertEqual(self.errors_for_formula("P≤10²/50000=0.002W"), [])
            for expression in ("a+1", "2**101", "1e309+1", "1/0", "1+" * 600 + "1"):
                with self.subTest(expression=expression[:30]), self.assertRaises((ValueError, ArithmeticError)):
                    MaterialTools.calculate(expression)


if __name__ == "__main__":
    unittest.main()
