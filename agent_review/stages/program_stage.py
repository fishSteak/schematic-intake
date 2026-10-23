# -*- coding: utf-8 -*-
"""Program self-check for non-Agent rules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_review.program_bridge import run_program_checkers, sync_enumeration_objects
from agent_review.rule_modes import split_rule_ids
from kernel.util import out_dir, write_json


def run(
    board: Path,
    kg: dict[str, Any],
    enumeration: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    modes = split_rule_ids(list(enumeration.get("rule_ids") or []))
    program = run_program_checkers(board, kg, modes["program"])
    enum2 = sync_enumeration_objects(enumeration, program)
    enum2["agent_rule_ids"] = modes["agent"]
    enum2["program_rule_ids"] = modes["program"]
    write_json(out_dir(board) / "agent_enumeration.json", enum2)
    n_cards = sum(len(v) for v in (program.get("by_rule_objects") or {}).values())
    print(
        f"OK\tprogram rules={len(modes['program'])} cards={n_cards} "
        f"是={program['summary'].get('是')} 否={program['summary'].get('否')} "
        f"INSUFFICIENT={program['summary'].get('INSUFFICIENT')}"
    )
    return program, enum2
