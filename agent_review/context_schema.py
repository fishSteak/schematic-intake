# -*- coding: utf-8 -*-
"""Context store entry schema: whitelist fields, forbidden verdict words, provenance."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

SOURCE_KINDS = frozenset(
    {
        "board_kg",
        "netlist",
        "bom",
        "datasheet_pdf",
        "taskbook",
        "operating_conditions",
        "jlc_project",
    }
)

KINDS = frozenset({"object", "mpn", "net"})

STATUSES = frozenset(
    {
        "planned",
        "building",
        "quarantine",
        "ready",
        "rejected",
        "stale",
        "amending",
        "tombstoned",
    }
)

# Absolute ban in context facts (judgment leakage)
_FORBIDDEN = re.compile(
    r"(verdict_zh|建议通过|应通过|检查通过|判定为|不符合项|"
    r"\b是\b|\b否\b|待确认|不适用|INSUFFICIENT)",
    re.I,
)

OBJECT_FIELD_KEYS = frozenset(
    {
        "ref",
        "type",
        "value",
        "mpn",
        "footprint",
        "manufacturer",
        "pins",
        "nets",
        "location",
        "neighbors",
        "in_bom",
        "in_tel",
        "in_jlc",
        "conflicts",
    }
)

MPN_FIELD_KEYS = frozenset(
    {
        "mpn",
        "datasheet_path",
        "pintable_path",
        "rated",
        "recommended",
        "abs_max",
        "params",
        "pages",
        "conflicts",
        "unresolved",
    }
)

NET_FIELD_KEYS = frozenset(
    {
        "name",
        "refs",
        "rail",
        "observed_voltage",
        "members",
        "conflicts",
        "unresolved",
    }
)

KIND_FIELDS = {
    "object": OBJECT_FIELD_KEYS,
    "mpn": MPN_FIELD_KEYS,
    "net": NET_FIELD_KEYS,
}

# Scalar electrical-ish keys that require provenance when present
ELECTRICAL_KEYS = frozenset(
    {
        "observed_voltage",
        "rated",
        "recommended",
        "abs_max",
        "params",
        "rail",
    }
)


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(fields: dict[str, Any], provenance: list[dict[str, Any]]) -> str:
    blob = canonical_json({"fields": fields or {}, "provenance": provenance or []})
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _walk_forbidden(obj: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if k == "verdict_zh" or _FORBIDDEN.search(str(k)):
                hits.append(p)
            hits.extend(_walk_forbidden(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(_walk_forbidden(v, f"{path}[{i}]"))
    elif isinstance(obj, str):
        if path.endswith("verdict_zh") or (path and "verdict" in path.lower()):
            hits.append(path)
        # Allow quotes in provenance; ban standalone judgment phrases in field values
        if path.startswith("fields") and _FORBIDDEN.search(obj):
            # short codes like net names containing Chinese are rare; ban clear judgment
            if any(x in obj for x in ("建议通过", "应通过", "判定为", "verdict_zh")):
                hits.append(path)
    return hits


def validate_provenance(prov: list[Any]) -> list[str]:
    errs: list[str] = []
    if not isinstance(prov, list):
        return ["provenance must be list"]
    for i, p in enumerate(prov):
        if not isinstance(p, dict):
            errs.append(f"provenance[{i}] not object")
            continue
        sk = p.get("source_kind")
        if sk not in SOURCE_KINDS:
            errs.append(f"provenance[{i}].source_kind invalid: {sk!r}")
        if not p.get("path"):
            errs.append(f"provenance[{i}].path required")
    return errs


def validate_candidate(cand: dict[str, Any]) -> list[str]:
    """Validate a quarantine candidate before commit."""
    errs: list[str] = []
    kind = cand.get("kind")
    if kind not in KINDS:
        errs.append(f"kind invalid: {kind!r}")
    key = cand.get("key")
    if not key or not isinstance(key, str):
        errs.append("key required")
    fields = cand.get("fields")
    if not isinstance(fields, dict):
        errs.append("fields must be object")
        fields = {}
    allowed = KIND_FIELDS.get(kind or "", frozenset())
    for fk in fields:
        if fk not in allowed:
            errs.append(f"field not allowed for {kind}: {fk}")
    prov = cand.get("provenance") or []
    errs.extend(validate_provenance(prov))
    errs.extend(_walk_forbidden(fields, "fields"))
    # electrical scalars need provenance
    for ek in ELECTRICAL_KEYS:
        if ek in fields and fields[ek] not in (None, "", {}, []):
            if not prov:
                errs.append(f"electrical field {ek} requires provenance")
    return errs


def safe_key_filename(key: str) -> str:
    s = re.sub(r"[^\w.\-+@]+", "_", key, flags=re.U)
    return s[:180] or "empty"
