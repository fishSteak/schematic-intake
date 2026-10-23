"""Step 3: 对已下载规格书抽 pintable.json（source=datasheet 才算 computed）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kernel.pintable import extract_pdf, safe_mpn_dir
from kernel.util import out_dir, write_json


def run(board: Path, graph: dict[str, Any], ds: dict[str, Any]) -> dict[str, Any]:
    cache = board / "cache" / "mpn"
    entries: list[dict[str, Any]] = []
    for item in ds.get("queue") or []:
        mpn = item["mpn"]
        slot = Path(item.get("cache") or (cache / safe_mpn_dir(mpn)))
        pdf = slot / "datasheet.pdf"
        pt = slot / "pintable.json"
        if pdf.is_file():
            body = extract_pdf(pdf, mpn=mpn)
            body["mpn"] = mpn
            body["pdf"] = str(pdf)
            pt.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            status = "computed" if body.get("source") == "datasheet" else "extract_failed"
            entries.append(
                {
                    "mpn": mpn,
                    "status": status,
                    "path": str(pt),
                    "pdf": str(pdf),
                    "pages": body.get("pages"),
                    "params": body.get("params") or {},
                    "recommended": body.get("recommended") or (body.get("params") or {}).get("recommended") or {},
                    "abs_max": body.get("abs_max") or (body.get("params") or {}).get("abs_max") or {},
                    "hit_families": body.get("hit_families") or [],
                }
            )
            continue
        if pt.is_file():
            try:
                body = json.loads(pt.read_text(encoding="utf-8-sig"))
            except Exception:
                body = {}
            entries.append(
                {
                    "mpn": mpn,
                    "status": "computed" if body.get("source") == "datasheet" else "seed",
                    "path": str(pt),
                    "pdf": None,
                    "params": body.get("params") or {},
                    "recommended": body.get("recommended") or (body.get("params") or {}).get("recommended") or {},
                    "abs_max": body.get("abs_max") or (body.get("params") or {}).get("abs_max") or {},
                }
            )
        else:
            entries.append({"mpn": mpn, "status": "unknown", "path": None, "pdf": None, "params": {}})
    computed = sum(1 for e in entries if e.get("status") == "computed")
    data = {
        "count": len(entries),
        "computed": computed,
        "unknown": sum(1 for e in entries if e.get("status") == "unknown"),
        "entries": entries,
        "note": "source=datasheet 的 pintable 可供 G2/G3/G4 代入；unknown 不得编造额定值",
    }
    write_json(out_dir(board) / "pintable_index.json", data)
    return data
