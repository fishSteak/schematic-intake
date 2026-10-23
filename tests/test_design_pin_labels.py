import json
import tempfile
import unittest
from pathlib import Path
from agent_review.design_pin_labels import design_pin_labels


class DesignPinLabelTests(unittest.TestCase):
    def test_original_aliases_and_unconnected_pins_survive_scope_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            pins = [{'object':'X.1','pinName':'SOURCE','net':'N','location':{'pinId':'a'}},
                    {'object':'X.3','pinName':'MID','net':None,'location':{'pinId':'b'}}]
            (out/'jlc_project.json').write_text(json.dumps({'instances':[
                {'ref':'X','pins':pins},{'ref':'Y','pins':[{'object':'Y.1','pinName':'OTHER'}]}]}),encoding='utf-8')
            labels = design_pin_labels(out, ['X'])
            self.assertEqual(set(labels), {'X'})
            self.assertEqual(labels['X']['pins'], pins)
            self.assertEqual(labels['X']['source']['path'], 'review_out/jlc_project.json')
            self.assertEqual(len(labels['X']['source']['sha256']),64)

    def test_missing_project_does_not_invent_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(design_pin_labels(Path(tmp), ['X']), {})


if __name__=='__main__': unittest.main()
