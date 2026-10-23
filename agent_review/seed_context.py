# -*- coding: utf-8 -*-
"""Seed quarantine candidates from BoardKG / pintable (facts only, no verdicts)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_review.batching import safe_object_name
from agent_review.context_store import ContextStore, planned_key_id
from kernel.util import read_json


def _prov(path: str, locator: str = "", quote: str = "") -> dict[str, Any]:
    return {
        "source_kind": "board_kg",
        "path": path,
        "locator": locator,
        "quote": quote,
    }


def seed_all(
    store: ContextStore,
    kg: dict[str, Any],
    out_dir: Path,
    planned: list[dict[str, str]],
) -> dict[str, Any]:
    """Write quarantine + commit for every planned key from materials."""
    board_kg_rel = "review_out/board_kg.json"
    comps = kg.get("components") or {}
    nets = kg.get("nets") or {}
    ok = 0
    fail = 0
    errors: list[Any] = []

    for p in planned:
        kind = p["kind"]
        key = p["key"]
        job_id = f"seed::{kind}::{safe_object_name(key)}"
        if kind == "object":
            c = comps.get(key) or {}
            pin_nets = []
            for pin in c.get("pins") or []:
                if isinstance(pin, dict) and pin.get("net"):
                    pin_nets.append(str(pin["net"]))
            neighbors = []
            for n in pin_nets:
                nd = nets.get(n) or {}
                for r in list(nd.get("refs") or [])[:20]:
                    if r != key:
                        neighbors.append(r)
            # 工况/应力包路径由 Judge 工作台 / 提示读盘；不写入 object 字段（schema 白名单）
            fields = {
                "ref": key,
                "type": c.get("type"),
                "value": c.get("value"),
                "mpn": c.get("mpn"),
                "footprint": c.get("footprint"),
                "manufacturer": c.get("manufacturer"),
                "pins": c.get("pins") or [],
                "nets": pin_nets,
                "location": c.get("location"),
                "neighbors": sorted(set(neighbors))[:32],
                "in_bom": c.get("in_bom"),
                "in_tel": c.get("in_tel"),
                "in_jlc": c.get("in_jlc"),
            }
            prov = [_prov(board_kg_rel, f"components.{key}")]
            from agent_review.batching import safe_object_name as _safe

            for rid in ("P11-A01", "P11-A03"):
                cp = out_dir / "operating_cases" / rid / f"{_safe(key)}.json"
                sp = out_dir / "program_stress" / rid / f"{_safe(key)}.json"
                if cp.is_file():
                    prov.append(
                        {
                            "source_kind": "operating_conditions",
                            "path": f"review_out/operating_cases/{rid}/{_safe(key)}.json",
                            "locator": "operating_cases",
                            "quote": "",
                        }
                    )
                if sp.is_file():
                    prov.append(
                        {
                            "source_kind": "operating_conditions",
                            "path": f"review_out/program_stress/{rid}/{_safe(key)}.json",
                            "locator": "program_stress",
                            "quote": "",
                        }
                    )
            cand = {
                "kind": "object",
                "key": key,
                "fields": fields,
                "provenance": prov,
                "agent_run_id": "seed_board_kg",
            }
        elif kind == "mpn":
            from agent_review.material_params import material_for_mpn
            material = material_for_mpn(out_dir, key, kg)
            fields = {k: v for k, v in material.items() if k != "sources"}
            prov = [_prov(board_kg_rel, f"mpn={key}")]
            for source in material["sources"]:
                prov.append(_prov(source["path"], "MPN material; limits retain original source scope",
                                  str(source)[:4000]))
            cand = {
                "kind": "mpn",
                "key": key,
                "fields": fields,
                "provenance": prov,
                "agent_run_id": "seed_board_kg",
            }
        else:  # net
            nd = nets.get(key) or {}
            rail = nd.get("rail") or {}
            fields = {
                "name": key,
                "refs": list(nd.get("refs") or [])[:40],
                "rail": rail,
                "observed_voltage": rail.get("voltage") if isinstance(rail, dict) else None,
                "members": list(nd.get("pins") or nd.get("members") or [])[:40],
            }
            prov = [_prov(board_kg_rel, f"nets.{key}")]
            cand = {
                "kind": "net",
                "key": key,
                "fields": fields,
                "provenance": prov,
                "agent_run_id": "seed_board_kg",
            }

        store.write_quarantine(job_id, cand)
        res = store.commit_quarantine(job_id, patch_mode=False)
        if res.get("ok"):
            ok += 1
        else:
            # retry as patch if already exists
            res2 = store.commit_quarantine(job_id, patch_mode=True)
            if res2.get("ok"):
                ok += 1
            else:
                fail += 1
                errors.append({"key": planned_key_id(kind, key), "errors": res.get("errors") or res2.get("errors")})

    return {"ok": ok, "fail": fail, "errors": errors[:40]}
