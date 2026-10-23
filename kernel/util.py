"""Shared paths and JSON helpers for V2.1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def out_dir(board: Path) -> Path:
    d = board / "review_out"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_board_path(board: str | Path) -> Path:
    raw = Path(board)
    if not raw.is_absolute():
        p = (ROOT / raw)
    else:
        p = raw
    try:
        resolved = p.resolve()
    except Exception:
        return p
    return Path(sdk_cwd(resolved, preferred=str(raw)))


def sdk_cwd(path: Path, preferred: str | None = None) -> str:
    """Cursor 本地 Agent 在 UNC cwd 下无法拉起 PowerShell；尽量保留盘符路径。"""
    pref = preferred or str(path)
    if len(pref) >= 2 and pref[1] == ":" and not pref.startswith("\\\\"):
        return pref
    s = str(path)
    if s.startswith("\\\\"):
        lower = s.replace("/", "\\")
        for needle in ("\\my-project\\", "\\project\\"):
            i = lower.lower().find(needle)
            if i >= 0:
                cand = Path("K:" + lower[i:])
                if cand.exists():
                    return str(cand)
    return s


def resolve_material_path(board: Path, raw: str | Path | None) -> Path | None:
    if not raw:
        return None
    p = Path(str(raw))
    if not p.is_absolute():
        p = (board / p).resolve()
    return p if is_file_safe(p) else None


def is_file_safe(path: str | Path) -> bool:
    """连不上的网络路径在 Windows 上 is_file() 会抛 OSError，按不存在处理。"""
    try:
        return Path(str(path)).is_file()
    except (OSError, ValueError):
        return False


def board_schematic_pdf(board: Path) -> Path | None:
    """产物里的绝对路径换机后可能失效：先按板配置相对路径找原始 PDF，再用 review_out 内的报告副本。"""
    cfg = board / "materials.json"
    try:
        raw = read_json(cfg).get("schematic_pdf") if is_file_safe(cfg) else None
    except (OSError, ValueError):
        raw = None
    pdf = resolve_material_path(board, raw)
    if pdf:
        return pdf
    copy = board / "review_out" / "sch" / "schematic.pdf"
    return copy if is_file_safe(copy) else None


def effective_rule_ids(
    materials: dict[str, Any],
    program: dict[str, Any] | None = None,
) -> list[str]:
    """板目录/CLI 显式给出的 rule_ids 原样执行；全量 15 项时仍强制插入 P42。"""
    ids = list(materials.get("rule_ids") or (program or {}).get("rule_ids") or [])
    if materials.get("rule_ids_explicit"):
        return ids
    for must in ("P42-A01", "P42-A04"):
        if must not in ids:
            ids.insert(0, must)
    return ids
