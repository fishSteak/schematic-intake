"""Preflight model-specified routes without rendering or changing published media."""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kernel.util import read_json, write_json, resolve_board_path
from agent_review.review_contract import is_v1, project
from agent_review.stress_figures import _states_of, primary_state_index, _spec_from_verified_figure


def check(board):
    o = board / 'review_out'
    config = read_json(board / 'selection_delivery.json')
    kg = read_json(o / 'board_kg.json')
    rows = []
    for rid, objects in config['by_rule'].items():
        completed = []
        for obj in objects:
            p = o / 'delivery_progress' / rid / (obj + '.json')
            if p.is_file() and read_json(p).get('status') == 'validated':
                completed.append(obj)
        # Geometry does not consume outer remediation prose, which is regenerated
        # separately after review changes and before the actual publication.
        for obj in completed:
            raw=read_json(o/'agent_jobs'/rid/(obj+'.json'))
            card=project(raw) if is_v1(raw) else raw
            if card.get('main_analysis'):
                from agent_review.main_analysis import drawing_states, selected_index
                states, primary = drawing_states(card), selected_index(card)
            else:
                states = _states_of(card)
                primary = primary_state_index(states) if states else None
            if primary is None or not states:
                continue
            state = states[primary]
            if not state.get('figure'):
                continue
            spec = _spec_from_verified_figure(o / 'sch/schematic.pdf', kg, card,
                                             rule_id=rid, obj=card['object'], state=state)
            pages = spec['pages'] if spec else []
            rows.append({'rule_id': rid, 'object': card['object'],
                         'pages': [{'pdf_page': p['pdf_page'], 'complete': p['routing_complete'],
                                    'issues': p['routing_issues'],
                                    'ambiguous_direction_edges': p.get('ambiguous_direction_edges', 0)} for p in pages],
                         'complete': bool(pages) and all(p['routing_complete'] for p in pages)})
    result = {'records': rows, 'incomplete': [r for r in rows if not r['complete']]}
    write_json(o / 'delivery_geometry_preflight.json', result)
    print('GEOMETRY', len(rows), 'diagrams;', len(result['incomplete']), 'with unresolved locations')
    for row in result['incomplete']:
        print(row)
    return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--board', required=True)
    check(resolve_board_path(ap.parse_args().board))
