# -*- coding: utf-8 -*-
"""深审检查项 → 工作台必须先备齐的额定字段。

检查项清单稳定，因此按 rule_id 写死。查参与料号缓存沿用 kernel.rated_registry 的字段名。
"""

from __future__ import annotations

from typing import Any

from agent_review.context_schema import content_hash
from agent_review.context_store import (
    ContextStore,
    live_path,
    load_manifest,
    planned_key_id,
    save_manifest,
    store_root,
)
from agent_review.mpn_param import read_agent_param_cache, run_mpn_param_jobs
from kernel.rated_hunt import is_placeholder_mpn
from kernel.rated_registry import FIELD_META
from kernel.util import out_dir, read_json, write_json

# 工作台 params 的键。值是查参/参数表里的字段名（可与展示键不同）。
RULE_PARAM_FIELDS: dict[str, list[tuple[str, str]]] = {
    "P11-A01": [("p_rated_w", "p_rated_w"), ("p_rated_temp_c", "p_rated_temp_c")],
    "P11-A02": [("v_rated_v", "rated_voltage_v")],
    "P11-A06": [("isat_a", "isat")],
    "P11-A03": [("v_rated_v", "rated_voltage_v")],
    "P11-A07": [("isat_a", "isat"), ("i_rms_a", "i_rms_a")],
    "P12-A01": [
        ("v_rec_min_v", "recommended.vcc_min"),
        ("v_rec_max_v", "recommended.vcc_max"),
        ("v_abs_max_v", "abs_max.vcc_max"),
    ],
    "P12-A02": [
        ("voh_min_v", "voh_min_v"),
        ("vol_max_v", "vol_max_v"),
        ("vih_min_v", "vih_min_v"),
        ("vil_max_v", "vil_max_v"),
    ],
    "P12-A03": [("t_op_min_c", "t_op_min_c"), ("t_op_max_c", "t_op_max_c")],
    "P12-A04": [("vds_max_v", "vds_rated_v")],
    "P12-A05": [("id_cont_a", "id_rated_a")],
    "P12-A06": [("vrwm_v", "vrwm_v"), ("vc_v", "vc_v")],
    "P12-A07": [
        ("i_rated_a", "i_rated_a"),
        ("mating_cycles", "mating_cycles"),
        ("ip_rating", "ip_rating"),
    ],
    "P21-A01": [
        ("voh_min_v", "voh_min_v"),
        ("vol_max_v", "vol_max_v"),
        ("vih_min_v", "vih_min_v"),
        ("vil_max_v", "vil_max_v"),
    ],
}

_NET_RULES = frozenset({"P12-A02", "P21-A01"})
_IC_TYPES = frozenset({"ic", "u", "opamp", "comparator", "mcu"})


def fields_for_rule(rule_id: str) -> list[tuple[str, str]]:
    return list(RULE_PARAM_FIELDS.get(str(rule_id or "")) or [])


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (dict, list)) and not value:
        return False
    return True


def _dig(doc: dict[str, Any] | None, lookup: str) -> Any:
    if not isinstance(doc, dict) or not lookup:
        return None
    if lookup in doc and _present(doc.get(lookup)):
        return doc.get(lookup)
    cur: Any = doc
    for part in lookup.split("."):
        if not isinstance(cur, dict) or part not in cur:
            cur = None
            break
        cur = cur.get(part)
    if _present(cur):
        return cur
    return None


def _from_store_fields(fields: dict[str, Any], lookup: str) -> Any:
    for bucket in ("params", "rated", "recommended", "abs_max"):
        hit = _dig(fields.get(bucket) if isinstance(fields.get(bucket), dict) else None, lookup)
        if hit is None and bucket in {"recommended", "abs_max"} and "." in lookup:
            head, _, tail = lookup.partition(".")
            if head == bucket:
                hit = _dig(fields.get(bucket) if isinstance(fields.get(bucket), dict) else None, tail)
        if _present(hit):
            return hit
    return _dig(fields, lookup)


def _read_pintable(o, mpn: str) -> dict[str, Any]:
    from agent_review.material_params import material_for_mpn
    return material_for_mpn(o, mpn)


def _read_store_fields(o, mpn: str) -> dict[str, Any]:
    path = live_path(store_root(o), "mpn", mpn)
    if not path.is_file():
        return {}
    try:
        rec = read_json(path)
    except Exception:
        return {}
    fields = rec.get("fields") if isinstance(rec, dict) else None
    return fields if isinstance(fields, dict) else {}


def param_entry(o, mpn: str, display: str, lookup: str) -> dict[str, Any]:
    """一个额定键：present 带值与来源，否则 missing。"""
    meta = FIELD_META.get(lookup) or FIELD_META.get(display) or {}
    unit = str(meta.get("unit") or "")
    base = {"key": display, "lookup": lookup, "unit": unit}

    pt = _read_pintable(o, mpn)
    for bucket, source in (
        ("params", "pintable"),
        ("rated", "pintable"),
        ("recommended", "pintable"),
        ("abs_max", "pintable"),
    ):
        hit = _dig(pt.get(bucket) if isinstance(pt.get(bucket), dict) else None, lookup)
        if hit is None and "." in lookup:
            head, _, tail = lookup.partition(".")
            if head == bucket:
                hit = _dig(pt.get(bucket) if isinstance(pt.get(bucket), dict) else None, tail)
        if _present(hit):
            return {**base, "status": "present", "value": hit, "source": source}

    hit = _from_store_fields(_read_store_fields(o, mpn), lookup)
    if _present(hit):
        return {**base, "status": "present", "value": hit, "source": "context_store"}

    board = _board_from_out(o)
    cached = read_agent_param_cache(board, mpn, lookup)
    if cached is None:
        cached = read_agent_param_cache(board, mpn, display)
    if isinstance(cached, dict):
        val = cached.get("value")
        if val is None and _present(cached.get("value_text")):
            val = cached.get("value_text")
        if _present(val):
            return {
                **base,
                "status": "present",
                "value": val,
                "source": "agent_cache",
                "cite": str(cached.get("cite") or ""),
            }
    return {**base, "status": "missing"}


