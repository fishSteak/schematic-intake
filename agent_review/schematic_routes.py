"""Render named branches using CAD pin coordinates and verified PDF copper.

No component-center interpolation. Disconnected nets remain separate polylines;
only explicitly traversed component bodies receive dashed bridges.
"""
from statistics import median
import re

from agent_review.stress_snap import _green_graph, _nearest, _bfs, _component


def _direct_calibration(page, instances, comps, locate):
    samples = []
    for inst in instances:
        ref = inst.get("ref", "")
        if (comps.get(ref) or {}).get("type") != "resistor":
            continue
        for loc in inst.get("locations") or []:
            if loc.get("pageName") not in page.get_text():
                # PDF page titles are not always printed; use KG page mapping.
                locs = (comps.get(ref) or {}).get("location") or []
                if not any(x.get("pdf_page") == page.number + 1 for x in locs):
                    continue
            point = locate(page, ref, near=None, comps=comps)
            if point:
                samples.append((loc["x"], loc["y"], *point))
    if len(samples) < 3:
        return None
    scales, offsets = [], []
    for axis in (0, 1):
        ratios = [(b[axis + 2] - a[axis + 2]) / (b[axis] - a[axis])
                  for i, a in enumerate(samples) for b in samples[i+1:]
                  if abs(b[axis] - a[axis]) > 60]
        if not ratios:
            return None
        candidates = []
        for scale in ratios:
            offset = median(s[axis+2] - scale*s[axis] for s in samples)
            errors = [abs(s[axis+2]-scale*s[axis]-offset) for s in samples]
            candidates.append((sum(e < 0.5 for e in errors), -median(errors), scale, offset))
        _, _, scale, offset = max(candidates)
        scales.append(scale)
        offsets.append(offset)
    sx, sy = scales
    if not 0.1 < abs(sx) < 5 or not 0.1 < abs(sy) < 5:
        return None
    tx, ty = offsets
    inliers = [s for s in samples if abs(s[2]-sx*s[0]-tx) < 1 and abs(s[3]-sy*s[1]-ty) < 1]
    if len(inliers) < 3:
        return None
    return lambda x, y: (round(sx*x+tx, 1), round(sy*y+ty, 1))


def calibration(page, instances, comps, locate):
    direct = _direct_calibration(page, instances, comps, locate)
    if direct:
        return direct
    # Sparse pages may share an export transform. Accept a peer transform only
    # after checking it against actual copper endpoints on the current page.
    graph = _green_graph(page)
    for peer in page.parent:
        if peer.number == page.number or peer.rect != page.rect:
            continue
        candidate = _direct_calibration(peer, instances, comps, locate)
        if not candidate:
            continue
        points = []
        for inst in instances:
            for pin in inst.get("pins") or []:
                loc = pin.get("location") or {}
                if any(x.get("pdf_page") == page.number+1 and x.get("pageId") == loc.get("pageId")
                       for x in (comps.get(inst.get("ref")) or {}).get("location") or []):
                    points.append(candidate(loc["x"], loc["y"]))
        hits = [p for p in points if _nearest(graph, p, limit=.8) is not None]
        if len(hits) >= 3 and len(hits) >= .8*len(points):
            return candidate
    return None


def directed_copper(lines):
    """One net direction per copper edge; conflicting estimates get no arrow."""
    directions = {}
    for line in lines:
        for a, b in zip(line, line[1:]):
            directions.setdefault(frozenset((a, b)), set()).add((a, b))
    ambiguous = {edge for edge, values in directions.items() if len(values) > 1}
    result, seen = [], set()
    for line in lines:
        run = []
        for a, b in zip(line, line[1:]):
            edge = frozenset((a, b))
            if edge in ambiguous or edge in seen:
                if len(run) > 1:
                    result.append(run)
                run = []
            else:
                if not run:
                    run = [a]
                run.append(b)
                seen.add(edge)
        if len(run) > 1:
            result.append(run)
    return result, len(ambiguous)


