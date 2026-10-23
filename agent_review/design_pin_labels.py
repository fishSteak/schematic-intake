"""Expose original design pin names/numbers without inferring pin functions."""
import hashlib
from pathlib import Path
from kernel.util import read_json


def design_pin_labels(out, refs):
    path = Path(out) / 'jlc_project.json'
    if not path.is_file(): return {}
    data = read_json(path)
    wanted = set(refs)
    result = {}
    source = {'path': 'review_out/jlc_project.json', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'original_project': data.get('source', {})}
    for instance in data.get('instances', []):
        ref = instance.get('ref')
        if ref not in wanted: continue
        pins = []
        for pin in instance.get('pins', []):
            if not pin.get('object') or not pin.get('pinName'): continue
            pins.append({key: pin.get(key) for key in ['object', 'pinName', 'net', 'location']})
        if pins:
            result[ref] = {'role': 'original_design_pin_labels', 'source': source,
                           'pins': pins,
                           'note': '原设计符号同时保存的数字脚号与脚名；须与准确厂家图比对，不代替厂家功能保证。'}
    return result
