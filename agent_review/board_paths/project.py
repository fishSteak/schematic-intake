# -*- coding: utf-8 -*-
"""Project board_paths index → per-device operating_cases."""

from __future__ import annotations

from typing import Any

from agent_review.board_paths.schema import STATUS_CLOSED, STATUS_UNMAPPED, case_id as make_case_id


def project_device_cases(
    index: dict[str, Any],
    *,
    ref: str,
) -> dict[str, Any]:
    """Build operating_cases payload for one device from sealed index."""
    cases: list[dict[str, Any]] = []
    for path in index.get("paths") or []:
        refs = [str(r) for r in (path.get("refs") or [])]
        if ref not in refs:
            continue
        path_id = str(path.get("path_id") or "")
        actors = list(path.get("actors") or [])
        for ev in path.get("events") or []:
            status = str(ev.get("status") or STATUS_UNMAPPED)
            if status not in (STATUS_CLOSED, STATUS_UNMAPPED):
                continue
            eid = str(ev.get("event_id") or "")
            name_zh = str(ev.get("name_zh") or "")
            # 未上电不参评稳态耗散
            if eid == "supply_off" or eid.endswith("::supply_off") or "未上电" in name_zh:
                continue
            cid = make_case_id(path_id, eid)
            case: dict[str, Any] = {
                "case_id": cid,
                "path_id": path_id,
                "event_id": ev.get("event_id"),
                "name_zh": ev.get("name_zh") or cid,
                "status": status,
                "path": {
                    "text": path.get("text") or "",
                    "refs": refs,
                    "supply_net": path.get("supply_net"),
                    "exit_net": path.get("exit_net"),
                    "nodes": list(path.get("nodes") or []),
                },
                "what_changes": ev.get("what_changes") or ev.get("name_zh") or "",
                "actors": actors,
                "group_id": path.get("group_id") or "",
                "assist_matched": bool(path.get("assist_matched")),
            }
            if status == STATUS_UNMAPPED:
                case["stuck"] = ev.get("stuck") or "程序未建模；缺可闭合应力模型"
                case["missing"] = _infer_missing(ev)
                case["operating_conditions"] = {
                    "note": "未建模；不填伪造电压",
                    "status": STATUS_UNMAPPED,
                }
            else:
                case["stuck"] = ""
                case["missing"] = []
                case["operating_conditions"] = {
                    "note": "由程序 situations/stress 闭合算数",
                    "status": STATUS_CLOSED,
                    "class_hint": ev.get("class_hint") or ev.get("event_id"),
                }
            cases.append(case)

    return {
        "ref": ref,
        "cases": cases,
        "meta": {
            "source": "board_paths_projection",
            "closed_n": sum(1 for c in cases if c.get("status") == STATUS_CLOSED),
            "unmapped_n": sum(1 for c in cases if c.get("status") == STATUS_UNMAPPED),
        },
    }


def _infer_missing(ev: dict[str, Any]) -> list[str]:
    stuck = str(ev.get("stuck") or "")
    out: list[str] = []
    if "配方" in stuck or "串联钳位" in stuck:
        out.append("出口模型/串联钳位算数配方")
    if "事件类型" in stuck or "未建模" in stuck:
        out.append("可闭合应力模型")
    if not out:
        out.append("程序算数模型或闭合输入")
    return out


def index_status_counts(index: dict[str, Any]) -> dict[str, int]:
    closed = unmapped = rejected = 0
    for p in index.get("paths") or []:
        for ev in p.get("events") or []:
            st = ev.get("status")
            if st == STATUS_CLOSED:
                closed += 1
            elif st == STATUS_UNMAPPED:
                unmapped += 1
    rejected = len(index.get("rejected") or [])
    return {"closed": closed, "unmapped": unmapped, "rejected": rejected}
