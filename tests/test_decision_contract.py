import copy
import unittest
import tempfile
from pathlib import Path

from agent_review.decision_contract import validate_decision_audit
from agent_review.validate_job import validate_power_decision, validate_worst_case, validate_judge_card
from agent_review.aggregate import load_judge_cards
from kernel.util import write_json


class PowerDecisionContractTests(unittest.TestCase):
    def supported_failure(self):
        return {
            "verdict_zh": "否",
            "electrical": {"P_limit": .5, "P_max": None},
            "states": [
                {"P": 1.7, "status": "conditional", "stress_kind": "modeled", "role": "worst_candidate"},
                {"P": .8, "status": "modeled", "stress_kind": "modeled", "role": "worst"},
            ],
            "decision_basis": {"kind": "normal_design_model", "state_index": 1, "value_w": .8, "limit_w": .5,
                               "model_scope": "nominal_design", "evidence": ["design materials"],
                               "conditions": "normal nominal operation", "applicability": "supported component model",
                               "coverage_argument": "additional loading cannot reduce this stress bound"},
            "decision_audit": {"version": 2, "normal_design_search": "independent internal loading model",
                               "state_reviews": [{"state_index": 0, "admissibility": "conditional", "reason": "external capability unknown"},
                                                 {"state_index": 1, "admissibility": "normal_design", "reason": "normal on-board model"}],
                               "gaps": [{"issue": "external capability", "effect": "maximum_only", "reason": "does not affect independent normal evidence"},
                                        {"issue": "parameter precision", "effect": "precision_only", "reason": "refines the computed stress"}]},
        }

    def test_independent_design_failure_survives_unknown_larger_candidate(self):
        self.assertEqual(validate_power_decision(self.supported_failure()), [])

    def test_same_evidence_cannot_be_pending_because_maximum_is_unknown(self):
        card = self.supported_failure()
        card.update(verdict_zh="待确认", decision_basis={"kind": "insufficient"})
        errors = validate_power_decision(card)
        self.assertTrue(any("normal-design exceedance" in e for e in errors), errors)

    def test_unsupported_external_candidate_is_not_a_failure_witness(self):
        card = self.supported_failure()
        card["decision_basis"].update(state_index=0, value_w=1.7)
        self.assertTrue(validate_power_decision(card))

    def test_real_decision_changing_gap_can_remain_pending(self):
        card = self.supported_failure()
        card.update(verdict_zh="待确认", decision_basis={"kind": "insufficient"})
        card["states"][1]["P"] = .2
        card["decision_audit"]["gaps"] = [{"issue": "actual sustained operating mode", "effect": "can_change_verdict",
                                           "reason": "normal internal model is below the limit; only unresolved external state exceeds",
                                           "countercase": {"description": "normal open high-impedance output", "evidence": ["documented output mode"],
                                                           "decision_effect": "remaining internal operation stays below limit"}}]
        self.assertEqual(validate_power_decision(card), [])
        card["decision_audit"]["gaps"][0].pop("countercase")
        self.assertTrue(validate_power_decision(card))

    def test_current_contract_cannot_reuse_legacy_card(self):
        self.assertTrue(validate_decision_audit({"verdict_zh": "待确认"}, required=True))
        card = self.supported_failure()
        card["decision_audit"]["state_reviews"].pop()
        self.assertTrue(validate_decision_audit(card, required=True))

    def test_normal_design_witness_is_not_a_confirmed_global_maximum(self):
        card = self.supported_failure()
        card['states'][1]['role'] = 'witness'
        card['states'][0]['conditions'] = 'external capability unknown'
        card['worst_case'] = {'confirmed_state_index': None, 'conditional_state_index': 0,
                              'comparison': [{'state_index': i, 'reason': 'compared'} for i in range(2)],
                              'unresolved': ['global operating envelope']}
        self.assertEqual(validate_worst_case(card), [])

    def test_business_power_formula_accepts_chinese_dissipation_label(self):
        card = self.supported_failure()
        for state in card['states']:
            state['formula_lines'] = [{'expr': '器件耗散 = (0.1A)² × 80Ω = 0.8W', 'src': 'material-supported current and resistance'}]
        errors = validate_judge_card(card, rule_id='P11-A01', obj='ANY')
        self.assertFalse(any('formula_lines 须含功耗' in e for e in errors), errors)

    def test_rated_values_and_objects_are_not_decision_special_cases(self):
        card = self.supported_failure()
        scaled = copy.deepcopy(card)
        for state in scaled["states"]:
            state["P"] *= 17
        scaled["electrical"]["P_limit"] *= 17
        scaled["decision_basis"]["value_w"] *= 17
        scaled["decision_basis"]["limit_w"] *= 17
        scaled["object"] = "UNRELATED_COMPONENT"
        self.assertEqual(validate_power_decision(scaled), [])

    def test_failed_or_inconsistent_card_is_not_published_by_aggregation(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td); path=out/'agent_jobs/P11-A01/ANY.json'
            card=self.supported_failure();write_json(path,card)
            self.assertEqual(len(load_judge_cards(out,'P11-A01',['ANY'])),1)
            card.update(verdict_zh='待确认',decision_basis={'kind':'insufficient'});write_json(path,card)
            self.assertEqual(load_judge_cards(out,'P11-A01',['ANY']),[])
            write_json(path,self.supported_failure());write_json(path.with_suffix('.invalid.json'),{'errors':['contract failure']})
            self.assertEqual(load_judge_cards(out,'P11-A01',['ANY']),[])
