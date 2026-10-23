"""Reconcile independent reviews and publish a validated representative delivery."""
import argparse, datetime, os, shutil, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kernel.util import read_json, write_json, resolve_board_path


def reconcile(board):
    from agent_review.stages import context_build, judge
    from agent_review.rule_modes import filter_enumeration_for_agent
    o = board / 'review_out'
    queue_path = o / 'delivery_followups.json'
    queue = read_json(queue_path)
    pairs = [p for p in queue['pairs'] if p['status'] == 'queued']
    if not pairs:
        return
    config = read_json(board / 'selection_delivery.json')
    import hashlib
    for rel,expected in config.get('raw_source_hashes',{}).items():
        if hashlib.sha256((board/rel).read_bytes()).hexdigest()!=expected:
            raise RuntimeError('Raw input changed before reconciliation: '+rel)
    by_rule = {}
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    for pair in pairs:
        rid, obj = pair['rule_id'], pair['object']
        if obj not in config['by_rule'].get(rid, []):
            raise ValueError('Follow-up outside frozen scope')
        by_rule.setdefault(rid, []).append(obj)
        archive = board / 'history' / config['run_key'] / 'before-reconciliation' / stamp / rid
        archive.mkdir(parents=True, exist_ok=True)
        for source in (o / 'agent_jobs' / rid).glob(obj + '.*'):
            if source.is_file():
                shutil.copy2(source, archive / source.name)
    kg = read_json(o / 'board_kg.json')
    scope = filter_enumeration_for_agent({'rule_ids': list(by_rule), 'by_rule': by_rule}, kg)
    previous_reuse=os.environ.get('AGENT_DELIVERY_REUSE_ANALYSIS')
    os.environ['AGENT_DELIVERY_REUSE_ANALYSIS']='1'
    try:
        context_build.run(board, kg, scope)
        result = judge.run(board, kg, scope, concurrency=2, resume=True, research_prepared=True)
    finally:
        if previous_reuse is None:os.environ.pop('AGENT_DELIVERY_REUSE_ANALYSIS',None)
        else:os.environ['AGENT_DELIVERY_REUSE_ANALYSIS']=previous_reuse
    from agent_review.delivery_recovery import revalidate
    recovery=revalidate(board)
    if recovery['recovered']:
        result.update(original_worker_failed=result['failed'],validation_transport_recovered=recovery['recovered'])
        result['done']+=recovery['recovered'];result['failed']-=recovery['recovered']
        write_json(o/'judge_result.json',result)
    from check_selection_delivery import audit
    audited={(r['rule_id'],r['object']):r['errors'] for r in audit(board)['records']}
    for pair in pairs:
        p = o / 'delivery_progress' / pair['rule_id'] / (pair['object'] + '.json')
        progress = read_json(p) if p.is_file() else {}
        pair['status'] = 'validated' if progress.get('status') == 'validated' and not audited.get((pair['rule_id'],pair['object']),['No current card']) else 'needs_repair'
        pair['reconciled_run_id'] = progress.get('run_id')
    write_json(queue_path, queue)
    write_json(o / 'delivery_reconciliation_result.json', result)
    if result.get('failed'):
        raise RuntimeError('Reconciliation has failed objects; publication not started')


