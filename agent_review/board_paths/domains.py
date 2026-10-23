# -*- coding: utf-8 -*-
"""Domain slicing for path_draft (page / module)."""

from __future__ import annotations

from typing import Any

# 一期试点域
PILOT_DOMAINS = (
    {
        "domain_id": "P08_SENSOR",
        "page_names": ("P08_SENSOR",),
        "note": "传感器驱动与钳位；应力通路试点",
    },
)


def _page_name(loc: dict[str, Any]) -> str:
    return str(loc.get("pageName") or loc.get("page_name") or "")


def refs_on_pages(kg: dict[str, Any], page_names: set[str]) -> list[str]:
    comps = kg.get("components") or {}
    out: list[str] = []
    for ref, c in comps.items():
        if not isinstance(c, dict):
            continue
        for loc in c.get("location") or []:
            if isinstance(loc, dict) and _page_name(loc) in page_names:
                out.append(str(ref))
                break
    return sorted(set(out))


def nets_touched_by_refs(kg: dict[str, Any], refs: list[str]) -> list[str]:
    comps = kg.get("components") or {}
    nets: set[str] = set()
    for ref in refs:
        c = comps.get(ref) or {}
        for p in c.get("pins") or []:
            if isinstance(p, dict) and p.get("net"):
                nets.add(str(p["net"]))
    return sorted(nets)


def build_domain_slice(kg: dict[str, Any], domain: dict[str, Any]) -> dict[str, Any]:
    pages = set(domain.get("page_names") or [])
    refs = refs_on_pages(kg, pages)
    comps = kg.get("components") or {}
    slim_comps: dict[str, Any] = {}
    for ref in refs:
        c = comps.get(ref) or {}
        slim_comps[ref] = {
            "type": c.get("type"),
            "value": c.get("value"),
            "mpn": c.get("mpn"),
            "pins": c.get("pins") or [],
            "location": c.get("location") or [],
        }
    nets = nets_touched_by_refs(kg, refs)
    slim_nets: dict[str, Any] = {}
    all_nets = kg.get("nets") or {}
    for n in nets:
        nd = all_nets.get(n) or {}
        slim_nets[n] = {
            "refs": list(nd.get("refs") or [])[:40],
            "rail": nd.get("rail"),
        }
    return {
        "domain_id": domain["domain_id"],
        "page_names": list(pages),
        "note": domain.get("note") or "",
        "refs": refs,
        "nets": nets,
        "components": slim_comps,
        "net_details": slim_nets,
    }


def pilot_domains() -> list[dict[str, Any]]:
    return [dict(d) for d in PILOT_DOMAINS]
