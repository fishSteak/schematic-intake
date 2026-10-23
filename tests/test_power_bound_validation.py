"""Numeric bound substitutions stay separate from reachable-point decisions."""
import copy
import unittest
from unittest.mock import patch

from agent_review.llm_tools import MaterialTools
from agent_review.validate_job import validate_judge_card, validate_power_decision, validate_worst_case


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
            ("耗散功率≦(2×10⁻³)×10⁶=2000µW", .002),
            ("P(RX) ≤ (1e1)**2/5e4=2e-3W", .002),
            ("耗散≤10²/45925.11=0.002177458W", 100 / 45925.11),
        ]
        for expression, power in expressions:
            with self.subTest(expression=expression):
                self.assertEqual(self.errors_for_formula(expression, power=power), [])
        self.assertEqual(self.errors_for_formula("耗散≥10²/50000=0.002W", kind="lower_bound"), [])

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