def publish(board):
    from check_selection_delivery import audit
    from agent_review.stages import aggregate_stage
    o = board / 'review_out'
    checked=audit(board)
    audited={(r['rule_id'],r['object']):r['errors'] for r in checked['records']}
    queue=read_json(o / 'delivery_followups.json')
    # A targeted contract repair can complete a failed reconciliation without
    # repeating its electrical analysis. Reconcile the queue with actual runs.
    for pair in queue['pairs']:
        if pair['status']=='needs_repair' and not audited.get((pair['rule_id'],pair['object']),['No current card']):
            pair['status']='validated'
    write_json(o / 'delivery_followups.json',queue)
    pending = [p for p in queue['pairs'] if p['status'] != 'validated']
    if pending:
        raise RuntimeError('Independent reconciliations are unfinished')
    if checked['errors']:
        raise RuntimeError('Source/run audit failed; publication not started')
    config = read_json(board / 'selection_delivery.json')
    enumeration = read_json(o / 'delivery_enumeration.json')
    names = ['agent_enumeration.json', 'verdicts.json', 'findings.json', 'evidence',
             'report_data.json', 'expert_review.html', 'engineer_review.html',
             'stress_paths', 'delivery_coverage.json', 'delivery_material_requests.json']
    resolved_out=o.resolve()
    if not resolved_out.is_relative_to(board.resolve()) or any(not (o/name).resolve().is_relative_to(resolved_out) for name in names):
        raise RuntimeError('Publication rollback targets escaped the board output directory')
    # Extend the report builder's transaction to aggregation and post-build audits.
    with tempfile.TemporaryDirectory(prefix='selection-publication-') as tmp:
        backup=Path(tmp); existed={name:(o/name).exists() for name in names}
        for name in names:
            source=o/name; target=backup/name; target.parent.mkdir(parents=True,exist_ok=True)
            if source.is_dir():shutil.copytree(source,target)
            elif source.is_file():shutil.copy2(source,target)
        try:
            # Renderer and aggregator read the same frozen explicit selection.
            write_json(o / 'agent_enumeration.json', enumeration)
            result = aggregate_stage.run(
                board, enumeration, materials=read_json(o / 'materials.json'),
                figure_rule_ids=config['rule_ids'],
                figure_objects=sorted({obj for objects in config['by_rule'].values() for obj in objects}),
            )
            checked = audit(board, published=True)
            if checked['errors']:raise RuntimeError('Published media audit failed')
            from check_detail_presentation import check as check_details
            check_details(board,published=True)
        except Exception:
            for name in names:
                target=o/name
                if target.is_dir():shutil.rmtree(target)
                else:target.unlink(missing_ok=True)
                if existed[name]:
                    if (backup/name).is_dir():shutil.copytree(backup/name,target)
                    else:shutil.copy2(backup/name,target)
            raise
    write_json(o / 'selection_delivery_publication.json', {
        'run_key': config['run_key'], 'published_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'status': 'published' if not checked['errors'] else 'audit_failed',
        'selected_pairs': checked['selected_pairs'], 'errors': checked['errors'],
    })
    receipt_path=o/'selection_delivery_run.json';receipt=read_json(receipt_path)
    receipt['first_pass_status']=receipt['status']
    if 'judge' in receipt:receipt['first_pass_judge']=receipt.pop('judge')
    receipt.update(status='published',published_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   final_counts=read_json(o/'delivery_coverage.json')['counts'],final_audit_errors=checked['errors'])
    write_json(receipt_path,receipt)
    if checked['errors']:
        raise RuntimeError('Published media audit failed')
    return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--board', required=True)
    ap.add_argument('--reconcile', action='store_true')
    ap.add_argument('--repair', action='store_true')
    ap.add_argument('--publish', action='store_true')
    ap.add_argument('--revalidate', action='store_true')
    a = ap.parse_args()
    b = resolve_board_path(a.board)
    os.environ['AGENT_JUDGE_MODEL'] = 'gpt-6.1-sol'
    os.environ.setdefault('AGENT_DELIVERY_TURN_TIMEOUT_MS', '600000')
    if not os.environ.get('SCHEMATIC_CODEX'):
        options = list((Path(os.environ['LOCALAPPDATA']) / 'OpenAI/Codex/bin').glob('*/codex.exe'))
        os.environ['SCHEMATIC_CODEX'] = str(max(options, key=lambda p: p.stat().st_mtime))
    if a.repair:
        from agent_review.repair_cards import repair
        cfg=read_json(b/'selection_delivery.json')
        queue=read_json(b/'review_out/delivery_followups.json')
        semantic={(p['rule_id'],p['object']) for p in queue['pairs'] if p['status']=='queued'}
        remaining={rid:[obj for obj in objects if (rid,obj) not in semantic] for rid,objects in cfg['by_rule'].items()}
        from check_delivery_geometry import check as check_geometry
        geometry=check_geometry(b)
        repair(b,read_json(b/'review_out/board_kg.json'),{'by_rule':remaining},concurrency=2,geometry_feedback=geometry['incomplete'])
    if a.reconcile:
        reconcile(b)
    if a.revalidate:
        from agent_review.delivery_recovery import revalidate
        recovered = revalidate(b)
        # This also supports a controller process started before transport
        # recovery existed. Preserve its worker outcome and record the real
        # post-validation state, without starting another model turn.
        from check_selection_delivery import audit
        checked = audit(b)
        by_pair = {(r['rule_id'],r['object']):r['errors'] for r in checked['records']}
        queue_path=b/'review_out/delivery_followups.json'
        queue=read_json(queue_path)
        for pair in queue['pairs']:
            errors=by_pair.get((pair['rule_id'],pair['object']),['No current card'])
            pair['status']='needs_repair' if errors else 'validated'
        write_json(queue_path,queue)
        write_json(b/'review_out/delivery_revalidation_result.json',{
            'recovered':recovered['recovered'],
            'validated_pairs':sum(not r['errors'] for r in checked['records']),
            'source_errors':checked['errors'],
        })
    if a.publish:
        publish(b)
