# -*- coding: utf-8 -*-
"""全量程序自评 + 仅 P11-A01 前 N 只电阻 Agent 判定（本批跳过 P11-A03 Agent）。

python -m agent_review.run_scoped_p11a01 --board boards\\0918全量 --limit 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_review.batching import safe_object_name
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
from kernel.util import out_dir, read_json, resolve_board_path, write_json


def _defer_card(o: Path, rule_id: str, obj: str, reason: str) -> dict:
    sn = safe_object_name(obj)
    cases = {}
    stress = {}
    cp = o / "operating_cases" / rule_id / f"{sn}.json"
    sp = o / "program_stress" / rule_id / f"{sn}.json"
    if cp.is_file():
        cases = read_json(cp)
    if sp.is_file():
        stress = read_json(sp)
    states = []
    for c in cases.get("cases") or []:
        sid = c.get("case_id")
        st = next((s for s in (stress.get("states") or []) if s.get("case_id") == sid), {})
        states.append(
            {
                "case_id": sid,
                "name_zh": c.get("name_zh") or st.get("name_zh"),
                "P": st.get("P"),
                "V_across": st.get("V_across"),
                "role": st.get("role") or "deferred",
            }
        )
    if not states:
        states = [{"case_id": "deferred", "name_zh": "未评", "role": "deferred"}]
    path_text = (cases.get("path") or {}).get("text") or reason
    elec = {
        "P_rated": stress.get("P_rated"),
        "V_rated": stress.get("V_rated"),
        "P_max": stress.get("P_max"),
        "V_max": stress.get("V_max"),
        "cite": stress.get("cite"),
    }
    return {
        "rule_id": rule_id,
        "object": obj,
        "verdict_zh": "待确认",
        "location_one_liner": obj,
        "reasoning_steps": [reason, "本对象未跑 Agent 判定"],
        "summary": f"{obj} 未评",
        "action": "后续批次补跑 Agent",
        "phenomenon": path_text,
        "calc": "未评",
        "stuck": reason,
        "conclusion": "待确认（未评）",
        "evidence_refs": [
            {"path": f"review_out/operating_cases/{rule_id}/{sn}.json", "note": "cases"},
            {"path": f"review_out/program_stress/{rule_id}/{sn}.json", "note": "stress"},
        ],
        "electrical": elec,
        "states": states,
        "meta": {"deferred": True, "batch_skip": True},
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True)
    ap.add_argument("--limit", type=int, default=3, help="P11-A01 Agent 对象数")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    ap.add_argument("--from-stage", default="program", choices=[
        "prep", "enumerate", "program", "cases", "calc", "context_build", "judge", "aggregate", "gate"
    ])
    args = ap.parse_args(argv)

    board = resolve_board_path(args.board)
    o = out_dir(board)
    stages = ["prep", "enumerate", "program", "cases", "calc", "context_build", "judge", "aggregate", "gate"]
    stages = stages[stages.index(args.from_stage) :]

    print(f"BOARD\t{board}")
    print(f"SCOPE\tP11-A01 first {args.limit} resistors; P11-A03 Agent skipped")
    print(f"STAGES\t{','.join(stages)}")
    print(f"MODE\tagent concurrency={args.concurrency}")

    ctx: dict = {"board": board}

    if "prep" in stages:
        ctx.update(prep.run(board, reuse_kg=True))
        print(f"OK\tprep reused={ctx.get('reused')}")
    else:
        ctx["materials"] = read_json(o / "materials.json")
        ctx["kg"] = read_json(o / "board_kg.json")

    if "enumerate" in stages:
        ctx["enumeration"] = enumerate_stage.run(
            board, ctx["kg"], rule_ids=ctx["materials"].get("rule_ids")
        )
        print(f"OK\tenumerate pairs={ctx['enumeration']['stats'].get('judge_pairs')}")
    else:
        ctx["enumeration"] = read_json(o / "agent_enumeration.json")

    if "program" in stages:
        prog, enum2 = program_stage.run(board, ctx["kg"], ctx["enumeration"])
        ctx["program"] = prog
        ctx["enumeration"] = enum2
    else:
        if (o / "program_candidate.json").is_file():
            ctx["program"] = read_json(o / "program_candidate.json")

    if "cases" in stages:
        ctx["cases"] = cases_stage.run(board, ctx["kg"])
    if "calc" in stages:
        ctx["calc"] = calc_stage.run(board, ctx["kg"])

    # Scope Agent: only P11-A01 first N
    enum_full = dict(ctx["enumeration"])
    by_rule = dict(enum_full.get("by_rule") or {})
    all_r = list(by_rule.get("P11-A01") or [])
    all_c = list(by_rule.get("P11-A03") or [])
    scoped = all_r[: max(0, args.limit)]
    print(f"P11-A01\tagent_objects={scoped} (of {len(all_r)})")

    from agent_review.rule_modes import _planned_for_rules

    agent_enum = filter_enumeration_for_agent(enum_full, ctx["kg"])
    agent_enum["rule_ids"] = ["P11-A01"]
    agent_enum["agent_rule_ids"] = ["P11-A01"]
    agent_enum["by_rule"] = {"P11-A01": scoped}
    planned, st = _planned_for_rules(ctx["kg"], agent_enum["by_rule"])
    agent_enum["planned_context"] = planned
    agent_enum["stats"] = st
    write_json(o / "agent_enumeration_scoped.json", agent_enum)

    enum_full["agent_rule_ids"] = ["P11-A01"]
    enum_full["program_rule_ids"] = split_rule_ids(enum_full.get("rule_ids")).get("program") or []
    write_json(o / "agent_enumeration.json", enum_full)
    ctx["enumeration"] = enum_full

    if "context_build" in stages:
        ctx["context"] = context_build.run(
            board,
            ctx["kg"],
            agent_enum,
            rebuild=False,
            use_sdk=False,
            concurrency=args.concurrency,
        )
        cov = ctx["context"].get("coverage") or {}
        print(
            f"OK\tcontext_build ready={cov.get('ready')}/{cov.get('planned')} "
            f"sealed={cov.get('sealed')}"
        )

    if "judge" in stages:
        ctx["judge"] = judge.run(
            board,
            ctx["kg"],
            agent_enum,
            dry_run=False,
            concurrency=args.concurrency,
            resume=False,
        )
        print(
            f"OK\tjudge done={ctx['judge'].get('done')} failed={ctx['judge'].get('failed')} "
            f"dry_run=False"
        )
        if ctx["judge"].get("failed"):
            print("WARN\tjudge had failures; continuing to aggregate", file=sys.stderr)

    # Judge 会清空 P11-A01 目录：Agent 卡写完后再补 deferred
    for rid, objs, reason in (
        ("P11-A01", all_r[args.limit :], "本轮仅评前若干电阻，其余未评"),
        ("P11-A03", all_c, "本轮未跑 P11-A03 Agent"),
    ):
        d = o / "agent_jobs" / rid
        d.mkdir(parents=True, exist_ok=True)
        if rid == "P11-A03":
            for old in list(d.glob("*.json")):
                if not any(
                    old.name.endswith(s) for s in (".context.json", ".failed.json", ".invalid.json")
                ):
                    old.unlink(missing_ok=True)
        for obj in objs:
            write_json(d / f"{safe_object_name(obj)}.json", _defer_card(o, rid, obj, reason))
        print(f"OK\tdeferred {rid} n={len(objs)}")

    if "aggregate" in stages:
        ctx["aggregate"] = aggregate_stage.run(
            board,
            ctx["enumeration"],
            materials=ctx.get("materials") or read_json(o / "materials.json"),
            context_meta=ctx.get("context"),
        )
        sm = ctx["aggregate"]["verdicts"]["summary"]
        print(
            f"OK\taggregate 是={sm.get('是')} 否={sm.get('否')} "
            f"INSUFFICIENT={sm.get('INSUFFICIENT')} 不适用={sm.get('不适用')}"
        )

    if "gate" in stages:
        # scoped run: allow incomplete relative to full-board agent seal
        ctx["gate"] = gate.run(
            board,
            ctx["enumeration"],
            materials=ctx.get("materials") or read_json(o / "materials.json"),
            context_meta=ctx.get("context"),
            allow_failed=True,
        )
        print(f"OK\tgate passed={ctx['gate'].get('passed')}")
        print(f"REPORT\t{o / 'expert_review.html'}")
        print(f"REPORT\t{o / 'engineer_review.html'}")

    write_json(
        o / "scoped_run.json",
        {
            "board": str(board),
            "p11_a01_agent": scoped,
            "p11_a03_agent": [],
            "judge": ctx.get("judge"),
            "gate": ctx.get("gate"),
            "agent_rules_config": load_agent_rule_ids(),
        },
    )
    return 0 if (ctx.get("gate") or {}).get("passed", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
