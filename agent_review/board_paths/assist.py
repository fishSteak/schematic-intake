# -*- coding: utf-8 -*-
"""Optional program seed assist (series clamp groups) — never vetoes Agent draft."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from agent_review.board_paths.schema import (
    EVENT_CLAMP,
    EVENT_PORT,
    EVENT_SUPPLY_OFF,
    EVENT_SWITCH,
    classify_event_name,
)
from agent_review.program_bridge import review_root
from kernel.util import out_dir, write_json

_ASSIST_WORKER = r'''
# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path
from kernel.util import read_json
from kernel.situations import (
    find_series_clamp_groups,
    build_clamp_situations,
    _comps,
    _clamp_devices,
    _attachments,
    _pin_nets,
    CLAMP_TYPES,
    PORT_TYPES,
    SWITCH_TYPES,
)

kg = read_json(Path(sys.argv[1]))
domain_refs = set(json.loads(sys.argv[2]))
out_path = Path(sys.argv[3])
comps = _comps(kg)
groups = find_series_clamp_groups(kg)


def _actors_for_group(g):
    exit_net = g["exit_net"]
    actors = []
    for ref, pin, typ in _clamp_devices(exit_net, comps):
        role = "port" if typ in PORT_TYPES else ("switch" if typ in SWITCH_TYPES else "exit_clamp")
        actors.append({"ref": ref, "pin": pin, "type": typ, "role": role, "net": exit_net})
    for ref, _pin, typ in _attachments(exit_net, comps):
        if typ not in CLAMP_TYPES:
            continue
        for _p2, n2 in _pin_nets(comps.get(ref) or {}):
            if not n2 or n2 == exit_net:
                continue
            for r3, pin3, t3 in _attachments(str(n2), comps):
                if t3 in SWITCH_TYPES:
                    actors.append({"ref": r3, "pin": pin3, "type": t3, "role": "switch", "net": str(n2)})
                if t3 in PORT_TYPES:
                    actors.append({"ref": r3, "pin": pin3, "type": t3, "role": "port", "net": str(n2)})
    seen = set()
    out = []
    for a in actors:
        k = (a["ref"], a["role"])
        if k in seen:
            continue
        seen.add(k)
        out.append(a)
    return out


paths = []
for g in groups:
    refs = list(g.get("refs") or [])
    if domain_refs and not (set(refs) & domain_refs):
        continue
    sits = build_clamp_situations(g, kg)
    events = []
    for s in sits:
        name = s.get("name") or s.get("id") or ""
        events.append({
            "event_id": s.get("id") or name,
            "name_zh": name,
            "what_changes": s.get("note") or name,
            "class_hint": None,
        })
    chain = []
    nodes = list(g.get("nodes") or [])
    for i, node in enumerate(nodes):
        chain.append(str(node))
        if i < len(refs):
            chain.append(refs[i])
    gid = str(g.get("group_id") or "_".join(refs)).replace(":", "_")
    paths.append({
        "path_id": f"assist_{gid}",
        "text": " — ".join(chain),
        "refs": refs,
        "supply_net": g.get("supply_net"),
        "exit_net": g.get("exit_net"),
        "nodes": nodes,
        "actors": _actors_for_group(g),
        "events": events,
        "source": "program_assist",
        "group_id": g.get("group_id"),
    })
out_path.write_text(json.dumps({"paths": paths}, ensure_ascii=False), encoding="utf-8")
'''


def run_assist_seed(board: Path, kg: dict[str, Any], domain_refs: list[str]) -> dict[str, Any]:
    o = out_dir(board)
    rev = review_root()
    kg_path = o / "board_kg.json"
    if not kg_path.is_file():
        write_json(kg_path, kg)
    with tempfile.TemporaryDirectory(prefix="bp_assist_") as td:
        raw = Path(td) / "assist.json"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(rev)
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                _ASSIST_WORKER,
                str(kg_path),
                json.dumps(list(domain_refs), ensure_ascii=False),
                str(raw),
            ],
            cwd=str(rev),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            return {"paths": [], "error": proc.stderr[-1500:]}
        doc = json.loads(raw.read_text(encoding="utf-8"))
    for p in doc.get("paths") or []:
        for ev in p.get("events") or []:
            ev["class_hint"] = classify_event_name(str(ev.get("name_zh") or ev.get("event_id") or ""))
    return doc


def collapse_assist_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse boundary variants into major class events for indexing."""
    buckets: dict[str, dict[str, Any]] = {}
    order = [EVENT_SUPPLY_OFF, EVENT_CLAMP, EVENT_SWITCH, EVENT_PORT]
    for ev in events:
        cls = ev.get("class_hint") or classify_event_name(str(ev.get("name_zh") or ""))
        if cls not in buckets:
            buckets[cls] = {
                "event_id": cls,
                "name_zh": {
                    EVENT_SUPPLY_OFF: "未上电",
                    EVENT_CLAMP: "正常钳位",
                    EVENT_SWITCH: "有源拉低",
                    EVENT_PORT: "外接接地",
                }.get(cls, ev.get("name_zh") or cls),
                "what_changes": ev.get("what_changes") or ev.get("name_zh") or cls,
                "class_hint": cls,
                "variant_ids": [str(ev.get("event_id") or "")],
            }
        else:
            buckets[cls]["variant_ids"].append(str(ev.get("event_id") or ""))
    out = []
    for cls in order:
        if cls in buckets:
            out.append(buckets.pop(cls))
    out.extend(buckets.values())
    return out
