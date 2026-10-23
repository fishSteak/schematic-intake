# -*- coding: utf-8 -*-
"""Validate and apply Agent facts_patch onto BoardKG.

Only patches with a board-local source are accepted. Narrative alone never writes KG.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from kernel.rated_fields import parse_rated_field, write_rated

SOURCE_KINDS = {"datasheet_page", "netlist_pin", "taskbook_line", "bom_row", "web_page", "lcsc_product"}

# Fields Agent may propose into component.pintable.params or net.rail
_PARAM_FIELDS = {
    "p_rated_w",
    "ptot_w",
    "rated_voltage_v",
    "vz_max_v",
    "vf_v",
    "vce_sat_v",
    "vbe_v",
    "rds_on_ohm",
    "isat",
    "cl_pf",
    "cin_pf",
    "t_max_c",
    "delta_t",
    "dt_c",
    "vcc_min",
    "vcc_max",
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
}
_RAIL_FIELDS = {"rail_value", "rail_status"}
_TYPE_FIELDS = {"device_type", "type"}
_DEVICE_TYPES = {
    "fuse",
    "diode",
    "ic",
    "connector",
    "header",
    "resistor",
    "capacitor",
    "inductor",
    "transistor",
    "mosfet",
    "crystal",
    "led",
    "ferrite",
    "tvs",
}
_IO_LEVEL_FIELDS = {"voh_min_v", "vol_max_v", "vih_min_v", "vil_max_v"}
# 施密特 VT、由电源推算的摆幅、测试条件里的饱和电压，都不是这四栏的额定。
_IO_NOT_RATED = re.compile(
    r"VT\+|VT−|VT-|threshold voltage|施密特|"
    r"V\+\s*[−\-−]\s*\d|→\s*VO[HL]|saturation voltage|ISINK|R_?L\s*=",
    re.I,
)


def validate_patch(patch: dict[str, Any], board: Path | None = None) -> tuple[bool, str]:
    """Return (ok, reason). Reject missing source or unknown source kind."""
    if not isinstance(patch, dict):
        return False, "patch_not_object"
    ref = patch.get("ref_or_net")
    field = patch.get("field")
    if not ref or not field:
        return False, "missing_ref_or_field"
    if "value" not in patch:
        return False, "missing_value"
    src = patch.get("source")
    if not isinstance(src, dict):
        return False, "missing_source"
    kind = src.get("kind")
    if kind not in SOURCE_KINDS:
        return False, f"bad_source_kind:{kind}"
    # Local file evidence preferred; path must stay under board if provided
    path = src.get("path") or src.get("local_path") or ""
    if path and board is not None:
        try:
            p = Path(path)
            if p.is_absolute():
                board_r = board.resolve()
                try:
                    p.resolve().relative_to(board_r)
                except ValueError:
                    return False, "source_path_outside_board"
        except Exception:
            return False, "source_path_invalid"
    if kind == "datasheet_page" and src.get("page") is None and not src.get("quote"):
        return False, "datasheet_needs_page_or_quote"
    if kind in {"web_page", "lcsc_product"}:
        if not src.get("url") or not src.get("quote"):
            return False, "web_needs_url_and_quote"
    if kind in {"taskbook_line", "bom_row", "netlist_pin"} and not (
        src.get("quote") or src.get("line") or src.get("pin") or src.get("row")
    ):
        return False, f"{kind}_needs_locator"
    if field in _IO_LEVEL_FIELDS and _IO_NOT_RATED.search(str(src.get("quote") or "")):
        return False, "io_level_not_rated_column"
    if field in _TYPE_FIELDS and str(patch.get("value")).strip().lower() not in _DEVICE_TYPES:
        return False, "unknown_device_type"
    return True, "ok"


def apply_patches(kg: dict[str, Any], patches: list[dict[str, Any]], board: Path | None = None) -> dict[str, Any]:
    """Return {applied:[], rejected:[{patch,reason}], kg: mutated copy}."""
    out_kg = copy.deepcopy(kg)
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    comps = out_kg.setdefault("components", {})
    nets = out_kg.setdefault("nets", {})

    for raw in patches or []:
        ok, reason = validate_patch(raw, board)
        if not ok:
            rejected.append({"patch": raw, "reason": reason})
            continue
        key = str(raw["ref_or_net"])
        field = str(raw["field"])
        value = raw["value"]
        src = raw["source"]
        # Agent often writes U1.3 for pin 3 of U1. Pin-level keys are not components.
        if (
            key not in comps
            and key not in nets
            and "." in key
            and (
                field in _PARAM_FIELDS
                or field in _TYPE_FIELDS
                or parse_rated_field(field)
            )
        ):
            head = key.split(".", 1)[0]
            if head in comps:
                key = head

        if field in _TYPE_FIELDS and key in comps:
            comps[key]["type"] = str(value).lower()
            comps[key].setdefault("sources", {})["type_patch"] = {"value": value, "source": src}
            applied.append(raw)
            continue

        if key in comps and parse_rated_field(field):
            c = comps[key]
            pt = c.setdefault("pintable", {})
            write_rated(pt, field, value, src)
            applied.append(raw)
            continue

        if field in _PARAM_FIELDS and key in comps:
            c = comps[key]
            pt = c.setdefault("pintable", {})
            params = pt.setdefault("params", {})
            params[field] = value
            if parse_rated_field(field):
                write_rated(pt, field, value, src)
            params.setdefault("patch_sources", {})[field] = src
            applied.append(raw)
            continue

        if field in _RAIL_FIELDS or field.startswith("rail."):
            nd = nets.setdefault(key, {"name": key})
            rail = nd.setdefault("rail", {})
            if field in {"rail_value", "rail.value"}:
                try:
                    rail["value"] = float(value)
                except (TypeError, ValueError):
                    rejected.append({"patch": raw, "reason": "rail_value_not_float"})
                    continue
                rail["status"] = "closed"
                rail["source"] = {"kind": "facts_patch", "origin": src}
            elif field in {"rail_status", "rail.status"}:
                rail["status"] = str(value)
                rail["source"] = {"kind": "facts_patch", "origin": src}
            else:
                rejected.append({"patch": raw, "reason": f"unknown_rail_field:{field}"})
                continue
            applied.append(raw)
            continue

        # Generic component param write under pintable.params
        if key in comps:
            c = comps[key]
            pt = c.setdefault("pintable", {})
            params = pt.setdefault("params", {})
            params[field] = value
            params.setdefault("patch_sources", {})[field] = src
            applied.append(raw)
            continue

        if key in nets:
            nd = nets[key]
            nd[field] = value
            nd.setdefault("patch_sources", {})[field] = src
            applied.append(raw)
            continue

        rejected.append({"patch": raw, "reason": "ref_or_net_not_found"})

    return {"kg": out_kg, "applied": applied, "rejected": rejected}


def extract_patches_from_pack(pack: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect facts_patch list from EvidencePack (top-level, meta, or critic reason[])."""
    out: list[dict[str, Any]] = []
    for key in ("facts_patch", "facts_patches"):
        raw = pack.get(key)
        if isinstance(raw, dict):
            out.append(raw)
        elif isinstance(raw, list):
            out.extend(x for x in raw if isinstance(x, dict))
    meta = pack.get("meta") or {}
    for key in ("facts_patch", "facts_patches"):
        raw = meta.get(key)
        if isinstance(raw, dict):
            out.append(raw)
        elif isinstance(raw, list):
            out.extend(x for x in raw if isinstance(x, dict))
    raw_reason = pack.get("reason") or pack.get("reasons") or pack.get("critic") or []
    if isinstance(raw_reason, dict):
        raw_reason = [raw_reason]
    for entry in raw_reason:
        if not isinstance(entry, dict) or str(entry.get("action") or "") != "facts_patch":
            continue
        inner = entry.get("facts_patch")
        if isinstance(inner, dict):
            out.append(inner)
        elif isinstance(inner, list):
            out.extend(x for x in inner if isinstance(x, dict))
        elif entry.get("ref_or_net") and entry.get("field"):
            out.append(entry)
    return out
