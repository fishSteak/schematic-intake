"""Report rebuild is an attested copy-and-render operation, never inference."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import rebuild_published_report as rebuild
from tests.test_llm_legacy_schema import add_main_analysis, power_card
from web_workbench.materials import REQUIRED
from web_workbench.store import Store, atomic_json


class ReportRebuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name) / 'state')
        project = self.store.create_board('Rebuild fixture')
        run = self.store.create_run(project['id'], {'mode': 'review', 'rule_ids': ['P11-A01'],
                    'objects': ['R7'], 'concurrency': 1, 'material_version': 'frozen-input', 'profile_id': 'fixture'})
        workspace = Path(run['workspace']);workspace.mkdir(parents=True)
        self.board = workspace / 'board';self.out = self.board / 'review_out'
        self.board.mkdir()
        config = {}
        for key in REQUIRED:
            path = self.board / 'material_inputs' / (key + '.txt')
            path.parent.mkdir(exist_ok=True);path.write_text('Frozen ' + key)
            config[key] = path.relative_to(self.board).as_posix()
        atomic_json(self.board / 'materials.json', config)
        atomic_json(self.board / 'material_snapshot.json', {'sha256': {
            key: rebuild.sha(self.board / value) for key, value in config.items()},
            'config_sha256': rebuild.sha(self.board / 'materials.json')})
        atomic_json(workspace / 'execution.json', {'board': str(self.board), 'material_version': 'frozen-input'})
        profile = workspace / 'model-profile.json'
        atomic_json(profile, {'provider': 'openai-compatible', 'base_url': 'http://localhost:9999/v1',
                             'model': 'fixture-no-network', 'wire_api': 'responses'}, private=True)
        self.store.update_run(run['id'], profile_path=str(profile))
        self.enumeration = {'rule_ids': ['P11-A01'], 'by_rule': {'P11-A01': ['R7']}, 'stats': {'judge_pairs': 1}}
        atomic_json(self.out / 'agent_enumeration.json', self.enumeration)
        atomic_json(self.out / 'materials.json', {})
        atomic_json(self.out / 'context_build_result.json', {})
        self.card = add_main_analysis(power_card())
        source = self.board / 'material_inputs/source.txt';source.write_text('Confirmed review envelope.')
        self.card_path = self.out / 'agent_jobs/P11-A01/R7.json'
        analysis = self.card_path.with_suffix('.analysis.md')
        analysis.parent.mkdir(parents=True);analysis.write_text('Original completed analysis.')
        research = self.card_path.with_suffix('.research.json');atomic_json(research, {'facts': []})
        proof = {'fingerprint': 'fixture-execution', 'run_id': 'original-model-run', 'model': 'fixture-no-network',
                 'provider': 'openai-compatible', 'self_check': True, 'research_hash': rebuild.sha(research),
                 'research_sources': {'material_inputs/source.txt': rebuild.sha(source)},
                 'output_hash': rebuild.digest(self.card)}
        self.card['_execution'] = proof;atomic_json(self.card_path, self.card)
        checkpoint = self.board / 'history/execution-checkpoints/P11-A01/R7/fixture-execution'
        saved = checkpoint / 'review_out/agent_jobs/P11-A01/R7.analysis.md'
        saved.parent.mkdir(parents=True);saved.write_bytes(analysis.read_bytes())
        atomic_json(checkpoint / 'conversation.json', {'messages': []})
        atomic_json(checkpoint / 'stage.json', {'stage': 'self_check', 'execution_hash': 'fixture-execution',
                     'analysis_hash': rebuild.sha(analysis), 'conversation_hash': rebuild.sha(checkpoint / 'conversation.json')})
        self.report = self.store.home / 'reports' / run['id']
        atomic_json(self.report / 'evidence/P11-A01.json', {'object_cards': [self.card]})
        copied = self.report / 'material_inputs/source.txt';copied.parent.mkdir();copied.write_bytes(source.read_bytes())
        atomic_json(self.report / 'snapshot.json', {'run_id': run['id'], 'board_id': run['board_id'],
                    'material_version': 'frozen-input', 'files': rebuild.inventory(self.report),
                    'sources': {'material_inputs/source.txt': 'material_inputs/source.txt'}})
        self.store.finish(run['id'], 'completed', report_dir=str(self.report), report_url='/reports/' + run['id'] + '/')
        self.ident = run['id']

    def test_completed_source_has_exact_model_and_checkpoint_provenance(self):
        source = rebuild.verify_source(self.store, self.ident)
        self.assertEqual(source['cards'][0]['execution'], self.card['_execution'])
        self.assertEqual(source['cards'][0]['analysis_sha256'], rebuild.sha(self.card_path.with_suffix('.analysis.md')))

    def test_changed_snapshot_or_raw_source_is_rejected_before_creating_run(self):
        for path in (self.report / 'evidence/P11-A01.json', self.board / 'material_inputs/source.txt',
                     self.board / 'material_inputs/netlist.txt'):
            with self.subTest(path=path):
                before = path.read_bytes();path.write_bytes(before + b'changed')
                try:
                    with self.assertRaises(ValueError):
                        rebuild.rebuild(self.store, self.ident)
                    self.assertEqual(len(self.store.runs()), 1)
                finally:
                    path.write_bytes(before)

    def test_changed_card_research_or_checkpoint_is_rejected(self):
        checkpoint = self.board / 'history/execution-checkpoints/P11-A01/R7/fixture-execution/conversation.json'
        for path in (self.card_path, self.card_path.with_suffix('.research.json'), checkpoint):
            with self.subTest(path=path):
                original = path.read_bytes()
                document = json.loads(original);document['unexpected'] = True;atomic_json(path, document)
                try:
                    with self.assertRaises(ValueError):
                        rebuild.verify_source(self.store, self.ident)
                finally:
                    path.write_bytes(original)

    def test_missing_model_attestation_and_failed_marker_are_rejected(self):
        document = copy.deepcopy(self.card);document['_execution']['self_check'] = False
        atomic_json(self.card_path, document)
        with self.assertRaises(ValueError):
            rebuild.verify_source(self.store, self.ident)
        atomic_json(self.card_path, self.card)
        atomic_json(self.card_path.with_suffix('.failed.json'), {'error': 'new attempt failed'})
        with self.assertRaises(ValueError):
            rebuild.verify_source(self.store, self.ident)

    def test_model_call_guard_fails_without_reaching_transport(self):
        from agent_review.llm import LLMClient
        with rebuild.no_model_requests(), self.assertRaisesRegex(RuntimeError, 'forbidden'):
            LLMClient.complete(None, [])

    def render(self, board, enumeration, **kwargs):
        self.assertFalse(kwargs['generate_prose'])
        out = board / 'review_out'
        atomic_json(out / 'verdicts.json', {'results': [{'rule_id': 'P11-A01'}], 'summary': {'是': 1}})
        atomic_json(out / 'report_data.json', {'review_details': {}})
        atomic_json(out / 'evidence/P11-A01.json', {'object_cards': [self.card]})
        return {'verdicts': {'summary': {'是': 1}}}

    def test_independent_new_run_uses_real_publisher_and_never_changes_source(self):
        before = rebuild.inventory(self.board);old = rebuild.inventory(self.report)
        from agent_review.stages import aggregate_stage, gate
        with patch.object(aggregate_stage, 'run', side_effect=self.render), \
                patch.object(gate, 'run', return_value={'passed': True, 'checks': [{'ok': True}]}) as gate_call:
            result = rebuild.rebuild(self.store, self.ident)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['report_rebuild_of'], self.ident)
        self.assertFalse(result['resume']);self.assertNotIn('parent_id', result)
        self.assertEqual(result['summary']['objects'], 1)
        self.assertEqual(result['summary']['model_request_count'], 0)
        self.assertTrue(result['summary']['model_analysis'])
        self.assertFalse(gate_call.call_args.kwargs['generate_prose'])
        self.assertFalse(gate_call.call_args.kwargs['allow_failed'])
        self.assertEqual(rebuild.inventory(self.board), before)
        self.assertEqual(rebuild.inventory(self.report), old)
        clone = Path(result['workspace']) / 'board'
        self.assertEqual(rebuild.protected_inventory(clone), rebuild.protected_inventory(self.board))
        published = json.loads((Path(result['report_dir']) / 'snapshot.json').read_text())
        self.assertIn('material_inputs/source.txt', published['sources'])
        self.assertEqual((Path(result['workspace']) / 'model-profile.json').stat().st_mode & 0o777, 0o600)

    def test_gate_rejection_does_not_publish_or_claim_new_analysis(self):
        from agent_review.stages import aggregate_stage, gate
        with patch.object(aggregate_stage, 'run', side_effect=self.render), \
                patch.object(gate, 'run', return_value={'passed': False, 'checks': []}), \
                self.assertRaisesRegex(ValueError, 'delivery gate'):
            rebuild.rebuild(self.store, self.ident)
        latest = self.store.runs()[0]
        self.assertEqual(latest['status'], 'failed')
        self.assertFalse((self.store.home / 'reports' / latest['id']).exists())
        self.assertFalse(latest['report_url'])


if __name__ == '__main__':
    unittest.main()
