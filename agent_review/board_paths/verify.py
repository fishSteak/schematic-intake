# -*- coding: utf-8 -*-
"""Verify Agent draft (+ optional assist) against netlist; assign status."""

from __future__ import annotations

from typing import Any

from agent_review.board_paths.schema import (
    EVENT_CLAMP,
    EVENT_PORT,
    EVENT_SUPPLY_OFF,
    EVENT_SWITCH,
    STATUS_CLOSED,
    STATUS_REJECTED,
    STATUS_UNMAPPED,
    classify_event_name,
    validate_path,
)

# program can compute these majors on series_clamp topology
_CLOSED_CLASSES = frozenset({EVENT_SUPPLY_OFF, EVENT_CLAMP, EVENT_SWITCH, EVENT_PORT})


def _comp_exists(kg: dict[str, Any], ref: str) -> bool:
    return ref in (kg.get("components") or {})


def _net_exists(kg: dict[str, Any], net: str) -> bool:
    if not net:
        return False
    return net in (kg.get("nets") or {})


def _refs_ok(kg: dict[str, Any], refs: list[str]) -> tuple[bool, list[str]]:
    bad = [r for r in refs if not _comp_exists(kg, r)]
    return (not bad), bad


def _assist_path_ids_by_refs(assist: dict[str, Any]) -> dict[frozenset[str], dict[str, Any]]:
    out: dict[frozenset[str], dict[str, Any]] = {}
    for p in assist.get("paths") or []:
        refs = frozenset(str(x) for x in (p.get("refs") or []))
        if refs:
            out[refs] = p
    return out


def verify_and_seal(
    *,
    board: str,
    domains: list[str],
    drafts: list[dict[str, Any]],
    assist: dict[str, Any],
    kg: dict[str, Any],
) -> dict[str, Any]:
    """Merge drafts + assist; tag closed/unmapped/rejected."""
    rejected: list[dict[str, Any]] = []
    sealed_paths: list[dict[str, Any]] = []
    assist_by_refs = _assist_path_ids_by_refs(assist)

    # Index draft paths
    draft_paths: list[dict[str, Any]] = []
    for d in drafts:
        for p in d.get("paths") or []:
            pp = dict(p)
            pp["_domain_id"] = d.get("domain_id")
            draft_paths.append(pp)

    # If draft empty, synthesize from assist so pipeline still runs
    if not draft_paths:
        from agent_review.board_paths.assist import collapse_assist_events

        for ap in assist.get("paths") or []:
            draft_paths.append(
                {
                    **ap,
                    "events": collapse_assist_events(list(ap.get("events") or [])),
                    "_domain_id": (domains[0] if domains else ""),
                    "source": "assist_fallback",
                }
            )

    seen_path_ids: set[str] = set()
    for raw in draft_paths:
        errs = validate_path(raw)
        refs = [str(r) for r in (raw.get("refs") or [])]
        ok, bad = _refs_ok(kg, refs)
        if errs or not ok:
            rejected.append(
                {
                    **{k: v for k, v in raw.items() if not str(k).startswith("_")},
                    "status": STATUS_REJECTED,
                    "reject_reason": "; ".join(errs + ([f"missing refs {bad}"] if bad else [])),
                }
            )
            continue

        path_id = str(raw.get("path_id") or f"path_{'_'.join(refs[:4])}")
        if path_id in seen_path_ids:
            path_id = f"{path_id}__{raw.get('_domain_id') or 'd'}"
        seen_path_ids.add(path_id)

        supply = str(raw.get("supply_net") or "")
        exit_n = str(raw.get("exit_net") or "")
        # soft net check — missing nets → still keep path but events may unmapped
        assist_hit = assist_by_refs.get(frozenset(refs))
        has_assist_model = bool(assist_hit)

        events_out: list[dict[str, Any]] = []
        for ev in raw.get("events") or []:
            if not isinstance(ev, dict):
                continue
            name = str(ev.get("name_zh") or ev.get("event_id") or "")
            eid = str(ev.get("event_id") or classify_event_name(name))
            cls = ev.get("class_hint") or classify_event_name(name)
            # force major id for known classes
            if cls in _CLOSED_CLASSES:
                eid = cls
            status = STATUS_UNMAPPED
            stuck = ""
            if cls in _CLOSED_CLASSES and has_assist_model:
                status = STATUS_CLOSED
            elif cls in _CLOSED_CLASSES and not has_assist_model:
                # Agent listed classic event but program has no clamp group for these refs
                status = STATUS_UNMAPPED
                stuck = "网表可核对但程序无串联钳位算数配方"
            else:
                status = STATUS_UNMAPPED
                stuck = "程序未建模该事件类型；缺可闭合应力模型"
            events_out.append(
                {
                    "event_id": eid,
                    "name_zh": {
                        EVENT_SUPPLY_OFF: "未上电",
                        EVENT_CLAMP: "正常钳位",
                        EVENT_SWITCH: "有源拉低",
                        EVENT_PORT: "外接接地",
                    }.get(cls, name or eid),
                    "what_changes": ev.get("what_changes") or name or eid,
                    "class_hint": cls,
                    "status": status,
                    "stuck": stuck,
                    "variant_ids": list(ev.get("variant_ids") or []),
                }
            )

        # Dedupe events by event_id (prefer closed)
        by_eid: dict[str, dict[str, Any]] = {}
        for ev in events_out:
            prev = by_eid.get(ev["event_id"])
            if prev is None or (prev.get("status") != STATUS_CLOSED and ev.get("status") == STATUS_CLOSED):
                by_eid[ev["event_id"]] = ev
        events_out = list(by_eid.values())

        # Merge assist-only classic events Agent forgot (coverage assist, not veto)
        if assist_hit:
            from agent_review.board_paths.assist import collapse_assist_events

            for aev in collapse_assist_events(list(assist_hit.get("events") or [])):
                eid = aev["event_id"]
                if eid in by_eid:
                    continue
                events_out.append(
                    {
                        **aev,
                        "status": STATUS_CLOSED,
                        "stuck": "",
                        "source_note": "program_assist_fill",
                    }
                )

        actors = list(raw.get("actors") or [])
        if not actors and assist_hit:
            actors = list(assist_hit.get("actors") or [])
        # drop actors with unknown refs
        actors = [a for a in actors if isinstance(a, dict) and _comp_exists(kg, str(a.get("ref") or ""))]

        sealed_paths.append(
            {
                "path_id": path_id,
                "domain_id": raw.get("_domain_id") or raw.get("domain_id") or "",
                "text": raw.get("text") or " — ".join(refs),
                "refs": refs,
                "supply_net": supply if _net_exists(kg, supply) else supply,
                "exit_net": exit_n if _net_exists(kg, exit_n) else exit_n,
                "nodes": list(raw.get("nodes") or []),
                "actors": actors,
                "events": events_out,
                "source": raw.get("source") or "agent_draft",
                "group_id": (assist_hit or {}).get("group_id") or raw.get("group_id") or "",
                "assist_matched": bool(assist_hit),
            }
        )

    return {
        "version": "1.0",
        "board": board,
        "domains": list(domains),
        "paths": sealed_paths,
        "rejected": rejected,
        "meta": {
            "seal": True,
            "draft_paths_n": len(draft_paths),
            "assist_paths_n": len(assist.get("paths") or []),
            "sealed_paths_n": len(sealed_paths),
            "rejected_n": len(rejected),
        },
    }
