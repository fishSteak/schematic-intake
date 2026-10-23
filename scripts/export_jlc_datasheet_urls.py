# -*- coding: utf-8 -*-
"""Export per-ref datasheet URLs from JLC Supplier Part (LCSC C-code)."""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

LCSC_DETAIL = "https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={code}"
C_CODE = re.compile(r"^C\d+$", re.I)


def _expand_designators(raw: str) -> list[str]:
    parts = [p.strip() for p in (raw or "").replace("，", ",").split(",") if p.strip()]
    return parts


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_bom(path: Path) -> list[dict[str, str]]:
    text = _read_text(path)
    first = (text.splitlines() or [""])[0]
    dialect = "excel-tab" if first.count("\t") > first.count(",") else "excel"
    rows = list(csv.DictReader(text.splitlines(), dialect=dialect))
    out: list[dict[str, str]] = []
    for row in rows:
        refs = _expand_designators(row.get("Designator") or "")
        for ref in refs:
            out.append(
                {
                    "ref": ref,
                    "mpn": (row.get("Manufacturer Part") or "").strip(),
                    "value": (row.get("Value") or row.get("Comment") or "").strip(),
                    "manufacturer": (row.get("Manufacturer") or "").strip(),
                    "supplier": (row.get("Supplier") or "").strip(),
                    "lcsc": (row.get("Supplier Part") or "").strip(),
                    "footprint": (row.get("Footprint") or "").strip(),
                }
            )
    return out


def _jlc_by_ref(jlc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    for inst in jlc.get("instances") or []:
        ref = str(inst.get("ref") or "").strip()
        if not ref:
            continue
        by[ref] = {
            "mpn": inst.get("mpn") or "",
            "manufacturer": inst.get("manufacturer") or "",
            "value": inst.get("value") or "",
            "package": inst.get("package") or "",
        }
    for sym in jlc.get("symbols") or []:
        ref = str(sym.get("ref") or "").strip()
        attrs = sym.get("attributes") or {}
        if not isinstance(attrs, dict):
            continue
        rec = by.setdefault(ref, {})
        rec["jlc_datasheet_attr"] = attrs.get("Datasheet") or ""
        rec["jlc_supplier_part"] = attrs.get("Supplier Part") or ""
        rec["jlc_mpn"] = attrs.get("Manufacturer Part") or rec.get("mpn") or ""
        rec["jlc_manufacturer"] = attrs.get("Manufacturer") or rec.get("manufacturer") or ""
    return by


def _lcsc_lookup(code: str, cache_dir: Path) -> dict[str, Any]:
    code = (code or "").strip()
    empty = {"ok": False, "pdf_url": "", "product_url": "", "error": "no_c_code", "product_model": ""}
    if not C_CODE.match(code):
        empty["error"] = "invalid_c_code" if code else "no_c_code"
        return empty
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{code}.json"
    if cache.is_file():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    url = LCSC_DETAIL.format(code=code)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        rec = {**empty, "error": f"http_{e.code}"}
        cache.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        return rec
    except Exception as e:
        rec = {**empty, "error": type(e).__name__}
        return rec
    result = raw.get("result") if isinstance(raw, dict) else None
    if not isinstance(result, dict):
        rec = {**empty, "error": "bad_payload"}
        cache.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        return rec
    pdf = result.get("pdfUrl") or result.get("pdfLinkUrl") or ""
    rec = {
        "ok": bool(pdf),
        "pdf_url": str(pdf or ""),
        "product_url": f"https://www.lcsc.com/product-detail/{code}.html",
        "product_model": str(result.get("productModel") or ""),
        "product_name": str(result.get("productNameEn") or result.get("title") or ""),
        "error": "" if pdf else "empty_pdfUrl",
        "lcsc_code": code,
    }
    cache.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bom", required=True)
    ap.add_argument("--jlc", default="")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    bom_rows = _read_bom(Path(args.bom))
    jlc_map: dict[str, dict[str, Any]] = {}
    if args.jlc:
        jlc_path = Path(args.jlc)
        if jlc_path.is_file():
            jlc_map = _jlc_by_ref(json.loads(jlc_path.read_text(encoding="utf-8-sig")))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = out_dir / "lcsc_cache"
    queried: dict[str, dict[str, Any]] = {}
    records: list[dict[str, Any]] = []
    for row in bom_rows:
        code = row["lcsc"]
        if code not in queried:
            queried[code] = _lcsc_lookup(code, cache_dir)
            if C_CODE.match(code or ""):
                time.sleep(0.12)
        lcsc = queried[code]
        jlc = jlc_map.get(row["ref"]) or {}
        jlc_attr = str(jlc.get("jlc_datasheet_attr") or "")
        url_from_jlc = jlc_attr if jlc_attr.startswith("http") else ""
        records.append(
            {
                "ref": row["ref"],
                "mpn": row["mpn"] or jlc.get("jlc_mpn") or "",
                "value": row["value"],
                "manufacturer": row["manufacturer"],
                "footprint": row["footprint"],
                "lcsc_code": code,
                "jlc_datasheet_attr": jlc_attr,
                "datasheet_url": url_from_jlc or lcsc.get("pdf_url") or "",
                "datasheet_source": "jlc_attr" if url_from_jlc else ("lcsc_pdfUrl" if lcsc.get("pdf_url") else ""),
                "lcsc_product_url": lcsc.get("product_url") or "",
                "lcsc_product_model": lcsc.get("product_model") or "",
                "lookup_ok": bool(url_from_jlc or lcsc.get("ok")),
                "lookup_error": "" if (url_from_jlc or lcsc.get("ok")) else (lcsc.get("error") or ""),
            }
        )
    json_path = out_dir / "jlc_datasheet_urls.json"
    csv_path = out_dir / "jlc_datasheet_urls.csv"
    summary = {
        "count": len(records),
        "with_url": sum(1 for r in records if r["datasheet_url"]),
        "missing_url": sum(1 for r in records if not r["datasheet_url"]),
        "unique_lcsc": len({r["lcsc_code"] for r in records if r["lcsc_code"]}),
        "note": "嘉立创器件 Datasheet 属性多为空；手册 URL 由立创商城料号 C 码查 LCSC pdfUrl。",
        "records": records,
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = [
        "ref",
        "mpn",
        "value",
        "manufacturer",
        "lcsc_code",
        "datasheet_url",
        "datasheet_source",
        "lcsc_product_url",
        "jlc_datasheet_attr",
        "lookup_ok",
        "lookup_error",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(records)
    print(f"OK count={summary['count']} with_url={summary['with_url']} miss={summary['missing_url']}")
    print(csv_path)
    print(json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
