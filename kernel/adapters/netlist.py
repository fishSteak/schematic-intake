"""Netlist adapters: Telesis (.tel) and Pinscope-native PADS/EDIF.

Telesis grammar for EasyEDA / PADS $PACKAGES (continuation commas allowed)::

    footprint ! footprint ! value [,] ; [,] ref ref ...

Value may be quoted. Refs may sit on the next line after ``;``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_PIN_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\.([0-9A-Za-z]+)$")


def _section(text: str, name: str) -> str:
    m = re.search(rf"\${name}\s*(.*?)\s*\$", text, re.S | re.I)
    if not m:
        raise ValueError(f"missing ${name} section")
    return m.group(1)


def _skip_ws_comma(s: str, i: int) -> int:
    n = len(s)
    while i < n and s[i] in " \t\r\n,":
        i += 1
    return i


def _read_token(s: str, i: int) -> tuple[str, int]:
    i = _skip_ws_comma(s, i)
    n = len(s)
    if i >= n:
        return "", i
    if s[i] in "'\"":
        q = s[i]
        i += 1
        start = i
        while i < n and s[i] != q:
            i += 1
        tok = s[start:i]
        if i < n:
            i += 1
        return tok, i
    start = i
    while i < n and s[i] not in " \t\r\n,!;":
        i += 1
    return s[start:i], i


def _peek_is_footprint(s: str, i: int) -> bool:
    """True if next token is followed by '!' (start of a package record)."""
    i = _skip_ws_comma(s, i)
    if i >= len(s) or s[i] == "$":
        return False
    _tok, j = _read_token(s, i)
    j = _skip_ws_comma(s, j)
    return j < len(s) and s[j] == "!"


def parse_telesis_packages(section: str) -> dict[str, dict[str, str]]:
    s = section
    i = 0
    n = len(s)
    parts: dict[str, dict[str, str]] = {}
    while True:
        i = _skip_ws_comma(s, i)
        if i >= n or s[i] == "$":
            break
        fp, i = _read_token(s, i)
        i = _skip_ws_comma(s, i)
        if i >= n or s[i] != "!":
            break
        i += 1
        _fp2, i = _read_token(s, i)
        i = _skip_ws_comma(s, i)
        if i >= n or s[i] != "!":
            raise ValueError(f"package record missing second '!': {fp!r}")
        i += 1
        val, i = _read_token(s, i)
        i = _skip_ws_comma(s, i)
        if i >= n or s[i] != ";":
            raise ValueError(f"package record missing ';': {fp!r} value={val!r}")
        i += 1
        refs: list[str] = []
        while True:
            i = _skip_ws_comma(s, i)
            if i >= n or s[i] == "$":
                break
            if _peek_is_footprint(s, i):
                break
            tok, i = _read_token(s, i)
            if tok:
                refs.append(tok)
        if not refs:
            raise ValueError(f"package record has no refs: {fp!r} value={val!r}")
        for ref in refs:
            if ref in parts:
                raise ValueError(f"duplicate ref in $PACKAGES: {ref}")
            parts[ref] = {"ref": ref, "footprint": fp, "value": val}
    return parts


def parse_telesis_nets(section: str) -> dict[str, list[tuple[str, str]]]:
    nets: dict[str, list[tuple[str, str]]] = {}
    cur: str | None = None
    for line in section.splitlines():
        raw = line.strip()
        if not raw:
            continue
        if ";" in raw and not raw.startswith(","):
            name, rest = raw.split(";", 1)
            cur = name.strip().strip("'\"")
            nets[cur] = []
            _append_pins(nets[cur], rest)
        else:
            if cur is None:
                raise ValueError(f"net continuation without name: {raw!r}")
            _append_pins(nets[cur], raw.lstrip(","))
    return nets


def _append_pins(bucket: list[tuple[str, str]], chunk: str) -> None:
    for tok in chunk.replace(",", " ").split():
        m = _PIN_RE.match(tok)
        if not m:
            continue
        bucket.append((m.group(1), m.group(2)))


def parse_telesis(text: str) -> tuple[dict[str, dict[str, str]], dict[str, list[tuple[str, str]]]]:
    packages = parse_telesis_packages(_section(text, "PACKAGES"))
    nets = parse_telesis_nets(_section(text, "NETS"))
    return packages, nets


def parse_netlist_file(path: str | Path) -> tuple[dict[str, dict[str, str]], dict[str, list[tuple[str, str]]], str]:
    p = Path(path)
    raw = p.read_text(encoding="utf-8", errors="replace")
    suffix = p.suffix.lower()
    if suffix == ".tel" or "$PACKAGES" in raw:
        parts, nets = parse_telesis(raw)
        return parts, nets, "tel"
    raise ValueError(f"unsupported netlist: {p} (tel adapter only in this module)")


def parts_as_pinscope(parts: dict[str, dict[str, str]]) -> dict[str, str]:
    """Pinscope parse_netlist_any shape: ref -> footprint."""
    return {ref: rec["footprint"] for ref, rec in parts.items()}


def dump_r1(tel_path: str | Path) -> dict[str, Any]:
    parts, nets, fmt = parse_netlist_file(tel_path)
    return {
        "format": fmt,
        "len_parts": len(parts),
        "len_nets": len(nets),
        "C18": parts.get("C18"),
        "U2": parts.get("U2"),
        "CN2": parts.get("CN2"),
        "refs_sorted": sorted(parts, key=lambda r: (re.sub(r"\d+", "", r), int(re.search(r"\d+", r).group()) if re.search(r"\d+", r) else 0, r)),
    }


if __name__ == "__main__":
    import json
    import sys

    tel = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        r"K:\my-project\claude-code-p\板卡检测v2.0\产品中心评审_原版拆分_R1_input"
        r"\Netlist_原版拆分-PDF重建-R1_2026-09-07.tel"
    )
    d = dump_r1(tel)
    print("len(parts)", d["len_parts"])
    print("len(nets)", d["len_nets"])
    print("parts['C18']", d["C18"])
    print("parts['U2']", d["U2"])
    print("parts['CN2']", d["CN2"])
    print("json", json.dumps({k: d[k] for k in ("format", "len_parts", "len_nets", "C18", "U2", "CN2")}, ensure_ascii=False))
