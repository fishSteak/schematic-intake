"""Cross-board knowledge: series ratings + topology recipes (no verdict contamination)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from kernel.util import ROOT

KNOW = ROOT / "knowledge"


def _load(name: str) -> dict[str, Any]:
    p = KNOW / name
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def series_ratings() -> list[dict[str, Any]]:
    return list((_load("series_ratings.json").get("series") or []))


def topology_recipes() -> list[dict[str, Any]]:
    return list((_load("topology_recipes.json").get("recipes") or []))


def match_series(mpn: str) -> dict[str, Any] | None:
    mpn = (mpn or "").strip()
    if not mpn:
        return None
    for row in series_ratings():
        pat = row.get("mpn_pattern") or ""
        if pat and re.search(pat, mpn, re.I):
            return row
    return None


def series_url_hints() -> dict[str, str]:
    """MPN → datasheet URL from series table (for url_hints seed)."""
    out: dict[str, str] = {}
    for row in series_ratings():
        url = row.get("datasheet_url_hint") or ""
        pat = row.get("mpn_pattern") or ""
        if not url or not pat:
            continue
        # only exact-ish keys for known single-MPN series; pattern series filled at runtime
        if re.fullmatch(r"\^[A-Za-z0-9._\-]+\$$", pat.replace("\\\\", "\\")):
            exact = pat.strip("^$")
            out[exact] = url
        if row.get("id") == "uni-royal-0603-waf":
            # seed one representative; s2 expands to all matching BOM MPNs
            out["__SERIES_UNI_ROYAL_0603_WAF__"] = url
    return out


def expand_series_hints_for_mpns(mpns: list[str]) -> dict[str, str]:
    hints: dict[str, str] = {}
    for mpn in mpns:
        row = match_series(mpn)
        if not row:
            continue
        url = row.get("datasheet_url_hint") or ""
        if url:
            hints[mpn] = url
    return hints


# EIA 陶瓷介质温区（料号/参数表常见；可密封）
_EIA_DIELECTRIC_TEMP = {
    "C0G": (-55.0, 125.0),
    "NP0": (-55.0, 125.0),
    "X7R": (-55.0, 125.0),
    "X5R": (-55.0, 85.0),
    "X6S": (-55.0, 105.0),
    "Y5V": (-30.0, 85.0),
    "Z5U": (10.0, 85.0),
}


def eia_dielectric_temp(mpn: str) -> tuple[float, float, str] | None:
    blob = (mpn or "").upper()
    for code, (lo, hi) in _EIA_DIELECTRIC_TEMP.items():
        if code in blob:
            return lo, hi, code
    return None


def apply_series_params(mpn: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge known series ratings into pintable params."""
    out = dict(params or {})
    eia = eia_dielectric_temp(mpn)
    if eia and out.get("t_op_min_c") is None and out.get("t_op_max_c") is None:
        lo, hi, code = eia
        out["t_op_min_c"] = lo
        out["t_op_max_c"] = hi
        out["t_op_source"] = {
            "kind": "eia_dielectric",
            "quote": f"EIA {code} → {lo:.0f}～{hi:.0f}℃",
            "mpn": mpn,
            "backend": "dielectric_code",
        }
    row = match_series(mpn)
    if not row:
        return out
    if row.get("p_rated_w") is not None and out.get("p_rated_w") is None:
        out["p_rated_w"] = float(row["p_rated_w"])
        out["p_rated_source"] = {
            "kind": "knowledge_series",
            "quote": row.get("extract_note") or row.get("power_code_meaning") or row.get("id"),
            "series_id": row.get("id"),
            "url": row.get("datasheet_url_hint") or "",
            "backend": "series_ratings",
        }
        out["p_rated_temp_c"] = row.get("p_rated_temp_c")
        out["extract_note"] = row.get("extract_note") or ""
    if row.get("vz_max_v") is not None and out.get("vz_max_v") is None:
        out["vz_nom_v"] = row.get("vz_nom_v")
        out["vz_min_v"] = row.get("vz_min_v")
        out["vz_max_v"] = float(row["vz_max_v"])
        out["vz_source"] = f"knowledge:{row.get('id')}"
        out["extract_note"] = row.get("extract_note") or out.get("extract_note") or ""
        for key in ("izt_a", "zzt_ohm", "izk_a"):
            if row.get(key) is not None and out.get(key) is None:
                out[key] = float(row[key])
    if row.get("vf_model_v") is not None and out.get("vf_model_v") is None:
        out["vf_model_v"] = float(row["vf_model_v"])
        out["vf_model_origin"] = "model"
    return out


def parse_power_from_text(text: str) -> float | None:
    """Parse 100mW / 1/10W / 0.1W from LCSC product name or attr."""
    s = text or ""
    m = re.search(r"(\d+(?:\.\d+)?)\s*mW", s, re.I)
    if m:
        return float(m.group(1)) / 1000.0
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*W", s, re.I)
    if m:
        return float(m.group(1)) / float(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)\s*W\b", s, re.I)
    if m:
        return float(m.group(1))
    return None


def parse_vz_from_text(text: str) -> float | None:
    s = text or ""
    m = re.search(r"(?:ZENER|稳压)\s*(\d+(?:\.\d+)?)\s*V", s, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*V\b", s)
    if m and "zener" in s.lower():
        return float(m.group(1))
    return None
