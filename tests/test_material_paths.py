import errno
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kernel.checkers import all15
from kernel.report_workbench import _resolve_schematic_pdf
from kernel.util import is_file_safe

# 旧产物里的 NAS 绝对路径：换机后连不上，Windows 的 is_file() 抛 OSError 而非返回 False。
UNREACHABLE = r"\\unreachable.invalid\share\SCH.pdf"
_REAL_STAT = Path.stat


def _stat(self, *args, **kwargs):
    if str(self).startswith(r"\\unreachable"):
        raise OSError(errno.EIO, "network name no longer available")
    return _REAL_STAT(self, *args, **kwargs)


def _board(tmp, *, raw_pdf=True, copy_pdf=False):
    board = Path(tmp) / "boards" / "B1"
    out = board / "review_out"
    out.mkdir(parents=True)
    (board / "materials.json").write_text(json.dumps({"schematic_pdf": "../../materials/SCH.pdf"}), encoding="utf-8")
    (out / "sch_index.json").write_text(json.dumps({"pdf": UNREACHABLE}), encoding="utf-8")
    (out / "materials.json").write_text(json.dumps({"schematic_pdf": UNREACHABLE}), encoding="utf-8")
    raw = Path(tmp) / "materials" / "SCH.pdf"
    if raw_pdf:
        raw.parent.mkdir()
        raw.write_bytes(b"%PDF-1.4")
    copy = out / "sch" / "schematic.pdf"
    if copy_pdf:
        copy.parent.mkdir()
        copy.write_bytes(b"%PDF-1.4")
    return board, raw, copy


@mock.patch.object(Path, "stat", _stat)
class UnreachableMaterialPathTests(unittest.TestCase):
    def test_unreachable_path_is_not_a_file(self):
        self.assertFalse(is_file_safe(UNREACHABLE))

    def test_report_falls_back_to_board_config_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            board, raw, _ = _board(tmp, copy_pdf=True)
            pdf = _resolve_schematic_pdf(board / "review_out")
            self.assertEqual(pdf.resolve(), raw.resolve())

    def test_report_falls_back_to_review_out_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            board, _, copy = _board(tmp, raw_pdf=False, copy_pdf=True)
            pdf = _resolve_schematic_pdf(board / "review_out")
            self.assertEqual(pdf.resolve(), copy.resolve())

    def test_checker_falls_back_to_board_config_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, raw, _ = _board(tmp)
            kg = {"board": "B1", "sources": {"pdf": UNREACHABLE}}
            cache = {}
            with mock.patch.object(all15, "ROOT", Path(tmp)):
                self.assertEqual(all15._kg_schematic_pdf(kg, cache).resolve(), raw.resolve())
                with mock.patch.object(all15, "board_schematic_pdf", side_effect=AssertionError):
                    all15._kg_schematic_pdf(kg, cache)

    def test_checker_without_board_config_skips_pdf_tokens(self):
        kg = {"board": "no-such-board", "sources": {"pdf": UNREACHABLE}}
        c = {"location": [{"pdf_page": 1}]}
        self.assertEqual(all15._pdf_ann_tokens(kg, "R1", c, {}), [])


if __name__ == "__main__":
    unittest.main()
