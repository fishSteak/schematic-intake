"""Step 4: schematic PDF text index (pymupdf if available)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from kernel.util import out_dir, write_json


def run(board: Path, materials: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
    pdf = Path(materials["schematic_pdf"])
    refs = sorted((graph.get("components") or {}).keys())
    pages: list[dict[str, Any]] = []
    text_all = ""
    try:
        import fitz  # type: ignore

        doc = fitz.open(str(pdf))
        for i, page in enumerate(doc):
            text = page.get_text("text") or ""
            text_all += text + "\n"
            pages.append({"page": i + 1, "chars": len(text), "preview": text[:400]})
        doc.close()
        engine = "pymupdf"
    except Exception as e:
        engine = f"unavailable:{e}"
        pages = []

    found = []
    missing = []
    for ref in refs:
        if re.search(rf"\b{re.escape(ref)}\b", text_all):
            found.append(ref)
        else:
            missing.append(ref)

    data = {
        "pdf": str(pdf),
        "engine": engine,
        "pages": len(pages),
        "page_detail": pages[:50],
        "text_chars": len(text_all),
        "refs_total": len(refs),
        "refs_found": len(found),
        "refs_missing_sample": missing[:40],
        "note": "图面文字索引；连线一律网表。JLC 坐标另见 jlc_project.json",
    }
    write_json(out_dir(board) / "sch_index.json", data)
    return data
