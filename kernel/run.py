# -*- coding: utf-8 -*-
"""前置流水线：收料 → 建图 → 找规格书 → 抽参数 → 原理图索引 → 板级知识图。

不含逐项判定。判定交给后续 Agent。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from kernel.steps import (
    s0_materials,
    s1_graph,
    s2_datasheet,
    s3_extract,
    s4_pdf_index,
    s5_board_kg,
)
from kernel.util import out_dir, read_json, resolve_board_path, write_json

STEP_ORDER = ["0", "1", "2", "3", "4", "5"]


def _load_ctx(board: Path) -> dict[str, Any]:
    o = out_dir(board)
    ctx: dict[str, Any] = {"board": board}
    for name, key in [
        ("materials.json", "materials"),
        ("design_graph.json", "graph"),
        ("datasheet_miss.json", "ds"),
        ("pintable_index.json", "extract"),
        ("sch_index.json", "sch"),
        ("board_kg.json", "kg"),
    ]:
        p = o / name
        if p.is_file():
            ctx[key] = read_json(p)
    return ctx


def run_pipeline(
    board_dir: str | Path,
    *,
    from_step: str | None = None,
    only_step: str | None = None,
) -> int:
    board = resolve_board_path(board_dir)
    start = from_step or "0"
    steps = [only_step] if only_step is not None else STEP_ORDER[STEP_ORDER.index(start) :]
    ctx = _load_ctx(board) if (only_step or (from_step and from_step != "0")) else {"board": board}

    print(f"BOARD\t{board}")
    print(f"STEPS\t{','.join(steps)}")

    if "0" in steps:
        ctx["materials"] = s0_materials.run(board)
        print("OK\t0 materials")
    materials = ctx["materials"]

    if "1" in steps:
        ctx["graph"] = s1_graph.run(board, materials)
        g = ctx["graph"]
        print(
            f"OK\t1 graph parts={g['stats']['tel_parts']} nets={g['stats']['nets']} "
            f"jlc={'yes' if g.get('jlc') else 'no'}"
        )
    if "2" in steps:
        ctx["ds"] = s2_datasheet.run(board, ctx["graph"])
        print(
            f"OK\t2 datasheet miss={len(ctx['ds'].get('miss') or [])} "
            f"hit={len(ctx['ds'].get('hit') or [])} "
            f"fetch_ran={bool((ctx['ds'].get('fetch') or {}).get('ran'))}"
        )
    if "3" in steps:
        ctx["extract"] = s3_extract.run(board, ctx["graph"], ctx["ds"])
        print(
            f"OK\t3 extract count={ctx['extract'].get('count')} "
            f"computed={ctx['extract'].get('computed')}"
        )
    if "4" in steps:
        ctx["sch"] = s4_pdf_index.run(board, materials, ctx["graph"])
        print(
            f"OK\t4 sch_index refs={ctx['sch'].get('refs_found')}/{ctx['sch'].get('refs_total')} "
            f"chars={ctx['sch'].get('text_chars')}"
        )
    if "5" in steps:
        ctx["kg"] = s5_board_kg.run(
            board, materials, ctx["graph"], ctx["ds"], ctx["extract"], ctx["sch"]
        )
        print(
            f"OK\t5 board_kg components={ctx['kg']['stats']['components']} "
            f"conflicts={ctx['kg']['stats']['conflicts']}"
        )
        write_json(out_dir(board) / "board_kg.json", ctx["kg"])
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="收料到板级知识图。不含逐项判定。")
    ap.add_argument("--board", required=True, help="板目录（含 materials.json 或六件套）")
    ap.add_argument("--from-step", dest="from_step", default=None)
    ap.add_argument("--only-step", dest="only_step", default=None)
    args = ap.parse_args(argv)
    try:
        return run_pipeline(args.board, from_step=args.from_step, only_step=args.only_step)
    except Exception as e:
        print(f"FAIL\t{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
