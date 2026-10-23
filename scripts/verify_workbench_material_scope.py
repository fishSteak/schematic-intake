"""Verify HTTP material replacement followed by a scoped program review.

Uses a separate workbench and two P05-A01/R1 tasks. Never calls a model or
changes repository materials. Pass --output-dir to retain acceptance artifacts.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kernel.adapters.bom import parse_bom_file
from web_workbench.materials import EXTENSIONS
from web_workbench.runner import process_alive
from web_workbench.store import Store, TERMINAL


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def hashes(directory):
    return {str(p.relative_to(directory)): digest(p)
            for p in sorted(directory.rglob('*')) if p.is_file()}


def until(get, predicate, *, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = get()
        if predicate(value):
            return value
        time.sleep(.2)
    raise TimeoutError('Material/scope acceptance did not reach the required state')


def replacement_bom(source, destination):
    """Change only the fixture's R1 Value cell, retaining encoding and newlines."""
    raw = source.read_bytes()
    encoding, bom = 'utf-8', b''
    for marker, codec in ((b'\xff\xfe', 'utf-16-le'), (b'\xfe\xff', 'utf-16-be'),
                          (b'\xef\xbb\xbf', 'utf-8')):
        if raw.startswith(marker):
            encoding, bom = codec, marker
            break
    original = raw[len(bom):].decode(encoding)
    dialect = csv.Sniffer().sniff(original[:4096], delimiters=',\t;')
    rows = list(csv.reader(io.StringIO(original), dialect))
    ref_index, value_index = rows[0].index('Designator'), rows[0].index('Value')
    lines, changed = original.splitlines(keepends=True), 0
    for index, line in enumerate(lines):
        row = next(csv.reader([line], dialect))
        if len(row) <= ref_index or row[ref_index] != 'R1':
            continue
        assert row[value_index] == '47kΩ', 'Fixture R1 no longer has the expected source value'
        cells = line.split(dialect.delimiter)
        assert len(cells) == len(row), 'Fixture contains a multiline or quoted delimiter'
        assert cells[value_index] in ('47kΩ', '"47kΩ"'), 'Unexpected fixture value syntax'
        cells[value_index] = cells[value_index].replace('47kΩ', '56kΩ')
        lines[index] = dialect.delimiter.join(cells)
        changed += 1
    assert changed == 1, 'Expected one R1 BOM row'
    replacement = bom + ''.join(lines).encode(encoding)
    assert len(raw) == len(replacement)
    # Both values have equal byte lengths. Exactly two digit bytes may change.
    assert sum(a != b for a, b in zip(raw, replacement)) == 2
    destination.write_bytes(replacement)
    before, after = parse_bom_file(source), parse_bom_file(destination)
    assert before.keys() == after.keys()
    differences = [(ref, key, value, after[ref][key])
                   for ref, row in before.items() for key, value in row.items()
                   if value != after[ref][key]]
    assert differences == [('R1', 'value', '47kΩ', '56kΩ')], differences
    return {'encoding': encoding, 'bom': bom.hex(), 'changed_bytes': 2,
            'semantic_differences': differences,
            'original_sha256': digest(source), 'replacement_sha256': digest(destination)}


