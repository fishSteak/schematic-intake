"""Auxiliary migration contracts, using temporary artifacts and stub model replies."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_review.context_store import ContextStore, live_path
from agent_review.llm import ConfigurationError, Profile
from agent_review.llm_auxiliary import run_jobs
from agent_review.mpn_param import read_agent_param_cache, run_mpn_param_jobs
from agent_review.mpn_vr import prepare_mpn_vr_jobs
from kernel.rated_hunt import write_agent_vr_cache


class AuxiliaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.board = Path(self.temp.name) / "board"
        self.out = self.board / "review_out"
        self.out.mkdir(parents=True)
        (self.out / "board_kg.json").write_text('{"components":{}}')
        self.profile = Profile(base_url="http://localhost:12345/v1", model="unit-test")
        loader = patch("agent_review.llm_auxiliary.load_profile", return_value=self.profile)
        loader.start()
        self.addCleanup(loader.stop)

    def reply(self, value):
        return patch("agent_review.llm_auxiliary.task", return_value=(value, {"id": "unit-test-run", "status": "completed"}))

    def test_parameter_lookup_validates_and_writes_compatible_sidecar(self):
        value = {"mpn": "REAL-PART-123", "field": "p_rated_w", "value": 0.125,
                 "unit": "W", "cite": "temporary datasheet page 1", "confidence": "high"}
        with self.reply(value), patch("subprocess.run", side_effect=AssertionError("No CLI permitted")):
            result = run_mpn_param_jobs(self.board, [("REAL-PART-123", "p_rated_w")])
        self.assertTrue(result["ok"])
        self.assertEqual(result["ok_n"], 1)
        self.assertEqual(read_agent_param_cache(self.board, "REAL-PART-123", "p_rated_w")["value"], 0.125)
        sidecar = json.loads((self.out / "sdk_mpn_param_result.json").read_text())
        self.assertEqual(sidecar["runtime"], "llm")
        self.assertTrue(sidecar["results"][0]["wrote"])

    def test_wrong_part_and_missing_citation_do_not_publish_parameter(self):
        job = {"job_id": "mpn_vr:part", "mpn": "PART", "out_path": "review_out/cache/result.json"}
        for value in ({"mpn": "OTHER", "value_v": 50, "cite": "source", "confidence": "high"},
                      {"mpn": "PART", "value_v": 50, "cite": "", "confidence": "high"}):
            with self.reply(value):
                result = run_jobs(self.board, "mpn_vr", [job])
            self.assertFalse(result["ok"])
            self.assertFalse((self.out / "cache/result.json").exists())

    def test_context_patch_keeps_existing_fact_and_fails_conflicting_change(self):
        store = ContextStore(self.out)
        store.init(board_kg_path=self.out / "board_kg.json", planned=[{"kind": "object", "key": "R1"}])
        job = {"job_id": "ctx::object::R1", "context_kind": "object", "key": "R1"}
        value = {"kind": "object", "key": "R1", "fields": {"ref": "R1", "value": "1k"},
                 "provenance": [{"source_kind": "board_kg", "path": "review_out/board_kg.json"}]}
        with self.reply(value):
            self.assertTrue(run_jobs(self.board, "context", [job], store=store)["ok"])
        value["fields"] = {"value": "2k"}
        with self.reply(value):
            self.assertFalse(run_jobs(self.board, "context", [job], store=store)["ok"])
        record = json.loads(live_path(store.root, "object", "R1").read_text())
        self.assertEqual(record["fields"]["value"], "1k")

    def test_path_outside_domain_and_numeric_claims_rejected(self):
        draft = self.out / "board_paths/draft"
        draft.mkdir(parents=True)
        (draft / "unit.slice.json").write_text('{"domain_id":"unit","refs":["R1"]}')
        job = {"job_id": "path_draft::unit", "domain_id": "unit",
               "slice_path": "review_out/board_paths/draft/unit.slice.json", "out_path": "review_out/board_paths/draft/unit.json"}
        path = {"path_id": "path-1", "text": "source test", "refs": ["R2"], "supply_net": "V",
                "exit_net": "GND", "nodes": [], "actors": [], "events": [], "evidence": []}
        with self.reply({"domain_id": "unit", "paths": [path]}):
            self.assertFalse(run_jobs(self.board, "path_draft", [job])["ok"])
        path["refs"] = ["R1"]
        path["power_w"] = 0.125
        with self.reply({"domain_id": "unit", "paths": [path]}):
            self.assertFalse(run_jobs(self.board, "path_draft", [job])["ok"])
        self.assertFalse((draft / "unit.json").exists())

    def test_no_profile_is_explicit_and_no_formal_output_written(self):
        with patch("agent_review.llm_auxiliary.load_profile", side_effect=ConfigurationError("Configure model")):
            with self.assertRaises(ConfigurationError):
                run_mpn_param_jobs(self.board, [("REAL-PART-123", "p_rated_w")])
        self.assertIsNone(read_agent_param_cache(self.board, "REAL-PART-123", "p_rated_w"))

    def test_transient_vr_failure_is_retryable(self):
        write_agent_vr_cache(self.board, "REAL-PART-123", {"value_v": None, "error": "agent_did_not_write"})
        self.assertEqual(len(prepare_mpn_vr_jobs(self.board, ["REAL-PART-123"])["jobs"]), 1)


if __name__ == "__main__":
    unittest.main()
