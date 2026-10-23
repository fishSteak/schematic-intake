# -*- coding: utf-8 -*-
"""额定栏统一读写：nested recommended/abs_max 与点字段共存。禁止 abs max 冒充推荐。"""
from __future__ import annotations

import re
from typing import Any

NEST_ROOTS = ("recommended", "abs_max")
SHORT_RECOMMENDED = {"vcc_min", "vcc_max", "vi_min", "vi_max", "vo_min", "vo_max"}
_REGULATOR_MPN = re.compile(
    r"L78L|L79L|LM78[0-9]|LM79[0-9]|AMS1117|LM317|78L\d|79L\d|L78M|L79M",
    re.I,
)
_VIN_PIN = re.compile(r"^(VIN|VI|INPUT|IN)$", re.I)
_VOUT_PIN = re.compile(r"^(VOUT|VO|OUTPUT|OUT)$", re.I)


def parse_rated_field(field: str) -> tuple[str, str] | None:
    """('recommended','vcc_min') or None if not a rated nest field."""
    f = str(field or "").strip()
    if not f:
        return None
    if "." in f:
        root, leaf = f.split(".", 1)
        root, leaf = root.strip(), leaf.strip()
        if root in NEST_ROOTS and leaf:
            return root, leaf
        return None
    if f in SHORT_RECOMMENDED:
        return "recommended", f
    return None


