import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from kernel.util import write_json
from agent_review.material_params import material_for_mpn
from agent_review.rated_keys import excerpt_for_mpn
from agent_review.validate_job import validate_power_decision, validate_judge_card, validate_worst_case
from agent_review.schematic_routes import route_branches, directed_copper
from agent_review.presentation import business_text, primary_state_index
from agent_review.p11_card_enrich import enrich_p11_card
from agent_review.stress_figures import _segment_pts


class MaterialTests(unittest.TestCase):
    def test_board_cache_and_kg_supplements_reach_workbench(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)/"review_out"
            write_json(out.parent/"cache/mpn/PART-A/pintable.json", {"params": {"p_rated_w": 1}})
            (out.parent/"cache/mpn/PART-A/datasheet.pdf").touch()
            write_json(out/"board_kg.json", {"components": {"X1": {
                "mpn": "PART-A", "pintable": {"params": {"test_current_a": .01}}}}})
            material = excerpt_for_mpn(out, "PART-A", "P11-A01")
            self.assertEqual(material["params"][0]["value"], 1)
            self.assertEqual(material["material_params"]["test_current_a"], .01)
            self.assertEqual(material["datasheet_path"], "cache/mpn/PART-A/datasheet.pdf")
            self.assertEqual(material_for_mpn(out, "PART-B")["params"], {})

    def test_disagreement_is_explicit_not_last_writer_wins(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)/"review_out"
            write_json(out.parent/"cache/mpn/PART/pintable.json", {"params": {"limit": 1}})
            material = material_for_mpn(out, "PART", {"components": {"X": {
                "mpn": "PART", "pintable": {"params": {"limit": 2}}}}})
            self.assertNotIn("limit", material["params"])
            self.assertEqual(material["conflicts"][0]["alternative"], 2)


class DecisionTests(unittest.TestCase):
    def card(self):
        return {"verdict_zh": "否", "electrical": {"P_max": None, "P_limit": .1},
                "states": [{"P": .2, "stress_kind": "lower_bound"}],
                "decision_basis": {"kind": "proven_lower_bound", "value_w": .2,
                                   "limit_w": .1, "state_index": 0,
                                   "evidence": ["source"], "conditions": "steady"}}

    def test_fail_witness_does_not_require_global_maximum(self):
        self.assertEqual(validate_power_decision(self.card()), [])

    def test_pending_candidate_or_lower_bound_cannot_prove_pass(self):
        card = self.card()
        card["states"][0]["status"] = "unmapped"
        self.assertTrue(validate_power_decision(card))
        card = self.card()
        card["verdict_zh"] = "是"
        self.assertTrue(validate_power_decision(card))

    def test_worst_ranking_retains_larger_conditional_path(self):
        card = {"states": [{"P": .08, "role": "worst", "stress_kind": "modeled"},
                            {"P": .25, "status": "conditional", "role": "comparison", "stress_kind": "modeled", "conditions": "switch driven"},
                            {"P": .28, "status": "conditional", "role": "worst_candidate", "stress_kind": "modeled", "conditions": "load sinks current"}],
                "worst_case": {"confirmed_state_index": 0, "conditional_state_index": 2,
                               "comparison": [{"state_index": i, "reason": "compared"} for i in range(3)],
                               "unresolved": ["duration"]}}
        self.assertEqual(validate_worst_case(card), [])
        card["worst_case"]["conditional_state_index"] = 1
        self.assertTrue(any("larger" in e for e in validate_worst_case(card)))
        witness = self.card()
        witness["states"][0]["status"] = "conditional"
        self.assertTrue(validate_power_decision(witness))


