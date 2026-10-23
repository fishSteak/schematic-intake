# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_review.enumerate import enumerate_board, write_enumeration
from kernel.util import out_dir


def run(board: Path, kg: dict[str, Any], rule_ids: list[str] | None = None) -> dict[str, Any]:
    data = enumerate_board(kg, rule_ids=rule_ids)
    write_enumeration(out_dir(board), data)
    return data
