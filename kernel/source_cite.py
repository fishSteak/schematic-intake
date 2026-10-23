# -*- coding: utf-8 -*-
"""公式已知栏的来源句。只区分规格书、立创、联网、未抽出。"""

from __future__ import annotations

from typing import Any

_MISSED = "未抽出：程序已走规格书、立创、联网"


def format_source(src: dict[str, Any] | None, *, missing: str = _MISSED) -> str:
    if not isinstance(src, dict):
        return missing
    kind = str(src.get("kind") or "")
    quote = str(src.get("quote") or "").strip()
    quote = " ".join(quote.split())[:140]
    url = str(src.get("url") or "").strip()
    if kind == "datasheet_page":
        page = src.get("page")
        head = f"规格书第{page}页" if page not in (None, "") else "规格书"
        return f"{head} {quote}".strip()
    if kind == "lcsc_product":
        return f"立创参数 {url} {quote}".strip()
    if kind == "web_page":
        return f"联网 {url} {quote}".strip()
    if kind in {"taskbook_line", "bom_row", "netlist_pin"}:
        return quote or missing
    return missing


def cite_param(params: dict[str, Any] | None, key: str, *, missing: str = _MISSED) -> str:
    """Prefer patch/hunt source, else page+quote written by extract_pdf."""
    params = params or {}
    sources = params.get("patch_sources") if isinstance(params.get("patch_sources"), dict) else {}
    src = params.get(f"{key}_source") or sources.get(key)
    if isinstance(src, dict) and src.get("kind"):
        return format_source(src, missing=missing)
    if params.get(key) is None:
        return missing
    page = params.get(f"{key}_page")
    quote = params.get(f"{key}_quote") or ""
    return format_source(
        {"kind": "datasheet_page", "page": page, "quote": quote},
        missing=missing,
    )
