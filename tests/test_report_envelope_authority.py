import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_review import execution_policy
from agent_review.context_store import ContextStore
from agent_review.delivery_gate import run_gate
from agent_review.p11_card_enrich import enrich_p11_card
from agent_review.seed_context import seed_all
from agent_review.validate_job import validate_judge_card
from kernel.util import read_json, write_json
from tests.test_llm_legacy_schema import add_main_analysis, power_card


class ReportEnvelopeAuthorityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.board = Path(temporary.name)
        self.out = self.board / "review_out"
        self.card = add_main_analysis(power_card())
        self.card_path = self.out / "agent_jobs/P11-A01/R7.json"
        self.kg = {"components": {"R7": {"type": "resistor", "value": "200", "pins": [],
                                           "mpn": "TEST-PART", "footprint": "0603"}}, "nets": {}}
        write_json(self.out / "board_kg.json", self.kg)
        planned = [{"kind": "object", "key": "R7"}]
        store = ContextStore(self.out)
        store.init(board_kg_path=self.out / "board_kg.json", planned=planned)
        seed_all(store, self.kg, self.out, planned)
        store.seal()
        write_json(self.out / "operating_cases/P11-A01/R7.json", {
            "ref": "R7", "meta": {"unmapped_n": 1, "closed_n": 0},
            "cases": [{"case_id": "legacy-projection", "status": "unmapped"}]})
        write_json(self.out / "program_stress/P11-A01/R7.json", {
            "P_rated": 9, "P_limit": 4.5, "P_max": 7, "ohm": 999,
            "states": [{"case_id": "legacy-projection", "P": 7}]})
        write_json(self.out / "board_paths/index.json", {
            "meta": {"status_counts": {"unmapped": 1}, "unmapped_preserved": True},
            "paths": [{"events": [{"status": "unmapped"}]}]})
        write_json(self.out / "evidence/P11-A01.json", {})
        write_json(self.out / "report_data.json", {})
        write_json(self.out / "verdicts.json", {})
        source = self.board / "material_inputs/source.txt"
        source.parent.mkdir();source.write_text("Confirmed review envelope.")
        self.enumeration = {"rule_ids": ["P11-A01"], "agent_rule_ids": ["P11-A01"],
                            "by_rule": {"P11-A01": ["R7"]}}

    def gate(self, card=None):
        write_json(self.card_path, self.card if card is None else card)
        return run_gate(self.out, self.enumeration, allow_failed=False)

    def path_check(self, result):
        return next(row for row in result["checks"] if row["id"] == "board_paths_unmapped")

    def test_direct_review_keeps_every_engineering_field_and_resource_metadata(self):
        before = copy.deepcopy(self.card)
        result = enrich_p11_card(self.out, self.card, self.kg)
        self.assertEqual(self.card, before)
        for key, value in before.items():
            self.assertEqual(result[key], value, key)
        self.assertEqual(result["identity"]["mpn"], "TEST-PART")
        self.assertIn("datasheet", result)
        for key in ("solution", "computed", "after", "recipe_id"):
            self.assertNotIn(key, result)
        self.assertEqual(read_json(self.out / "operating_cases/P11-A01/R7.json")["meta"]["unmapped_n"], 1)

    def test_direct_review_also_preserves_existing_resource_metadata(self):
        self.card["datasheet"] = {"local_path": "review_out/research_assets/original.pdf", "note": "model-adopted source"}
        result = enrich_p11_card(self.out, self.card, self.kg)
        self.assertEqual(result["datasheet"], self.card["datasheet"])

    def legacy_card(self):
        card = copy.deepcopy(self.card)
        card.pop("analysis_contract")
        card.pop("analysis_steps")
        card["reasoning_steps"] = ["核对 R7 的源端口范围。", "按原始范围计算共同边界。", "以边界对照限额。"]
        return card

    def test_legacy_projection_cannot_be_promoted_and_remains_visible(self):
        card = self.legacy_card()
        self.assertEqual(validate_judge_card(card, rule_id="P11-A01", obj="R7", board=self.board), [])
        result = enrich_p11_card(self.out, card, self.kg)
        self.assertEqual(result["verdict_zh"], "待确认")
        self.assertEqual(result["meta"]["unmapped_n"], 1)
        checked = self.gate(card)
        self.assertFalse(checked["passed"])
        self.assertEqual(self.path_check(checked)["green_with_unmapped"], ["P11-A01::R7"])

    def test_valid_independent_envelope_preserves_gaps_without_rejudging(self):
        before = copy.deepcopy(self.card)
        checked = self.gate()
        self.assertTrue(checked["passed"], checked)
        paths = self.path_check(checked)
        self.assertEqual(paths["validated_envelope_cards"], ["P11-A01::R7"])
        self.assertEqual(paths["green_with_unmapped"], [])
        self.assertEqual(paths["unmapped_n"], 1)
        self.assertEqual(paths["sealed_unmapped"], 1)
        self.assertTrue(paths["preserved"])
        self.assertEqual(read_json(self.card_path), before)

    def test_direct_label_does_not_bypass_invalid_basis_source_or_arithmetic(self):
        def missing_source(card):
            card["evidence_refs"] = []
        def wrong_power(card):
            card["states"][0]["formula_lines"][0]["expr"] = "R7耗散≤2²/200=0.03W"
        def conditional_witness(card):
            card["states"][0]["status"] = "conditional"
        def unmapped_state(card):
            card["states"][1]["status"] = "unmapped"
        def unknown_identity(card):
            card["object"] = "OTHER"
        def incomplete_proof(card):
            card["main_analysis"]["proof_checks"][0]["status"] = "unresolved"
        def missing_basis(card):
            card.pop("decision_basis")
        for mutate in (missing_source, wrong_power, conditional_witness, unmapped_state,
                       unknown_identity, incomplete_proof, missing_basis):
            with self.subTest(mutation=mutate.__name__):
                card = copy.deepcopy(self.card);mutate(card)
                checked = self.gate(card)
                self.assertFalse(checked["passed"])
                self.assertEqual(self.path_check(checked)["validated_envelope_cards"], [])
                self.assertEqual(self.path_check(checked)["green_with_unmapped"], ["P11-A01::R7"])

    def test_main_analysis_is_required_even_if_other_fields_validate(self):
        card = copy.deepcopy(self.card)
        card["states"][0]["figure"] = card.pop("main_analysis")["figure"]
        self.assertEqual(validate_judge_card(card, rule_id="P11-A01", obj="R7", board=self.board), [])
        self.assertFalse(self.gate(card)["passed"])

    def test_valid_envelope_does_not_hide_deleted_or_miscounted_path_gaps(self):
        path = self.out / "board_paths/index.json"
        original = read_json(path)
        for key, value in (("unmapped_preserved", False), ("status_counts", {"unmapped": 2})):
            with self.subTest(key=key):
                changed = copy.deepcopy(original);changed["meta"][key] = value;write_json(path, changed)
                checked = self.gate()
                self.assertFalse(checked["passed"])
                self.assertFalse(self.path_check(checked)["ok"])

    def test_gate_contract_changes_invalidate_execution_signature(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "agent_review/delivery_gate.py"
            source.parent.mkdir();source.write_text("first gate contract")
            with patch.object(execution_policy, "ROOT", root), \
                    patch.object(execution_policy, "profile_fingerprint", return_value="fixed-model"):
                before = execution_policy.execution_hash("P11-A01", {}, "fixed-materials")
                source.write_text("changed gate contract")
                after = execution_policy.execution_hash("P11-A01", {}, "fixed-materials")
            self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
