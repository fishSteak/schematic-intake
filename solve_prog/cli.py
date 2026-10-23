# -*- coding: utf-8 -*-
"""命令行：python -m solve_prog --kg board_kg.json --rule P11-A01 --object R52"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from solve_prog.deliver import run, write_delivery


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="solve_prog")
    parser.add_argument("--kg", required=True, help="board_kg.json 路径")
    parser.add_argument("--rule", required=True, help="检查项")
    parser.add_argument("--object", required=True, dest="obj", help="位号")
    args = parser.parse_args(argv)
    kg = json.loads(Path(args.kg).read_text(encoding="utf-8"))
    payload = run(kg, args.rule, args.obj)
    path = write_delivery(payload)
    print(f"{path}\tdeliverable={payload['deliverable']}\tconclusion={payload['conclusion']}\tsource={payload['source']}")
    return 0 if payload["deliverable"] else 2
