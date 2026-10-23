# -*- coding: utf-8 -*-
"""Stage prep: materials → board_kg (s0–s5) or reuse existing."""

from __future__ import annotations

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
from kernel.util import out_dir, read_json, write_json


def run(board: Path, *, reuse_kg: bool = True) -> dict[str, Any]:
    o = out_dir(board)
    kg_path = o / "board_kg.json"
    if reuse_kg and kg_path.is_file() and (o / "materials.json").is_file():
        materials = read_json(o / "materials.json")
        kg = read_json(kg_path)
        return {"materials": materials, "kg": kg, "reused": True}

    materials = s0_materials.run(board)
    graph = s1_graph.run(board, materials)
    ds = s2_datasheet.run(board, graph)
    extract = s3_extract.run(board, graph, ds)
    sch = s4_pdf_index.run(board, materials, graph)
    kg = s5_board_kg.run(board, materials, graph, ds, extract, sch)
    write_json(o / "agent_review_prep.json", {"ok": True, "reused": False})
    return {
        "materials": materials,
        "kg": kg,
        "graph": graph,
        "ds": ds,
        "extract": extract,
        "sch": sch,
        "reused": False,
    }