def verify(directory):
    home = directory / 'data'
    source_board = ROOT / 'boards/0918全量'
    config_file = source_board / 'materials.json'
    config = json.loads(config_file.read_text(encoding='utf-8-sig'))
    sources = {role: (source_board / path).resolve() for role, path in config.items()
               if role in EXTENSIONS and isinstance(path, str)}
    original_sources = {role: digest(path) for role, path in sources.items()}
    original_config = digest(config_file)
    results = {'scope': 'P05-A01/R1', 'mode': 'program', 'artifacts': str(directory)}
    replacement = directory / 'BOM-R1-one-field-replacement.csv'
    results['bom_change'] = replacement_bom(sources['bom'], replacement)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    # Deployment/auth/model settings from the caller cannot leak into this test.
    env = {k: v for k, v in os.environ.items() if not k.startswith('SCHEMATIC_')}
    env['SCHEMATIC_WORKBENCH_HOME'] = str(home)
    server = None
    store = Store(home)
    with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=30) as client, \
            (directory / 'server.log').open('wb') as log:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        def upload(board_id, role, path):
            return request('POST', f'/api/boards/{board_id}/materials',
                           data={'role': role}, files={'file': (
                               path.name, path.read_bytes(), 'application/octet-stream')})

        def run_scoped(board_id):
            run = request('POST', f'/api/boards/{board_id}/runs', json={
                'mode': 'program', 'rule_ids': ['P05-A01'], 'objects': ['R1']})
            run = until(lambda: request('GET', '/api/runs/' + run['id']),
                        lambda value: value['status'] in TERMINAL)
            assert run['status'] == 'completed', run.get('errors')
            assert run['rule_ids'] == ['P05-A01'] and run['objects'] == ['R1']
            assert run['summary']['objects'] == 1 and run['summary']['gate_passed'] is True
            assert run['summary']['model_analysis'] is False and run['profile_id'] is None
            assert not any(e['type'] == 'model' for e in store.events(run['id'], limit=10000))
            return run

        def inspect(run, value, expected_bom):
            stored = store.run(run['id'])
            workspace = Path(stored['workspace'])
            board = Path(json.loads((workspace / 'execution.json').read_text())['board'])
            report = Path(stored['report_dir'])
            kg = json.loads((board / 'review_out/board_kg.json').read_text())
            assert kg['components']['R1']['value'] == value
            frozen = json.loads((board / 'material_snapshot.json').read_text())
            assert frozen['sha256']['bom'] == expected_bom
            data = request('GET', f"/api/reports/{run['id']}/data")
            assert [r['rule_id'] for r in data['verdicts']['results']] == ['P05-A01']
            assert set(data['evidence']) == {'P05-A01'}
            cards = data['evidence']['P05-A01']['object_cards']
            assert len(cards) == 1 and cards[0]['object'] == 'R1'
            assert cards[0]['identity']['value_bom'] == value
            assert any(m.get('text') == value for m in cards[0]['review_detail']['materials'])
            manifest = json.loads((report / 'snapshot.json').read_text())
            assert manifest['material_version'] == run['material_version']
            assert all(digest(report / p) == sha for p, sha in manifest['files'].items())
            source = next(asset for asset in data['source_assets'].values()
                          if asset['sha256'] == expected_bom)
            response = client.get(source['url'])
            response.raise_for_status()
            assert hashlib.sha256(response.content).hexdigest() == expected_bom
            return {'run_id': run['id'], 'material_version': run['material_version'],
                    'kg_sha256': digest(board / 'review_out/board_kg.json'),
                    'kg_path': str(board / 'review_out/board_kg.json'), 'kg_r1_value': value,
                    'report_r1_value': cards[0]['identity']['value_bom'],
                    'bom_sha256': expected_bom, 'object_count': run['summary']['objects'],
                    'gate_passed': True, 'model_analysis': False, 'model_events': 0,
                    'report_dir': str(report), 'report_files': len(hashes(report)),
                    'manifest_files_verified': len(manifest['files']),
                    'frozen_bom_http_sha256_verified': True}

        try:
            server = subprocess.Popen([sys.executable, '-m', 'web_workbench', '--port', str(port),
                                       '--data-dir', str(home)], cwd=ROOT, env=env,
                                      stdin=subprocess.DEVNULL, stdout=log, stderr=log)

            def ready():
                if server.poll() is not None:
                    raise RuntimeError('Isolated HTTP service exited')
                try:
                    return client.get('/api/runtime').status_code == 200
                except httpx.TransportError:
                    return False

            until(ready, bool, timeout=20)
            assert request('GET', '/api/model-profiles')['profiles'] == []
            board_id = request('POST', '/api/boards', json={
                'name': 'Isolated material replacement and targeted review acceptance'})['id']
            results['board_id'] = board_id
            for role, path in sources.items():
                upload(board_id, role, path)
            assert request('POST', f'/api/boards/{board_id}/preflight')['ok'] is True
            first = run_scoped(board_id)
            baseline = inspect(first, '47kΩ', original_sources['bom'])
            results['baseline'] = baseline
            old_report = Path(baseline['report_dir'])
            old_hashes = hashes(old_report)

            inventory = upload(board_id, 'bom', replacement)
            assert inventory['version'] != first['material_version']
            assert request('POST', f'/api/boards/{board_id}/preflight')['ok'] is True
            second = run_scoped(board_id)
            assert second['id'] != first['id'] and second.get('parent_id') is None
            assert second['material_version'] == inventory['version']
            changed = inspect(second, '56kΩ', digest(replacement))
            results['replacement'] = changed
            assert changed['kg_sha256'] != baseline['kg_sha256']
            assert hashes(old_report) == old_hashes
            assert digest(baseline['kg_path']) == baseline['kg_sha256']
            old_data = request('GET', f"/api/reports/{first['id']}/data")
            assert old_data['evidence']['P05-A01']['object_cards'][0]['identity']['value_bom'] == '47kΩ'
            reports = request('GET', f'/api/boards/{board_id}/reports')['reports']
            assert [r['id'] for r in reports] == [second['id'], first['id']]
            board = next(b for b in request('GET', '/api/boards')['boards'] if b['id'] == board_id)
            assert board['latest_report']['id'] == second['id']
            assert original_sources == {role: digest(path) for role, path in sources.items()}
            assert original_config == digest(config_file)
            results.update(passed=True, old_report_all_files_unchanged=True,
                           old_kg_unchanged=True, source_materials_and_config_unchanged=True,
                           llm_called=False, replacement_is_new_task=True)
        except BaseException as exc:
            results.update(passed=False, error=f'{type(exc).__name__}: {exc}')
            raise
        finally:
            # Cleanup is limited to workers owned by this isolated database.
            for run in store.runs(active=True):
                if process_alive(run) is True:
                    process = psutil.Process(run['pid'])
                    children = process.children(recursive=True)
                    for child in reversed(children):
                        try:
                            child.kill()
                        except psutil.NoSuchProcess:
                            pass
                    process.kill()
                    psutil.wait_procs([*children, process], timeout=5)
            if server is not None:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
            (directory / 'results.json').write_text(
                json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, help='Retain evidence in a new, empty directory')
    args = parser.parse_args()
    if args.output_dir:
        directory = args.output_dir.resolve()
        directory.mkdir(parents=True, exist_ok=False)
        results = verify(directory)
    else:
        with tempfile.TemporaryDirectory(prefix='workbench-material-scope-') as directory:
            results = verify(Path(directory))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
