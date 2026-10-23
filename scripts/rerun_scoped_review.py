"""Re-review explicit existing report objects and publish through the normal pipeline."""
import argparse
import datetime
import hashlib
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kernel.util import read_json, write_json, resolve_board_path
from agent_review.rule_modes import filter_enumeration_for_agent
from agent_review.stages import context_build, judge, aggregate_stage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--board', required=True)
    ap.add_argument('--rule', required=True)
    ap.add_argument('--objects', nargs='+', required=True)
    ap.add_argument('--model', required=True)
    ap.add_argument('--concurrency', type=int, default=2)
    args = ap.parse_args()
    board = resolve_board_path(args.board)
    out = board / 'review_out'
    full = read_json(out / 'agent_enumeration.json')
    allowed = set(full['by_rule'].get(args.rule, []))
    if not set(args.objects) <= allowed:
        raise ValueError('Scope must be existing enumerated report objects')
    archive = board / 'history' / ('scoped-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    archive.mkdir(parents=True)
    for name in ('verdicts.json', 'report_data.json', 'expert_review.html', 'agent_enumeration.json'):
        if (out / name).exists():
            shutil.copy2(out / name, archive / name)
    for source in (out / 'agent_jobs' / args.rule).glob('*'):
        if source.is_file():
            dest = archive / 'agent_jobs' / args.rule / source.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
    untouched = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (out / 'agent_jobs').glob('*/*.json')
                 if p.name.count('.') == 1 and not (p.parent.name == args.rule and p.stem in args.objects)}
    os.environ['AGENT_JUDGE_MODEL'] = args.model
    if not os.environ.get('SCHEMATIC_CODEX'):
        candidates = list((Path(os.environ['LOCALAPPDATA']) / 'OpenAI/Codex/bin').glob('*/codex.exe'))
        if candidates:
            os.environ['SCHEMATIC_CODEX'] = str(max(candidates, key=lambda p: p.stat().st_mtime))
    kg = read_json(out / 'board_kg.json')
    scope = filter_enumeration_for_agent({'rule_ids': [args.rule], 'by_rule': {args.rule: args.objects}}, kg)
    status = {'status': 'running', 'rule': args.rule, 'objects': args.objects,
              'model': args.model, 'concurrency': args.concurrency, 'archive': str(archive)}
    receipt = out / 'scoped_review_run.json'
    write_json(receipt, status)
    try:
        context = context_build.run(board, kg, scope)
        # Resume validates execution fingerprints, including per-rule prompts.
        # Existing autonomous evidence remains available; judge may inspect raw materials.
        result = judge.run(board, kg, scope, concurrency=args.concurrency, resume=True, research_prepared=True)
        status['judge'] = result
        if result.get('failed'):
            raise RuntimeError('Scoped judge failed; published report preserved')
        for rel, expected in untouched.items():
            if hashlib.sha256((out / rel).read_bytes()).hexdigest() != expected:
                raise RuntimeError('An out-of-scope card changed: ' + rel)
        aggregate_stage.run(board, full, materials=read_json(out / 'materials.json'),
                            context_meta=context, figure_rule_ids=[args.rule], figure_objects=args.objects)
        status['status'] = 'completed'
    except Exception as exc:
        status.update(status='incomplete', error=str(exc))
        raise
    finally:
        write_json(receipt, status)


if __name__ == '__main__':
    main()
