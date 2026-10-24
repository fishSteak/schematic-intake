"""Packaging changes file locations, never intra-document reference identity."""
import copy
from pathlib import Path
import tempfile
import unittest

from agent_review.main_analysis import validate as validate_main
from tests.test_llm_legacy_schema import add_main_analysis, power_card
from vendor.expert_html.portable_paths import make_pack_portable


class PortablePathsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.board = Path(temporary.name)
        self.out = self.board / "review_out"
        self.out.mkdir()
        self.asset = self.out / "figures" / "figure.png"
        self.asset.parent.mkdir()
        self.asset.write_bytes(b"fixture image")
        self.source = self.board / "material_inputs" / "source.pdf"
        self.source.parent.mkdir()
        self.source.write_bytes(b"fixture source")

    def test_packaged_main_analysis_retains_its_formal_basis_and_quantity(self):
        card = add_main_analysis(power_card())
        marker = card["main_analysis"]["figure"]["pages"][0]["markers"][0]
        marker["quantity_ref"] = "/electrical/P_limit"
        card["evidence_refs"][0]["path"] = str(self.source)
        pack = {"rule_id": "P11-A01", "object_cards": [card]}
        original = copy.deepcopy(pack)
        self.assertEqual(validate_main(card), [])

        portable = make_pack_portable(pack, self.out)
        delivered = portable["object_cards"][0]
        self.assertEqual(delivered["main_analysis"]["basis_ref"], "/decision_basis")
        self.assertEqual(delivered["main_analysis"]["figure"]["pages"][0]["markers"][0]["quantity_ref"],
                         "/electrical/P_limit")
        self.assertEqual(validate_main(delivered), [])
        self.assertEqual(delivered["evidence_refs"][0]["path"], "material_inputs/source.pdf")
        self.assertEqual(pack, original)
        self.assertEqual(make_pack_portable(portable, self.out), portable)

    def test_nested_analysis_assumption_and_scenario_pointers_are_preserved(self):
        card = {
            "key_assumptions": [{"id": "A1"}], "scenarios": [{"id": "S1"}],
            "analysis_steps": [{"assumption_refs": ["/key_assumptions/0", "A1"]}],
            "extreme": {"primary_scenario_ref": "/scenarios/0"},
            "links": ["/analysis_steps/0", ["/key_assumptions/0"]],
        }
        pack = {"object_cards": [card]}
        self.assertEqual(make_pack_portable(pack, self.out), pack)

    def test_escaped_and_unresolved_references_keep_their_original_semantics(self):
        pack = {
            "a/b~c": [1], "empty": {"": True},
            "links": ["/a~1b~0c/0", "/empty/"],
            "quantity_ref": "/future_schema/value", "input_refs": ["/future_schema/input/0"],
            "$ref": "#/definitions/claim",
        }
        self.assertEqual(make_pack_portable(pack, self.out), pack)

    def test_explicit_paths_and_legacy_file_lists_still_become_portable(self):
        pack = {
            "input_dir": str(self.board),
            "screenshot": str(self.asset), "local_path": str(self.asset),
            "source_file": str(self.source), "pdf": str(self.source),
            "evidence_refs": [{"path": str(self.source)}, str(self.source)],
            "assets": [str(self.asset), {"file": str(self.asset)}],
            "other_absolute_asset": str(self.asset),
            "website": "https://example.test/report.pdf",
        }
        portable = make_pack_portable(pack, self.out)
        self.assertEqual(portable["input_dir"], "..")
        for key in ("screenshot", "local_path", "other_absolute_asset"):
            self.assertEqual(portable[key], "figures/figure.png")
        for key in ("source_file", "pdf"):
            self.assertEqual(portable[key], "material_inputs/source.pdf")
        self.assertEqual(portable["evidence_refs"], [{"path": "material_inputs/source.pdf"}, "material_inputs/source.pdf"])
        self.assertEqual(portable["assets"], ["figures/figure.png", {"file": "figures/figure.png"}])
        self.assertEqual(portable["website"], pack["website"])

    def test_explicit_file_field_takes_precedence_over_a_matching_json_key(self):
        # A Unix path can begin with a name which also occurs in a JSON object.
        first = self.asset.parts[1]
        pack = {first: {}, "path": str(self.asset)}
        self.assertEqual(make_pack_portable(pack, self.out)["path"], "figures/figure.png")


if __name__ == "__main__":
    unittest.main()
