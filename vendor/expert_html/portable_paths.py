# -*- coding: utf-8 -*-
"""Portable (relative) paths for packaging review_out to other machines."""
from __future__ import annotations

import re
from pathlib import Path

_ABS_WIN = re.compile(r"^[A-Za-z]:[\\/]")
_ABS_UNC = re.compile(r"^\\\\|^//")


def is_abs_path_str(s: str) -> bool:
    s = (s or "").strip()
    if not s:
        return False
    if _ABS_WIN.match(s) or _ABS_UNC.match(s):
        return True
    # Unix absolute (but not protocol-relative //)
    if s.startswith("/") and not s.startswith("//"):
        return True
    return False


def portable_path(out_dir: Path, path: str | None) -> str:
    """Map any path to one relative to review_out (forward slashes).

    Falls back to filename if the path sits outside the package tree.
    HTTP(S) URLs are left unchanged.
    """
    if not path:
        return ""
    raw = str(path).strip()
    low = raw.lower()
    if low.startswith(("http://", "https://", "mailto:", "data:")):
        return raw
    out_res = out_dir.resolve()
    p = Path(raw)
    try:
        cand = p.resolve() if (p.exists() or is_abs_path_str(raw)) else Path(raw.replace("\\", "/"))
        if cand.is_absolute() or is_abs_path_str(raw):
            try:
                return cand.resolve().relative_to(out_res).as_posix()
            except Exception:
                try:
                    return cand.resolve().relative_to(out_res.parent).as_posix()
                except Exception:
                    return cand.name
    except Exception:
        pass
    return raw.replace("\\", "/")


def portable_input_dir(out_dir: Path, input_dir: str | None) -> str:
    """Board input folder relative to review_out (usually '..')."""
    if not input_dir:
        return ".."
    try:
        return Path(str(input_dir)).resolve().relative_to(out_dir.resolve()).as_posix()
    except Exception:
        try:
            rel = Path(str(input_dir)).resolve().relative_to(out_dir.parent.resolve()).as_posix()
            return rel if rel not in {".", ""} else ".."
        except Exception:
            name = Path(str(input_dir)).name
            return name or ".."


def make_pack_portable(pack: dict, out_dir: Path) -> dict:
    """Deep-copy pack with absolute paths rewritten for delivery."""
    import json

    obj = json.loads(json.dumps(pack, ensure_ascii=False))

    path_keys = {
        "input_dir",
        "screenshot",
        "local_path",
        "path",
        "source_file",
        "file",
        "pdf",
    }

    def is_json_reference(value: str, field: str | None, parents: tuple) -> bool:
        # JSON Pointers and Unix filenames share the leading slash. Resolve the
        # first pointer token against the enclosing document/card, not the host
        # filesystem; escaped property names retain their RFC 6901 spelling.
        if not value.startswith("/") or value.startswith("//"):
            return False
        first = value[1:].split("/", 1)[0].replace("~1", "/").replace("~0", "~")
        if any(first in parent for parent in parents):
            return True
        if field and (field == "$ref" or field.endswith(("_ref", "_refs"))):
            # Keep unresolved semantic references too: portability must not
            # repair or destroy them. Existing files in legacy reference lists
            # remain file paths, as do all explicit path/file/pdf fields below.
            try:
                return not Path(value).exists()
            except OSError:
                return True
        return False

    def walk(node: object, parents: tuple = (), field: str | None = None) -> None:
        if isinstance(node, dict):
            parents = (node, *parents)
            for k, v in list(node.items()):
                if k == "input_dir" and isinstance(v, str):
                    node[k] = portable_input_dir(out_dir, v)
                elif k in path_keys and isinstance(v, str) and v:
                    if not str(v).lower().startswith(("http://", "https://")):
                        if is_abs_path_str(v) or "\\" in v:
                            node[k] = portable_path(out_dir, v)
                        else:
                            node[k] = str(v).replace("\\", "/")
                elif isinstance(v, (dict, list)):
                    walk(v, parents, k)
                elif isinstance(v, str) and is_abs_path_str(v) and not is_json_reference(v, k, parents):
                    node[k] = portable_path(out_dir, v)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                if isinstance(v, str) and is_abs_path_str(v) and not is_json_reference(v, field, parents):
                    node[i] = portable_path(out_dir, v)
                else:
                    walk(v, parents, field)

    walk(obj)
    return obj
