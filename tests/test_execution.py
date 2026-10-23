import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from kernel.runtime import node_executable
from agent_review.stages.judge import reusable_card, _deps_for_object
from agent_review.run import run
from agent_review.decision_contract import power_contract_fingerprint


class ExecutionTests(unittest.TestCase):
    def test_node_uses_bundled_runtime_when_path_is_empty(self):
        with tempfile.TemporaryDirectory() as td:
            executable = Path(td) / "node.exe"
            executable.touch()
            with patch.dict(os.environ, {"PATH": "", "SCHEMATIC_NODE": "", "CODEX_MCP_NODE_PATH": str(executable)}):
                self.assertEqual(node_executable(), str(executable.resolve()))

    def test_resume_requires_valid_card_and_current_context(self):
        with tempfile.TemporaryDirectory() as td:
            board = Path(td)
            card_path = board / "review_out/agent_jobs/P11-A01/R1.json"
            card_path.parent.mkdir(parents=True)
            store = Mock()
            store.execution_material_hash='materials'
            card_path.with_suffix('.context.json').write_text('{}',encoding='utf-8')
            from agent_review.execution_policy import execution_hash
            from agent_review.execution_policy import digest
            signature=execution_hash('P11-A01',{},'materials')
            store.fetch.return_value = {"entries": {"object:R1": {"content_hash": "new"}}}
            self.assertFalse(reusable_card(board, {}, store, "P11-A01", "R1"))
            card_path.write_text("{broken", encoding="utf-8")
            self.assertFalse(reusable_card(board, {}, store, "P11-A01", "R1"))
            card_path.write_text(json.dumps({"context_hashes": {"object:R1": "old"}}), encoding="utf-8")
            with patch("agent_review.stages.judge.validate_judge_card", return_value=[]), patch("agent_review.stages.judge.validate_decision_audit", return_value=[]):
                self.assertFalse(reusable_card(board, {}, store, "P11-A01", "R1"))
                valid={"context_hashes": {"object:R1": "new"}, "decision_contract_hash": power_contract_fingerprint()}
                valid['_execution']={'fingerprint':signature,'output_hash':digest(valid)}
                card_path.write_text(json.dumps(valid), encoding="utf-8")
                self.assertTrue(reusable_card(board, {}, store, "P11-A01", "R1"))
                with patch('agent_review.stages.judge.power_contract_fingerprint',return_value='changed-policy'):
                    self.assertFalse(reusable_card(board,{},store,'P11-A01','R1'))
                card_path.write_text(json.dumps({"context_hashes": {"object:R1": "new"}, "meta": {"dry_run": True}}), encoding="utf-8")
                self.assertFalse(reusable_card(board, {}, store, "P11-A01", "R1"))
            # Matching hashes cannot rescue a card that fails the real contract.
            self.assertFalse(reusable_card(board, {}, store, "P11-A01", "R1"))

    def test_resume_does_not_reuse_legacy_power_decision_contract(self):
        with tempfile.TemporaryDirectory() as td:
            board=Path(td); card=board/'review_out/agent_jobs/P11-A01/X.json';card.parent.mkdir(parents=True)
            card.write_text(json.dumps({'context_hashes':{'object:X':'same'}}),encoding='utf-8')
            store=Mock();store.fetch.return_value={'entries':{'object:X':{'content_hash':'same'}}}
            with patch('agent_review.stages.judge.validate_judge_card',return_value=[]):
                self.assertFalse(reusable_card(board,{},store,'P11-A01','X'))

    def test_signal_rule_fetches_net_context(self):
        self.assertEqual(_deps_for_object({}, "SIGNAL", "P12-A02"), ["net:SIGNAL"])

    def test_placeholder_mpn_does_not_request_unplanned_facts(self):
        kg = {"components": {"C18": {"mpn": "Cap", "pins": [{"net": "GND"}]}}}
        self.assertEqual(_deps_for_object(kg, "C18", "P11-A03"), ["object:C18", "net:GND"])

    def test_default_scope_ignores_cached_partial_scope(self):
        with tempfile.TemporaryDirectory() as td:
            board = Path(td)
            o = board / "review_out"
            o.mkdir()
            (o / "materials.json").write_text(json.dumps({"rule_ids": ["P11-A01"], "rule_ids_explicit": True}), encoding="utf-8")
            (o / "board_kg.json").write_text("{}", encoding="utf-8")
            run(board, only_stage="enumerate", dry_run=True)
            result = json.loads((o / "agent_enumeration.json").read_text(encoding="utf-8"))
            self.assertEqual(len(result["rule_ids"]), 30)
            run(board, only_stage="enumerate", dry_run=True, rule_ids=["P05-A01"])
            result = json.loads((o / "agent_enumeration.json").read_text(encoding="utf-8"))
            self.assertEqual(result["rule_ids"], ["P05-A01"])


if __name__ == "__main__":
    unittest.main()
