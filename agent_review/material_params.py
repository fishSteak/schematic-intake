"""Exact MPN material lookup shared by context seeding and the workbench.

BoardKG contains extraction plus knowledge/hunt supplements. Never discard these
supplements by reading only the raw PDF extraction, or match a similar part.
"""
from copy import deepcopy
from pathlib import Path

from kernel.pintable import safe_mpn_dir
from kernel.util import read_json


def material_for_mpn(out: Path, mpn: str, kg=None):
    board = out.parent
    if kg is None:
        path = out / "board_kg.json"
        kg = read_json(path) if path.is_file() else {}
    result = {"mpn": mpn, "params": {}, "rated": {}, "recommended": {},
              "abs_max": {}, "conflicts": [], "unresolved": [], "sources": []}
    key = safe_mpn_dir(mpn)
    for root in (board / "cache" / "mpn" / key, out / "cache" / "mpn" / key):
        pt = root / "pintable.json"
        pdf = root / "datasheet.pdf"
        if pdf.is_file():
            result.setdefault("datasheet_path", pdf.relative_to(board).as_posix())
        if pt.is_file():
            result.setdefault("pintable_path", pt.relative_to(board).as_posix())
            _merge(result, read_json(pt), pt.relative_to(board).as_posix())
    for ref, comp in (kg.get("components") or {}).items():
        if str(comp.get("mpn") or "").strip() != mpn:
            continue
        pt = comp.get("pintable") or {}
        _merge(result, pt, f"review_out/board_kg.json#components.{ref}.pintable")
        # Matching the part number does not establish matching manufacturer.
        if comp.get("manufacturer"):
            note = "BOM manufacturer=" + str(comp["manufacturer"]) + "; cached PDF vendor/applicability must be checked before treating its limits as guaranteed."
            if note not in result["unresolved"]:
                result["unresolved"].append(note)
    result["rated"] = deepcopy(result["params"])
    return result


def _merge(result, doc, source):
    result["sources"].append({"path": source, "source": doc.get("source"),
                              "hits": doc.get("hits") or [],
                              "hit_families": doc.get("hit_families") or []})
    for bucket in ("params", "recommended", "abs_max"):
        for key, value in (doc.get(bucket) or {}).items():
            if value is None:
                continue
            prior = result[bucket].get(key)
            if prior is not None and prior != value:
                conflict = {"field": f"{bucket}.{key}", "previous": prior,
                            "alternative": value, "source": source}
                if conflict not in result["conflicts"]:
                    result["conflicts"].append(conflict)
                # Conflicting electrical values must not silently become a fact.
                result[bucket].pop(key, None)
            elif not any(c["field"] == f"{bucket}.{key}" for c in result["conflicts"]):
                result[bucket][key] = deepcopy(value)
    if doc.get("pages") is not None:
        result.setdefault("pages", doc["pages"])
