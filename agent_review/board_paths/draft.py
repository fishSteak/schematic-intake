# -*- coding: utf-8 -*-
"""Agent path_draft per domain (or assist-only when skip_agent)."""

from __future__ import annotations

from agent_review.llm_auxiliary import run_jobs

import json
import os
from pathlib import Path
from typing import Any

from agent_review.board_paths.domains import build_domain_slice, pilot_domains
from kernel.util import ROOT, out_dir, write_json


def prepare_domain_jobs(board: Path, kg: dict[str, Any], domains: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    o = out_dir(board)
    draft_dir = o / "board_paths" / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    domains = domains or pilot_domains()
    jobs = []
    for d in domains:
        slice_doc = build_domain_slice(kg, d)
        domain_id = str(d["domain_id"])
        write_json(draft_dir / f"{domain_id}.slice.json", slice_doc)
        jobs.append(
            {
                "job_id": f"path_draft::{domain_id}",
                "domain_id": domain_id,
                "slice_path": f"review_out/board_paths/draft/{domain_id}.slice.json",
                "out_path": f"review_out/board_paths/draft/{domain_id}.json",
                "page_names": list(d.get("page_names") or []),
            }
        )
    doc = {"version": "1.0", "jobs": jobs}
    write_json(o / "board_paths" / "path_draft_jobs.json", doc)
    return doc


def load_drafts(board: Path, domain_ids: list[str]) -> list[dict[str, Any]]:
    o = out_dir(board)
    drafts: list[dict[str, Any]] = []
    for did in domain_ids:
        p = o / "board_paths" / "draft" / f"{did}.json"
        if not p.is_file():
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(doc, dict):
            doc.setdefault("domain_id", did)
            drafts.append(doc)
    return drafts


def run_agent_path_draft(
    board: Path,
    *,
    concurrency: int = 2,
    model: str = "auto",
) -> dict[str, Any]:
    """Run bounded domain reasoning through the configured LLM provider."""
    board = Path(board).resolve()
    jobs_path = out_dir(board) / "board_paths" / "path_draft_jobs.json"
    jobs = json.loads(jobs_path.read_text(encoding="utf-8-sig")).get("jobs") or []
    return run_jobs(board, "path_draft", jobs, concurrency=concurrency, model=model)



def maybe_run_path_draft(
    board: Path,
    kg: dict[str, Any],
    *,
    skip_agent: bool = False,
    concurrency: int = 2,
    model: str = "auto",
) -> dict[str, Any]:
    jobs = prepare_domain_jobs(board, kg)
    domain_ids = [str(j["domain_id"]) for j in jobs.get("jobs") or []]
    agent_result: dict[str, Any] = {"skipped": True}
    if not skip_agent and os.environ.get("BOARD_PATHS_SKIP_AGENT", "").strip() not in {"1", "true", "yes"}:
        agent_result = run_agent_path_draft(board, concurrency=concurrency, model=model)
        agent_result["skipped"] = False
    drafts = load_drafts(board, domain_ids)
    return {
        "jobs": jobs,
        "domain_ids": domain_ids,
        "drafts_n": len(drafts),
        "drafts": drafts,
        "agent": agent_result,
    }
