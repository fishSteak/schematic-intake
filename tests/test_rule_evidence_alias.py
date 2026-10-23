import json,tempfile,unittest
from pathlib import Path
from agent_review.review_contract import rule_evidence_base, evidence_hashes

class RuleAliasTests(unittest.TestCase):
    def test_only_source_identical_configured_alias_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            board=Path(tmp);(board/'material_inputs').mkdir()
            original=board/'configured.json';original.write_text('{"rule":"source"}',encoding='utf-8')
            (board/'materials.json').write_text(json.dumps({'rules':'configured.json'}),encoding='utf-8')
            alias=board/'material_inputs/rules.json';alias.write_bytes(original.read_bytes())
            self.assertEqual(rule_evidence_base(board,'material_inputs/rules.json'),(board.resolve(),True))
            self.assertIn('material_inputs/rules.json',evidence_hashes({'evidence':[{'kind':'rule','path':'material_inputs/rules.json'}]},board))
            alias.write_text('{"rule":"different"}',encoding='utf-8')
            self.assertFalse(rule_evidence_base(board,'material_inputs/rules.json')[1])
            self.assertFalse(rule_evidence_base(board,'../outside.json')[1])

if __name__=='__main__':unittest.main()
