"""Resolve executable paths without relying on an interactive shell's PATH."""
import os
import shutil
from pathlib import Path


def node_executable() -> str:
    for candidate in (os.environ.get("SCHEMATIC_NODE"), shutil.which("node"), os.environ.get("CODEX_MCP_NODE_PATH")):
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise RuntimeError("Node.js unavailable: install Node >=24 or set SCHEMATIC_NODE to node.exe")
