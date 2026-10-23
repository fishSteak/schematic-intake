# -*- coding: utf-8 -*-
"""把规格书和系列表里的数填进脚行为入参。不改求解。"""

from __future__ import annotations

from typing import Any

from kernel.knowledge import apply_series_params
from kernel.pintable import extract_vf_max

_DIODE_KEYS = ("vf_max_v", "vf_model_v")
_ZENER_KEYS = ("vz_min_v", "vz_nom_v", "vz_max_v", "izt_a", "zzt_ohm", "izk_a")
_RESISTOR_KEYS = ("ohm", "p_rated_w")


def params_for(mpn: str, text: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """系列表合并规格书文本。正向压降上限不写入典型值 vf_v。"""
    params = apply_series_params(mpn, dict(extra or {}))
    vmax = extract_vf_max(text)
    if vmax is not None and params.get("vf_max_v") is None:
        params["vf_max_v"] = vmax
        params["vf_max_origin"] = "datasheet"
    return params


def fill_elements(elements: list[dict[str, Any]], params_by_ref: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """缺的数值从该位号的参数字典补上。已有数值不覆盖。vf_v 不当作 vf_max。"""
    filled = []
    for item in elements:
        merged = dict(item)
        params = dict(params_by_ref.get(item.get("ref") or "") or {})
        mpn = str(merged.get("mpn") or params.get("mpn") or "")
        if mpn:
            params = apply_series_params(mpn, params)
        behavior = merged.get("behavior")
        if behavior == "diode":
            _take(merged, params, _DIODE_KEYS)
        elif behavior == "zener":
            _take(merged, params, _ZENER_KEYS)
        elif behavior in {"switch", "open_drain"}:
            if merged.get("on_drop_v") is None and params.get("vce_sat_v") is not None:
                merged["on_drop_v"] = float(params["vce_sat_v"])
                merged["on_drop_origin"] = "extracted"
        elif behavior == "resistor":
            _take(merged, params, _RESISTOR_KEYS)
        filled.append(merged)
    return filled


def _take(merged: dict[str, Any], params: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if merged.get(key) is None and params.get(key) is not None:
            merged[key] = float(params[key])
            origin = params.get(f"{key}_origin")
            if origin is None and key == "vf_model_v":
                origin = params.get("vf_model_origin")
            merged[f"{key}_origin"] = str(origin or "extracted")
