import json
import tempfile
import unittest
from pathlib import Path
from agent_review.criteria import criteria_for_rule


class CriteriaTests(unittest.TestCase):
    def test_exact_definition_is_preserved_and_traceable(self):
        item = {'rule_id': 'EXAMPLE', 'name': 'Example', 'check_steps': ['Required scope'],
                'rule_detail': {'object_scope': 'Original scope', 'basis': 'Explicit exception'}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'rules').mkdir()
            p = root / 'rules/xlsx_detail_15.json'
            p.write_text(json.dumps({'items': [item]}), encoding='utf-8')
            snapshot = criteria_for_rule('EXAMPLE', root=root)
            self.assertEqual(snapshot['rule'], item)
            old_hash = snapshot['source']['sha256']
            p.write_text(json.dumps({'items': [dict(item, name='Changed')]}), encoding='utf-8')
            self.assertNotEqual(criteria_for_rule('EXAMPLE', root=root)['source']['sha256'], old_hash)

    def test_missing_or_ambiguous_scope_is_not_silently_replaced_with_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'rules').mkdir()
            p = root / 'rules/xlsx_detail_15.json'
            for items in [[], [{'rule_id':'X'}, {'rule_id':'X'}]]:
                p.write_text(json.dumps({'items':items}), encoding='utf-8')
                with self.assertRaises(ValueError): criteria_for_rule('X', root=root)

    def test_real_capacitor_scope_contains_dynamic_requirement(self):
        criterion = criteria_for_rule('P11-A03')['rule']
        self.assertIn('纹波', criterion['rule_detail']['materials_params'])
        self.assertIn('规定瞬态', criterion['check_process_xlsx'])


if __name__ == '__main__': unittest.main()
