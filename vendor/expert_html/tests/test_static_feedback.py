import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class StaticFeedbackTests(unittest.TestCase):
    def test_generated_report_has_no_legacy_feedback_network_or_storage(self):
        vendor = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('static_feedback_builder', vendor / 'build_expert_html.py')
        builder = importlib.util.module_from_spec(spec)
        with patch.object(sys, 'path', [str(vendor), *sys.path]):
            spec.loader.exec_module(builder)
        item = {'rule_id': 'TEST-RULE', 'name': '展示回归检查', 'category': '测试'}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            board = {'name': '临时展示测试', 'input_dir': directory, 'files': []}
            with patch.object(builder, 'load_product_items', return_value=[item]), \
                    patch.object(builder, 'board_from_facts', return_value=board), \
                    patch.object(builder, 'load_workbench', return_value={}), \
                    patch.object(builder, 'install_pdfjs', return_value=False):
                report = builder.build_html(output)
        self.assertIn('此静态报告不保存批注', report)
        self.assertIn('选择本批次和对象后保存复核状态与批注', report)
        for obsolete in ('/api/feedback', 'expert-html-feedback-v2', 'saveEx(', 'exportFeedback(', 'restoreOne(', 'startFeedbackSync('):
            self.assertNotIn(obsolete, report)
        self.assertNotIn('name="ex-TEST-RULE"', report)
        self.assertNotIn('id="tx-TEST-RULE"', report)


if __name__ == '__main__':
    unittest.main()
