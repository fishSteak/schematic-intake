# -*- coding: utf-8 -*-
"""Build sealed context_store. Seed from BoardKG; optional SDK enrichment."""

from __future__ import annotations

from kernel.runtime import node_executable

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent_review.batching import build_context_jobs
from agent_review.context_store import ContextStore, board_kg_hash
from agent_review.seed_context import seed_all
from kernel.util import ROOT, out_dir, write_json


def _hash_file(p: Path) -> str:
    if not p.is_file():
        return ""
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()[:16]


def run(
    board: Path,
    kg: dict[str, Any],
    enumeration: dict[str, Any],
    *,
    rebuild: bool = False,
    use_sdk: bool = False,
    concurrency: int = 16,
) -> dict[str, Any]:
    o = out_dir(board)
    store = ContextStore(o)
    planned = list(enumeration.get("planned_context") or [])
    kg_path = o / "board_kg.json"
    # Seeding code and extracted parameter changes must invalidate sealed reuse.
    inputs = ["material-params-v2", _hash_file(o / "materials.json")]
    for cache in (board / "cache" / "mpn", o / "cache" / "mpn"):
        for pt in sorted(cache.glob("*/pintable.json")):
            inputs.append(str(pt.relative_to(board)) + ":" + _hash_file(pt))
    materials_hash = hashlib.sha256("\n".join(inputs).encode("utf-8")).hexdigest()[:16]
    library_hash = _hash_file(o / "datasheets" / "library.json")

    man = store.init(
        board_kg_path=kg_path,
        planned=planned,
        rebuild=rebuild,
        materials_hash=materials_hash,
        library_hash=library_hash,
    )
    if man.get("reused") and (man.get("seal") or {}).get("sealed"):
        return {"reused": True, "seal": man.get("seal"), "coverage": store.ready_coverage()}

    # Deterministic fact seed from BoardKG (quarantine → commit)
    seed_result = seed_all(store, kg, o, planned)

    if use_sdk:
        jobs = build_context_jobs(planned)
        write_json(o / "context_jobs.json", {"jobs": jobs})
        orch = ROOT / "agent_review" / "orchestrator.mjs"
        if orch.is_file():
            import os

            env = os.environ.copy()
            subprocess.run(
                [
                    node_executable(),
                    str(orch),
                    "--board",
                    str(board),
                    "--mode",
                    "context",
                    "--concurrency",
                    str(concurrency),
                ],
                check=False,
                cwd=str(ROOT),
                env=env,
            )

    # commit any remaining quarantine from SDK
    qdir = store.root / "quarantine"
    for qf in qdir.glob("*.json"):
        jid = qf.stem
        store.commit_quarantine(jid, patch_mode=True)

    seal = store.seal(allow_waive=False)
    cov = store.ready_coverage()
    write_json(
        o / "context_build_result.json",
        {"seed": seed_result, "seal": seal, "coverage": cov, "board_kg_hash": board_kg_hash(kg_path)},
    )
    return {"reused": False, "seed": seed_result, "seal": seal, "coverage": cov}
