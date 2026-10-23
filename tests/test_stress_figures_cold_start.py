import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from agent_review import stress_figures
from agent_review.detail_view import basis_source_hash
from agent_review.stages import aggregate_stage
from tests.test_main_analysis import example


class ColdDrawingTests(unittest.TestCase):
    def board(self, root, *, pdf_kind="valid", text_only=False):
        board = Path(root) / "board"
        out = board / "review_out"
        jobs = out / "agent_jobs" / "P11-A01"
        jobs.mkdir(parents=True)
        source = Path(root) / "material_inputs" / "source.pdf"
        source.parent.mkdir()
        if pdf_kind == "valid":
            with fitz.open() as doc:
                page = doc.new_page(width=300, height=200)
                page.insert_text((80, 90), "X1")
                page.insert_text((110, 95), "ORIGINAL")
                page.draw_line((50, 100), (200, 100), color=(0, .6, 0))
                doc.save(source)
        elif pdf_kind == "corrupt":
            source.write_bytes(b"%PDF-1.7\nnot a readable PDF")
        elif pdf_kind == "encrypted":
            with fitz.open() as doc:
                doc.new_page()
                doc.save(source, encryption=fitz.PDF_ENCRYPT_AES_256,
                         owner_pw="test-owner", user_pw="test-reader")
        (board / "materials.json").write_text(json.dumps({"schematic_pdf": "../material_inputs/source.pdf"}))
        (out / "board_kg.json").write_text("{}")
        enum = {"rule_ids": ["P11-A01"], "by_rule": {"P11-A01": ["X1"]}}
        (out / "agent_enumeration.json").write_text(json.dumps(enum))
        card = example()
        if text_only:
            card = {"object": "X1", "rule_id": "P11-A01", "verdict_zh": "待确认", "states": []}
        (jobs / "X1.json").write_text(json.dumps(card))
        return board, source, card, enum

    def test_aggregate_renders_from_raw_pdf_before_report_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            board, source, card, enum = self.board(tmp)
            out = board / "review_out"
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertFalse((out / "sch").exists())
            self.assertFalse((out / "stress_paths").exists())
            diagram = out / "stress_paths/P11-A01/X1/envelope/flow.png"

            def aggregate(*args, **kwargs):
                # These are real renderer outputs, already present when the
                # report aggregation begins; no model or report fixture writes them.
                self.assertEqual(diagram.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
                meta = json.loads((diagram.parent / "meta.json").read_text())
                self.assertEqual(meta["basis_ref"], "/decision_basis")
                self.assertEqual(meta["basis_state_index"], 1)
                self.assertEqual(meta["basis_source_hash"], basis_source_hash(card))
                self.assertTrue(meta["relation_diagram"])
                self.assertTrue(meta["pages"])
                self.assertTrue(all(not page["arrows_enabled"] for page in meta["pages"]))
                return {"verdicts": {}, "findings": []}

            with patch.object(aggregate_stage, "aggregate", side_effect=aggregate) as aggregate_call, \
                    patch.object(aggregate_stage, "build_report_data", return_value={}), \
                    patch.object(aggregate_stage, "write_expert_deliverables", return_value={}):
                aggregate_stage.run(board, enum, generate_prose=False,
                                    figure_rule_ids=["P11-A01"], figure_objects=["X1"])
            aggregate_call.assert_called_once()
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
            self.assertEqual((out / "sch/schematic.pdf").read_bytes(), source.read_bytes())
            self.assertEqual(json.loads((out / "agent_jobs/P11-A01/X1.json").read_text()), card)
            # A warm rerun keeps the completed drawing cache.
            with patch.object(stress_figures, "render_spec", side_effect=AssertionError("unexpected redraw")):
                self.assertEqual(stress_figures.run(board)["drawn"], 1)

    def test_missing_corrupt_and_encrypted_sources_fail_before_drawing(self):
        for kind, error in [("missing", FileNotFoundError), ("corrupt", ValueError), ("encrypted", ValueError)]:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                board, source, _, _ = self.board(tmp, pdf_kind=kind)
                before = source.read_bytes() if source.exists() else None
                with self.assertRaisesRegex(error, "原理图 PDF"):
                    stress_figures.run(board)
                self.assertFalse((board / "review_out/stress_paths").exists())
                self.assertFalse((board / "review_out/sch/schematic.pdf").exists())
                self.assertEqual(source.read_bytes() if source.exists() else None, before)

    def test_copy_failure_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            board, _, _, _ = self.board(tmp)
            with patch("kernel.report_workbench.ensure_schematic_pdf", return_value=False):
                with self.assertRaisesRegex(OSError, "报告副本准备失败"):
                    stress_figures.run(board)

    def test_calculation_marker_preserves_exact_bound_in_milliwatts(self):
        with tempfile.TemporaryDirectory() as tmp:
            board, source, _, _ = self.board(tmp)
            pdf, _ = stress_figures._prepare_schematic_pdf(board / "review_out")
            source_before = source.read_bytes()
            location = {"pageId": "page1", "pdf_page": 1, "x": 80, "y": 100}
            (board / "review_out/jlc_project.json").write_text(json.dumps({
                "instances": [{"ref": "X1", "locations": [location]}]}))
            kg = {"components": {"X1": {"location": [location]}}}
            cases = [
                ("upper_bound", .012543, "≤", "12.543"),
                ("lower_bound", .012547, "≥", "12.547"),
                ("upper_bound", 1e-10, "≤", "0.0000001"),
                ("lower_bound", .02, "≥", "20"),
                ("upper_bound", 1, "≤", "1000"),
                ("upper_bound", 0, "≤", "0"),
            ]
            for kind, power, operator, expected in cases:
                with self.subTest(kind=kind, power=power):
                    state = {"P": power, "stress_kind": kind,
                             "formula_lines": [{"expr": f"P {operator} {power} W", "src": "source.pdf"}],
                             "figure": {"pages": [{"pdf_page": 1}]}}
                    before = json.dumps(state, sort_keys=True)
                    with patch("agent_review.schematic_routes.calibration", return_value=lambda x, y: (x, y)), \
                            patch("agent_review.schematic_routes.route_branches", return_value=([], [], [])):
                        spec = stress_figures._spec_from_verified_figure(
                            pdf, kg, {"verdict_zh": "待确认"}, rule_id="P11-A01", obj="X1", state=state)
                    markers = [m for m in spec["pages"][0]["markers"] if m.get("kind") == "calculation"]
                    self.assertEqual(len(markers), 1)
                    label = "功耗上界" if kind == "upper_bound" else "功耗下界"
                    self.assertEqual(markers[0]["title"], f"X1 {label} {operator} {expected} mW")
                    self.assertEqual(json.dumps(state, sort_keys=True), before)
            self.assertEqual(source.read_bytes(), source_before)

    def alternate_pdf(self, data):
        # Change a visible label without changing stream length or PDF offsets.
        old = b"4f524947494e414c"
        new = b"5245504c41434544"
        self.assertIn(old, data)
        changed = data.replace(old, new)
        self.assertEqual(len(changed), len(data))
        with fitz.open(stream=changed, filetype="pdf") as doc:
            self.assertIn("REPLACED", doc[0].get_text())
        return changed

    def test_same_size_newer_copy_is_replaced_by_exact_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            board, source, _, _ = self.board(tmp)
            original = source.read_bytes()
            copied = board / "review_out/sch/schematic.pdf"
            copied.parent.mkdir()
            copied.write_bytes(self.alternate_pdf(original))
            os.utime(source, ns=(1_000_000_000, 1_000_000_000))
            os.utime(copied, ns=(2_000_000_000, 2_000_000_000))
            prepared, digest = stress_figures._prepare_schematic_pdf(board / "review_out")
            self.assertEqual(prepared, copied)
            self.assertEqual(copied.read_bytes(), original)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(digest, hashlib.sha256(original).hexdigest())

    def test_same_size_and_mtime_source_change_redraws_using_new_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            board, source, _, _ = self.board(tmp)
            self.assertEqual(stress_figures.run(board)["drawn"], 1)
            dest = board / "review_out/stress_paths/P11-A01/X1/envelope"
            first_meta = json.loads((dest / "meta.json").read_text())
            first_png = (dest / "flow.png").read_bytes()
            original_stat = source.stat()
            changed = self.alternate_pdf(source.read_bytes())
            source.write_bytes(changed)
            os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            self.assertEqual(source.stat().st_mtime_ns, original_stat.st_mtime_ns)
            self.assertEqual(source.stat().st_size, original_stat.st_size)
            with patch.object(stress_figures, "render_spec", wraps=stress_figures.render_spec) as render:
                self.assertEqual(stress_figures.run(board)["drawn"], 1)
            render.assert_called_once()
            second_meta = json.loads((dest / "meta.json").read_text())
            self.assertNotEqual(second_meta["fingerprint"], first_meta["fingerprint"])
            self.assertNotEqual(hashlib.sha256((dest / "flow.png").read_bytes()).hexdigest(),
                                hashlib.sha256(first_png).hexdigest())
            self.assertEqual(second_meta["source_pdf_sha256"], hashlib.sha256(changed).hexdigest())
            self.assertEqual(source.read_bytes(), changed)
            self.assertEqual((board / "review_out/sch/schematic.pdf").read_bytes(), changed)

    def test_hash_mismatch_copy_failure_or_ineffective_copy_cannot_draw(self):
        for side_effect in (OSError("test copy failure"), None):
            with self.subTest(side_effect=side_effect), tempfile.TemporaryDirectory() as tmp:
                board, source, _, _ = self.board(tmp)
                original = source.read_bytes()
                copied = board / "review_out/sch/schematic.pdf"
                copied.parent.mkdir()
                copied.write_bytes(self.alternate_pdf(original))
                # A newer equal-sized copy takes the public helper's fast path.
                newer = source.stat().st_mtime_ns + 1_000_000_000
                os.utime(copied, ns=(newer, newer))
                with patch("shutil.copy2", side_effect=side_effect):
                    with self.assertRaisesRegex(OSError, "内容不一致"):
                        stress_figures.run(board)
                self.assertEqual(source.read_bytes(), original)
                self.assertFalse((board / "review_out/stress_paths").exists())

    def test_text_only_and_excluded_objects_do_not_require_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            board, _, _, _ = self.board(tmp, pdf_kind="missing", text_only=True)
            self.assertEqual(stress_figures.run(board)["drawn"], 0)
            self.assertFalse((board / "review_out/sch").exists())
        with tempfile.TemporaryDirectory() as tmp:
            board, _, _, _ = self.board(tmp, pdf_kind="missing")
            self.assertEqual(stress_figures.run(board, objects=["OTHER"])["drawn"], 0)
            self.assertFalse((board / "review_out/sch").exists())


if __name__ == "__main__":
    unittest.main()
