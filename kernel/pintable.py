# -*- coding: utf-8 -*-
"""从已下载规格书 PDF 抽 pintable（页码+摘录+弱解析参数）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

FAMILY_PATTERNS: list[tuple[str, list[re.Pattern[str]]]] = [
    ("recommended_operating", [re.compile(p, re.I) for p in [r"recommended operating", r"operating conditions", r"recommended supply", r"推荐工作"]]),
    ("absolute_maximum", [re.compile(p, re.I) for p in [r"absolute maximum", r"abs\.?\s*max", r"绝对最大"]]),
    ("supply_voltage", [re.compile(p, re.I) for p in [r"\bVCC\b", r"\bVDD\b", r"supply voltage", r"operating voltage", r"供电电压"]]),
    ("decoupling", [re.compile(p, re.I) for p in [r"decoupl", r"bypass cap", r"0\.1\s*µ?u?F", r"100\s*nF", r"去耦", r"旁路"]]),
    ("load_capacitance", [re.compile(p, re.I) for p in [r"load capacitance", r"\bC_?L\b", r"crystal", r"负载电容"]]),
    ("power_rating", [re.compile(p, re.I) for p in [r"rated power", r"power dissipation", r"\bPtot\b", r"额定功率"]]),
    ("voltage_rating", [re.compile(p, re.I) for p in [r"rated voltage", r"working voltage", r"\bWVDC\b", r"额定耐压"]]),
    ("operating_temp", [re.compile(p, re.I) for p in [
        r"operating\s+temperature", r"operating\s+temp", r"\bTOPR\b", r"T_?opr",
        r"工作温度", r"ambient\s+temperature\s+range", r"temperature\s+range",
    ]]),
    ("isat_irms", [re.compile(p, re.I) for p in [r"saturation current", r"\bIsat\b", r"\bIrms\b", r"温升"]]),
    ("vf_forward", [re.compile(p, re.I) for p in [r"\bV_?F\b", r"forward voltage", r"正向压降", r"正向电压"]]),
    ("vce_sat", [re.compile(p, re.I) for p in [r"V_?CE\s*\(\s*sat", r"Vce\s*\(sat\)", r"饱和压降"]]),
    ("vbe", [re.compile(p, re.I) for p in [
        r"V_?BE\s*\(\s*sat", r"Vbe\s*\(sat\)", r"V_?BE\s*\(\s*on", r"VBE\s*\(\s*sat",
        r"Base[\s\-]*emitter\s+saturation", r"Base[\s\-]*emitter\s+voltage",
        r"基极[\s\-]*发射极饱和", r"基极[\s\-]*发射极",
    ]]),
]

VRANGE = re.compile(
    r"(\d+(?:\.\d+)?)\s*V\s*(?:to|–|-|~|至)\s*(\d+(?:\.\d+)?)\s*V",
    re.I,
)
# TI/ST 表：MIN 2 MAX 30 V；或 3 36 V（单位只出现一次）
VMINMAX = re.compile(
    r"MIN\s+(\d+(?:\.\d+)?)\s+MAX\s+(\d+(?:\.\d+)?)\s*V",
    re.I,
)
VTWOCOL = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*V\b",
    re.I,
)
FEATURES_VS = re.compile(
    r"(?:wide\s+)?supply\s+(?:range|voltage)\s+(?:of\s+)?(\d+(?:\.\d+)?)\s*V\s+to\s+(\d+(?:\.\d+)?)\s*V",
    re.I,
)
VI_RANGE = re.compile(
    r"\bV_?I\b\s*=\s*(\d+(?:\.\d+)?)\s*(?:to|–|-|~|至)\s*(\d+(?:\.\d+)?)\s*V",
    re.I,
)
VO_TYP_ROW = re.compile(
    r"Output voltage[^0-9]{0,40}?(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*V",
    re.I,
)
_SUPPLY_HINT = re.compile(
    r"supply\s+voltage|\bVCC\b|\bVDD\b|\bV_?S\b|供电电压|工作电压",
    re.I,
)
_RATING_HINT = re.compile(
    r"voltage\s+rating|working\s+voltage|额定耐压|WVDC|rated\s+voltage",
    re.I,
)
CL_PF = re.compile(r"\bC_?L\b[^0-9]{0,12}(\d+(?:\.\d+)?)\s*pF", re.I)
DECAP_UF = re.compile(r"0\.1\s*µ?u?F|100\s*nF", re.I)
_NO_EXTERNAL = re.compile(r"no external components are required", re.I)
_TEST_CAP = re.compile(
    r"\bC_?[IO]\b\s*=|unless otherwise specified|test circuits?",
    re.I,
)
_REQUIRE_DECAP = re.compile(
    r"(decoupl\w+|bypass)\s+capacit\w+[^.]{0,80}?(required|shall|must|recommend)"
    r"|(required|shall|must|recommend)[^.]{0,80}?(decoupl\w+|bypass)\s+capacit"
    r"|外接[^。]{0,24}(去耦|旁路)电容[^。]{0,24}(必须|要求)",
    re.I,
)
RATED_V = re.compile(
    r"(?:rated|working)\s+voltage[^0-9]{0,40}(\d+(?:\.\d+)?)\s*V"
    r"|额定(?:耐压|电压)[^0-9]{0,24}(\d+(?:\.\d+)?)\s*V"
    r"|Voltage\s*Rating[^0-9]{0,24}(\d+(?:\.\d+)?)\s*V"
    r"|(\d{3})\s*=\s*(\d+(?:\.\d+)?)\s*V(?:DC)?",
    re.I,
)
PTOT = re.compile(
    r"(?:Ptot|power\s+dissipation|rated\s+power|额定功率)"
    r"[^0-9]{0,24}(\d+(?:\.\d+)?)\s*(mW|W)\b",
    re.I,
)
PTOT_FRAC = re.compile(r"(?:rated\s+power|额定功率|功率)[^0-9]{0,16}(\d+)\s*/\s*(\d+)\s*W", re.I)
# 工作温度：兼容换行、Temp. Range、unicode minus −、全角冒号
TOP_RANGE = re.compile(
    r"(?:TOPR|T_?opr|T_?J\b|operating(?:\s+\w+){0,3}\s+temp(?:erature|\.)?(?:\s*range)?|"
    r"工作(?:结)?温(?:度)?(?:范围)?)"
    r"[^0-9+\-−]{0,48}"
    r"([+\-−]?\d+(?:\.\d+)?)\s*(?:°?\s*C|℃)?"
    r"\s*(?:to|TO|–|—|-|−|~|～|至|\.\.\.|…)?\s*"
    r"([+\-−]?\d+(?:\.\d+)?)\s*(?:°?\s*C|℃)",
    re.I | re.DOTALL,
)
# 独立温区行：-55℃~+125℃（陶瓷料号页常见）
TOP_RANGE_BARE = re.compile(
    r"(?<![0-9.])([+\-−]\d+(?:\.\d+)?)\s*℃\s*[~～]\s*([+\-−]?\d+(?:\.\d+)?)\s*℃",
)
I_RATED = re.compile(
    r"(?:rated\s+current|电流额定|额定电流)[^0-9]{0,20}(\d+(?:\.\d+)?)\s*A\b",
    re.I,
)
LIB_SAFE = re.compile(r"[^A-Za-z0-9._\-]+")


def safe_mpn_dir(mpn: str) -> str:
    return "".join(c if c.isalnum() or c in "-._" else "_" for c in (mpn or "").strip()) or "part"


def library_pdf_name(mpn: str) -> str:
    base = LIB_SAFE.sub("_", (mpn or "part").strip()).strip("._")[:80] or "part"
    return base if base.lower().endswith(".pdf") else base + ".pdf"


ISAT = re.compile(r"(?:I_?sat|saturation current)[^0-9]{0,24}(\d+(?:\.\d+)?)\s*(A|mA)", re.I)
IRMS = re.compile(r"(?:I_?rms)[^0-9]{0,16}(\d+(?:\.\d+)?)\s*(A|mA)", re.I)
TMAX = re.compile(r"(?:T_?max|最高(?:允许)?温度)[^0-9]{0,20}(\d+(?:\.\d+)?)\s*°?\s*C", re.I)
SCOPEISH = re.compile(r"oscilloscope|time\s*\(|ns/div|typical characteristics", re.I)
VF_TABLE = re.compile(
    r"(?:正向电压|forward\s+voltage)\s+V_?F\b.{0,48}?"
    r"(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*V",
    re.I,
)
VF_VAL = re.compile(
    r"(?:V_?F(?:orward)?|forward\s+voltage|正向(?:压降|电压))"
    r"[^\d]{0,48}?(\d+(?:\.\d+)?)\s*V",
    re.I,
)
VF_MAX = re.compile(
    r"(?:forward\s+voltage|正向(?:压降|电压))"
    r"(?:(?:(?!\d+(?:\.\d+)?\s*V).){0,80})"
    r"(\d+(?:\.\d+)?)\s*V",
    re.I,
)
VCE_VAL = re.compile(
    r"(?:V_?CE\s*\(\s*sat\s*\)|VCE\(sat\)|Vce\s*\(sat\)|饱和压降|"
    r"Collector[\s\-]*Emitter\s+Saturation)"
    r"[^\d]{0,48}?(\d+(?:\.\d+)?)\s*V",
    re.I,
)
VBE_VAL = re.compile(
    r"(?:V_?BE\s*\(\s*(?:sat|on)\s*\)|VBE\((?:sat|on)\)|Vbe\s*\((?:sat|on)\)|"
    r"Base[\s\-]*emitter\s+(?:saturation\s+)?voltage|"
    r"基极[\s\-]*发射极(?:饱和)?(?:压降|电压)?)"
    r"[^\d]{0,48}?(\d+(?:\.\d+)?)\s*V",
    re.I,
)


def extract_vf_max(text: str) -> float | None:
    """正向压降只有上限时取出该上限。测试条件里的电流不当成电压。"""
    found: float | None = None
    for match in VF_MAX.finditer(text or ""):
        try:
            value = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if 0.2 <= value <= 4.5:
            found = value
    return found


def _first_in_range(pat: re.Pattern[str], text: str, lo: float, hi: float) -> float | None:
    """First in-range value; prefer a match whose prefix mentions Typ/典型."""
    first: float | None = None
    for m in pat.finditer(text or ""):
        try:
            v = float(m.group(1))
        except (TypeError, ValueError):
            continue
        if v < lo or v > hi:
            continue
        ctx = (text[max(0, m.start() - 28) : m.start()]).lower()
        if first is None:
            first = v
        if "typ" in ctx or "典型" in ctx:
            return v
    return first


def _sane_vcc(lo: float, hi: float, *, abs_max: bool = False) -> bool:
    if lo >= hi:
        return False
    if abs_max:
        return -2.0 <= lo <= 5.0 and 3.0 <= hi <= 80.0
    return 0.0 <= lo <= 18.0 and 1.5 <= hi <= 80.0


def _pair_from_window(window: str, *, abs_max: bool = False) -> tuple[float, float, str] | None:
    m = VMINMAX.search(window)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if _sane_vcc(lo, hi, abs_max=abs_max):
            return lo, hi, m.group(0)[:120]
    m = VRANGE.search(window)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if _sane_vcc(lo, hi, abs_max=abs_max):
            return lo, hi, m.group(0)[:120]
    m = VTWOCOL.search(window)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if _sane_vcc(lo, hi, abs_max=abs_max):
            return lo, hi, m.group(0)[:120]
    return None


def _slice_section(text: str, start_pat: str, end_pats: list[str]) -> str:
    m = re.search(start_pat, text or "", re.I)
    if not m:
        return ""
    chunk = text[m.start() :]
    end = re.search("|".join(end_pats), chunk[40:], re.I)
    if end:
        chunk = chunk[: 40 + end.start()]
    return chunk


def parse_vcc_recommended(text: str, mpn: str = "") -> tuple[float, float, str] | None:
    """Recommended Operating 栏的供电范围。电压额定页不得当 Vcc。"""
    if _RATING_HINT.search(text or "") and not _SUPPLY_HINT.search(text or ""):
        return None
    chunk = _slice_section(
        text or "",
        r"Recommended Operating Conditions",
        [r"Thermal Information", r"ESD Ratings", r"Electrical Characteristics", r"Absolute Maximum"],
    )
    if not chunk:
        if re.search(r"recommended operating", text or "", re.I) and _SUPPLY_HINT.search(text or ""):
            chunk = text or ""
        else:
            return None
    if mpn:
        stem = re.search(r"(LM358B|LM358|LM393B|LM393|LM339|LM2904B|LM2904|LM392)", mpn, re.I)
        if stem:
            row = re.search(
                re.escape(stem.group(1)) + r"[A-Z0-9,/ \-]{0,80}?(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*V",
                chunk,
                re.I,
            )
            if row:
                lo, hi = float(row.group(1)), float(row.group(2))
                if _sane_vcc(lo, hi):
                    return lo, hi, row.group(0)[:120]
    for m in _SUPPLY_HINT.finditer(chunk):
        window = chunk[m.start() : m.start() + 420]
        hit = _pair_from_window(window)
        if hit:
            return hit
    return _pair_from_window(chunk[:800])


def parse_vcc_abs_max(text: str) -> tuple[float, float, str] | None:
    chunk = _slice_section(
        text or "",
        r"Absolute Maximum Ratings",
        [r"Recommended Operating", r"ESD Ratings", r"Thermal Information", r"Electrical Characteristics"],
    )
    if not chunk:
        return None
    mneg = re.search(
        r"(?:VCC|Supply voltage|VS)[^\n]{0,80}?(?:–|-)?\s*(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*V",
        chunk,
        re.I,
    )
    if mneg:
        a, b = float(mneg.group(1)), float(mneg.group(2))
        lo, hi = (min(a, b), max(a, b))
        if hi <= 80 and hi > lo:
            return lo, hi, mneg.group(0)[:120]
    m_or = re.search(r"±\s*(\d+(?:\.\d+)?)\s+or\s+(\d+(?:\.\d+)?)\s*V", chunk, re.I)
    if m_or:
        hi = max(float(m_or.group(1)) * 2, float(m_or.group(2)))
        if 3 <= hi <= 80:
            return 0.0, hi, m_or.group(0)[:120]
    return _pair_from_window(chunk[:600], abs_max=True)


def parse_features_vs_candidate(text: str) -> tuple[float, float, str] | None:
    m = FEATURES_VS.search(text or "")
    if not m:
        return None
    lo, hi = float(m.group(1)), float(m.group(2))
    if _sane_vcc(lo, hi):
        return lo, hi, m.group(0)[:120]
    return None


def parse_regulator_vi(text: str, mpn: str = "") -> tuple[float, float, str] | None:
    blob = text or ""
    stem = re.search(r"(L78L\d+|L79L\d+|L78M\d+|LM78\d+)", mpn, re.I)
    if stem:
        key = stem.group(1)
        msec = re.search(key + r"[A-Z]{0,8}.{0,2500}", blob, re.I | re.S)
        if not msec:
            return None
        blob = msec.group(0) + blob[msec.end() : msec.end() + 200]
    best: tuple[float, float, str] | None = None
    for m in VI_RANGE.finditer(blob):
        lo, hi = float(m.group(1)), float(m.group(2))
        if 4 <= lo < hi <= 45:
            best = (lo, hi, m.group(0)[:120])
            if stem and re.search(r"12", stem.group(1)) and 14 <= lo <= 16:
                return best
    return best


def parse_regulator_vo(text: str, mpn: str = "") -> tuple[float, float, str] | None:
    blob = text or ""
    stem = re.search(r"(L78L\d+|L79L\d+|L78M\d+|LM78\d+)", mpn, re.I)
    if stem:
        msec = re.search(stem.group(1) + r"[A-Z]{0,8}.{0,2500}", blob, re.I | re.S)
        if not msec:
            return None
        blob = msec.group(0)
    m = VO_TYP_ROW.search(blob)
    if m:
        a, b, c = float(m.group(1)), float(m.group(2)), float(m.group(3))
        lo, hi = min(a, b, c), max(a, b, c)
        if 1.0 <= lo < hi <= 40:
            return lo, hi, m.group(0)[:120]
    return None


def parse_io_levels(text: str) -> dict[str, dict[str, Any]]:
    """VOH min / VOL max / VIH min / VIL max from electrical or recommended tables.

    Absolute Maximum is ignored. A value written as ``VOH = 2.5 V`` is a test
    condition, not the output level.
    """
    raw = text or ""
    if re.search(r"Absolute Maximum", raw, re.I) and not re.search(
        r"Electrical Characteristics|Recommended Operating", raw, re.I
    ):
        return {}
    chunks = []
    for start, ends in (
        (r"Electrical Characteristics", [r"Absolute Maximum", r"Typical Characteristics", r"Parameter Measurement"]),
        (r"Recommended Operating Conditions", [r"Electrical Characteristics", r"Absolute Maximum", r"Thermal"]),
    ):
        chunk = _slice_section(raw, start, ends)
        if chunk:
            chunks.append(chunk)
    if not chunks:
        return {}
    flat = re.sub(r"\s+", " ", " ".join(chunks))
    num = r"\d+(?:\.\d+)?"
    dash = r"[−\-–—]"
    found: dict[str, dict[str, Any]] = {}

    def take(key: str, pattern: str) -> None:
        if key in found:
            return
        m = re.search(pattern, flat, re.I)
        if not m:
            return
        val = float(m.group(1))
        if not 0 <= val <= 18:
            return
        found[key] = {"value": val, "quote": re.sub(r"\s+", " ", m.group(0))[:140]}

    # Onsemi-style: symbol, three VDD columns, then the limit. Skip ``VOH =``.
    take("voh_min_v", rf"(?<!=)\bVOH\b(?!\s*=)\s+(?:{num}\s+){{3}}({num})")
    take("vol_max_v", rf"(?<!=)\bVOL\b(?!\s*=)\s+(?:{num}\s+){{3}}(?:{dash}+\s+){{0,8}}({num})")
    take("vih_min_v", rf"(?<!=)\bVIH\b(?!\s*=)\s+(?:{num}\s+){{1,3}}({num})")
    take("vil_max_v", rf"(?<!=)\bVIL\b(?!\s*=)\s+(?:{num}\s+){{1,3}}({num})")
    # TI-style header: SYMBOL ... MIN n / MAX n
    take("voh_min_v", rf"\bVOH\b.{{0,80}}?\bMIN\b\s+({num})")
    take("vol_max_v", rf"\bVOL\b.{{0,80}}?\bMAX\b\s+({num})")
    take("vih_min_v", rf"\bVIH\b.{{0,80}}?\bMIN\b\s+({num})")
    take("vil_max_v", rf"\bVIL\b.{{0,80}}?\bMAX\b\s+({num})")
    return found


def parse_surge_ratings(text: str) -> dict[str, dict[str, Any]]:
    """TVS/varistor/GDT ratings. Zener Vz is not a surge rating."""
    raw = text or ""
    if not re.search(r"TVS|varistor|gas discharge|压敏|瞬态电压抑制", raw, re.I):
        return {}
    if re.search(r"\bVz\b|zener voltage", raw, re.I) and not re.search(r"\bV_?RWM\b|clamping voltage", raw, re.I):
        return {}
    flat = re.sub(r"\s+", " ", raw)
    found: dict[str, dict[str, Any]] = {}

    def take(key: str, pattern: str) -> None:
        m = re.search(pattern, flat, re.I)
        if not m:
            return
        found[key] = {"value": float(m.group(1)), "quote": m.group(0)[:140]}

    take("vrwm_v", r"(?:V_?RWM|stand-?off voltage|工作峰值)[^0-9]{0,24}(\d+(?:\.\d+)?)\s*V")
    take("vc_v", r"(?:clamping voltage|V_?C\b)[^0-9]{0,24}(\d+(?:\.\d+)?)\s*V")
    take("ipp_a", r"(?:I_?PP|peak pulse current)[^0-9]{0,20}(\d+(?:\.\d+)?)\s*A")
    return found


def parse_reset_levels(text: str) -> dict[str, dict[str, Any]]:
    """Reset input thresholds from electrical characteristics, not abs max."""
    chunk = _slice_section(
        text or "",
        r"Electrical Characteristics",
        [r"Absolute Maximum", r"Typical Characteristics"],
    )
    if not chunk or not re.search(r"reset", chunk, re.I):
        return {}
    flat = re.sub(r"\s+", " ", chunk)
    found: dict[str, dict[str, Any]] = {}
    m_hi = re.search(
        r"reset[^.]{0,40}?(?:high|VIH)[^0-9]{0,16}(\d+(?:\.\d+)?)\s*V",
        flat,
        re.I,
    )
    m_lo = re.search(
        r"reset[^.]{0,40}?(?:low|VIL)[^0-9]{0,16}(\d+(?:\.\d+)?)\s*V",
        flat,
        re.I,
    )
    if m_hi:
        found["reset_vih_v"] = {"value": float(m_hi.group(1)), "quote": m_hi.group(0)[:140]}
    if m_lo:
        found["reset_vil_v"] = {"value": float(m_lo.group(1)), "quote": m_lo.group(0)[:140]}
    return found


def _fill_pair(dst: dict[str, Any], prefix: str, lo: float, hi: float, page: int, quote: str) -> None:
    dst[f"{prefix}_min"] = lo
    dst[f"{prefix}_max"] = hi
    dst["page"] = page
    dst["quote"] = quote


def decoupling_hint_from_page(text: str) -> str | None:
    """Only an explicit external-cap requirement. Test-circuit CI/CO and
    'no external components are required' do not become a hint."""
    if not text or _NO_EXTERNAL.search(text) or not _REQUIRE_DECAP.search(text):
        return None
    m = DECAP_UF.search(text)
    if not m:
        return None
    window = text[max(0, m.start() - 160) : m.end() + 80]
    if _TEST_CAP.search(window):
        return None
    return "100nF" if re.search(r"100\s*nF", m.group(0), re.I) else "0.1uF"


def extract_pdf(pdf: Path, *, max_pages: int = 40, mpn: str = "") -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    params: dict[str, Any] = {}
    recommended: dict[str, Any] = {}
    abs_max: dict[str, Any] = {}
    curve_pages: list[int] = []
    pages = 0
    engine = "none"
    features_cand: tuple[float, float, str, int] | None = None
    rec_pages = 0
    no_external = False
    try:
        import fitz  # type: ignore

        doc = fitz.open(str(pdf))
        pages = doc.page_count
        engine = "pymupdf"
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            text = page.get_text("text") or ""
            if not text.strip():
                continue
            low_hits: list[str] = []
            for family, pats in FAMILY_PATTERNS:
                if any(p.search(text) for p in pats):
                    low_hits.append(family)
                    hits.append({"page": i + 1, "family": family, "snippet": _window(text, pats[0], 280)})
            if "recommended_operating" in low_hits:
                rec_pages += 1
                rec = parse_vcc_recommended(text, mpn)
                if rec and "vcc_min" not in recommended:
                    lo, hi, quote = rec
                    _fill_pair(recommended, "vcc", lo, hi, i + 1, quote)
            if "absolute_maximum" in low_hits:
                am = parse_vcc_abs_max(text)
                if am and "vcc_max" not in abs_max:
                    lo, hi, quote = am
                    _fill_pair(abs_max, "vcc", lo, hi, i + 1, quote)
            if features_cand is None:
                feat = parse_features_vs_candidate(text)
                if feat:
                    features_cand = (feat[0], feat[1], feat[2], i + 1)
            vi = parse_regulator_vi(text, mpn)
            if vi and "vi_min" not in recommended:
                lo, hi, quote = vi
                recommended["vi_min"] = lo
                recommended["vi_max"] = hi
                recommended.setdefault("page", i + 1)
                recommended["vi_quote"] = quote
            vo = parse_regulator_vo(text, mpn)
            if vo and "vo_min" not in recommended:
                lo, hi, quote = vo
                recommended["vo_min"] = lo
                recommended["vo_max"] = hi
                recommended.setdefault("page", i + 1)
                recommended["vo_quote"] = quote
            levels = parse_io_levels(text)
            levels.update(parse_surge_ratings(text))
            levels.update(parse_reset_levels(text))
            for key, rec in levels.items():
                if key in params:
                    continue
                params[key] = rec["value"]
                params[f"{key}_page"] = i + 1
                params[f"{key}_quote"] = rec["quote"]
                params[f"{key}_source"] = {
                    "kind": "datasheet_page",
                    "page": i + 1,
                    "quote": rec["quote"],
                }
            if "load_capacitance" in low_hits and "cl_pf" not in params and not SCOPEISH.search(text):
                m = CL_PF.search(text)
                if m:
                    params["cl_pf"] = float(m.group(1))
                    params["cl_page"] = i + 1
            if _NO_EXTERNAL.search(text):
                no_external = True
                params.pop("decoupling_hint", None)
            elif (
                not no_external
                and "decoupling" in low_hits
                and "decoupling_hint" not in params
            ):
                hint = decoupling_hint_from_page(text)
                if hint:
                    params["decoupling_hint"] = hint
            if ("voltage_rating" in low_hits or "额定电压" in text or "Voltage Rating" in text) and "rated_voltage_v" not in params:
                m = RATED_V.search(text)
                if m:
                    gs = [g for g in m.groups() if g]
                    params["rated_voltage_v"] = float(gs[-1])
            if "power_rating" in low_hits:
                if "ptot_text" not in params or "p_rated_w" not in params:
                    mf = PTOT_FRAC.search(text)
                    m = PTOT.search(text)
                    hit = mf or m
                    if hit:
                        params.setdefault("ptot_text", hit.group(0))
                    if "p_rated_w" not in params:
                        try:
                            if mf:
                                val = float(mf.group(1)) / float(mf.group(2))
                                quote = mf.group(0)
                            elif m:
                                val = float(m.group(1))
                                if str(m.group(2) or "W").lower() == "mw":
                                    val = val / 1000.0
                                quote = m.group(0)
                            else:
                                val = None
                                quote = ""
                            if val is not None and 0 < val <= 100:
                                params["p_rated_w"] = val
                                params["p_rated_w_page"] = i + 1
                                params["p_rated_w_quote"] = quote[:140]
                        except (TypeError, ValueError, ZeroDivisionError):
                            pass
            if "t_op_min_c" not in params or "t_op_max_c" not in params:
                m = None
                if "operating_temp" in low_hits:
                    m = TOP_RANGE.search(text)
                if m is None:
                    m = TOP_RANGE_BARE.search(text)
                if m:
                    try:
                        def _temp(tok: str) -> float:
                            s = (tok or "").strip().replace("−", "-").replace("＋", "+")
                            return float(s)

                        lo, hi = _temp(m.group(1)), _temp(m.group(2))
                        if lo > hi:
                            lo, hi = hi, lo
                        # 排除误命中的窄量程/曲线轴（真工作温区通常跨度≥40℃且上沿≥60）
                        if -80 <= lo <= hi <= 200 and (hi - lo) >= 40 and hi >= 60:
                            params.setdefault("t_op_min_c", lo)
                            params.setdefault("t_op_max_c", hi)
                            params["t_op_page"] = i + 1
                            params["t_op_quote"] = m.group(0)[:140]
                    except (TypeError, ValueError):
                        pass
            if "i_rated_a" not in params:
                m = I_RATED.search(text)
                if m:
                    try:
                        val = float(m.group(1))
                        if 0 < val <= 200:
                            params["i_rated_a"] = val
                            params["i_rated_a_page"] = i + 1
                            params["i_rated_a_quote"] = m.group(0)[:140]
                    except (TypeError, ValueError):
                        pass
            if "isat_irms" in low_hits:
                curve_pages.append(i + 1)
                if "isat" not in params:
                    m = ISAT.search(text)
                    if m:
                        val = float(m.group(1))
                        if m.group(2).lower() == "ma":
                            val /= 1000.0
                        params["isat"] = val
                        params["isat_page"] = i + 1
                if "irms" not in params:
                    m = IRMS.search(text)
                    if m:
                        val = float(m.group(1))
                        if m.group(2).lower() == "ma":
                            val /= 1000.0
                        params["irms"] = val
                tm = TMAX.search(text)
                if tm and "t_max_c" not in params:
                    params["t_max_c"] = float(tm.group(1))
                    params["t_max_page"] = i + 1
            flat = re.sub(r"\s+", " ", text)
            if "vf_v" not in params:
                tm = VF_TABLE.search(flat)
                if tm:
                    try:
                        typ = float(tm.group(2))
                        vmax = float(tm.group(3))
                    except (TypeError, ValueError):
                        typ = None
                        vmax = None
                    if typ is not None and 0.4 <= typ <= 4.5:
                        params["vf_v"] = typ
                        params["vf_page"] = i + 1
                    if vmax is not None and 0.2 <= vmax <= 4.5 and "vf_max_v" not in params:
                        params["vf_max_v"] = vmax
                        params["vf_max_page"] = i + 1
                if "vf_v" not in params:
                    vf = _first_in_range(VF_VAL, flat, 0.4, 4.5)
                    if vf is not None:
                        params["vf_v"] = vf
                        params["vf_page"] = i + 1
            if "vf_max_v" not in params:
                vmax = extract_vf_max(flat)
                if vmax is not None:
                    params["vf_max_v"] = vmax
                    params["vf_max_page"] = i + 1
            if "vce_sat_v" not in params:
                vsat = _first_in_range(VCE_VAL, flat, 0.01, 2.0)
                if vsat is not None:
                    params["vce_sat_v"] = vsat
                    params["vce_sat_page"] = i + 1
            if "vbe_v" not in params:
                vbe = _first_in_range(VBE_VAL, flat, 0.3, 1.5)
                if vbe is not None:
                    params["vbe_v"] = vbe
                    params["vbe_page"] = i + 1
        doc.close()
    except Exception as e:
        engine = f"unavailable:{e}"
    if "vcc_min" not in recommended and features_cand and rec_pages:
        lo, hi, quote, page = features_cand
        corroborated = False
        for h in hits:
            if h.get("family") in {"recommended_operating", "supply_voltage"}:
                sn = str(h.get("snippet") or "")
                if re.search(rf"{re.escape(str(int(lo) if lo == int(lo) else lo))}.*{re.escape(str(int(hi) if hi == int(hi) else hi))}", sn):
                    corroborated = True
                    break
                if f"{int(hi)}" in sn and _SUPPLY_HINT.search(sn):
                    corroborated = True
                    break
        if corroborated:
            _fill_pair(recommended, "vcc", lo, hi, page, quote)
            recommended["corroborated"] = "features+recommended_section"
    if recommended:
        params["recommended"] = recommended
        # 顶栏 vcc 只来自推荐，禁止用 abs max 冒充
        if "vcc_min" in recommended:
            params["vcc_min"] = recommended["vcc_min"]
            params["vcc_max"] = recommended["vcc_max"]
    if abs_max:
        params["abs_max"] = abs_max
    if curve_pages:
        params["curve_pages"] = sorted(set(curve_pages))
        params["curve_note"] = "曲线页只供读点；读不出点标缺手册，禁止口头ΔT"
    trimmed: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for h in hits:
        fam = h["family"]
        counts[fam] = counts.get(fam, 0) + 1
        if counts[fam] <= 3:
            trimmed.append(h)
    return {
        "source": "datasheet" if pages else "extract_failed",
        "engine": engine,
        "pages": pages,
        "params": params,
        "recommended": recommended,
        "abs_max": abs_max,
        "hits": trimmed,
        "hit_families": sorted({h["family"] for h in trimmed}),
    }


def _window(text: str, pat: re.Pattern[str], radius: int) -> str:
    m = pat.search(text)
    if not m:
        return text[: radius * 2].replace("\n", " ").strip()
    a = max(0, m.start() - radius)
    b = min(len(text), m.end() + radius)
    return re.sub(r"\s+", " ", text[a:b]).strip()
