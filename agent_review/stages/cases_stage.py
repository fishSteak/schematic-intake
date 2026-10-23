# -*- coding: utf-8 -*-
"""P11 operating-cases stage: board_paths draft→seal→project + legacy fill."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent_review.p11_pipeline import run_cases


def run(board: Path, kg: dict[str, Any], *, skip_agent: bool = False) -> dict[str, Any]:
    skip_agent = skip_agent or os.environ.get("BOARD_PATHS_SKIP_AGENT", "").strip() in {"1", "true", "yes"}
    result = run_cases(board, kg, skip_path_agent=skip_agent)
    st = result.get("cases") or {}
    a01 = st.get("P11-A01") or {}
    a03 = st.get("P11-A03") or {}
    bp = result.get("board_paths") or {}
    print(
        f"OK\tcases P11-A01={a01.get('objects', 0)} complete={a01.get('complete', 0)} "
        f"P11-A03={a03.get('objects', 0)} complete={a03.get('complete', 0)} "
        f"board_paths closed={bp.get('closed', 0)} unmapped={bp.get('unmapped', 0)} "
        f"rejected={bp.get('rejected', 0)}"
    )
    return result