def route_branches(page, raw, instances, comps, locate, direction_audit=None):
    transform = calibration(page, instances, comps, locate)
    if transform is None:
        return [], [], ["CAD/PDF coordinate calibration unavailable"]
    graph = _green_graph(page)
    pins, refs = {}, {}
    for inst in instances:
        ref = inst.get("ref")
        for pin in inst.get("pins") or []:
            loc = pin.get("location") or {}
            if not any(x.get("pdf_page") == page.number + 1 and x.get("pageId") == loc.get("pageId")
                       for x in (comps.get(ref) or {}).get("location") or []):
                continue
            token = pin.get("object")
            kg_net = next((p.get("net") for p in (comps.get(ref) or {}).get("pins") or []
                           if str(p.get("pin")) == str(token).partition(".")[2]), None)
            if not kg_net or kg_net != pin.get("net"):
                continue
            point = transform(loc["x"], loc["y"])
            snapped = _nearest(graph, point, limit=0.8)
            if snapped is not None:
                pins[token] = {"ref": ref, "net": kg_net, "point": snapped}
                refs.setdefault(ref, []).append(token)
    lines, bridges, missing = [], [], []
    selected = []
    declared = {(str(b.get("ref")), str(b.get("from")).split(".")[-1], str(b.get("to")).split(".")[-1])
                for b in raw.get("through_body") or [] if isinstance(b, dict)}
    paths = ([raw["path"]] if raw.get("path") else []) + list(raw.get("paths") or [])
    nets = {p["net"] for p in pins.values()}
    starts = {p[0] for p in paths if isinstance(p, list) and p and p[0] in nets}
    ends = {p[-1] for p in paths if isinstance(p, list) and p and p[-1] in nets}
    junctions = starts & ends
    source_pins, sink_pins = {}, {}
    for path in paths:
        if not isinstance(path, list):
            continue
        for a, b in zip(path, path[1:]):
            if a in pins and b in pins and pins[a]["ref"] == pins[b]["ref"]:
                declared.add((pins[a]["ref"], a.partition(".")[2], b.partition(".")[2]))
    for tokens in paths:
        if not isinstance(tokens, list):
            missing.append("invalid branch")
            continue
        expanded = []
        for i, token in enumerate(tokens):
            if token in pins:
                expanded.append(token)
            elif token in comps:
                pair = refs.get(token) or []
                if len(pair) != 2:
                    missing.append(f"{token}: explicit pin numbers required")
                    expanded.append(None)
                    continue
                prev = tokens[i-1] if i else None
                nxt = tokens[i+1] if i+1 < len(tokens) else None
                prev_nets = {pins[x]["net"] for x in refs.get(prev, [])} | ({pins[prev]["net"]} if prev in pins else {prev})
                next_nets = {pins[x]["net"] for x in refs.get(nxt, [])} | ({pins[nxt]["net"]} if nxt in pins else {nxt})
                if pins[pair[1]]["net"] in prev_nets or pins[pair[0]]["net"] in next_nets:
                    pair = list(reversed(pair))
                expanded.extend(pair)
            elif token not in {p["net"] for p in pins.values()}:
                missing.append(f"{token}: not located on page")
                expanded.append(None)
        for a, b in zip(expanded, expanded[1:]):
            if a is None or b is None:
                continue
            pa, pb = pins[a], pins[b]
            if pa["ref"] == pb["ref"]:
                pair = (pa["ref"], a.partition(".")[2], b.partition(".")[2])
                if len((comps.get(pa["ref"]) or {}).get("pins") or []) > 2 and pair not in declared:
                    missing.append(f"{a} → {b}: internal transition not declared")
                else:
                    via = [pa["point"], pb["point"]]
                    inst = next((i for i in instances if i.get("ref") == pa["ref"]), {})
                    loc = next((l for l in inst.get("locations") or [] if any(
                        x.get("pdf_page") == page.number+1 and x.get("pageId") == l.get("pageId")
                        for x in (comps.get(pa["ref"]) or {}).get("location") or [])), None)
                    if loc and len((comps.get(pa["ref"]) or {}).get("pins") or []) > 2:
                        cx, cy = transform(loc["x"], loc["y"])
                        x1, y1 = pa["point"]; x2, y2 = pb["point"]
                        if abs(x1-x2) < .5 and abs(cx-x1) > 2:
                            inside = x1 + .3*(cx-x1)
                            via = [(x1,y1), (inside,y1), (inside,y2), (x2,y2)]
                        elif abs(y1-y2) < .5 and abs(cy-y1) > 2:
                            inside = y1 + .3*(cy-y1)
                            via = [(x1,y1), (x1,inside), (x2,inside), (x2,y2)]
                    bridges.append({"via": via, "text": f"{a} → {b}"})
            elif pa["net"] == pb["net"]:
                path = _bfs(graph, pa["point"], pb["point"], max_nodes=250)
                if path:
                    lines.append(path)
                else:
                    missing.append(f"{a} → {b}: same net, PDF copper disconnected (logical continuation)")
            else:
                missing.append(f"{a} → {b}: net mismatch")
        selected.extend(t for t in expanded if t is not None and t not in selected)
        # Include the local source/return stub rather than stopping at the pin.
        for is_start, token, pin_token in ((True, tokens[0] if tokens else None, expanded[0] if expanded else None),
                                           (False, tokens[-1] if tokens else None, expanded[-1] if expanded else None)):
            if pin_token is None or token in comps or token in pins:
                continue
            pin = pins[pin_token]
            if token != pin["net"]:
                missing.append(f"{token}: endpoint net mismatch")
                continue
            if token in junctions:
                # A net joining two named paths is a junction, not a detour
                # through its printed label/port in both directions.
                group = sink_pins if is_start else source_pins
                group.setdefault(token, []).append(pin_token)
                continue
            component = _component(graph, pin["point"])
            if re.fullmatch(r"(?:[ADP]?GND|VSS|VCC\w*|\+?\d+(?:\.\d+)?V)", str(token)):
                pin_points = {p["point"] for p in pins.values()}
                endpoints = [p for p in component if len(graph[p]) == 1 and p not in pin_points]
            else:
                labels = [((w[0]+w[2])/2, (w[1]+w[3])/2)
                          for w in page.get_text("words") if w[4] == token]
                endpoints = [min(component, key=lambda p: min(abs(p[0]-x)+abs(p[1]-y) for x,y in labels))] if labels else []
            if endpoints:
                end = min(endpoints, key=lambda p: abs(p[0]-pin["point"][0])+abs(p[1]-pin["point"][1]))
                path = _bfs(graph, pin["point"], end, max_nodes=250)
                if path:
                    lines.append(list(reversed(path)) if is_start else path)
    for net in junctions:
        for source in dict.fromkeys(source_pins.get(net, [])):
            for sink in dict.fromkeys(sink_pins.get(net, [])):
                path = _bfs(graph, pins[source]["point"], pins[sink]["point"], max_nodes=250)
                if path:
                    lines.append(path)
    if direction_audit is not None:
        arrows, ambiguous = directed_copper(lines)
        direction_audit.update(arrow_polylines=arrows, ambiguous_edges=ambiguous)
    # Different named branches can start on one physical net. Join their pin
    # stubs along copper as well; otherwise the shared branch trunk is omitted.
    grouped = {}
    covered = {frozenset((a, b)) for line in lines for a, b in zip(line, line[1:])}
    for token in selected:
        pin = pins[token]
        group = grouped.setdefault(pin["net"], [])
        route = next((path for other in group
                      if (path := _bfs(graph, pins[other]["point"], pin["point"], max_nodes=250))), None)
        if route:
            pending = []
            for a, b in zip(route, route[1:]):
                edge = frozenset((a, b))
                if edge in covered:
                    if len(pending) > 1:
                        lines.append(pending)
                    pending = []
                else:
                    if not pending:
                        pending = [a]
                    pending.append(b)
                    covered.add(edge)
            if len(pending) > 1:
                lines.append(pending)
        group.append(token)
    # Keep shared trunks once, preserving the first branch's direction.
    unique, seen = [], set()
    for line in lines:
        run = []
        for a, b in zip(line, line[1:]):
            edge = frozenset((a, b))
            if edge in seen:
                if len(run) > 1:
                    unique.append(run)
                run = []
            else:
                if not run:
                    run = [a]
                run.append(b)
                seen.add(edge)
        if len(run) > 1:
            unique.append(run)
    bodies = {tuple(tuple(p) for p in b["via"]): b for b in bridges}
    return unique, list(bodies.values()), list(dict.fromkeys(missing))