def _as_float(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def write_rated(pt: dict[str, Any], field: str, value: Any, source: dict[str, Any] | None = None) -> bool:
    parsed = parse_rated_field(field)
    if not parsed or not isinstance(pt, dict):
        return False
    root, leaf = parsed
    val = _as_float(value)
    bucket = pt.setdefault(root, {})
    if not isinstance(bucket, dict):
        bucket = {}
        pt[root] = bucket
    bucket[leaf] = val
    params = pt.setdefault("params", {})
    if not isinstance(params, dict):
        params = {}
        pt["params"] = params
    params[f"{root}.{leaf}"] = val
    nested = params.get(root)
    if not isinstance(nested, dict):
        nested = {}
        params[root] = nested
    nested[leaf] = val
    if root == "recommended" and leaf in {"vcc_min", "vcc_max"}:
        params[leaf] = val
    if source:
        ps = params.setdefault("patch_sources", {})
        if isinstance(ps, dict):
            ps[f"{root}.{leaf}"] = source
            ps[leaf] = source
    return True


def _from_params(params: dict[str, Any], root: str, leaf: str) -> Any:
    dotted = params.get(f"{root}.{leaf}")
    if dotted is not None:
        return dotted
    nested = params.get(root)
    if isinstance(nested, dict) and nested.get(leaf) is not None:
        return nested.get(leaf)
    if root == "recommended" and params.get(leaf) is not None:
        return params.get(leaf)
    return None


def read_rated(pt: dict[str, Any] | None, field: str) -> Any:
    parsed = parse_rated_field(field)
    if not parsed:
        return None
    root, leaf = parsed
    pt = pt if isinstance(pt, dict) else {}
    bucket = pt.get(root)
    if isinstance(bucket, dict) and bucket.get(leaf) is not None:
        return bucket.get(leaf)
    params = pt.get("params") if isinstance(pt.get("params"), dict) else {}
    return _from_params(params, root, leaf)


def merge_bucket(pt: dict[str, Any] | None, root: str) -> dict[str, Any]:
    """Merge nested + params dotted into one dict (does not copy sources)."""
    pt = pt if isinstance(pt, dict) else {}
    out: dict[str, Any] = {}
    bucket = pt.get(root)
    if isinstance(bucket, dict):
        for k, v in bucket.items():
            if k in {"page", "source", "quote"}:
                continue
            if v is not None:
                out[k] = v
    params = pt.get("params") if isinstance(pt.get("params"), dict) else {}
    prefix = root + "."
    for k, v in params.items():
        if k.startswith(prefix) and v is not None:
            out[k[len(prefix) :]] = v
    nested = params.get(root)
    if isinstance(nested, dict):
        for k, v in nested.items():
            if k not in {"page", "source", "quote"} and v is not None and k not in out:
                out[k] = v
    if root == "recommended":
        for k in ("vcc_min", "vcc_max"):
            if k not in out and params.get(k) is not None:
                out[k] = params[k]
    return out


def normalize_pintable(pt: dict[str, Any] | None) -> dict[str, Any]:
    """Promote dotted params into nested recommended/abs_max. Mutates pt."""
    if not isinstance(pt, dict):
        return {}
    rec = merge_bucket(pt, "recommended")
    abs_m = merge_bucket(pt, "abs_max")
    if rec:
        pt["recommended"] = rec
        params = pt.setdefault("params", {})
        if isinstance(params, dict):
            params["recommended"] = dict(rec)
            if "vcc_min" in rec:
                params["vcc_min"] = rec["vcc_min"]
            if "vcc_max" in rec:
                params["vcc_max"] = rec["vcc_max"]
    if abs_m:
        pt["abs_max"] = abs_m
        params = pt.setdefault("params", {})
        if isinstance(params, dict):
            params["abs_max"] = dict(abs_m)
    return pt


def is_regulator_mpn(mpn: str) -> bool:
    return bool(_REGULATOR_MPN.search(mpn or ""))


def pin_supply_role(comp: dict[str, Any], pin: str) -> str:
    """vcc | vi | vo. 3 脚 L78L TO-92：1=VO 3=VI。"""
    name = str(pin or "").strip()
    mpn = str(comp.get("mpn") or comp.get("value") or "")
    if _VIN_PIN.match(name) or re.search(r"VIN|INPUT", name, re.I):
        return "vi"
    if _VOUT_PIN.match(name) or re.search(r"VOUT|OUTPUT", name, re.I):
        return "vo"
    if is_regulator_mpn(mpn):
        if name == "1":
            return "vo"
        if name == "3":
            return "vi"
    return "vcc"


def range_for_role(pt: dict[str, Any] | None, role: str) -> tuple[float | None, float | None, str]:
    rec = merge_bucket(pt, "recommended")

    def pair(a: str, b: str) -> tuple[Any, Any]:
        return rec.get(a), rec.get(b)

    if role == "vi":
        lo, hi = pair("vi_min", "vi_max")
        if lo is None or hi is None:
            lo, hi = pair("vcc_min", "vcc_max")
        return _as_float(lo) if lo is not None else None, _as_float(hi) if hi is not None else None, "VI"
    if role == "vo":
        lo, hi = pair("vo_min", "vo_max")
        return _as_float(lo) if lo is not None else None, _as_float(hi) if hi is not None else None, "VO"
    lo, hi = pair("vcc_min", "vcc_max")
    return _as_float(lo) if lo is not None else None, _as_float(hi) if hi is not None else None, "Vcc"


def field_present(pt: dict[str, Any] | None, field: str) -> bool:
    if field == "rated_voltage_v":
        params = (pt or {}).get("params") if isinstance(pt, dict) else {}
        return isinstance(params, dict) and params.get("rated_voltage_v") is not None
    if field in {
        "p_rated_w",
        "ptot_w",
        "isat",
        "t_max_c",
        "t_op_min_c",
        "t_op_max_c",
        "cl_pf",
        "voh_min_v",
        "vol_max_v",
        "vih_min_v",
        "vil_max_v",
        "vrwm_v",
        "vc_v",
        "ipp_a",
        "reset_vih_v",
        "reset_vil_v",
        "reset_default",
        "vds_rated_v",
        "id_rated_a",
        "vceo_v",
        "ic_max_a",
        "i_rated_a",
        "vz_max_v",
        "vz_nom_v",
        "vf_v",
        "mating_cycles",
        "ip_rating",
    }:
        params = (pt or {}).get("params") if isinstance(pt, dict) else {}
        if not isinstance(params, dict):
            return False
        if params.get(field) is not None:
            return True
        # 二极管常只抽出 Vf max
        if field == "vf_v" and params.get("vf_max_v") is not None:
            return True
        return False
    return read_rated(pt, field) is not None
