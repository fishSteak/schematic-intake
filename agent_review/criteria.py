"""Snapshot the actual check definition without inferring electronic answers."""
import hashlib
from kernel.util import ROOT, read_json


def criteria_for_rule(rule_id, *, root=ROOT):
    path = root / 'rules/xlsx_detail_15.json'
    matches = [item for item in read_json(path).get('items', []) if item.get('rule_id') == rule_id]
    if len(matches) != 1:
        raise ValueError(f'Expected one complete check definition for {rule_id}, found {len(matches)}')
    return {
        'schema_version': 'criteria.v1',
        'source': {'path': 'rules/xlsx_detail_15.json', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()},
        'rule': matches[0],
    }
