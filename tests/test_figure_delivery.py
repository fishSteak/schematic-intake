import unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
import fitz
from agent_review.stress_path_render import circuit_crop_box
from agent_review.presentation import primary_state_index
from agent_review.detail_view import explicit_primary


class FigureDeliveryTests(unittest.TestCase):
    def test_diagram_field_aliases_preserve_model_notes_without_inference(self):
        from agent_review.figure_contract import normalize,validate_shape
        source={'pages':[{'pdf_page':1,'paths':[{'nodes':['N','X.1'],'label':'模型支路说明'}],
                          'markers':[{'ref':'X.1','label':'模型标注'}],'notes':'完整的一条说明'}]}
        normalized=normalize(source)
        self.assertEqual(normalized['pages'][0]['paths'],[['N','X.1']])
        self.assertEqual(normalized['pages'][0]['markers'][0]['on'],'X.1')
        self.assertEqual(normalized['pages'][0]['notes'],['完整的一条说明'])
        self.assertEqual(normalized['pages'][0]['_branch_descriptions'],['模型支路说明'])
        self.assertIn('nodes',source['pages'][0]['paths'][0])
        self.assertFalse(validate_shape(source))
        self.assertTrue(validate_shape({'pages':[{'pdf_page':1,'paths':[{'unknown':['N']}]}]}))

    def test_crop_keeps_all_disconnected_circuit_regions_and_axis_lines(self):
        with fitz.open() as doc:
            page=doc.new_page(width=900,height=600)
            page.draw_rect(fitz.Rect(5,5,895,595),color=(1,0,0))
            page.insert_text((650,570),'EDA TITLE BLOCK',color=(0,0,1))
            page.draw_line((60,100),(160,100),color=(0,.6,0))
            page.draw_line((760,300),(760,420),color=(0,.6,0))
            page.insert_text((762,310),'C99',color=(0,0,1))
            box=circuit_crop_box(page)
            self.assertLessEqual(box[0],60);self.assertGreaterEqual(box[2],780)
            self.assertLessEqual(box[1],100);self.assertGreaterEqual(box[3],420)
            self.assertLess(box[3],550)

    def test_unknown_monochrome_layout_is_preserved_rather_than_guessed(self):
        with fitz.open() as doc:
            p=doc.new_page(width=400,height=900)
            p.draw_line((10,20),(390,880),color=(0,0,0))
            self.assertEqual(circuit_crop_box(p),tuple(p.rect))
    def test_padding_does_not_reintroduce_nearby_sheet_border(self):
        with fitz.open() as doc:
            p=doc.new_page(width=900,height=600)
            p.draw_line((100,100),(860,400),color=(0,.6,0))
            p.draw_line((870,5),(870,595),color=(1,0,0))
            p.insert_text((871,350),'B',color=(1,0,0))
            box=circuit_crop_box(p)
            self.assertGreaterEqual(box[2],860)
            self.assertLess(box[2],870)
    def test_collinear_circuit_is_not_lost_when_a_label_is_added(self):
        with fitz.open() as doc:
            p=doc.new_page(width=900,height=600)
            p.draw_line((60,100),(800,100),color=(0,.6,0))
            p.insert_text((62,95),'R1',color=(0,0,1))
            box=circuit_crop_box(p)
            self.assertLessEqual(box[0],60);self.assertGreaterEqual(box[2],800)

    def test_display_and_drawing_choose_the_same_explicit_candidate(self):
        states=[{'name':'local static maximum','role':'worst'},
                {'name':'conditional extreme','role':'worst_candidate'}]
        self.assertEqual(primary_state_index(states),1)
        from agent_review.detail_view import scenario_rows
        card={'states':states}
        self.assertEqual(explicit_primary(card),scenario_rows(card)[1]['id'])

    def test_ambiguous_or_unlabelled_states_are_not_ranked_by_numbers(self):
        self.assertIsNone(primary_state_index([{'P':1},{'P':9}]))
        self.assertIsNone(primary_state_index([{'role':'primary'},{'role':'primary'}]))
        self.assertEqual(primary_state_index([{'role':'primary'},{'role':'worst_candidate'}]),0)
    def test_non_power_check_keeps_its_generated_primary_diagram(self):
        from agent_review.detail_view import build
        card={'rule_id':'P21-A01','object':'SIGNAL','verdict_zh':'是','analysis_contract':'analysis.v1',
              'analysis_steps':[{'text':'核对SIGNAL驱动与接收端。','assumption_refs':[]}], 'key_assumptions':[],
              'states':[{'label':'接收端关系核查','role':'primary','route_summary':'实际驱动接到SIGNAL接收端。','figure':{'pages':[{'pdf_page':1}]}}]}
        with tempfile.TemporaryDirectory() as tmp,patch('agent_review.stress_figures.lookup',return_value={'status':'ok','flow':'stress_paths/result.png','pages':[{'src':'stress_paths/page.png','label':'第1页'}]}):
            view=build(card,Path(tmp))
            self.assertEqual(view['primary']['figure'],'stress_paths/result.png')
            self.assertEqual(view['primary']['title'],'接收端关系核查')
            self.assertEqual(len(view['primary']['figure_pages']),1)
    def test_explicit_electrical_diagram_is_not_limited_by_rule_allowlist(self):
        from agent_review import stress_figures
        with tempfile.TemporaryDirectory() as tmp:
            b=Path(tmp);o=b/'review_out';(o/'sch').mkdir(parents=True)
            with fitz.open() as d:d.new_page();d.save(o/'sch/schematic.pdf')
            (o/'board_kg.json').write_text('{}')
            (o/'agent_enumeration.json').write_text(json.dumps({'rule_ids':['P12-A08'],'by_rule':{'P12-A08':['O1']}}))
            card={'object':'O1','rule_id':'P12-A08','verdict_zh':'待确认','states':[{'name':'反接关系','role':'primary','figure':{'pages':[{'pdf_page':1}]}}]}
            spec={'object':'O1','rule_id':'P12-A08','pages':[{'pdf_page':1,'routing_complete':True,'routing_issues':[]}]}
            def render(board,spec,dest):
                dest.mkdir(parents=True);(dest/'meta.json').write_text('{"pages":[]}');(dest/'flow.png').write_bytes(b'preview')
            with patch.object(stress_figures,'load_judge_cards',return_value=[card]),patch.object(stress_figures,'_spec_from_verified_figure',return_value=spec),patch.object(stress_figures,'render_spec',side_effect=render):
                self.assertEqual(stress_figures.run(b)['drawn'],1)

    def test_source_index_wins_over_another_cases_numeric_id(self):
        from agent_review.stress_figures import lookup
        with tempfile.TemporaryDirectory() as tmp:
            o=Path(tmp);(o/'stress_paths').mkdir()
            (o/'stress_paths/index.json').write_text(json.dumps({'rules':{'R':{'O':{
                '1':{'state_index':0,'status':'text_only'},
                'main':{'state_index':1,'status':'ok','flow':'stress_paths/main.png'}
            }}}}))
            self.assertEqual(lookup(o,'R','O',1)['flow'],'stress_paths/main.png')
            self.assertIsNone(lookup(o,'R','O',2))

    def test_conflicting_operating_directions_are_reported_not_silently_hidden(self):
        from agent_review.stress_figures import _spec_from_verified_figure
        with tempfile.TemporaryDirectory() as tmp:
            pdf=Path(tmp)/'schematic.pdf'
            with fitz.open() as d:d.new_page();d.save(pdf)
            def route(page,raw,instances,comps,locate,audit):
                audit.update(arrow_polylines=[],ambiguous_edges=1)
                return [],[],[]
            for kind,complete in [('operating_path',False),('cross_case_relations',True)]:
                state={'figure':{'kind':kind,'pages':[{'pdf_page':1}]}}
                with patch('agent_review.schematic_routes.route_branches',side_effect=route),patch('agent_review.schematic_routes.calibration',return_value=None):
                    spec=_spec_from_verified_figure(pdf,{}, {},rule_id='RULE',obj='O',state=state)
                self.assertEqual(spec['pages'][0]['routing_complete'],complete)
                if not complete:self.assertIn('方向声明冲突',spec['pages'][0]['routing_issues'][0])

    def test_cached_diagram_keeps_unresolved_route_warning(self):
        from agent_review import stress_figures
        with tempfile.TemporaryDirectory() as tmp:
            b=Path(tmp);o=b/'review_out';(o/'sch').mkdir(parents=True)
            with fitz.open() as d:d.new_page();d.save(o/'sch/schematic.pdf')
            (o/'board_kg.json').write_text('{}')
            (o/'agent_enumeration.json').write_text(json.dumps({'rule_ids':['P12-A08'],'by_rule':{'P12-A08':['O1']}}))
            card={'object':'O1','rule_id':'P12-A08','verdict_zh':'待确认','states':[{'name':'关系','role':'primary','figure':{'pages':[{'pdf_page':1}]}}]}
            spec={'object':'O1','rule_id':'P12-A08','pages':[{'pdf_page':1,'routing_complete':False,'routing_issues':['missing pin']}]}
            def render(board,spec,dest):
                dest.mkdir(parents=True);(dest/'meta.json').write_text('{"pages":[]}');(dest/'flow.png').write_bytes(b'preview')
            with patch.object(stress_figures,'load_judge_cards',return_value=[card]),patch.object(stress_figures,'_spec_from_verified_figure',return_value=spec),patch.object(stress_figures,'render_spec',side_effect=render) as renderer:
                stress_figures.run(b);stress_figures.run(b)
                self.assertEqual(renderer.call_count,1)
                row=stress_figures.lookup(o,'P12-A08','O1',0)
                self.assertTrue(row['cached']);self.assertFalse(row['routing_complete'])
                self.assertEqual(row['reason'],'missing pin')

    def test_fresh_model_route_is_not_overridden_by_published_legacy_prose(self):
        from agent_review import stress_figures
        with tempfile.TemporaryDirectory() as tmp:
            b=Path(tmp);o=b/'review_out';(o/'sch').mkdir(parents=True)
            with fitz.open() as d:d.new_page();d.save(o/'sch/schematic.pdf')
            (o/'board_kg.json').write_text('{}')
            (o/'agent_enumeration.json').write_text(json.dumps({'rule_ids':['P12-A08'],'by_rule':{'P12-A08':['O1']}}))
            (o/'verdicts.json').write_text(json.dumps({'results':[{'rule_id':'P12-A08','object_cards':[{'object':'O1'}]}]}))
            card={'object':'O1','rule_id':'P12-A08','verdict_zh':'待确认','analysis_contract':'analysis.v1','states':[{'name':'关系','role':'primary','route_summary':'本轮模型具体通路','figure':{'pages':[{'pdf_page':1}]}}]}
            spec={'object':'O1','rule_id':'P12-A08','pages':[{'pdf_page':1,'routing_complete':True,'routing_issues':[]}]}
            captured=[]
            def render(board,spec,dest):
                captured.append(spec['pages'][0]['takeaway'])
                dest.mkdir(parents=True);(dest/'meta.json').write_text('{"pages":[]}');(dest/'flow.png').write_bytes(b'preview')
            with patch.object(stress_figures,'load_judge_cards',return_value=[card]),patch.object(stress_figures,'_spec_from_verified_figure',return_value=spec),patch.object(stress_figures,'render_spec',side_effect=render),patch('agent_review.detail_prose.load') as old_prose:
                stress_figures.run(b)
                old_prose.assert_not_called()
                self.assertEqual(captured,['本轮模型具体通路'])


if __name__=='__main__':unittest.main()
