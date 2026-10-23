"""Step 1: design_graph from netlist+BOM; optional JLC convert."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from kernel.adapters.bom import parse_bom_file
from kernel.adapters.jlc import convert_eprj2, ensure_node_modules
from kernel.adapters.netlist import parse_netlist_file
from kernel.util import out_dir, write_json


def run(board: Path, materials: dict[str, Any]) -> dict[str, Any]:
    tel = Path(materials["netlist"])
    bom_path = Path(materials["bom"])
    tel_parts, nets, fmt = parse_netlist_file(tel)
    bom_parts = parse_bom_file(bom_path)

    pin_of: dict[str, dict[str, str]] = defaultdict(dict)
    for net_name, pins in nets.items():
        for ref, pin in pins:
            pin_of[ref][pin] = net_name

    components: dict[str, Any] = {}
    all_refs = sorted(set(tel_parts) | set(bom_parts))
    for ref in all_refs:
        t = tel_parts.get(ref, {})
        b = bom_parts.get(ref, {})
        components[ref] = {
            "ref": ref,
            "value_tel": t.get("value", ""),
            "value_bom": b.get("value", ""),
            "mpn": b.get("mpn") or "",
            "footprint": t.get("footprint") or b.get("footprint") or "",
            "manufacturer": b.get("manufacturer") or "",
            "in_tel": ref in tel_parts,
            "in_bom": ref in bom_parts,
            "pins": [
                {"pin": pin, "net": net}
                for pin, net in sorted(pin_of.get(ref, {}).items(), key=lambda x: (len(x[0]), x[0]))
            ],
        }

    jlc_meta: dict[str, Any] | None = None
    eprj2 = materials.get("eprj2")
    if eprj2:
        ensure_node_modules()
        jlc_path = out_dir(board) / "jlc_project.json"
        jlc = convert_eprj2(Path(eprj2), jlc_path)
        jlc_meta = {
            "path": str(jlc_path),
            "summary": jlc.get("summary"),
            "complete": jlc.get("complete"),
            "convert": jlc.get("_convert"),
        }

    graph = {
        "format": fmt,
        "netlist": str(tel),
        "bom": str(bom_path),
        "jlc": jlc_meta,
        "stats": {
            "tel_parts": len(tel_parts),
            "bom_parts": len(bom_parts),
            "nets": len(nets),
            "components": len(components),
        },
        "components": components,
        "nets": {
            name: {"pins": [[r, p] for r, p in plist], "refs": sorted({r for r, _ in plist})}
            for name, plist in nets.items()
        },
        "tel_parts": tel_parts,
        "bom_parts": bom_parts,
    }
    write_json(out_dir(board) / "design_graph.json", graph)
    return graph
