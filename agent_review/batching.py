# -*- coding: utf-8 -*-
"""Fine batches: one rule_id；P11 默认一对象一深审，其余 ≤3。"""

from __future__ import annotations

from typing import Any

from agent_review.rule_modes import is_deep_three_turn

DEFAULT_BATCH_SIZE = 3


def safe_object_name(obj: str) -> str:
    return (
        obj.replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace("+", "p")
        .replace(" ", "_")
    )


def batch_size_for_rule(rule_id: str, default: int = DEFAULT_BATCH_SIZE) -> int:
    # 深审：一对象一 job
    if is_deep_three_turn(rule_id):
        return 1
    return default


def build_judge_jobs(
    enumeration: dict[str, Any],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    out_prefix: str = "agent_jobs",
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    by_rule = enumeration.get("by_rule") or {}
    for rid in enumeration.get("rule_ids") or []:
        objs = list(by_rule.get(rid) or [])
        if not objs:
            # empty rule still gets a sentinel job for 不适用 aggregation
            jobs.append(
                {
                    "job_id": f"{rid}::__empty__",
                    "kind": "judge",
                    "rule_id": rid,
                    "objects": [],
                    "status": "pending",
                    "empty_rule": True,
                }
            )
            continue
        bs = batch_size_for_rule(str(rid), batch_size)
        for i in range(0, len(objs), bs):
            chunk = objs[i : i + bs]
            jid = f"{rid}::batch{i // bs:04d}"
            jobs.append(
                {
                    "job_id": jid,
                    "kind": "judge",
                    "rule_id": rid,
                    "objects": chunk,
                    "status": "pending",
                    "batch_size": bs,
                    "out_paths": {
                        o: f"{out_prefix}/{rid}/{safe_object_name(o)}.json" for o in chunk
                    },
                }
            )
    return jobs


def build_context_jobs(planned: list[dict[str, str]]) -> list[dict[str, Any]]:
    jobs = []
    for p in planned:
        kind = p["kind"]
        key = p["key"]
        jid = f"ctx::{kind}::{safe_object_name(key)}"
        jobs.append(
            {
                "job_id": jid,
                "kind": "context",
                "context_kind": kind,
                "key": key,
                "status": "pending",
            }
        )
    return jobs
