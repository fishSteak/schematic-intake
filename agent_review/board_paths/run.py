# -*- coding: utf-8 -*-
"""board_paths pipeline: draft → assist → verify/seal → project."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_review.batching import safe_object_name
from agent_review.board_paths.assist import run_assist_seed
from agent_review.board_paths.draft import maybe_run_path_draft
from agent_review.board_paths.domains import pilot_domains, refs_on_pages
from agent_review.board_paths.project import index_status_counts, project_device_cases
from agent_review.board_paths.schema import STATUS_CLOSED, STATUS_UNMAPPED
from agent_review.board_paths.verify import verify_and_seal
from kernel.util import out_dir, write_json


def run_board_paths(
    board: Path,
    kg: dict[str, Any],
    *,
    skip_agent: bool = False,
    concurrency: int = 2,
    model: str = "auto",
) -> dict[str, Any]:
    o = out_dir(board)
    bp = o / "board_paths"
    bp.mkdir(parents=True, exist_ok=True)

    domains = pilot_domains()
    domain_ids = [str(d["domain_id"]) for d in domains]
    page_names: set[str] = set()
    for d in domains:
        page_names.update(str(x) for x in (d.get("page_names") or []))
    domain_refs = refs_on_pages(kg, page_names)

    draft_info = maybe_run_path_draft(
        board, kg, skip_agent=skip_agent, concurrency=concurrency, model=model
    )
    assist = run_assist_seed(board, kg, domain_refs)
    write_json(bp / "assist.json", assist)

    index = verify_and_seal(
        board=board.name,
        domains=domain_ids,
        drafts=list(draft_info.get("drafts") or []),
        assist=assist,
        kg=kg,
    )
    # gate: never silently drop unmapped — record pre/post counts
    counts = index_status_counts(index)
    index["meta"] = {
        **(index.get("meta") or {}),
        "status_counts": counts,
        "draft_info": {
            "drafts_n": draft_info.get("drafts_n"),
            "agent": {
                "skipped": (draft_info.get("agent") or {}).get("skipped"),
                "ok": (draft_info.get("agent") or {}).get("ok"),
                "returncode": (draft_info.get("agent") or {}).get("returncode"),
            },
        },
        "unmapped_preserved": True,
    }
    write_json(bp / "index.json", index)

    # project per-ref under board_paths/devices (raw) — cases_stage merges into operating_cases
    devices_root = bp / "devices"
    devices_root.mkdir(parents=True, exist_ok=True)
    covered_refs: set[str] = set()
    for path in index.get("paths") or []:
        for r in path.get("refs") or []:
            covered_refs.add(str(r))

    projected: dict[str, Any] = {}
    for ref in sorted(covered_refs):
        doc = project_device_cases(index, ref=ref)
        # enrich to operating_cases shape
        projected[ref] = _to_operating_doc(ref, doc, index)
        write_json(devices_root / f"{safe_object_name(ref)}.json", projected[ref])

    write_json(
        bp / "projection_stats.json",
        {
            "covered_refs_n": len(covered_refs),
            "status_counts": counts,
            "refs": sorted(covered_refs),
        },
    )
    return {
        "index": index,
        "projected": projected,
        "covered_refs": sorted(covered_refs),
        "status_counts": counts,
        "draft_info": draft_info,
        "assist_paths_n": len(assist.get("paths") or []),
    }


def _to_operating_doc(ref: str, proj: dict[str, Any], index: dict[str, Any]) -> dict[str, Any]:
    cases = list(proj.get("cases") or [])
    # pick primary path = first path containing ref
    primary = None
    actors: list[Any] = []
    for path in index.get("paths") or []:
        if ref in [str(r) for r in (path.get("refs") or [])]:
            primary = path
            actors = list(path.get("actors") or [])
            break
    path_block = (cases[0].get("path") if cases else None) or {}
    if primary:
        path_block = {
            "chain_refs": list(primary.get("refs") or []),
            "nodes": list(primary.get("nodes") or []),
            "supply_net": primary.get("supply_net"),
            "exit_net": primary.get("exit_net"),
            "text": primary.get("text") or path_block.get("text") or "",
            "group_id": primary.get("group_id") or "",
            "path_id": primary.get("path_id") or "",
        }
    unmapped_n = sum(1 for c in cases if c.get("status") == STATUS_UNMAPPED)
    closed_n = sum(1 for c in cases if c.get("status") == STATUS_CLOSED)
    stuck_bits = []
    for c in cases:
        if c.get("status") == STATUS_UNMAPPED:
            miss = c.get("missing") or []
            stuck_bits.append(
                f"{c.get('case_id')}: {c.get('stuck') or '未建模'}"
                + (f"（缺：{'/'.join(miss)}）" if miss else "")
            )
    completeness = "complete" if unmapped_n == 0 and closed_n > 0 else "incomplete"
    # normalize cases for downstream
    out_cases = []
    for c in cases:
        eid = str(c.get("event_id") or "")
        cid = str(c.get("case_id") or "")
        if (
            eid == "supply_off"
            or cid.endswith("::supply_off")
            or "未上电" in str(c.get("name_zh") or "")
        ):
            continue
        out_cases.append(
            {
                "case_id": c.get("case_id"),
                "path_id": c.get("path_id"),
                "event_id": c.get("event_id"),
                "name_zh": c.get("name_zh"),
                "what_changes": c.get("what_changes") or "",
                "qualitative": c.get("what_changes") or "",
                "status": c.get("status"),
                "stuck": c.get("stuck") or "",
                "missing": list(c.get("missing") or []),
                "operating_conditions": c.get("operating_conditions") or {},
                "variant_ids": list(c.get("variant_ids") or []),
            }
        )
    return {
        "ref": ref,
        "rule_id": "P11-A01",
        "path": path_block,
        "actors": actors,
        "cases": out_cases,
        "completeness": completeness,
        "stuck": "；".join(stuck_bits) if stuck_bits else "",
        "kind": "board_paths",
        "meta": {
            **(proj.get("meta") or {}),
            "source": "board_paths_projection",
            "closed_n": closed_n,
            "unmapped_n": unmapped_n,
        },
    }
