"""Control-plane regression tests use isolated data; no production cards or models."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from web_workbench.app import create_app
from web_workbench.execution import profile_signature, scope_objects, snapshot_board, validate_snapshot
from web_workbench.materials import inventory
from web_workbench.runner import process_alive, reconcile
from web_workbench.store import Conflict, Store, atomic_json
from web_workbench.worker import main as worker_main, publish_report


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.repo = self.base / "repo"
        self.board = self.repo / "boards/fixture"
        self.board.mkdir(parents=True)
        self.home = self.base / "home"
        for role, filename in {
            "schematic_pdf": "schematic.pdf", "bom": "bom.csv", "netlist": "netlist.tel", "eprj2": "board.eprj2",
            "operating_conditions": "conditions.yaml", "design_task_book": "task.yaml",
        }.items():
            (self.board / filename).write_text(role)
        self.config = {"board_name": "测试板", "schematic_pdf": "schematic.pdf", "bom": "bom.csv", "netlist": "netlist.tel", "eprj2": "board.eprj2", "operating_conditions": "conditions.yaml", "design_task_book": "task.yaml"}
        atomic_json(self.board / "materials.json", self.config)
        self.app = create_app(self.home, repository=self.repo, launch_workers=False)
        self.store = self.app.state.store
        self.board_id = self.store.boards()[0]["id"]
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def start(self, **overrides):
        return self.client.post(f"/api/boards/{self.board_id}/runs", json={"mode": "program", "rule_ids": ["P05-A01"], **overrides})

    def fixture_report(self, publish=True):
        run = self.start().json()
        private = self.store.run(run["id"])
        out = Path(private["workspace"]) / "board/review_out"
        out.mkdir(parents=True)
        atomic_json(out / "verdicts.json", {"results": [{"rule_id": "P05-A01", "object_cards": [{"object": "R1"}]}]})
        atomic_json(out / "report_data.json", {"board": "fixture"})
        atomic_json(out / "evidence/P05-A01.json", {"object_cards": [{"object": "R1", "verdict_zh": "否"}]})
        (out / "expert_review.html").write_text("<html>fixture</html>")
        if not publish:
            return run, out, None
        path = publish_report(self.store, private, out)
        self.store.finish(run["id"], "completed", report_dir=str(path), report_url=f"/reports/{run['id']}/")
        return run, out, path

    def test_material_import_copies_repository_board_and_changes_version(self):
        before = inventory(self.store.board(self.board_id))
        response = self.client.post(f"/api/boards/{self.board_id}/materials", data={"role": "bom"}, files={"file": ("new.csv", b"Reference,Value\nR1,10k\n", "text/csv")})
        self.assertEqual(response.status_code, 200, response.text)
        after = response.json()
        self.assertNotEqual(before["version"], after["version"])
        self.assertEqual((self.board / "bom.csv").read_text(), "bom")
        self.assertEqual(json.loads((self.board / "materials.json").read_text()), self.config)
        self.assertTrue(Path(self.store.board(self.board_id)["path"]).is_relative_to(self.home))
        self.assertEqual(after["missing"], [])

    def test_upload_during_active_run_and_wrong_extension_rejected(self):
        response = self.client.post(f"/api/boards/{self.board_id}/materials", data={"role": "bom"}, files={"file": ("bad.exe", b"hello")})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.start().status_code, 202)
        response = self.client.post(f"/api/boards/{self.board_id}/materials", data={"role": "bom"}, files={"file": ("bom.csv", b"hello")})
        self.assertEqual(response.status_code, 409)

    def test_single_board_run_lock_is_transactional(self):
        def create(_):
            try:
                return self.store.create_run(self.board_id, {"mode": "materials"})["id"]
            except Conflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(create, range(6)))
        self.assertEqual(results.count("conflict"), 5)
        self.assertEqual(len(self.store.runs(active=True)), 1)

    def test_idempotent_start_and_different_payload_rejected(self):
        url = f"/api/boards/{self.board_id}/runs"
        payload = {"mode": "program", "rule_ids": ["P05-A01"]}
        a = self.client.post(url, json=payload, headers={"idempotency-key": "one-request"})
        b = self.client.post(url, json=payload, headers={"idempotency-key": "one-request"})
        self.assertEqual(a.status_code, 202, a.text)
        self.assertEqual(b.status_code, 202, b.text)
        self.assertEqual(a.json()["id"], b.json()["id"])
        c = self.client.post(url, json={**payload, "rule_ids": ["P05-A06"]}, headers={"idempotency-key": "one-request"})
        self.assertEqual(c.status_code, 409)

    def test_mode_scope_and_missing_model_are_explicit(self):
        self.assertEqual(self.start(rule_ids=["P11-A01"]).status_code, 422)
        self.assertEqual(self.start(rule_ids=["unknown"]).status_code, 422)
        response = self.start(mode="review", rule_ids=["P11-A01"])
        self.assertEqual(response.status_code, 404)
        self.assertIn("模型", response.text)
        self.assertEqual(self.store.runs(), [])

    def test_profile_secret_is_not_returned_and_run_uses_frozen_profile(self):
        secret = "test-secret-do-not-echo"
        profile = self.client.put("/api/model-profiles", json={"base_url": "http://127.0.0.1:9999/v1", "model": "fixture", "api_key": secret}).json()
        self.assertTrue(profile["has_api_key"])
        self.assertNotIn(secret, json.dumps(profile))
        self.assertNotIn(secret, self.client.get("/api/model-profiles").text)
        run = self.start(mode="review", rule_ids=["P11-A01"], profile_id=profile["id"]).json()
        self.assertNotIn(secret, json.dumps(run))
        frozen = Path(self.store.run(run["id"])["profile_path"])
        self.assertEqual(json.loads(frozen.read_text())["model"], "fixture")
        self.assertEqual(frozen.stat().st_mode & 0o777, 0o600)
        self.client.put("/api/model-profiles", json={"id": profile["id"], "base_url": "http://127.0.0.1:9999/v1", "model": "new"})
        self.assertEqual(json.loads(frozen.read_text())["model"], "fixture")
        self.assertEqual(self.store.profile(profile["id"], private=True)["api_key"], secret)

    def test_profile_unknown_fields_and_invalid_protocol_are_rejected(self):
        payload = {"base_url": "http://127.0.0.1:9999/v1", "model": "fixture"}
        for extra in ({"has_api_key": True}, {"profile_path": "/tmp/foreign.json"},
                      {"provider": "codex-cli"}, {"api_key_env": "INVALID NAME"}, {"max_retries": 6}):
            response = self.client.put("/api/model-profiles", json={**payload, **extra})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.store.profiles(), [])
        response = self.client.put("/api/model-profiles", json={**payload, "stream": True,
            "supports_images": False, "structured_output": "json_object", "token_parameter": "max_completion_tokens"})
        self.assertEqual(response.status_code, 200, response.text)
        profile = self.store.profile(response.json()["id"], private=True)
        self.assertTrue(profile["stream"])
        self.assertFalse(profile["supports_images"])

    def test_context_budget_is_nullable_positive_integer_and_persisted(self):
        base = {"base_url": "http://127.0.0.1:9999/v1", "model": "fixture"}
        for value in (0, -1, True, "8192"):
            response = self.client.put("/api/model-profiles", json={**base, "max_context_tokens": value})
            self.assertEqual(response.status_code, 422, response.text)
        for value in (None, 32768):
            response = self.client.put("/api/model-profiles", json={**base, "max_context_tokens": value})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["max_context_tokens"], value)
            private = self.store.profile(response.json()["id"], private=True)
            self.assertEqual(private["max_context_tokens"], value)

    def test_probe_is_persistent_and_applies_only_to_current_profile_signature(self):
        base = {"base_url": "http://127.0.0.1:9999/v1", "model": "fixture", "max_context_tokens": 32768}
        profile = self.client.put("/api/model-profiles", json=base).json()
        receipt = {"ok": True, "model": "fixture", "tested": {"tools": True, "vision": True},
                   "vision": "tested", "context": {"max_context_tokens": 32768, "status": "configured", "provider_window_verified": False}}
        with patch("agent_review.llm.probe_profile", return_value=receipt):
            response = self.client.post(f"/api/model-profiles/{profile['id']}/test")
        self.assertEqual(response.status_code, 200, response.text)
        probe = response.json()
        self.assertEqual(probe["capabilities"], receipt["tested"])
        self.assertIn("tested_at", probe)
        self.assertIn("profile_signature", probe)
        reopened = Store(self.home, repository=self.repo)
        self.assertEqual(reopened.profile(profile["id"])["probe"], probe)
        private_path = Path(self.store.profile(profile["id"])["profile_path"])
        self.assertNotIn("probe", json.loads(private_path.read_text()))
        self.client.put("/api/model-profiles", json={**base, "id": profile["id"], "name": "renamed"})
        self.assertEqual(self.store.profile(profile["id"])["probe"], probe)
        self.client.put("/api/model-profiles", json={**base, "id": profile["id"], "max_context_tokens": 65536})
        self.assertIsNone(self.store.profile(profile["id"])["probe"])
        visible = self.client.get("/api/model-profiles").json()["profiles"][0]
        self.assertIsNone(visible["probe"])
        self.assertNotIn("credential_hash", visible)

    def test_probe_failure_replaces_old_success_without_exposing_credential(self):
        secret = "fixture-probe-secret"
        profile = self.client.put("/api/model-profiles", json={"base_url": "http://127.0.0.1:9999/v1", "model": "fixture", "api_key": secret}).json()
        with patch("agent_review.llm.probe_profile", return_value={"ok": True, "tested": {"connection": True}}):
            self.assertEqual(self.client.post(f"/api/model-profiles/{profile['id']}/test").status_code, 200)
        with patch("agent_review.llm.probe_profile", side_effect=RuntimeError("fixture failure " + secret)):
            response = self.client.post(f"/api/model-profiles/{profile['id']}/test")
        self.assertEqual(response.status_code, 422)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["capabilities"], {})
        self.assertEqual(response.json()["vision"], "not_tested")
        visible = self.client.get("/api/model-profiles")
        self.assertNotIn(secret, response.text + visible.text)
        self.assertFalse(visible.json()["profiles"][0]["probe"]["ok"])

    def test_probe_is_invalidated_by_credential_rotation_and_uses_env_precedence(self):
        base = {"base_url": "http://127.0.0.1:9999/v1", "model": "fixture", "api_key": "old-fixture-key", "api_key_env": "WORKBENCH_TEST_KEY"}
        with patch.dict(os.environ, {"WORKBENCH_TEST_KEY": "env-fixture-key"}):
            profile = self.client.put("/api/model-profiles", json=base).json()
            with patch("agent_review.llm.probe_profile", return_value={"ok": True, "tested": {"connection": True}}):
                self.assertEqual(self.client.post(f"/api/model-profiles/{profile['id']}/test").status_code, 200)
            self.assertIsNotNone(self.store.profile(profile["id"])["probe"])
            with patch.dict(os.environ, {"WORKBENCH_TEST_KEY": "rotated-env-fixture-key"}):
                self.assertIsNone(self.store.profile(profile["id"])["probe"])
            self.assertIsNotNone(self.store.profile(profile["id"])["probe"])
        # The explicit environment reference no longer resolves to the tested key.
        self.assertIsNone(self.store.profile(profile["id"])["probe"])

    def test_agent_review_rejects_disabled_tools_but_program_prose_can_start(self):
        profile = self.client.put("/api/model-profiles", json={"base_url": "http://127.0.0.1:9999/v1", "model": "fixture", "supports_tools": False}).json()
        response = self.start(mode="review", rule_ids=["P11-A01"], profile_id=profile["id"])
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("工具", response.text)
        self.assertEqual(self.store.runs(), [])
        response = self.start(mode="review", rule_ids=["P05-A01"], profile_id=profile["id"])
        self.assertEqual(response.status_code, 202, response.text)

    def test_review_requires_input_room_beyond_configured_output_budget(self):
        profile = self.client.put("/api/model-profiles", json={"base_url": "http://127.0.0.1:9999/v1", "model": "fixture", "max_tokens": 8192, "max_context_tokens": 8192}).json()
        response = self.start(mode="review", rule_ids=["P05-A01"], profile_id=profile["id"])
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("输入材料", response.text)
        self.assertEqual(self.store.runs(), [])

    def test_late_probe_receipt_cannot_validate_concurrently_changed_settings(self):
        base = {"base_url": "http://127.0.0.1:9999/v1", "model": "fixture"}
        profile = self.client.put("/api/model-profiles", json=base).json()
        def probe(_):
            self.store.save_profile({**base, "id": profile["id"], "model": "changed-during-test"})
            return {"ok": True, "tested": {"connection": True}}
        with patch("agent_review.llm.probe_profile", side_effect=probe):
            response = self.client.post(f"/api/model-profiles/{profile['id']}/test")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.store.profile(profile["id"])["probe"])

    def test_resume_rejects_changed_materials_scope_and_completed_run(self):
        run = self.start(objects=["R1"]).json()
        self.store.finish(run["id"], "stopped")
        response = self.start(objects=["R2"], resume=True, parent_id=run["id"])
        self.assertEqual(response.status_code, 422)
        self.assertIn("范围", response.text)
        (self.board / "bom.csv").write_text("changed source")
        response = self.client.post(f"/api/runs/{run['id']}/resume")
        self.assertEqual(response.status_code, 422)
        self.assertIn("材料", response.text)
        newer = self.start().json()
        self.store.finish(newer["id"], "completed")
        response = self.client.post(f"/api/runs/{newer['id']}/resume")
        self.assertEqual(response.status_code, 422)

    def test_resume_requires_same_model_policy_but_allows_key_rotation(self):
        base = {"base_url": "http://127.0.0.1:9999/v1", "model": "fixture", "api_key": "old-test-key"}
        profile = self.client.put("/api/model-profiles", json=base).json()
        run = self.start(mode="review", rule_ids=["P11-A01"], profile_id=profile["id"]).json()
        self.store.finish(run["id"], "stopped")
        self.client.put("/api/model-profiles", json={**base, "id": profile["id"], "model": "different"})
        response = self.client.post(f"/api/runs/{run['id']}/resume")
        self.assertEqual(response.status_code, 422)
        self.assertIn("策略", response.text)
        self.client.put("/api/model-profiles", json={**base, "id": profile["id"], "api_key": "new-test-key"})
        response = self.client.post(f"/api/runs/{run['id']}/resume")
        self.assertEqual(response.status_code, 202, response.text)
        prior = self.store.run(run["id"])
        current = self.store.run(response.json()["id"])
        self.assertEqual(profile_signature(prior["profile_path"]), profile_signature(current["profile_path"]))
        self.assertEqual(json.loads(Path(current["profile_path"]).read_text())["api_key"], "new-test-key")

    def test_snapshot_is_independent_and_tampered_material_cannot_resume(self):
        cache = self.board / "cache/mpn/part/datasheet.pdf"
        cache.parent.mkdir(parents=True)
        cache.write_bytes(b"temporary source PDF")
        before = inventory(self.store.board(self.board_id))["version"]
        snapshot = snapshot_board(self.board, self.home / "runs/snapshot-test")
        validate_snapshot(snapshot)
        cache.write_bytes(b"new source PDF")
        self.assertNotEqual(inventory(self.store.board(self.board_id))["version"], before)
        self.assertEqual((snapshot / "cache/mpn/part/datasheet.pdf").read_bytes(), b"temporary source PDF")
        validate_snapshot(snapshot)
        (snapshot / "cache/mpn/part/datasheet.pdf").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "快照"):
            validate_snapshot(snapshot)

    def test_object_scope_rejects_unknown_and_preserves_only_requested_objects(self):
        enumeration = {"by_rule": {"P05-A01": ["C1", "C2"], "P11-A01": ["R1"]},
                       "stats": {"judge_pairs": 3}, "rule_ids": ["P05-A01", "P11-A01"]}
        scoped = scope_objects(enumeration, ["C2"])
        self.assertEqual(scoped["by_rule"], {"P05-A01": ["C2"], "P11-A01": []})
        self.assertEqual(scoped["stats"]["judge_pairs"], 1)
        self.assertEqual(enumeration["stats"]["judge_pairs"], 3)
        with self.assertRaisesRegex(ValueError, "不在检查范围"):
            scope_objects(enumeration, ["DOES_NOT_EXIST"])

    def test_stop_blocks_late_completion_and_resume_creates_new_attempt(self):
        run = self.start().json()
        stopping = self.client.post(f"/api/runs/{run['id']}/stop").json()
        self.assertEqual(stopping["status"], "stopping")
        self.store.finish(run["id"], "completed", report_dir="invalid", report_url="invalid")
        stopped = self.store.run(run["id"])
        self.assertEqual(stopped["status"], "stopped")
        self.assertFalse(stopped.get("report_url"))
        self.store.update_run(run["id"], status="running")
        self.assertEqual(self.store.run(run["id"])["status"], "stopped")
        next_run = self.client.post(f"/api/runs/{run['id']}/resume")
        self.assertEqual(next_run.status_code, 202, next_run.text)
        self.assertNotEqual(run["id"], next_run.json()["id"])
        self.assertEqual(next_run.json()["parent_id"], run["id"])

    def test_sse_replays_only_events_after_last_id(self):
        run = self.start().json()
        e = self.store.event(run["id"], "progress", {"completed": 1, "total": 3})
        self.store.finish(run["id"], "completed", summary={"count": 3})
        response = self.client.get(f"/api/runs/{run['id']}/events", headers={"last-event-id": str(e["seq"])})
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: completed", response.text)
        self.assertNotIn("event: progress", response.text)
        self.assertNotIn("event: queued", response.text)

    def test_report_snapshot_is_immutable_and_partial_report_not_published(self):
        run, out, frozen = self.fixture_report()
        original = (frozen / "evidence/P05-A01.json").read_bytes()
        atomic_json(out / "evidence/P05-A01.json", {"changed": True})
        self.assertEqual((frozen / "evidence/P05-A01.json").read_bytes(), original)
        second = self.start().json()
        private = self.store.run(second["id"])
        incomplete = Path(private["workspace"]) / "bad"
        incomplete.mkdir(parents=True)
        atomic_json(incomplete / "verdicts.json", {"results": [{"rule_id": "P05-A01"}]})
        atomic_json(incomplete / "report_data.json", {})
        with self.assertRaises(ValueError):
            publish_report(self.store, private, incomplete)
        self.store.finish(second["id"], "failed", errors=["missing detail"])
        reports = self.client.get(f"/api/boards/{self.board_id}/reports").json()["reports"]
        self.assertEqual([r["id"] for r in reports], [run["id"]])

    def test_publication_requires_referenced_assets_and_keeps_source_assets(self):
        run, out, _ = self.fixture_report(publish=False)
        private = self.store.run(run["id"])
        atomic_json(out / "evidence/P05-A01.json", {"object_cards": [{"object": "R1", "review_detail": {
            "primary": {"figure": "page_evidence/part.png"},
            "resources": {"pages": [{"pdf": "research_assets/source.pdf", "page": 1}]}}}]})
        with self.assertRaisesRegex(ValueError, "引用资源"):
            publish_report(self.store, private, out)
        for relative in ("page_evidence/part.png", "research_assets/source.pdf", "pdfjs/web/viewer.html"):
            path = out / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"temporary resource")
        (out / "model-profile.json").write_text('{"api_key":"test-not-a-real-key"}')
        frozen = publish_report(self.store, private, out)
        self.assertTrue((frozen / "page_evidence/part.png").is_file())
        self.assertTrue((frozen / "research_assets/source.pdf").is_file())
        self.assertFalse((frozen / "model-profile.json").exists())

    def test_publication_rejects_symlinks_and_escaping_asset_paths(self):
        run, out, _ = self.fixture_report(publish=False)
        private = self.store.run(run["id"])
        (out / "assets").mkdir()
        outside = self.base / "outside.txt"
        outside.write_text("private fixture")
        (out / "assets/private.txt").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            publish_report(self.store, private, out)
        (out / "assets/private.txt").unlink()
        atomic_json(out / "evidence/P05-A01.json", {"object_cards": [{"object": "R1", "review_detail": {
            "primary": {"figure": "../outside.txt"}}}]})
        with self.assertRaisesRegex(ValueError, "引用资源"):
            publish_report(self.store, private, out)

    def test_research_sources_and_raw_materials_are_frozen_without_proposal_context(self):
        run, out, _ = self.fixture_report(publish=False)
        private = self.store.run(run["id"])
        board = out.parent
        sources = {
            "review_out/evidence_proposals/proposal-1/assets/source.txt": b"manufacturer source quotation",
            "material_inputs/bom/bom.csv": b"Reference,Value\nR1,47k\n",
            "review_out/board_kg.json": b'{"components":{"R1":{"value":"47k"}}}',
            "cache/mpn/part/pintable.json": b'{"params":{"rated_voltage_v":50}}',
        }
        for original, content in sources.items():
            file = board / original
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
        atomic_json(board / "materials.json", {"bom": "material_inputs/bom/bom.csv"})
        proposal = out / "evidence_proposals/proposal-1"
        (proposal / "run.json").write_text('{"messages":["private test transcript"]}')
        (proposal / "research.context.json").write_text('{"prompt":"private test context"}')
        atomic_json(out / "evidence_store/current.json", {"_provenance": {"evidence_hashes": {
            path: hashlib.sha256(content).hexdigest() for path, content in sources.items()}}})
        frozen = publish_report(self.store, private, out)
        self.store.finish(run["id"], "completed", report_dir=str(frozen))
        result = self.client.get(f"/api/reports/{run['id']}/data").json()
        for original, content in sources.items():
            asset = result["source_assets"][original]
            self.assertEqual(asset, result["source_assets"][str(board / original)])
            response = self.client.get(asset["url"])
            self.assertEqual(response.status_code, 200, original)
            self.assertEqual(response.content, content)
            self.assertEqual(asset["sha256"], hashlib.sha256(content).hexdigest())
            (board / original).write_bytes(b"changed after publication")
            self.assertEqual(self.client.get(asset["url"]).content, content)
        self.assertFalse((frozen / "evidence_proposals/proposal-1/run.json").exists())
        self.assertFalse((frozen / "evidence_proposals/proposal-1/research.context.json").exists())
        self.assertEqual(result["source_assets"]["materials.json"]["path"], "source_inputs/materials.json")

    def test_only_referenced_material_workbench_and_rule_files_are_downloadable(self):
        run, out, _ = self.fixture_report(publish=False)
        private = self.store.run(run["id"])
        workbench = out / "agent_jobs/P05-A01/R1.context.json"
        atomic_json(workbench, {"identity": {"ref": "R1", "value": "47k"}})
        (workbench.parent / "R1.run.json").write_text('{"messages":["private transcript"]}')
        original = "review_out/agent_jobs/P05-A01/R1.context.json"
        atomic_json(out / "evidence/P05-A01.json", {"object_cards": [{"object": "R1", "evidence_refs": [
            {"path": original}, {"path": "rules/product_center_15.json"}]}]})
        frozen = publish_report(self.store, private, out)
        self.store.finish(run["id"], "completed", report_dir=str(frozen))
        data = self.client.get(f"/api/reports/{run['id']}/data").json()
        self.assertEqual(self.client.get(data["source_assets"][original]["url"]).content, workbench.read_bytes())
        self.assertEqual(self.client.get(data["source_assets"]["rules/product_center_15.json"]["url"]).status_code, 200)
        self.assertFalse((frozen / "agent_jobs/P05-A01/R1.run.json").exists())

    def test_publication_rejects_missing_or_changed_sealed_source(self):
        run, out, _ = self.fixture_report(publish=False)
        private = self.store.run(run["id"])
        original = "review_out/evidence_proposals/proposal-1/assets/source.txt"
        atomic_json(out / "evidence_store/current.json", {"_provenance": {"evidence_hashes": {original: hashlib.sha256(b"expected source").hexdigest()}}})
        with self.assertRaisesRegex(ValueError, "缺少已封存研究来源"):
            publish_report(self.store, private, out)
        source = out.parent / original
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"changed source")
        with self.assertRaisesRegex(ValueError, "字节已变化"):
            publish_report(self.store, private, out)

    def test_feedback_bound_to_published_run_object(self):
        run, _, _ = self.fixture_report()
        response = self.client.put(f"/api/reviews/{run['id']}/P05-A01/R1/feedback", json={"mark": "disputed", "note": "待补证"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.put(f"/api/reviews/{run['id']}/P05-A01/R2/feedback", json={"mark": "confirmed"}).status_code, 404)
        data = self.client.get(f"/api/reports/{run['id']}/data").json()
        self.assertEqual(data["evidence"]["P05-A01"]["object_cards"][0]["verdict_zh"], "否")
        feedback = self.client.get(f"/api/reviews/{run['id']}/feedback").json()["feedback"]
        self.assertEqual(feedback["P05-A01::R1"]["mark"], "disputed")

    def test_same_origin_and_path_boundaries(self):
        response = self.client.post("/api/boards", json={"name": "x"}, headers={"origin": "https://external.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/api/boards", headers={"host": "external.example"}).status_code, 403)
        run, _, _ = self.fixture_report()
        self.assertEqual(self.client.get(f"/reports/{run['id']}/%2e%2e/%2e%2e/workbench.sqlite3").status_code, 404)
        self.assertEqual(self.client.get("/api/no-such-endpoint").status_code, 404)

    def test_reconcile_only_verified_dead_process(self):
        run = self.start().json()
        self.store.update_run(run["id"], pid=os.getpid(), process_started=0)
        with self.store.connection(write=True) as c:
            c.execute("UPDATE runs SET created_at='2020-01-01T00:00:00+00:00' WHERE id=?", (run["id"],))
        with patch("web_workbench.runner.process_alive", return_value=None):
            reconcile(self.store)
        self.assertEqual(self.store.run(run["id"])["status"], "queued")
        with patch("web_workbench.runner.process_alive", return_value=True):
            reconcile(self.store)
        self.assertEqual(self.store.run(run["id"])["status"], "queued")
        with patch("web_workbench.runner.process_alive", return_value=False):
            reconcile(self.store)
        self.assertEqual(self.store.run(run["id"])["status"], "interrupted")

    def test_worker_preserves_original_error_when_api_key_env_is_null(self):
        run = self.start().json()
        private = self.store.run(run["id"])
        profile = Path(private["workspace"]) / "model-profile.json"
        atomic_json(profile, {"base_url": "http://127.0.0.1:9999/v1", "model": "fixture", "api_key_env": None, "api_key": None}, private=True)
        self.store.update_run(run["id"], profile_path=str(profile))
        with patch.dict(os.environ, {"SCHEMATIC_WORKBENCH_HOME": str(self.home)}), \
                patch("web_workbench.execution.execute_run", side_effect=ValueError("original execution failure")), \
                patch("web_workbench.worker.signal.signal"):
            result = worker_main(run["id"])
        finished = self.store.run(run["id"])
        self.assertEqual(result, 1)
        self.assertEqual(finished["status"], "failed")
        self.assertEqual(finished["errors"], ["original execution failure"])

    def test_worker_redacts_env_and_inline_credentials_on_failure(self):
        run = self.start().json()
        private = self.store.run(run["id"])
        profile = Path(private["workspace"]) / "model-profile.json"
        atomic_json(profile, {"base_url": "http://127.0.0.1:9999/v1", "model": "fixture", "api_key_env": "WORKBENCH_TEST_KEY", "api_key": "inline-fixture-key"}, private=True)
        self.store.update_run(run["id"], profile_path=str(profile))
        with patch.dict(os.environ, {"SCHEMATIC_WORKBENCH_HOME": str(self.home), "WORKBENCH_TEST_KEY": "env-fixture-key"}), \
                patch("web_workbench.execution.execute_run", side_effect=ValueError("env-fixture-key inline-fixture-key")), \
                patch("web_workbench.worker.signal.signal"), patch("builtins.print"):
            self.assertEqual(worker_main(run["id"]), 1)
        self.assertEqual(self.store.run(run["id"])["errors"], ["[redacted] [redacted]"])


if __name__ == "__main__":
    unittest.main()
