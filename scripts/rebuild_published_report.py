#!/usr/bin/env python3
"""Re-render an attested completed review without making any model requests.

This creates an independent report_rebuild run. It is not review resumption:
engineering cards, research, analysis and checkpoints are copied byte-for-byte.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_review.batching import safe_object_name
from agent_review.execution_policy import digest
from agent_review.validate_job import validate_judge_card
from kernel.util import read_json
from web_workbench.execution import profile_signature, validate_snapshot
from web_workbench.store import Store, atomic_json, now
from web_workbench.worker import evidence_paths, publish_report


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def contained(root, relative):
    root = Path(root).resolve()
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('Report rebuild input escapes its recorded directory')
    return path


def inventory(root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('Report rebuild does not accept symbolic links')
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Report rebuild does not accept symbolic links')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha(path)
    return result


def protected_inventory(board):
    """Rendering must not change review inputs or any model-authored output."""
    paths = ('material_inputs', 'history', 'review_out/agent_jobs',
             'review_out/context_store', 'review_out/evidence_store',
             'review_out/evidence_proposals', 'review_out/research_assets',
             'review_out/detail_prose', 'review_out/remediation')
    return {name + '/' + rel: value for name in paths
            for rel, value in inventory(Path(board) / name).items()}


def verify_source(store, ident):
    run = store.run(ident)
    if run['status'] != 'completed' or run.get('mode') != 'review' or not run.get('report_dir'):
        raise ValueError('Only a completed published review can be rebuilt')
    report = contained(store.home / 'reports', run['report_dir'])
    manifest = read_json(report / 'snapshot.json')
    before = inventory(report)
    if (manifest.get('run_id') != ident or manifest.get('board_id') != run['board_id']
            or manifest.get('material_version') != run.get('material_version')
            or manifest.get('files') != {k: v for k, v in before.items() if k != 'snapshot.json'}):
        raise ValueError('Published snapshot identity or file hashes do not match')
    workspace = contained(store.home / 'runs', run['workspace'])
    execution = read_json(workspace / 'execution.json')
    board = contained(store.home / 'runs', execution['board'])
    if execution.get('material_version') != run.get('material_version'):
        raise ValueError('Frozen material version does not match the published run')
    validate_snapshot(board)
    profile = contained(workspace, run.get('profile_path') or 'missing-profile')
    signature = profile_signature(profile)
    enumeration = read_json(board / 'review_out/agent_enumeration.json')
    if set(enumeration['rule_ids']) != set(run['rule_ids']):
        raise ValueError('Stored review scope does not match the published run')
    if run.get('objects') is not None and set().union(*map(set, enumeration['by_rule'].values())) != set(run['objects']):
        raise ValueError('Stored review objects do not match the published run')

    # Both input aliases and output-relative source aliases must still identify
    # the same bytes that were actually published, including sealed PDF sources.
    aliases = manifest.get('sources') or {}
    if not aliases:
        raise ValueError('Published source provenance is missing')
    for original, published in aliases.items():
        raw = Path(original)
        if raw.is_absolute():
            if raw.is_relative_to(board):
                raw = contained(board, raw)
            elif raw.is_relative_to(store.repository / 'rules'):
                raw = contained(store.repository / 'rules', raw)
            else:
                raise ValueError('Published source is outside frozen material scope')
        elif original == 'materials.json' or original.startswith(('material_inputs/', 'review_out/', 'cache/')):
            raw = contained(board, original)
        elif original.startswith('rules/'):
            raw = contained(store.repository, original)
        else:
            raw = contained(board / 'review_out', original)
        if not raw.is_file() or sha(raw) != manifest['files'].get(published):
            raise ValueError('A published source has changed or is missing: ' + original[:180])

    cards = []
    from agent_review.rule_modes import split_rule_ids
    for rid in split_rule_ids(run['rule_ids'])['agent']:
        pack = read_json(report / 'evidence' / (safe_object_name(rid) + '.json'))
        for obj in enumeration['by_rule'].get(rid, []):
            path = board / 'review_out/agent_jobs' / rid / (safe_object_name(obj) + '.json')
            card = read_json(path)
            # Keep this operations command deliberately narrow: older unattested
            # cards and alternate migration formats require a new full review.
            proof = card.get('_execution') or {}
            published = next((c for c in pack.get('object_cards', []) if c.get('object') == obj), {})
            if (not proof.get('self_check') or not proof.get('run_id') or not proof.get('fingerprint')
                    or proof != published.get('_execution')
                    or proof.get('output_hash') != digest({k: v for k, v in card.items() if k != '_execution'})
                    or validate_judge_card(card, rule_id=rid, obj=obj, board=board)):
                raise ValueError('Current review card does not match validated published execution: ' + rid + '/' + obj)
            if any(path.with_suffix(suffix).exists() for suffix in ('.failed.json', '.invalid.json')):
                raise ValueError('Current review has an unresolved failure marker')
            if proof.get('research_hash') != sha(path.with_suffix('.research.json')):
                raise ValueError('Current research record differs from published execution')
            for source, expected in (proof.get('research_sources') or {}).items():
                if sha(contained(board, source)) != expected:
                    raise ValueError('A model-adopted research source has changed')
            checkpoint = board / 'history/execution-checkpoints' / rid / safe_object_name(obj) / proof['fingerprint']
            stage = read_json(checkpoint / 'stage.json')
            relative = Path('review_out/agent_jobs') / rid / (safe_object_name(obj) + '.analysis.md')
            if (stage.get('stage') != 'self_check' or stage.get('execution_hash') != proof['fingerprint']
                    or stage.get('analysis_hash') != sha(checkpoint / relative)
                    or stage.get('analysis_hash') != sha(board / relative)
                    or stage.get('conversation_hash') != sha(checkpoint / 'conversation.json')):
                raise ValueError('Completed analysis/checkpoint provenance is missing or changed')
            cards.append({'rule_id': rid, 'object': obj, 'execution': proof,
                          'card_sha256': sha(path), 'analysis_sha256': stage['analysis_hash'],
                          'conversation_sha256': stage['conversation_hash']})
    if not cards:
        raise ValueError('No attested direct LLM cards in the published scope')
    return {'run': run, 'board': board, 'report': report, 'report_files': before,
            'board_files': inventory(board), 'profile': profile, 'profile_sha256': sha(profile),
            'profile_signature': signature, 'enumeration': enumeration, 'cards': cards}


@contextmanager
def no_model_requests():
    from agent_review.llm import LLMClient
    with patch.object(LLMClient, 'complete', side_effect=RuntimeError('Model requests are forbidden during report rebuild')):
        yield


def rebuild(store, ident):
    source = verify_source(store, ident)
    old = source['run']
    values = {key: old.get(key) for key in ('mode', 'rule_ids', 'objects', 'concurrency',
                                          'profile_id', 'material_version', 'board_path')}
    values.update(operation='report_rebuild', report_rebuild_of=ident, resume=False,
                  model_request_count=0, model_analysis_reused=True)
    run = store.create_run(old['board_id'], values)
    rid = run['id']
    report = None
    try:
        if not store.claim_run(rid):
            raise RuntimeError('Another task is running; report rebuild was not started')
        store.update_run(rid, pid=os.getpid(), started_at=now(), stage='报告重建')
        workspace = Path(run['workspace']);workspace.mkdir(parents=True)
        board = workspace / 'board'
        store.event(rid, 'stage', {'stage': '验证并复制原评审', 'completed': 0, 'total': 3,
                                   'report_rebuild_of': ident, 'model_request_count': 0})
        shutil.copytree(source['board'], board)
        if inventory(board) != source['board_files']:
            raise ValueError('Frozen review changed while copying')
        profile = workspace / 'model-profile.json'
        shutil.copy2(source['profile'], profile);os.chmod(profile, 0o600)
        if sha(profile) != source['profile_sha256']:
            raise ValueError('Frozen model profile changed while copying')
        run = store.update_run(rid, profile_path=str(profile))
        atomic_json(workspace / 'execution.json', {'board': str(board), 'material_version': old['material_version'],
                    'operation': 'report_rebuild', 'report_rebuild_of': ident, 'model_request_count': 0})
        protected = protected_inventory(board)
        from agent_review.stages import aggregate_stage, gate
        out = board / 'review_out'
        materials = read_json(out / 'materials.json')
        context = read_json(out / 'context_build_result.json')
        with no_model_requests():
            store.event(rid, 'stage', {'stage': '重新生成报告', 'completed': 1, 'total': 3})
            summary = aggregate_stage.run(board, source['enumeration'], materials=materials, context_meta=context,
                         figure_rule_ids=old['rule_ids'], figure_objects=old.get('objects'), generate_prose=False)
            store.event(rid, 'stage', {'stage': '交付校验', 'completed': 2, 'total': 3})
            checked = gate.run(board, source['enumeration'], materials=materials, context_meta=context,
                               generate_prose=False, allow_failed=False)
        if not checked.get('passed') or any(not row.get('ok') for row in checked.get('checks', [])):
            raise ValueError('Rebuilt report failed the ordinary delivery gate')
        if protected_inventory(board) != protected:
            raise ValueError('Report rendering changed protected engineering artifacts')
        if (inventory(source['board']) != source['board_files'] or inventory(source['report']) != source['report_files']
                or sha(source['profile']) != source['profile_sha256']):
            raise ValueError('Original review or published snapshot changed during rebuild')
        receipt = {'report_rebuild_of': ident, 'model_request_count': 0, 'source_snapshot_sha256':
                   source['report_files']['snapshot.json'], 'source_snapshot_files': source['report_files'],
                   'protected_files': protected, 'cards': source['cards'], 'gate': checked,
                   'profile_signature': source['profile_signature'], 'completed_at': now()}
        atomic_json(workspace / 'report_rebuild.json', receipt)
        report = publish_report(store, run, out)
        manifest = read_json(report / 'snapshot.json')
        # Do not publish a link map that silently loses an old absolute source.
        for rule in old['rule_ids']:
            pack = read_json(report / 'evidence' / (safe_object_name(rule) + '.json'))
            if not evidence_paths(pack) <= set(manifest['sources']):
                raise ValueError('Rebuilt report lacks an exact evidence source alias')
        if inventory(source['report']) != source['report_files'] or inventory(source['board']) != source['board_files']:
            raise ValueError('Original review changed during publication')
        store.event(rid, 'progress', {'stage': '完成', 'completed': 3, 'total': 3,
                                      'report_rebuild_of': ident, 'model_request_count': 0})
        return store.finish(rid, 'completed', errors=[], report_dir=str(report), report_url=f'/reports/{rid}/',
                            published_at=now(), summary={**summary['verdicts']['summary'], 'mode': old['mode'],
                            'operation': 'report_rebuild', 'report_rebuild_of': ident, 'model_request_count': 0,
                            'model_analysis': True, 'model_analysis_reused': True,
                            'objects': source['enumeration']['stats']['judge_pairs'], 'gate_passed': True})
    except BaseException as error:
        # An unpublished new bundle belongs solely to this operation. Never
        # leave a failed post-publication verification accessible as a report.
        if report is not None and store.run(rid)['status'] != 'completed':
            shutil.rmtree(report)
        store.finish(rid, 'failed', errors=[str(error)[:1500]])
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id')
    parser.add_argument('--home', type=Path)
    args = parser.parse_args()
    result = rebuild(Store(args.home), args.run_id)
    print(json.dumps({k: result[k] for k in ('id', 'status', 'report_rebuild_of', 'report_url', 'summary')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
