"""Unified analysis prose with machine-readable, source-bound assumptions."""
from __future__ import annotations
import re

VERSION = 'analysis.v1'


def has_direct_analysis(card):
    source = card.get('review_v1') or card
    return source.get('analysis_contract') == VERSION and 'analysis_steps' in source


def assumptions_for(card):
    native = card.get('review_v1') or (card if card.get('schema_version') == 'review.v1' else None)
    if native:
        return [dict(p, source={'field': 'premises', 'index': i}) for i, p in enumerate(native['premises'])]
    if 'key_assumptions' in card:
        return [dict(p, source=p.get('source', {'field': 'key_assumptions', 'index': i}))
                for i, p in enumerate(card['key_assumptions'])]
    raw = card.get('assumptions') or []
    if isinstance(raw, str): raw = [raw]
    return [{'id': f'legacy-assumption-{i}', 'text': str(p), 'status': 'recorded',
             'evidence_refs': [], 'source': {'field': 'assumptions', 'index': i}}
            for i, p in enumerate(raw)]


def required_assumptions(card):
    native = card.get('review_v1') or (card if card.get('schema_version') == 'review.v1' else None)
    if native:
        return native.get('display', {}).get('premise_refs', [])
    return [p['id'] for p in assumptions_for(card)]


def step_schema(ref_name='assumption_refs'):
    return {'type': 'array', 'maxItems': 3, 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['text', ref_name], 'properties': {
                'text': {'type': 'string', 'minLength': 1, 'maxLength': 500},
                ref_name: {'type': 'array', 'uniqueItems': True, 'items': {'type': 'string'}}}}}


def validate_links(steps, assumptions, required=(), ref_name='assumption_refs'):
    """Validate identity and uncertainty without inventing electrical knowledge."""
    errors = []
    ids = [p.get('id') for p in assumptions]
    if len(set(ids)) != len(ids): errors.append('duplicate key assumption id')
    by_id = {p.get('id'): p for p in assumptions}
    used = set()
    for step in steps:
        if not isinstance(step, dict):
            errors.append('analysis step must be structured'); continue
        refs = step.get(ref_name, [])
        if not isinstance(refs, list):
            errors.append('analysis assumption references must be a list'); continue
        for ref in refs:
            if ref not in by_id:
                errors.append('unknown analysis assumption reference: ' + str(ref)); continue
            used.add(ref)
            status = by_id[ref].get('status')
            if status == 'unresolved' and not re.search(r'若|未|尚|待|需|假设|条件', step.get('text', '')):
                errors.append('unresolved assumption must remain conditional in analysis')
            if status == 'model_assumption' and not re.search(r'模型|假设|暂|按|取|若|条件', step.get('text', '')):
                errors.append('model assumption must remain qualified in analysis')
    if set(required) - used: errors.append('required key assumptions missing from analysis references')
    return errors
