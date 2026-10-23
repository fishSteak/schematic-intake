"""BOM xlsx/csv → per-ref rows. Designator column may be comma-separated."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import openpyxl


def _norm(s: object) -> str:
    return str(s or "").strip()


def explode_designators(cell: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,;]\s*", cell) if p.strip()]


def parse_bom_xlsx(path: str | Path) -> dict[str, dict[str, str]]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValueError(f"empty BOM: {path}")
    header = [_norm(c) for c in rows[0]]
    idx = {h.lower(): i for i, h in enumerate(header) if h}

    def col(*names: str) -> int | None:
        for n in names:
            if n.lower() in idx:
                return idx[n.lower()]
        return None

    i_ref = col("Designator", "Reference", "位号")
    i_val = col("Value", "Comment")
    i_mpn = col("Manufacturer Part", "Manufacturer Part Number", "MPN")
    i_fp = col("Footprint")
    if i_ref is None:
        raise ValueError(f"BOM missing Designator: {header}")

    out: dict[str, dict[str, str]] = {}
    for raw in rows[1:]:
        if raw is None:
            continue
        des = _norm(raw[i_ref] if i_ref < len(raw) else "")
        if not des:
            continue
        val = _norm(raw[i_val] if i_val is not None and i_val < len(raw) else "")
        mpn = _norm(raw[i_mpn] if i_mpn is not None and i_mpn < len(raw) else "")
        fp = _norm(raw[i_fp] if i_fp is not None and i_fp < len(raw) else "")
        for ref in explode_designators(des):
            if ref in out:
                raise ValueError(f"duplicate BOM ref: {ref}")
            out[ref] = {"ref": ref, "value": val, "mpn": mpn, "footprint": fp}
    return out


def parse_bom_csv(path: str | Path) -> dict[str, dict[str, str]]:
    p = Path(path)
    raw = p.read_bytes()
    enc = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    text = raw.decode(enc)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        delim = dialect.delimiter
    except csv.Error:
        delim = "\t" if "\t" in sample.splitlines()[0] else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    if not reader.fieldnames:
        raise ValueError(f"BOM missing header: {p}")
    fields = { (h or "").strip(): (h or "").strip() for h in reader.fieldnames }

    def col(*names: str) -> str | None:
        lower = {k.lower(): k for k in fields}
        for n in names:
            if n.lower() in lower:
                return lower[n.lower()]
        return None

    k_ref = col("Designator", "Reference", "位号")
    k_val = col("Value", "Comment")
    k_mpn = col("Manufacturer Part", "Manufacturer Part Number", "MPN")
    k_fp = col("Footprint")
    k_mfr = col("Manufacturer")
    k_desc = col("drawing:Description", "Description")
    k_comment = col("Comment")
    if k_ref is None:
        raise ValueError(f"BOM missing Designator: {list(fields)}")

    out: dict[str, dict[str, str]] = {}
    for line in reader:
        des = _norm(line.get(k_ref))
        if not des:
            continue
        val = _norm(line.get(k_val) if k_val else "")
        mpn = _norm(line.get(k_mpn) if k_mpn else "")
        fp = _norm(line.get(k_fp) if k_fp else "")
        mfr = _norm(line.get(k_mfr) if k_mfr else "")
        desc = _norm(line.get(k_desc) if k_desc else "")
        comment = _norm(line.get(k_comment) if k_comment else "")
        for ref in explode_designators(des):
            if ref in out:
                raise ValueError(f"duplicate BOM ref: {ref}")
            out[ref] = {
                "ref": ref,
                "value": val,
                "mpn": mpn,
                "footprint": fp,
                "manufacturer": mfr,
                "desc": desc,
                "comment": comment,
            }
    return out


def parse_bom_file(path: str | Path) -> dict[str, dict[str, str]]:
    p = Path(path)
    suf = p.suffix.lower()
    if suf in {".xlsx", ".xlsm"}:
        return parse_bom_xlsx(p)
    if suf in {".csv", ".tsv", ".txt"}:
        return parse_bom_csv(p)
    raise ValueError(f"unsupported BOM: {p}")
