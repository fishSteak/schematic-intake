"""Step 0: 六件套盘点。缺一写 gate_failed.json 并停评。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kernel.oc_taskbook import write_oc_and_task
from kernel.util import ROOT, out_dir, read_json, resolve_material_path, write_json

OC_HINTS = ("工况", "operating", "oc_", "operating_conditions", "工作条件")
TASK_HINTS = ("任务书", "task_book", "design_task", "设计任务")
SKIP_NAMES = ("materials.json",)


def _load_rules(rules_path: Path | None) -> list[str]:
    default = ROOT / "rules" / "product_center_15.json"
    path = rules_path if rules_path and rules_path.is_file() else default
    data = read_json(path)
    return list(data.get("rule_ids") or [])


def _hint_hit(name: str, hints: tuple[str, ...]) -> bool:
    low = name.lower()
    for h in hints:
        if h.lower() in low:
            return True
    return False


def _discover(board: Path) -> dict[str, Path | None]:
    found: dict[str, Path | None] = {
        "netlist": None,
        "bom": None,
        "schematic_pdf": None,
        "eprj2": None,
        "operating_conditions": None,
        "design_task_book": None,
    }
    for p in board.iterdir():
        if not p.is_file() or p.name in SKIP_NAMES:
            continue
        suf = p.suffix.lower()
        name = p.name.lower()
        if suf == ".tel" and found["netlist"] is None:
            found["netlist"] = p
        elif suf in {".csv", ".xlsx", ".xlsm"} and found["bom"] is None:
            if "bom" in name or suf in {".xlsx", ".xlsm"}:
                if not _hint_hit(name, TASK_HINTS + OC_HINTS):
                    found["bom"] = p
        elif suf == ".pdf" and found["schematic_pdf"] is None:
            if not _hint_hit(name, OC_HINTS + TASK_HINTS):
                found["schematic_pdf"] = p
        elif suf in {".eprj2", ".epro2"} and found["eprj2"] is None:
            found["eprj2"] = p
        if found["operating_conditions"] is None and _hint_hit(name, OC_HINTS):
            found["operating_conditions"] = p
        if found["design_task_book"] is None and _hint_hit(name, TASK_HINTS):
            found["design_task_book"] = p
    return found


def _fail_gate(board: Path, missing: list[str], materials: dict[str, Any]) -> None:
    payload = {
        "board": str(board),
        "missing": missing,
        "gate": "六件套缺一停评",
        "required": [
            "schematic_pdf",
            "bom",
            "netlist",
            "eprj2",
            "operating_conditions",
            "design_task_book",
        ],
        "note": "规格书由 AI 自搜，不在六件套内。不接默认工况库补缺。",
    }
    write_json(out_dir(board) / "gate_failed.json", payload)
    write_json(out_dir(board) / "materials.json", materials)
    raise RuntimeError(f"六件套不齐，整板停评: {', '.join(missing)}")


def run(board: Path) -> dict[str, Any]:
    cfg_path = board / "materials.json"
    cfg: dict[str, Any] = {}
    if cfg_path.is_file():
        cfg = read_json(cfg_path)

    discovered = _discover(board)
    netlist = resolve_material_path(board, cfg.get("netlist")) or discovered["netlist"]
    bom = resolve_material_path(board, cfg.get("bom")) or discovered["bom"]
    pdf = resolve_material_path(board, cfg.get("schematic_pdf")) or discovered["schematic_pdf"]
    eprj2 = resolve_material_path(board, cfg.get("eprj2")) or discovered["eprj2"]
    oc = resolve_material_path(board, cfg.get("operating_conditions")) or discovered["operating_conditions"]
    task = resolve_material_path(board, cfg.get("design_task_book")) or discovered["design_task_book"]

    missing = []
    if pdf is None:
        missing.append("schematic_pdf")
    if bom is None:
        missing.append("bom")
    if netlist is None:
        missing.append("netlist(.tel)")
    if eprj2 is None:
        missing.append("eprj2")
    if oc is None:
        missing.append("工况")
    if task is None:
        missing.append("设计任务书")

    rules_raw = cfg.get("rules")
    rules_path = resolve_material_path(board, rules_raw) if rules_raw else (ROOT / "rules" / "product_center_15.json")
    if rules_path is None:
        rules_path = ROOT / "rules" / "product_center_15.json"
    cfg_ids = [str(x) for x in (cfg.get("rule_ids") or []) if x]
    rule_ids_explicit = bool(cfg_ids)
    rule_ids = cfg_ids if rule_ids_explicit else _load_rules(rules_path)

    materials = {
        "board_name": cfg.get("board_name") or board.name,
        "product": cfg.get("product") or "",
        "board": str(board),
        "netlist": str(netlist) if netlist else None,
        "bom": str(bom) if bom else None,
        "schematic_pdf": str(pdf) if pdf else None,
        "eprj2": str(eprj2) if eprj2 else None,
        "operating_conditions": str(oc) if oc else None,
        "design_task_book": str(task) if task else None,
        "rules": str(rules_path),
        "rule_ids": rule_ids,
        "rule_ids_explicit": rule_ids_explicit,
        "missing": missing,
        "note": cfg.get("note") or "",
        "jlc_optional": False,
        "eprj2_present": eprj2 is not None,
        "gate": "六件套",
    }
    if missing:
        _fail_gate(board, missing, materials)

    stale_gate = out_dir(board) / "gate_failed.json"
    if stale_gate.is_file():
        stale_gate.unlink()

    write_json(out_dir(board) / "materials.json", materials)
    write_oc_and_task(out_dir(board), oc, task)
    return materials
