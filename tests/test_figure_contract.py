import unittest,copy
from agent_review.figure_contract import normalize

class FigureContractTests(unittest.TestCase):
    def test_explicit_paths_and_labels_are_preserved_without_inference(self):
        f={'pages':[{'paths':[{'path':['U9.1','NET'],'description':'已给定支路'}],'markers':[{'on':'U9.1','text':'给定标注'}]}]};before=copy.deepcopy(f)
        r=normalize(f)
        self.assertEqual(f,before);self.assertEqual(r['pages'][0]['paths'],[['U9.1','NET']])
        self.assertEqual(r['pages'][0]['markers'][0]['title'],'给定标注')
        self.assertNotIn('through_body',r['pages'][0])
    def test_unknown_shapes_are_not_guessed_or_removed(self):
        f={'pages':[{'paths':[{'tokens':['A','B']}]}]}
        self.assertEqual(normalize(f)['pages'][0]['paths'],f['pages'][0]['paths'])
    def test_unanchored_note_does_not_invent_a_marker_location(self):
        r=normalize({'pages':[{'markers':['方向尚未闭合']} ]})
        self.assertEqual(r['pages'][0]['markers'],[])
        self.assertEqual(r['pages'][0]['_unanchored_notes'],['方向尚未闭合'])
    def test_legacy_label_is_an_explicit_state_title(self):
        from agent_review.stress_figures import _states_of
        source={'states':[{'label':'已给出的释放工况','role':'primary'}]}
        self.assertEqual(_states_of(source)[0]['name'],'已给出的释放工况')
        self.assertNotIn('name',source['states'][0])

if __name__=='__main__':unittest.main()