class RoutingTests(unittest.TestCase):
    def test_opposite_traversals_do_not_become_opposite_current_arrows(self):
        a,b,c=(10,10),(40,10),(70,10)
        arrows, ambiguous=directed_copper([[a,b,c],[c,b]])
        self.assertEqual(ambiguous,1)
        self.assertEqual(arrows,[[a,b]])

    def test_single_pin_return_points_toward_ground(self):
        doc=fitz.open();page=doc.new_page()
        page.draw_line((20,10),(20,40),color=(0,.53,0))
        comps={"X1":{"location":[{"pdf_page":1,"pageId":"p"}],"pins":[{"pin":"1","net":"GND"}]}}
        instances=[{"ref":"X1","pins":[{"object":"X1.1","net":"GND","location":{"x":20,"y":10,"pageId":"p"}}]}]
        audit={}
        with patch("agent_review.schematic_routes.calibration",return_value=lambda x,y:(x,y)):
            lines,_,issues=route_branches(page,{"path":["X1.1","GND"]},instances,comps,None,audit)
        self.assertEqual(issues,[])
        self.assertEqual(audit['arrow_polylines'],[[(20.0,10.0),(20.0,40.0)]])
        doc.close()

    def test_internal_net_junction_does_not_detour_through_label(self):
        doc=fitz.open();page=doc.new_page()
        for a,b in (((10,10),(50,10)),((50,10),(90,10)),((50,10),(50,40))):
            page.draw_line(a,b,color=(0,.53,0))
        page.insert_text((82,8),'MID')
        comps,instances={},[]
        for ref,x,y in [('X1',10,10),('X2',50,40)]:
            comps[ref]={"location":[{"pdf_page":1,"pageId":"p"}],"pins":[{"pin":"1","net":"MID"}]}
            instances.append({"ref":ref,"pins":[{"object":ref+'.1',"net":"MID","location":{"x":x,"y":y,"pageId":"p"}}]})
        audit={}
        with patch("agent_review.schematic_routes.calibration",return_value=lambda x,y:(x,y)):
            lines,_,issues=route_branches(page,{"paths":[['X1.1','MID'],['MID','X2.1']]},instances,comps,None,audit)
        self.assertEqual(issues,[])
        self.assertFalse(any((90.0,10.0) in line for line in lines))
        self.assertEqual(audit['ambiguous_edges'],0)
        doc.close()
    def test_branch_only_page_is_a_valid_figure_contract(self):
        card = {"verdict_zh": "待确认", "states": [{"name_zh": "candidate", "status": "unmapped",
                "figure": {"pages": [{"pdf_page": 1, "path": [], "paths": [["A", "B"]],
                                      "markers": [{"on": "B", "title": "node"}]}]}}]}
        errors = validate_judge_card(card, rule_id="P11-A01", obj="X")
        self.assertFalse(any("figure" in error or "圆点" in error for error in errors), errors)
        card["states"][0]["figure"]["pages"][0]["paths"] = []
        self.assertTrue(any("path/paths" in error for error in validate_judge_card(card, rule_id="P11-A01", obj="X")))

    def test_disconnected_vectors_never_become_diagonal_chord(self):
        doc = fitz.open()
        page = doc.new_page()
        page.draw_line((10, 10), (20, 10), color=(0, .53, 0))
        page.draw_line((70, 40), (90, 40), color=(0, .53, 0))
        self.assertEqual(_segment_pts(page, (10, 10), (90, 40)), [])
        doc.close()


class PresentationTests(unittest.TestCase):
    def test_pin_span_is_business_wording_but_node_voltage_is_not_changed(self):
        self.assertEqual(business_text('V(R9.2) − V(R9.1) = 3V'), 'V(R9) 两端电压差 = 3V')
        self.assertEqual(business_text('R9.2－R9.1 = 3V'), 'V(R9) 两端电压差 = 3V')
        self.assertEqual(business_text('V(R9.2)=24V'), 'V(R9.2)=24V')

    def test_best_or_worst_is_one_explicit_primary_case(self):
        self.assertEqual(primary_state_index([{'P':5},{'P':2,'role':'best'}]),1)
        self.assertEqual(primary_state_index([{'P':5},{'P':2,'role':'worst_candidate'}]),1)

    def test_conditional_exceedance_is_not_a_confirmed_state_failure(self):
        with tempfile.TemporaryDirectory() as td:
            card={'rule_id':'P11-A01','object':'RX','verdict_zh':'待确认',
                  'electrical':{'P_rated':1,'P_limit':.5,'P_max':None},
                  'states':[{'name_zh':'条件吸流','P':.8,'status':'conditional','role':'worst_candidate',
                             '结论':'0.8W超过0.5W限额，不能作为已确认超限见证',
                             'formula_lines':[{'expr':'P=0.8W','src':'condition'}]}]}
            shown=enrich_p11_card(Path(td),card,{})
            self.assertEqual(shown['verdict_zh'],'待确认')
            self.assertEqual(shown['solution']['states'][0]['verdict_zh'],'待确认')
            self.assertEqual(shown['solution']['states'][0]['comparison_zh'],'模型超限')

    def test_separate_branches_do_not_join_and_net_mismatch_is_reported(self):
        doc = fitz.open()
        page = doc.new_page()
        page.draw_line((10, 10), (30, 10), color=(0, .53, 0))
        page.draw_line((10, 40), (30, 40), color=(0, .53, 0))
        comps, instances = {}, []
        for ref, x, y, net in (("X1", 10, 10, "A"), ("X2", 30, 10, "A"),
                               ("X3", 10, 40, "B"), ("X4", 30, 40, "B")):
            comps[ref] = {"location": [{"pdf_page": 1, "pageId": "p"}],
                          "pins": [{"pin": "1", "net": net}]}
            instances.append({"ref": ref, "pins": [{"object": ref+".1", "net": net,
                              "location": {"x": x, "y": y, "pageId": "p"}}]})
        with patch("agent_review.schematic_routes.calibration", return_value=lambda x,y:(x,y)):
            lines, bridges, issues = route_branches(page,
                {"path": ["X1.1", "X2.1"], "paths": [["X3.1", "X4.1"]]}, instances, comps, None)
            self.assertEqual(len(lines), 2)
            self.assertEqual(issues, [])
            self.assertEqual(bridges, [])
            lines, _, issues = route_branches(page, {"path": ["X1.1", "X4.1"]}, instances, comps, None)
            self.assertEqual(lines, [])
            self.assertIn("net mismatch", issues[0])
        doc.close()
