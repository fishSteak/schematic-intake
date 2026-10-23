"""Research retries receive safe actionable feedback without resetting budgets."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from agent_review import evidence_loop, llm_tasks
from agent_review.llm import (
    BudgetExceeded, ConfigurationError, LLMClient, LLMHTTPError,
    LLMTransportError, OutputValidationError, Profile,
)
from kernel.util import read_json, write_json


class EvidenceRetryFeedbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.board = Path(temporary.name)
        write_json(self.board / "materials.json", {})
        self.enumeration = {"rule_ids": ["P11-A01"], "by_rule": {"P11-A01": ["R7"]}}
        self.valid = {"schema_version": "evidence.v1", "facts": [], "conflicts": [], "requests": [],
                      "search_log": [{"source": "materials.json", "purpose": "Inspect supplied sources",
                                      "outcome": "No decisive facts supplied"}]}
        self.payloads = []
        self.stdout = io.StringIO()
        llm_tasks.configure()
        self.addCleanup(llm_tasks.configure)

    def setup_client(self, handler, **options):
        profile = Profile(base_url="http://localhost:11434/v1", model="fixture", wire_api="responses",
                          retry_backoff=0, **options)
        def transport(request):
            self.payloads.append(json.loads(request.content))
            return handler(request)
        http = httpx.Client(transport=httpx.MockTransport(transport))
        self.addCleanup(http.close)
        return profile, LLMClient(profile, http)

    def response(self, text):
        return httpx.Response(200, json={"id": "fixture-response-" + str(len(self.payloads)),
            "model": "actual-fixture-model", "status": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            "output": [{"type": "message", "role": "assistant", "phase": "final_answer",
                        "status": "completed", "content": [{"type": "output_text", "text": text}]}]})

    def execute(self, profile, client):
        with patch("agent_review.llm.load_profile", return_value=profile), \
                patch("agent_review.llm.LLMClient", return_value=client), \
                contextlib.redirect_stdout(self.stdout):
            return evidence_loop.run_research(self.board, self.enumeration, force=True)

    def context(self, index):
        users = [item for item in self.payloads[index]["input"] if item.get("role") == "user"]
        return json.loads(users[-1]["content"].split("\n\nTask context:\n", 1)[1])

    def run_record(self):
        return read_json(next((self.board / "review_out/evidence_proposals").glob("*/run.json")))

    def test_bad_json_feedback_reaches_next_real_request_without_raw_output(self):
        rejected = 'PRIVATE_REJECTED_MODEL_TEXT {"facts":'
        profile, client = self.setup_client(lambda request: self.response(
            rejected if len(self.payloads) == 1 else json.dumps(self.valid)))
        result = self.execute(profile, client)
        self.assertFalse(result["reused"])
        self.assertEqual(len(self.payloads), 2)
        self.assertEqual(self.context(0)["validation_errors"], [])
        feedback = self.context(1)["validation_errors"]
        self.assertEqual(len(feedback), 1)
        self.assertTrue(feedback[0].startswith("Model response is not a complete JSON document"))
        self.assertEqual(client.request_count, 2)
        self.assertEqual(client.metadata()["usage"]["total_tokens"], 30)
        self.assertNotIn(rejected, self.stdout.getvalue())
        for path in (self.board / "review_out").rglob("*.json"):
            self.assertNotIn(rejected, path.read_text(), str(path))

    def test_schema_feedback_keeps_location_and_validator_for_repair(self):
        profile, client = self.setup_client(lambda request: self.response(
            "{}" if len(self.payloads) == 1 else json.dumps(self.valid)))
        self.execute(profile, client)
        errors = self.context(1)["validation_errors"]
        self.assertEqual(len(errors), 1)
        self.assertIn("JSON schema validation failed", errors[0])
        self.assertIn("required", errors[0])

    def test_feedback_is_redacted_before_model_and_disk_receive_it(self):
        profile, client = self.setup_client(lambda request: self.response(json.dumps(self.valid)),
                                            api_key="fixture-private-api-credential")
        actual_complete = client.complete
        failed = False
        def complete(*args, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                raise OutputValidationError("JSON schema validation failed at /facts: required; " + profile.api_key)
            return actual_complete(*args, **kwargs)
        with patch.object(client, "complete", side_effect=complete):
            self.execute(profile, client)
        feedback = self.context(0)["validation_errors"]
        self.assertIn("[redacted]", feedback[0])
        self.assertNotIn(profile.api_key, json.dumps(self.payloads) + self.stdout.getvalue())
        for path in (self.board / "review_out").rglob("*"):
            if path.is_file():
                self.assertNotIn(profile.api_key, path.read_text(), str(path))

    def test_bad_json_stops_after_three_semantic_attempts(self):
        profile, client = self.setup_client(lambda request: self.response("not JSON"))
        with self.assertRaisesRegex(RuntimeError, "did not deliver source-valid evidence"):
            self.execute(profile, client)
        self.assertEqual(len(self.payloads), 3)
        self.assertEqual(client.request_count, 3)
        for index in (1, 2):
            feedback = self.context(index)["validation_errors"]
            self.assertEqual(len(feedback), 1)
            self.assertTrue(feedback[0].startswith("Model response is not a complete JSON document"))
        self.assertFalse((self.board / "review_out/evidence_store/current.json").exists())

    def test_budget_is_shared_and_exhaustion_cannot_start_another_semantic_repair(self):
        profile, client = self.setup_client(lambda request: self.response("not JSON"), max_requests=1)
        with self.assertRaises(BudgetExceeded):
            self.execute(profile, client)
        self.assertEqual(len(self.payloads), 1)
        self.assertEqual(client.request_count, 1)
        self.assertEqual(self.run_record()["error_kind"], "budget")
        self.assertFalse(self.run_record()["repairable"])

    def test_transport_http_and_configuration_failures_do_not_get_semantic_retry(self):
        variants = [("timeout", LLMTransportError, 2), ("http", LLMHTTPError, 1),
                    ("configuration", ConfigurationError, 0)]
        for kind, error, calls in variants:
            with self.subTest(kind=kind):
                self.payloads.clear()
                def handler(request):
                    if kind == "timeout":
                        raise httpx.ReadTimeout("private upstream detail", request=request)
                    return httpx.Response(401, text="private upstream detail")
                profile, client = self.setup_client(handler, max_retries=1, supports_tools=kind != "configuration")
                with patch.object(llm_tasks, "task", wraps=llm_tasks.task) as task:
                    with self.assertRaises(error):
                        self.execute(profile, client)
                self.assertEqual(task.call_count, 1)
                self.assertEqual(len(self.payloads), calls)
                self.assertFalse((self.board / "review_out/evidence_store/current.json").exists())
                records = [read_json(path) for path in (self.board / "review_out/evidence_proposals").glob("*/run.json")]
                self.assertTrue(any(row.get("error_kind") == kind and row.get("repairable") is False for row in records))
                self.assertNotIn("private upstream detail", json.dumps(records))


if __name__ == "__main__":
    unittest.main()
