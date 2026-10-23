# -*- coding: utf-8 -*-
"""混合评审阶段机：程序自评 + P11 工况/计算 + Agent 精简判定。

python -m agent_review.run --board boards\\0918全量
python -m agent_review.run --board boards\\0918全量 --dry-run
python -m agent_review.run --board boards\\0918全量 --resume
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from agent_review.defaults import DEFAULT_CONCURRENCY
from agent_review.rule_modes import filter_enumeration_for_agent, load_agent_rule_ids, split_rule_ids
from agent_review.stages import (
    aggregate_stage,
    calc_stage,
    cases_stage,
    context_build,
    enumerate_stage,
    gate,
    judge,
    prep,
    program_stage,
)
from kernel.util import ROOT, out_dir, read_json, resolve_board_path, write_json
from agent_review.enumerate import load_program_rules


STAGES = [
    "prep",
    "enumerate",
    "program",
    "cases",
    "calc",
    "context_build",
    "judge",
    "stress_figures",
    "aggregate",
    "gate",
]


def run(
    board_dir: str | Path,
    *,
    resume: bool = False,
    rebuild_context: bool = False,
    dry_run: bool = False,
    rule_ids: list[str] | None = None,
    from_stage: str | None = None,
    only_stage: str | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    reuse_kg: bool = True,
    allow_failed: bool = False,
    use_sdk_context: bool = False,
) -> int:
    board = resolve_board_path(board_dir)
    o = out_dir(board)
    start = from_stage or "prep"
    stages = [only_stage] if only_stage else STAGES[STAGES.index(start) :]

    print(f"BOARD\t{board}")
    print(f"STAGES\t{','.join(stages)}")
    print(f"ROOT\t{ROOT}")
    print(f"AGENT_RULES\t{','.join(load_agent_rule_ids())}")
    print(f"MODE\t{'dry-run' if dry_run else 'agent'} concurrency={concurrency}")

    ctx: dict[str, Any] = {"board": board}
    state_path = o / "agent_review_state.json"

    if "prep" in stages:
        ctx.update(prep.run(board, reuse_kg=reuse_kg))
        print(f"OK\tprep reused={ctx.get('reused')}")
    else:
        ctx["materials"] = read_json(o / "materials.json")
        ctx["kg"] = read_json(o / "board_kg.json")

    scope = list(rule_ids) if rule_ids else list(load_program_rules()["rule_ids"])
    ctx["materials"]["rule_ids"] = scope
    ctx["materials"]["rule_ids_explicit"] = bool(rule_ids)
    write_json(o / "materials.json", ctx["materials"])

    if "enumerate" in stages:
        ctx["enumeration"] = enumerate_stage.run(
            board, ctx["kg"], rule_ids=scope
        )
        st = ctx["enumeration"]["stats"]
        modes = split_rule_ids(ctx["enumeration"].get("rule_ids"))
        print(
            f"OK\tenumerate rules={st['rules']} agent={len(modes['agent'])} "
            f"program={len(modes['program'])} pairs={st['judge_pairs']}"
        )
        for rid, reason in (ctx["enumeration"].get("rule_na_reasons") or {}).items():
            g = (ctx["enumeration"].get("rule_gated") or {}).get(rid) or {}
            extra = f" candidates={g.get('n')}" if g.get("n") else ""
            print(f"GATE\t{rid}\tNA{extra}\t{reason}")
    else:
        ctx["enumeration"] = read_json(o / "agent_enumeration.json")
        if ctx["enumeration"].get("rule_ids") != scope:
            ctx["enumeration"] = enumerate_stage.run(board, ctx["kg"], rule_ids=scope)

    if "program" in stages:
        prog, enum2 = program_stage.run(board, ctx["kg"], ctx["enumeration"])
        ctx["program"] = prog
        ctx["enumeration"] = enum2
    else:
        if (o / "program_candidate.json").is_file():
            ctx["program"] = read_json(o / "program_candidate.json")

    if "cases" in stages:
        ctx["cases"] = cases_stage.run(board, ctx["kg"], skip_agent=dry_run)
    if "calc" in stages:
        ctx["calc"] = calc_stage.run(board, ctx["kg"])

    agent_enum = filter_enumeration_for_agent(ctx["enumeration"], ctx.get("kg"))

    if "context_build" in stages:
        if agent_enum.get("rule_ids"):
            ctx["context"] = context_build.run(
                board,
                ctx["kg"],
                agent_enum,
                rebuild=rebuild_context,
                use_sdk=use_sdk_context and not dry_run,
                concurrency=concurrency,
            )
            cov = ctx["context"].get("coverage") or {}
            print(
                f"OK\tcontext_build ready={cov.get('ready')}/{cov.get('planned')} "
                f"sealed={cov.get('sealed')} reused={ctx['context'].get('reused')}"
            )
        else:
            ctx["context"] = {"coverage": {"skipped": True}, "reused": True}
            print("OK\tcontext_build skipped (no agent rules)")
    else:
        from agent_review.context_store import ContextStore

        ctx["context"] = {"coverage": ContextStore(o).ready_coverage()}

    if "judge" in stages:
        if agent_enum.get("rule_ids"):
            ctx["judge"] = judge.run(
                board,
                ctx["kg"],
                agent_enum,
                dry_run=dry_run,
                concurrency=concurrency,
                resume=resume,
            )
            print(
                f"OK\tjudge done={ctx['judge'].get('done')} failed={ctx['judge'].get('failed')} "
                f"dry_run={dry_run} rules={len(agent_enum.get('rule_ids') or [])}"
            )
            if not dry_run:
                if ctx['judge'].get('failed'):
                    from agent_review.repair_cards import repair
                    ctx['judge']['repairs']=repair(board,ctx['kg'],agent_enum,concurrency=concurrency)
                    ctx['judge']['initial_failed']=ctx['judge']['failed']
                    ctx['judge']['done']+=ctx['judge']['failed']
                    ctx['judge']['failed']=0
                from agent_review.evidence_followup import run as finish_evidence
                ctx['evidence_followup']=finish_evidence(board,ctx['kg'],agent_enum,concurrency=concurrency)
        else:
            ctx["judge"] = {"done": 0, "failed": 0, "dry_run": dry_run, "jobs": 0}
            print("OK\tjudge skipped (no agent rules)")

    if "stress_figures" in stages:
        from agent_review.stress_figures import run as run_stress_figures

        ctx["stress_figures"] = run_stress_figures(board)
        print(
            f"OK\tstress_figures drawn={ctx['stress_figures'].get('drawn')} "
            f"ungenerated={ctx['stress_figures'].get('ungenerated')}"
        )

    if "aggregate" in stages:
        ctx["aggregate"] = aggregate_stage.run(
            board,
            ctx["enumeration"],
            materials=ctx.get("materials"),
            context_meta=ctx.get("context"),
        )
        sm = ctx["aggregate"]["verdicts"]["summary"]
        print(
            f"OK\taggregate 是={sm.get('是')} 否={sm.get('否')} "
            f"INSUFFICIENT={sm.get('INSUFFICIENT')} 不适用={sm.get('不适用')}"
        )
    if "gate" in stages:
        af = allow_failed or dry_run
        ctx["gate"] = gate.run(
            board,
            ctx["enumeration"],
            materials=ctx.get("materials"),
            context_meta=ctx.get("context"),
            allow_failed=af,
        )
        print(f"OK\tgate passed={ctx['gate'].get('passed')}")
        print(f"REPORT\tpython -m agent_review.serve --board {board}")

    write_json(
        state_path,
        {
            "board": str(board),
            "dry_run": dry_run,
            "stages": stages,
            "agent_rule_ids": load_agent_rule_ids(),
            "gate": ctx.get("gate"),
        },
    )
    if ctx.get("gate") and not ctx["gate"].get("passed") and not dry_run:
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="混合评审：程序自评 + P11 工况/计算 + Agent 判定")
    ap.add_argument("--board", required=True)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--rebuild-context", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="Agent 项不调 SDK；程序/工况/计算仍实跑")
    ap.add_argument("--rule-ids", default=None, help="逗号分隔，如 P11-A01,P05-A01")
    ap.add_argument("--from-stage", default=None, choices=STAGES)
    ap.add_argument("--only-stage", default=None, choices=STAGES)
    ap.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"同时开多少路 Codex CLI 深审（默认 {DEFAULT_CONCURRENCY}）",
    )
    ap.add_argument("--no-reuse-kg", action="store_true")
    ap.add_argument("--allow-failed", action="store_true")
    ap.add_argument("--enrich-context", "--use-sdk-context", dest="use_sdk_context", action="store_true")
    args = ap.parse_args(argv)
    ids = None
    if args.rule_ids:
        ids = [x.strip() for x in str(args.rule_ids).split(",") if x.strip()]
    try:
        return run(
            args.board,
            resume=args.resume,
            rebuild_context=args.rebuild_context,
            dry_run=args.dry_run,
            rule_ids=ids,
            from_stage=args.from_stage,
            only_stage=args.only_stage,
            concurrency=args.concurrency,
            reuse_kg=not args.no_reuse_kg,
            allow_failed=args.allow_failed,
            use_sdk_context=args.use_sdk_context,
        )
    except Exception as e:
        print(f"FAIL\t{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
