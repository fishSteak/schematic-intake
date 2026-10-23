# -*- coding: utf-8 -*-
"""P11 calc stage: numeric stress per sealed operating case."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_review.p11_pipeline import run_calc


def run(board: Path, kg: dict[str, Any]) -> dict[str, Any]:
    result = run_calc(board, kg)
    st = result.get("stress") or {}
    a01 = st.get("P11-A01") or {}
    a03 = st.get("P11-A03") or {}
    print(
        f"OK\tcalc P11-A01={a01.get('objects', 0)} peak={a01.get('with_peak', 0)} "
        f"P11-A03={a03.get('objects', 0)} peak={a03.get('with_peak', 0)}"
    )
    return result
