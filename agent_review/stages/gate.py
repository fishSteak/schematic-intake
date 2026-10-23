# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_review.delivery_gate import run_gate
from agent_review.expert_report import write_expert_deliverables
from agent_review.report import build_report_data
from kernel.util import out_dir, read_json


def run(
    board: Path,
    enumeration: dict[str, Any],
    *,
    materials: dict[str, Any] | None = None,
    context_meta: dict[str, Any] | None = None,
    allow_failed: bool = False,
) -> dict[str, Any]:
    o = out_dir(board)
    gate = run_gate(o, enumeration, allow_failed=allow_failed)
    verdicts = read_json(o / "verdicts.json")
    findings = read_json(o / "findings.json").get("findings") or []
    ds = read_json(o / "datasheet_miss.json") if (o / "datasheet_miss.json").is_file() else None
    data = build_report_data(
        board.name,
        enumeration,
        verdicts,
        findings,
        materials=materials,
        context_meta=context_meta,
        gate=gate,
        datasheet_miss=ds,
    )
    write_expert_deliverables(
        o,
        board,
        verdicts,
        materials=materials,
        report_data=data,
    )
    gate = run_gate(o, enumeration, allow_failed=allow_failed)
    return gate
