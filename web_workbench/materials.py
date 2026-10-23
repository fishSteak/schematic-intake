"""Material inventory and controlled local imports; never edit review results."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import uuid

from .store import ACTIVE, Conflict, ROOT, Store, atomic_json

REQUIRED = ("schematic_pdf", "bom", "netlist", "eprj2", "operating_conditions", "design_task_book")
EXTENSIONS = {
    "schematic_pdf": {".pdf"}, "bom": {".csv", ".xlsx", ".xlsm"}, "netlist": {".tel"},
    "eprj2": {".eprj2", ".epro2"}, "operating_conditions": {".yaml", ".yml", ".json", ".txt", ".docx"},
    "design_task_book": {".yaml", ".yml", ".json", ".txt", ".docx"},
    "design_task_book_docx": {".docx"}, "drc": {".txt", ".json"},
}


def load_config(board):
    path = Path(board["path"]) / "materials.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve(board, value):
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    return (Path(board["path"]) / path).resolve() if not path.is_absolute() else path.resolve()


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inventory(board):
    config, rows, hashes = load_config(board), [], {}
    for role in EXTENSIONS:
        p = resolve(board, config.get(role))
        exists = bool(p and p.is_file())
        digest = sha256(p) if exists else None
        rows.append({"role": role, "name": p.name if p else "", "path": str(p) if p else None,
                     "exists": exists, "required": role in REQUIRED, "size": p.stat().st_size if exists else 0, "sha256": digest})
        hashes[role] = digest
    # A run also freezes these execution inputs. A resume must not silently use
    # different rules, settings or cached source PDFs under the same version.
    hashes["config"] = hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    base = Path(board["path"])
    rule_path = resolve(board, config.get("rules")) or ROOT / "rules/product_center_15.json"
    hashes["rules"] = sha256(rule_path) if rule_path.is_file() else None
    for filename in ("main_analysis.json", "selection_delivery.json", "review_config.json", "detail_presentation.json"):
        path = base / filename
        hashes[filename] = sha256(path) if path.is_file() else None
    for folder in ("cache", "review_out/datasheets"):
        for path in sorted((base / folder).rglob("*.pdf")):
            if path.is_file():
                hashes[str(path.relative_to(base))] = sha256(path)
    version = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return {"materials": rows, "missing": [r["role"] for r in rows if r["required"] and not r["exists"]], "version": version}


def import_file(store: Store, board_id, role, filename, content):
    if role not in EXTENSIONS:
        raise ValueError("不支持的材料角色")
    filename = Path(str(filename).replace("\\", "/")).name
    suffix = Path(filename).suffix.lower()
    if suffix not in EXTENSIONS[role]:
        raise ValueError(f"{role} 支持的文件格式：{', '.join(sorted(EXTENSIONS[role]))}")
    if not content:
        raise ValueError("材料文件为空")
    if len(content) > 50 * 1024 * 1024:
        raise ValueError("单个材料文件不能超过 50 MB")
    board = store.board(board_id)
    with store.connection(write=True) as c:
        if c.execute("SELECT 1 FROM runs WHERE board_id=? AND status IN ('queued','running','stopping')", (board_id,)).fetchone():
            raise Conflict("项目正在执行任务，停止或完成后才能更新材料")
        config = load_config(board)
        destination = store.home / "boards" / board_id
        destination.mkdir(parents=True, exist_ok=True)
        # Imported repository projects are copied on write. Their original inputs,
        # board configuration and published review_out remain untouched.
        if not board["managed"]:
            for key in EXTENSIONS:
                source = resolve(board, config.get(key))
                if source and source.is_file():
                    rel = Path("materials") / key / source.name
                    target = destination / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    config[key] = str(rel)
            rules = resolve(board, config.get("rules"))
            if rules and rules.is_file():
                target = destination / "materials/rules" / rules.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(rules, target)
                config["rules"] = str(target.relative_to(destination))
            else:
                config["rules"] = str(store.repository / "rules/product_center_15.json")
            original = Path(board["path"])
            for folder in ("cache", "review_out/datasheets"):
                for source in (original / folder).rglob("*.pdf"):
                    target = destination / source.relative_to(original)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            for name in ("main_analysis.json", "selection_delivery.json", "review_config.json", "detail_presentation.json"):
                if (original / name).is_file():
                    shutil.copy2(original / name, destination / name)
        relative = Path("materials") / role / (uuid.uuid4().hex[:8] + "-" + filename)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        config[role] = str(relative)
        config["board_name"] = board["name"]
        atomic_json(destination / "materials.json", config)
        c.execute("UPDATE boards SET path=?,managed=1 WHERE id=?", (str(destination), board_id))
    return inventory(store.board(board_id))


def preflight(board, rules):
    inv = inventory(board)
    checks = [{"name": r["role"], "ok": r["exists"], "detail": r["name"] if r["exists"] else "缺少材料"} for r in inv["materials"] if r["required"]]
    paths = {r["role"]: Path(r["path"]) for r in inv["materials"] if r["exists"]}
    stats = {}
    if "netlist" in paths:
        try:
            from kernel.adapters.netlist import parse_netlist_file
            parts, nets, _ = parse_netlist_file(paths["netlist"])
            if not parts or not nets:
                raise ValueError("网表未识别出器件或网络")
            stats.update(components=len(parts), nets=len(nets))
            checks.append({"name": "netlist_parse", "ok": True, "detail": f"{len(parts)} 个器件，{len(nets)} 个网络"})
        except Exception as exc:
            checks.append({"name": "netlist_parse", "ok": False, "detail": str(exc)})
    if "bom" in paths:
        try:
            from kernel.adapters.bom import parse_bom_file
            rows = parse_bom_file(paths["bom"])
            if not rows:
                raise ValueError("BOM 未识别出器件")
            stats["bom_components"] = len(rows)
            checks.append({"name": "bom_parse", "ok": True, "detail": f"{len(rows)} 个 BOM 位号"})
        except Exception as exc:
            checks.append({"name": "bom_parse", "ok": False, "detail": str(exc)})
    if "schematic_pdf" in paths:
        try:
            import pymupdf
            with pymupdf.open(paths["schematic_pdf"]) as doc:
                if doc.needs_pass or doc.page_count == 0:
                    raise ValueError("PDF 无法读取或没有页面")
                stats["pdf_pages"] = doc.page_count
            checks.append({"name": "pdf_parse", "ok": True, "detail": f"{stats['pdf_pages']} 页原理图"})
        except Exception as exc:
            checks.append({"name": "pdf_parse", "ok": False, "detail": str(exc)})
    for role in ("operating_conditions", "design_task_book"):
        if role in paths:
            try:
                from kernel.oc_taskbook import parse_structured_file
                parse_structured_file(paths[role])
                checks.append({"name": role + "_parse", "ok": True, "detail": "文件可解析；工况适用性将在评审中核对"})
            except Exception as exc:
                checks.append({"name": role + "_parse", "ok": False, "detail": str(exc)})
    return {"ok": all(r["ok"] for r in checks), "checks": checks, "missing": inv["missing"], "stats": stats, "rules": rules, "version": inv["version"]}
