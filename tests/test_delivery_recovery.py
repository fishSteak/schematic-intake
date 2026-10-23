import json,tempfile,unittest
from pathlib import Path
from agent_review.repair_cards import interrupted_analysis, completed_delivery_check, recover_source_jobs
from agent_review.delivery_recovery import completed_phases


class InterruptedDeliveryTests(unittest.TestCase):
    def test_dispatch_recovery_requires_exact_current_card_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            board=Path(tmp);folder=board/'review_out/agent_jobs/X';folder.mkdir(parents=True)
            (folder/'O.json').write_text(json.dumps({'_execution':{'fingerprint':'correct'}}),encoding='utf-8')
            for name,signature in [('a','wrong'),('b','correct')]:
                archive=board/'history'/name;archive.mkdir(parents=True)
                (archive/'judge_jobs.json').write_text(json.dumps({'jobs':[{'job_id':name,'rule_id':'X','objects':['O'],'execution_hash':signature}]}),encoding='utf-8')
            jobs=recover_source_jobs(board,{}, {'by_rule':{'X':['O']}})
            self.assertEqual(jobs[('X','O')]['execution_hash'],'correct')
            self.assertEqual(jobs[('X','O')]['job_id'],'b')

    def test_repair_cannot_skip_check_using_an_old_runs_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            board=Path(tmp);log=board/'review_out/agent_jobs/X/O.run.jsonl'
            log.parent.mkdir(parents=True)
            rows=[{'type':'phase_completed','phase':'analysis_draft','run_id':'old'},
                  {'type':'phase_completed','phase':'decisive_check','run_id':'old'},
                  {'type':'phase_completed','phase':'analysis_draft','run_id':'current'}]
            log.write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8')
            self.assertFalse(completed_delivery_check(board,'X','O'))
            self.assertFalse(completed_delivery_check(board,'X','O',run_id='current'))
            self.assertTrue(completed_delivery_check(board,'X','O',run_id='current',requires_check=False))
            rows.append({'type':'phase_completed','phase':'decisive_check','run_id':'current'})
            log.write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8')
            self.assertTrue(completed_delivery_check(board,'X','O',run_id='current'))

    def test_validation_transport_recovery_needs_this_runs_completed_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'run.jsonl'
            rows=[{'type':'phase_completed','phase':'analysis_draft','run_id':'current'},
                  {'type':'phase_completed','phase':'decisive_check','run_id':'old'}]
            log.write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8')
            self.assertEqual(completed_phases(log,'current',requires_check=True),[])
            self.assertEqual(completed_phases(log,'current',requires_check=False),['analysis_draft'])
            self.assertEqual(completed_phases(log,'missing',requires_check=False),[])
            rows.append({'type':'phase_completed','phase':'decisive_check','run_id':'current'})
            log.write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8')
            self.assertEqual(completed_phases(log,'current',requires_check=True),['analysis_draft','decisive_check'])

    def test_only_matching_source_snapshot_is_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            board=Path(tmp);rel=Path('review_out/agent_jobs/P99/O1')
            context=board/rel.with_suffix('.context.json');context.parent.mkdir(parents=True)
            context.write_text('{"source":"first"}',encoding='utf-8')
            snapshot=board/'history/review-checkpoints/run';source=snapshot/rel.with_suffix('.context.json')
            source.parent.mkdir(parents=True);source.write_text(context.read_text(),encoding='utf-8')
            (snapshot/rel.with_suffix('.analysis.md')).write_text('# Analysis\n## Completed derivation\nFrom actual sources.',encoding='utf-8')
            log=board/rel.with_suffix('.run.jsonl')
            log.write_text(json.dumps({'event':{'message':'review interrupted; analysis checkpoint: '+str(snapshot)}}),encoding='utf-8')
            self.assertEqual(interrupted_analysis(board,'P99','O1'),str(snapshot.resolve()))
            context.write_text('{"source":"changed"}',encoding='utf-8')
            self.assertIsNone(interrupted_analysis(board,'P99','O1'))


if __name__=='__main__':unittest.main()
