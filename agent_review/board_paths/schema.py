# -*- coding: utf-8 -*-
"""board_paths schema + case_id helpers."""

from __future__ import annotations

from typing import Any

STATUS_CLOSED = "closed"
STATUS_UNMAPPED = "unmapped"
STATUS_REJECTED = "rejected"
STATUSES = frozenset({STATUS_CLOSED, STATUS_UNMAPPED, STATUS_REJECTED})

EVENT_SUPPLY_OFF = "supply_off"
EVENT_CLAMP = "clamp_work"
EVENT_SWITCH = "switch_pull"
EVENT_PORT = "port_gnd"
EVENT_DC = "dc_work"
EVENT_OTHER = "other"

KNOWN_EVENTS = frozenset(
    {EVENT_SUPPLY_OFF, EVENT_CLAMP, EVENT_SWITCH, EVENT_PORT, EVENT_DC, EVENT_OTHER}
)


def case_id(path_id: str, event_id: str) -> str:
    return f"{path_id}::{event_id}"


def parse_case_id(cid: str) -> tuple[str, str]:
    if "::" not in cid:
        return "", cid
    a, b = cid.split("::", 1)
    return a, b


def classify_event_name(name: str) -> str:
    n = name or ""
    if "未上电" in n or n in {"supply_off", "idle"}:
        return EVENT_SUPPLY_OFF
    if "外部接" in n or ("接地" in n and "未上电" not in n) or "port_gnd" in n:
        return EVENT_PORT
    if "导通" in n or "拉低" in n or "Q4" in n or "switch" in n.lower():
        return EVENT_SWITCH
    # 直流优先于裸 Vf，避免显示管 Vf 误标钳位
    if "直流" in n or "常通" in n or n.lower() == "dc":
        return EVENT_DC
    if "钳位" in n or "稳压" in n or "Vz" in n or "clamp" in n.lower():
        return EVENT_CLAMP
    if "Vf" in n or "VF" in n:
        return EVENT_DC
    return EVENT_OTHER


def empty_index(*, board: str, domains: list[str]) -> dict[str, Any]:
    return {
        "version": "1.0",
        "board": board,
        "domains": list(domains),
        "paths": [],
        "rejected": [],
        "meta": {"seal": False},
    }


def validate_path(path: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    if not path.get("path_id"):
        errs.append("path_id required")
    if not isinstance(path.get("refs"), list) or not path.get("refs"):
        errs.append("refs required non-empty list")
    events = path.get("events") or []
    if not isinstance(events, list):
        errs.append("events must be list")
    else:
        for ev in events:
            if not isinstance(ev, dict):
                errs.append("event not object")
                continue
            if not ev.get("event_id"):
                errs.append("event_id required")
            st = ev.get("status")
            if st is not None and st not in STATUSES:
                errs.append(f"bad status {st!r}")
    return errs
