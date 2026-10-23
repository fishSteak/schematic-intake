import tempfile
import unittest
import importlib.util
import sys
from unittest.mock import patch
from pathlib import Path

from agent_review.p11_card_enrich import enrich_p11_card
from kernel.util import write_json


class ReportAuthorityTests(unittest.TestCase):
    def test_model_action_survives_unknown_global_maximum(self):
        vendor = Path(__file__).resolve().parents[1] / 'vendor/expert_html'
        spec = importlib.util.spec_from_file_location('expert_builder_test', vendor / 'build_expert_html.py')
        builder = importlib.util.module_from_spec(spec)
        with patch.object(sys, 'path', [str(vendor), *sys.path]):
            spec.loader.exec_module(builder)
        for verdict in ('fail', 'wait'):
            card = {'ref': 'ANY_COMPONENT', 'p_w': None, 'action': '按已核算应力调整选型，并补足工作包络。'}
            result = builder._advice_p11_a01([card] if verdict == 'fail' else [], [card] if verdict == 'wait' else [])
            self.assertEqual(result, [('ANY_COMPONENT', card['action'])])

    def test_explicit_unknown_or_zero_is_not_replaced_by_program_peak(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td)
            write_json(output / "program_stress/P11-A01/R4.json", {
                "P_rated": 0.1, "P_limit": 0.05, "P_max": 0.2755,
                "P_max_case": "old_trial", "states": [],
            })
            for peak in (None, 0, 0.04):
                with self.subTest(peak=peak):
                    card = {"rule_id": "P11-A01", "object": "R4", "verdict_zh": "待确认",
                            "electrical": {"P_rated": 0.1, "P_limit": 0.05, "P_max": peak},
                            "states": [{"name_zh": "供电经串联电阻到接口，持续灌流条件未闭合",
                                        "status": "unmapped", "P": None, "结论": "待确认"}]}
                    result = enrich_p11_card(output, card, {})
                    self.assertEqual(result["electrical"]["P_max"], peak)
                    self.assertNotIn("P_max_case", result["electrical"])
                    self.assertEqual(result["states"][0]["status"], "unmapped")


if __name__ == "__main__":
    unittest.main()
