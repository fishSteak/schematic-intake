# -*- coding: utf-8 -*-
"""LCSC 商品查询：pdfUrl + paramVOList（额定电压等）。缓存可刷新。"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any

LCSC_DETAIL = "https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={code}"
C_CODE = re.compile(r"^C\d+$", re.I)
_VR_NAME = re.compile(r"额定电压|voltage\s*rating|rated\s*voltage|working\s*voltage|\bWVDC\b", re.I)
_VR_VAL = re.compile(r"(\d+(?:\.\d+)?)\s*V", re.I)
_SUPPLY_NAME = re.compile(
    r"工作电压|供电电压|电源电压|supply\s*voltage|operating\s*voltage|operating\s*supply",
    re.I,
)
_SUPPLY_RANGE = re.compile(
    r"(\d+(?:\.\d+)?)\s*V?\s*(?:~|to|–|-|至)\s*(\d+(?:\.\d+)?)\s*V",
    re.I,
)


def lookup_product(code: str, cache_dir: Path, *, refresh: bool = False) -> dict[str, Any]:
    code = (code or "").strip()
    empty = {
        "ok": False,
        "pdf_url": "",
        "product_url": "",
        "error": "no_c_code",
        "product_model": "",
        "product_name": "",
        "paramVOList": [],
        "product_intro": "",
        "lcsc_code": code,
    }
    if not C_CODE.match(code):
        empty["error"] = "invalid_c_code" if code else "no_c_code"
        return empty
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{code}.json"
    if cache.is_file() and not refresh:
        try:
            rec = json.loads(cache.read_text(encoding="utf-8-sig"))
        except Exception:
            rec = {}
        if isinstance(rec, dict) and rec.get("paramVOList") is not None:
            return rec
    url = LCSC_DETAIL.format(code=code)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        rec = {**empty, "error": type(e).__name__}
        return rec
    result = raw.get("result") if isinstance(raw, dict) else None
    if not isinstance(result, dict):
        rec = {**empty, "error": "bad_payload"}
        cache.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return rec
    pdf = result.get("pdfUrl") or result.get("pdfLinkUrl") or ""
    rec = {
        "ok": True,
        "pdf_url": str(pdf or ""),
        "product_url": f"https://www.lcsc.com/product-detail/{code}.html",
        "product_model": str(result.get("productModel") or ""),
        "product_name": str(result.get("productNameEn") or result.get("title") or ""),
        "product_intro": str(result.get("productIntroEn") or result.get("productDescEn") or ""),
        "paramVOList": list(result.get("paramVOList") or []),
        "error": "" if pdf else "empty_pdfUrl",
        "lcsc_code": code,
    }
    cache.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rec


def voltage_from_product(rec: dict[str, Any] | None) -> tuple[float | None, str]:
    """(Vr, quote). Prefer paramVOList 额定电压, then intro text."""
    if not isinstance(rec, dict):
        return None, ""
    for p in rec.get("paramVOList") or []:
        if not isinstance(p, dict):
            continue
        label = f"{p.get('paramName') or ''} {p.get('paramNameEn') or ''}"
        if not _VR_NAME.search(label):
            continue
        blob = f"{p.get('paramValueEn') or ''} {p.get('paramValue') or ''}"
        m = _VR_VAL.search(blob)
        if not m:
            continue
        v = float(m.group(1))
        if v <= 0 or v > 2000:
            continue
        quote = f"{(p.get('paramName') or '额定电压')}={p.get('paramValue') or p.get('paramValueEn')}"
        return v, quote
    intro = str(rec.get("product_intro") or rec.get("product_name") or "")
    m = _VR_VAL.search(intro)
    if m:
        v = float(m.group(1))
        if 1 <= v <= 2000:
            return v, intro.strip()[:160]
    return None, ""


_NAMED_PARAM = {
    "voh_min_v": re.compile(r"输出高电平|(?<![A-Za-z])VOH(?![A-Za-z])|output\s+logic\s+level\s*-\s*high", re.I),
    "vol_max_v": re.compile(r"输出低电平|(?<![A-Za-z])VOL(?![A-Za-z])|output\s+logic\s+level\s*-\s*low", re.I),
    "vih_min_v": re.compile(r"输入高电平|(?<![A-Za-z])VIH(?![A-Za-z])|input\s+logic\s+level\s*-\s*high", re.I),
    "vil_max_v": re.compile(r"输入低电平|(?<![A-Za-z])VIL(?![A-Za-z])|input\s+logic\s+level\s*-\s*low", re.I),
    "vrwm_v": re.compile(r"反向工作|断态电压|(?<![A-Za-z])Vrwm(?![A-Za-z])|stand-?off", re.I),
    "vc_v": re.compile(r"钳位电压|clamping\s+voltage", re.I),
    "ipp_a": re.compile(r"峰值脉冲电流|(?<![A-Za-z])Ipp(?![A-Za-z])|peak\s+pulse\s+current", re.I),
    "reset_vih_v": re.compile(r"复位高|reset\s+high", re.I),
    "reset_vil_v": re.compile(r"复位低|reset\s+low", re.I),
    "p_rated_w": re.compile(r"额定功率|功率\(功耗\)|power\s*\(?rating\)?|(?<![A-Za-z])Ptot(?![A-Za-z])|power\s+dissipation", re.I),
    "isat": re.compile(r"饱和电流|(?<![A-Za-z])Isat(?![A-Za-z])|saturation\s+current", re.I),
    "id_rated_a": re.compile(r"漏极电流|(?<![A-Za-z])Id\s*\(?(?:max|连续)?\)?|continuous\s+drain\s+current", re.I),
    "i_rated_a": re.compile(
        r"额定电流|电流额定|保持电流|Ihold|rated\s+current|current\s+rating|hold\s+current",
        re.I,
    ),
    "ic_max_a": re.compile(
        r"集电极电流|(?<![A-Za-z])Ic\s*\(?max\)?|collector\s+current",
        re.I,
    ),
    "vds_rated_v": re.compile(r"漏源电压|(?<![A-Za-z])Vds(?![A-Za-z])|drain[- ]source", re.I),
    "vceo_v": re.compile(
        r"集射极击穿电压|集电极[-–]?发射极|(?<![A-Za-z])Vceo(?![A-Za-z])|collector[- ]emitter",
        re.I,
    ),
    "t_max_c": re.compile(r"最高工作温度|储存?温度|operating\s+temperature|t_?max", re.I),
    "t_op_max_c": re.compile(r"工作(?:结)?温(?:度)?(?:范围)?|operating\s+(?:junction\s+)?temp", re.I),
    "t_op_min_c": re.compile(r"工作(?:结)?温(?:度)?(?:范围)?|operating\s+(?:junction\s+)?temp", re.I),
}
_VOLT_VAL = re.compile(r"(\d+(?:\.\d+)?)\s*V\b", re.I)
_AMP_VAL = re.compile(r"(\d+(?:\.\d+)?)\s*A\b", re.I)
_WATT_VAL = re.compile(r"(\d+(?:\.\d+)?)\s*(mW|W)\b", re.I)
_TEMP_VAL = re.compile(r"(-?\d+(?:\.\d+)?)\s*°?\s*C\b", re.I)
_CURRENT_FIELDS = frozenset({"ipp_a", "isat", "id_rated_a", "i_rated_a", "ic_max_a"})
_POWER_FIELDS = frozenset({"p_rated_w"})
_TEMP_FIELDS = frozenset({"t_max_c", "t_op_min_c", "t_op_max_c"})


def named_param(rec: dict[str, Any] | None, field: str) -> tuple[float | None, str]:
    """只收参数表里名称对得上的那一栏。简介里的电压不收。"""
    pat = _NAMED_PARAM.get(field)
    if not isinstance(rec, dict) or pat is None:
        return None, ""
    for p in rec.get("paramVOList") or []:
        if not isinstance(p, dict):
            continue
        label = f"{p.get('paramName') or ''} {p.get('paramNameEn') or ''}"
        if not pat.search(label):
            continue
        blob = f"{p.get('paramValueEn') or ''} {p.get('paramValue') or ''}".strip()
        # 温度允许范围串；电压/电流范围行不取单点
        if re.search(r"[~～]|至|\bto\b", blob, re.I) and field not in _TEMP_FIELDS:
            continue
        # 电压字段跳过「电流」行；电流字段跳过纯「电压」行
        if field not in _CURRENT_FIELDS and field not in _POWER_FIELDS and field not in _TEMP_FIELDS:
            if re.search(r"电流|current", label, re.I) and not re.search(r"电压|voltage", label, re.I):
                continue
        if field in _CURRENT_FIELDS and re.search(r"电压|voltage", label, re.I) and not re.search(
            r"电流|current", label, re.I
        ):
            continue
        # 截止电流 Icbo 等不是 Ic max
        if field == "ic_max_a" and re.search(r"Icbo|截止|cutoff", label, re.I):
            continue
        quote = f"{(p.get('paramName') or field)}={p.get('paramValue') or p.get('paramValueEn')}"
        if field in _POWER_FIELDS:
            m = _WATT_VAL.search(blob)
            if not m:
                continue
            v = float(m.group(1))
            if str(m.group(2)).lower() == "mw":
                v = v / 1000.0
            if 0 < v <= 100:
                return v, quote
            continue
        if field in _TEMP_FIELDS:
            nums = [float(x) for x in re.findall(r"(-?\d+(?:\.\d+)?)", blob)]
            if field == "t_op_min_c" and nums:
                return min(nums), quote
            if field in {"t_op_max_c", "t_max_c"} and nums:
                return max(nums), quote
            m = _TEMP_VAL.search(blob)
            if m:
                return float(m.group(1)), quote
            continue
        val_pat = _AMP_VAL if field in _CURRENT_FIELDS or field.endswith("_a") else _VOLT_VAL
        m = val_pat.search(blob)
        if not m:
            # 允许 200mA / 100mA
            if field in _CURRENT_FIELDS or field.endswith("_a"):
                mm = re.search(r"(\d+(?:\.\d+)?)\s*mA\b", blob, re.I)
                if mm:
                    return float(mm.group(1)) / 1000.0, quote
            continue
        return float(m.group(1)), quote
    return None, ""


def power_from_product(rec: dict[str, Any] | None) -> tuple[float | None, str]:
    """电阻/器件额定功率 (W, quote)。"""
    return named_param(rec, "p_rated_w")


def supply_range_from_product(rec: dict[str, Any] | None) -> tuple[float | None, float | None, str]:
    """IC 工作电压范围 (lo, hi, quote)。"""
    if not isinstance(rec, dict):
        return None, None, ""
    for p in rec.get("paramVOList") or []:
        if not isinstance(p, dict):
            continue
        label = f"{p.get('paramName') or ''} {p.get('paramNameEn') or ''}"
        if not _SUPPLY_NAME.search(label):
            continue
        blob = f"{p.get('paramValueEn') or ''} {p.get('paramValue') or ''}"
        m = _SUPPLY_RANGE.search(blob)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            if 0 <= lo < hi <= 80:
                quote = f"{(p.get('paramName') or '工作电压')}={p.get('paramValue') or p.get('paramValueEn')}"
                return lo, hi, quote
        nums = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*V", blob, re.I)]
        if len(nums) >= 2 and 0 <= nums[0] < nums[1] <= 80:
            quote = f"{(p.get('paramName') or '工作电压')}={p.get('paramValue') or p.get('paramValueEn')}"
            return nums[0], nums[1], quote
    intro = str(rec.get("product_intro") or rec.get("product_name") or "")
    m = _SUPPLY_RANGE.search(intro)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if 0 <= lo < hi <= 80:
            return lo, hi, intro.strip()[:160]
    return None, None, ""
