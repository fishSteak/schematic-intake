"""嘉立创 .eprj2 → review_out/jlc_project.json（可选材料）。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from kernel.util import ROOT, write_json
from kernel.runtime import node_executable

VENDOR = ROOT / "vendor" / "jlc-project-json"
CLI = VENDOR / "cli.mjs"


def convert_eprj2(eprj2: Path, out_json: Path, *, node: str | None = None) -> dict[str, Any]:
    """Run vendor CLI. Requires Node; package engines prefer >=24."""
    if not CLI.is_file():
        raise FileNotFoundError(f"JLC converter missing: {CLI}")
    if not eprj2.is_file():
        raise FileNotFoundError(f"eprj2 not found: {eprj2}")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    if out_json.exists():
        out_json.unlink()
    cmd = [node or node_executable(), str(CLI), str(eprj2.resolve()), str(out_json.resolve())]
    proc = subprocess.run(
        cmd,
        cwd=str(VENDOR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode not in (0, 2) or not out_json.is_file():
        raise RuntimeError(
            f"JLC convert failed rc={proc.returncode}\n"
            f"stdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-2000:]}"
        )
    data = json.loads(out_json.read_text(encoding="utf-8"))
    data["_convert"] = {
        "rc": proc.returncode,
        "complete": bool(data.get("complete")),
        "connectionIssues": len(data.get("connectionIssues") or []),
        "issues": len(data.get("issues") or []),
    }
    write_json(out_json, data)
    return data


def converter_ready() -> bool:
    return CLI.is_file() and (VENDOR / "node_modules" / "adm-zip").exists()


def ensure_node_modules() -> None:
    if (VENDOR / "node_modules" / "adm-zip").exists():
        return
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm not found; cannot install JLC converter deps")
    subprocess.run(
        [npm, "ci", "--ignore-scripts"],
        cwd=str(VENDOR),
        check=True,
        capture_output=True,
        text=True,
    )
