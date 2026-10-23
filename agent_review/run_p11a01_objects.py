# -*- coding: utf-8 -*-
"""仅对指定对象跑 P11-A01 Agent Judge。

python -m agent_review.run_p11a01_objects --board boards\\0918全量 --objects R4,R14
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime
import shutil
import sys
from pathlib import Path

from agent_review.batching import safe_object_name
from agent_review.defaults import DEFAULT_CONCURRENCY
from agent_review.rule_modes import filter_enumeration_for_agent, load_agent_rule_ids, _planned_for_rules
from agent_review.stages import aggregate_stage, context_build, gate, judge
from agent_review.workbench import build_judge_workbench
from kernel.util import out_dir, read_json, resolve_board_path, write_json


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True)
    ap.add_argument("--objects", required=True, help="逗号分隔，如 R4,R14")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    ap.add_argument("--skip-aggregate", action="store_true")
    ap.add_argument("--contract-v1", action="store_true", help="仅对本次指定对象启用并记录 review.v1 契约")
    ap.add_argument("--analysis-checkpoint", type=Path, help="仅 critique：从失败执行的本地快照恢复分析，核对当前工作台后重新自检落卡")
    ap.add_argument("--resume-card", action="store_true", help="仅 critique：分析自检已写盘后，只续接汇总卡生成")
    ap.add_argument(
        "--critique",
        action="store_true",
        help="难例续聊：保留已有 analysis，再追问一轮并同步 JSON（不清空）",
    )
    args = ap.parse_args(argv)
    if args.analysis_checkpoint:
        if not args.critique:
            ap.error("--analysis-checkpoint requires --critique")
        os.environ["AGENT_ANALYSIS_CHECKPOINT_ROOT"] = str(args.analysis_checkpoint.resolve())
    if args.resume_card:
        if not args.critique:
            ap.error("--resume-card requires --critique")
        os.environ["AGENT_RESUME_CARD_ONLY"] = "1"

    board = resolve_board_path(args.board)
    o = out_dir(board)
    objs = [x.strip() for x in args.objects.split(",") if x.strip()]
    if not objs:
        print("FAIL\tempty --objects", file=sys.stderr)
        return 2
    if args.contract_v1:
        registry_path = o / "review_contracts.json"
        registry = read_json(registry_path) if registry_path.is_file() else {}
        registry.setdefault("P11-A01", {}).update({obj: "review.v1" for obj in objs})
        write_json(registry_path, registry)

    kg = read_json(o / "board_kg.json")
    materials = read_json(o / "materials.json") if (o / "materials.json").is_file() else {}
    enum_full = read_json(o / "agent_enumeration.json")
    by_rule = dict(enum_full.get("by_rule") or {})
    all_r = list(by_rule.get("P11-A01") or [])
    missing = [x for x in objs if x not in all_r and x not in (kg.get("components") or {})]
    if missing:
        print(f"WARN\tobjects not in P11-A01 enum (仍尝试): {missing}", file=sys.stderr)

    agent_enum = filter_enumeration_for_agent(enum_full, kg)
    agent_enum["rule_ids"] = ["P11-A01"]
    agent_enum["agent_rule_ids"] = ["P11-A01"]
    agent_enum["by_rule"] = {"P11-A01": objs}
    planned, st = _planned_for_rules(kg, agent_enum["by_rule"])
    agent_enum["planned_context"] = planned
    agent_enum["stats"] = st
    write_json(o / "agent_enumeration_scoped.json", agent_enum)

    print(f"BOARD\t{board}")
    print(f"SCOPE\tP11-A01 objects={objs}")
    print(
        f"MODE\t{'critique' if args.critique else 'agent'} concurrency={args.concurrency}"
    )

    # A fresh scoped review cannot silently reuse an older analysis or verdict.
    if not args.critique:
        snapshot = board / "history" / datetime.now().strftime("%Y%m%d-%H%M%S-scoped-P11-A01")
        for obj in objs:
            for suffix in (".json", ".analysis.md", ".run.jsonl"):
                previous = o / "agent_jobs" / "P11-A01" / f"{safe_object_name(obj)}{suffix}"
                if previous.is_file():
                    snapshot.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(previous, snapshot / previous.name)
                    previous.unlink()

    # 预写工作台，便于 Agent 首读
    for obj in objs:
        wb = build_judge_workbench(board, kg, obj, "P11-A01")
        dest = o / "agent_jobs" / "P11-A01" / f"{safe_object_name(obj)}.context.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_json(dest, wb)
        print(f"OK\tworkbench {obj} neighbors={len(wb.get('neighbors') or [])}")

    ctx = context_build.run(
        board,
        kg,
        agent_enum,
        rebuild=False,
        use_sdk=False,
        concurrency=args.concurrency,
    )
    cov = ctx.get("coverage") or {}
    print(f"OK\tcontext_build ready={cov.get('ready')}/{cov.get('planned')} sealed={cov.get('sealed')}")

    jres = judge.run(
        board,
        kg,
        agent_enum,
        dry_run=False,
        concurrency=args.concurrency,
        resume=False,
        critique=bool(args.critique),
    )
    print(f"OK\tjudge done={jres.get('done')} failed={jres.get('failed')}")

    # 非本批对象不在此脚本里清掉整目录（judge 已按 agent_enum 清空并只写本批）
    for obj in objs:
        p = o / "agent_jobs" / "P11-A01" / f"{safe_object_name(obj)}.json"
        inv = p.with_suffix(".invalid.json")
        fail = p.with_suffix(".failed.json")
        if p.is_file():
            card = read_json(p)
            if card.get('schema_version')=='review.v1':
                from agent_review.review_contract import project
                card = project(card)
            print(
                f"CARD\t{obj}\tverdict={card.get('verdict_zh')}\t"
                f"states={len(card.get('states') or [])}\t"
                f"summary={(card.get('summary') or '')[:120]}"
            )
        elif inv.is_file():
            print(f"INVALID\t{obj}\t{inv}")
        elif fail.is_file():
            print(f"FAILED\t{obj}\t{read_json(fail)}")
        else:
            print(f"MISSING\t{obj}")

    if not args.skip_aggregate and not jres.get("failed"):
        # Refresh the canonical report from existing cards; never fabricate unreviewed objects.
        enum_for_agg = dict(enum_full)
        enum_for_agg["agent_rule_ids"] = load_agent_rule_ids()
        agg = aggregate_stage.run(
            board,
            enum_for_agg,
            materials=materials,
            context_meta=ctx,
            figure_rule_ids=["P11-A01"],
            figure_objects=objs,
        )
        sm = agg["verdicts"]["summary"]
        print(
            f"OK\taggregate 是={sm.get('是')} 否={sm.get('否')} "
            f"INSUFFICIENT={sm.get('INSUFFICIENT')} 不适用={sm.get('不适用')}"
        )
        g = gate.run(
            board,
            enum_for_agg,
            materials=materials,
            context_meta=ctx,
            allow_failed=True,
        )
        print(f"OK\tgate passed={g.get('passed')}")
        print(f"REPORT\t{o / 'expert_review.html'}")
    elif jres.get("failed"):
        print("REPORT_UNCHANGED\tJudge 未完成，不以缺卡结果覆盖已发布页面")

    write_json(
        o / "scoped_run_r4_r14.json",
        {
            "board": str(board),
            "objects": objs,
            "judge": jres,
            "agent_rules_config": load_agent_rule_ids(),
        },
    )
    return 0 if not jres.get("failed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