def _board_from_out(o):
    return o.parent


def excerpt_for_mpn(o, mpn: str, rule_id: str) -> dict[str, Any]:
    mpn = str(mpn or "").strip()
    material = _read_pintable(o, mpn) if mpn else {}
    return {
        "mpn": mpn,
        "datasheet_path": material.get("datasheet_path") or "",
        "pintable_path": material.get("pintable_path") or "",
        "params": [param_entry(o, mpn, disp, lookup)
                   for disp, lookup in fields_for_rule(rule_id)] if mpn else [],
        "material_params": material.get("params") or {},
        "recommended": material.get("recommended") or {},
        "abs_max": material.get("abs_max") or {},
        "sources": material.get("sources") or [],
        "conflicts": material.get("conflicts") or [],
        "unresolved": material.get("unresolved") or [],
    }


def _ic_like(comp: dict[str, Any], ref: str) -> bool:
    tk = str(comp.get("type") or "").strip().lower()
    if tk in _IC_TYPES:
        return True
    return str(ref).upper().startswith("U")


def mpns_for_object(kg: dict[str, Any], obj: str, rule_id: str) -> list[str]:
    comps = kg.get("components") or {}
    if obj in comps:
        mpn = str((comps.get(obj) or {}).get("mpn") or "").strip()
        if mpn and not is_placeholder_mpn(mpn):
            return [mpn]
        return []
    if rule_id not in _NET_RULES:
        return []
    net = (kg.get("nets") or {}).get(obj) or {}
    found: list[str] = []
    for ref in list(net.get("refs") or []):
        comp = comps.get(ref) or {}
        if not _ic_like(comp, str(ref)):
            continue
        mpn = str(comp.get("mpn") or "").strip()
        if not mpn or is_placeholder_mpn(mpn) or mpn in found:
            continue
        found.append(mpn)
    return found


def missing_pairs(board, kg: dict[str, Any], enumeration: dict[str, Any]) -> list[tuple[str, str]]:
    o = out_dir(board)
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    by_rule = enumeration.get("by_rule") or {}
    for rid in enumeration.get("rule_ids") or []:
        spec = fields_for_rule(str(rid))
        if not spec:
            continue
        for obj in list(by_rule.get(rid) or []):
            for mpn in mpns_for_object(kg, str(obj), str(rid)):
                for _disp, lookup in spec:
                    ent = param_entry(o, mpn, _disp, lookup)
                    if ent.get("status") == "present":
                        continue
                    key = (mpn, lookup)
                    if key in seen:
                        continue
                    seen.add(key)
                    pairs.append(key)
    return pairs


def _merge_cached_into_store(board, pairs: list[tuple[str, str]]) -> int:
    o = out_dir(board)
    root = store_root(o)
    man = load_manifest(root)
    index = man.setdefault("index", {})
    n = 0
    for mpn, lookup in pairs:
        cached = read_agent_param_cache(board, mpn, lookup)
        if not isinstance(cached, dict):
            continue
        val = cached.get("value")
        if val is None and _present(cached.get("value_text")):
            val = cached.get("value_text")
        if not _present(val):
            continue
        path = live_path(root, "mpn", mpn)
        if not path.is_file():
            continue
        rec = read_json(path)
        if not isinstance(rec, dict):
            continue
        fields = dict(rec.get("fields") or {})
        params = dict(fields.get("params") or {}) if isinstance(fields.get("params"), dict) else {}
        if _present(params.get(lookup)):
            continue
        params[lookup] = val
        fields["params"] = params
        rated = dict(fields.get("rated") or {}) if isinstance(fields.get("rated"), dict) else {}
        if not _present(rated.get(lookup)):
            rated[lookup] = val
            fields["rated"] = rated
        prov = list(rec.get("provenance") or [])
        prov.append(
            {
                "source_kind": "agent_lookup",
                "path": f"review_out/cache/mpn/{mpn}/agent_{lookup}.json",
                "locator": lookup,
                "quote": str(cached.get("cite") or "")[:240],
            }
        )
        rec["fields"] = fields
        rec["provenance"] = prov
        rec["content_hash"] = content_hash(fields, prov)
        rec["revision"] = int(rec.get("revision") or 0) + 1
        from agent_review.context_store import _now

        rec["updated_at"] = _now()
        write_json(path, rec)
        pk = planned_key_id("mpn", mpn)
        if pk in index:
            index[pk]["content_hash"] = rec["content_hash"]
            index[pk]["revision"] = rec["revision"]
            index[pk]["status"] = "ready"
        n += 1
    if n:
        save_manifest(root, man)
        try:
            ContextStore(o).seal()
        except Exception:
            pass
    return n


def ensure_rule_params(board, kg: dict[str, Any], enumeration: dict[str, Any]) -> dict[str, Any]:
    """缺额定键时按料号查一次，写回 cache 与密封料号。已有值不重复查。"""
    pairs = missing_pairs(board, kg, enumeration)
    if not pairs:
        return {"jobs": 0, "merged": 0, "skipped": True}
    ran = run_mpn_param_jobs(board, pairs, concurrency=4)
    merged = _merge_cached_into_store(board, pairs)
    return {"jobs": ran.get("jobs"), "merged": merged, "ok": ran.get("ok"), "skipped": False}
