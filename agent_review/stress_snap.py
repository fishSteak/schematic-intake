# -*- coding: utf-8 -*-
"""把工况文案里的位号/网名吸到原理图绿网上，形成可出图的折点。

吸不上（绿网不通、路径过长、像电源轨一样铺满）就返回 None，调用方不得硬画。
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from weakref import WeakKeyDictionary

import fitz

_REF = re.compile(r"\b([A-Z]{1,4}\d{1,4})\b")
_NET = re.compile(r"\b((?:N_|VCC|POW_|DS_)[A-Z0-9_]+|GND|\+\d+V)\b")


def tokens_from_state(state: dict[str, Any], obj: str) -> list[str]:
    """按出现顺序抽出本工况提到的位号和网名。对象本身放在末尾作为被测点。"""
    blob = str(state.get("name_zh") or "")
    for line in state.get("formula_lines") or []:
        if isinstance(line, dict):
            blob += " " + str(line.get("expr") or "")
    found: list[str] = []
    for rx in (_NET, _REF):
        for m in rx.finditer(blob):
            tok = m.group(1)
            if tok not in found:
                found.append(tok)
    if obj and obj not in found:
        found.append(obj)
    return found[:8]


_GRAPH_CACHE = WeakKeyDictionary()


def _green_graph(page: fitz.Page) -> dict[tuple[float, float], set[tuple[float, float]]]:
    cached = _GRAPH_CACHE.get(page)
    if cached is not None:
        return cached
    adj: dict[tuple[float, float], set[tuple[float, float]]] = defaultdict(set)
    nodes: list[tuple[float, float]] = []

    def q(x: float, y: float) -> tuple[float, float]:
        return (round(x, 1), round(y, 1))

    for d in page.get_drawings():
        col = d.get("color") or ()
        if len(col) < 3 or abs(col[0]) > 0.05 or abs(col[1] - 0.53) > 0.12:
            continue
        for it in d.get("items") or []:
            if it[0] != "l":
                continue
            a = q(it[1].x, it[1].y)
            b = q(it[2].x, it[2].y)
            if a == b:
                continue
            adj[a].add(b)
            adj[b].add(a)
            nodes.append(a)
            nodes.append(b)
    # Split a segment at a wire endpoint touching its interior (T junction).
    # A crossing without an endpoint is deliberately not joined.
    endpoints = list(adj)
    for a in endpoints:
        for b in list(adj[a]):
            if b <= a:
                continue
            dx, dy = b[0]-a[0], b[1]-a[1]
            length2 = dx*dx+dy*dy
            middle = []
            for p in endpoints:
                if p in (a, b):
                    continue
                t = ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/length2
                if 0 < t < 1 and abs(p[0]-a[0]-t*dx) <= .15 and abs(p[1]-a[1]-t*dy) <= .15:
                    middle.append((t, p))
            if middle:
                adj[a].discard(b)
                adj[b].discard(a)
                chain = [a] + [p for _, p in sorted(middle)] + [b]
                for p, qpt in zip(chain, chain[1:]):
                    adj[p].add(qpt)
                    adj[qpt].add(p)
    # Only subpixel rounding gaps may be joined; close parallel wires stay apart.
    cell = 2.0
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for n in list(adj):
        buckets[(int(n[0] // cell), int(n[1] // cell))].append(n)
    for n in list(adj):
        cx, cy = int(n[0] // cell), int(n[1] // cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for m in buckets.get((cx + dx, cy + dy), ()):
                    if m <= n:
                        continue
                    if abs(n[0] - m[0]) <= 0.2 and abs(n[1] - m[1]) <= 0.2:
                        adj[n].add(m)
                        adj[m].add(n)
    _GRAPH_CACHE[page] = adj
    return adj


def _component(adj: dict, start: tuple[float, float]) -> set[tuple[float, float]]:
    seen = {start}
    q = [start]
    while q:
        cur = q.pop()
        for nb in adj.get(cur) or ():
            if nb not in seen:
                seen.add(nb)
                q.append(nb)
    return seen


def _nearest(
    adj: dict,
    pt: tuple[float, float],
    limit: float = 40.0,
    prefer: set[tuple[float, float]] | None = None,
) -> tuple[float, float] | None:
    """位号吸到附近绿线端点。已在同一段绿网上的点优先，避免吸到更近的旁路。"""
    best = None
    best_d = limit
    best_pref = None
    best_pref_d = limit
    for n in adj:
        d = abs(n[0] - pt[0]) + abs(n[1] - pt[1])
        if prefer and n in prefer and d < best_pref_d:
            best_pref_d = d
            best_pref = n
        if d < best_d:
            best_d = d
            best = n
    return best_pref if best_pref is not None else best


def _bfs(
    adj: dict[tuple[float, float], set[tuple[float, float]]],
    start: tuple[float, float],
    goal: tuple[float, float],
    max_nodes: int = 40,
) -> list[tuple[float, float]] | None:
    if start == goal:
        return [start]
    prev: dict[tuple[float, float], tuple[float, float] | None] = {start: None}
    q = [start]
    seen = 0
    while q:
        cur = q.pop(0)
        seen += 1
        if seen > 800:
            return None
        for nb in adj.get(cur) or ():
            if nb in prev:
                continue
            prev[nb] = cur
            if nb == goal:
                path = [goal]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])  # type: ignore[arg-type]
                path.reverse()
                if len(path) > max_nodes:
                    return None
                return path
            q.append(nb)
    return None


def _trace_once(path: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """来回走同一段绿线时只保留一次，从端点画到端点。"""
    adj: dict[tuple[float, float], list[tuple[float, float]]] = defaultdict(list)
    for a, b in zip(path, path[1:]):
        if a == b or b in adj[a]:
            continue
        adj[a].append(b)
        adj[b].append(a)
    if not adj:
        return path
    start = next((n for n, nbs in adj.items() if len(nbs) == 1), path[0])
    out = [start]
    prev = None
    cur = start
    seen = 0
    while seen < 64:
        seen += 1
        nxts = [n for n in adj[cur] if n != prev]
        if not nxts:
            break
        nxt = nxts[0]
        out.append(nxt)
        prev, cur = cur, nxt
    return out


def _simplify(path: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(path) < 3:
        return path
    out = [path[0]]
    for i in range(1, len(path) - 1):
        a, b, c = out[-1], path[i], path[i + 1]
        if (a[0] == b[0] == c[0]) or (a[1] == b[1] == c[1]):
            continue
        out.append(b)
    out.append(path[-1])
    return out


def snap_page_path(
    page: fitz.Page,
    anchors: list[str],
) -> tuple[list[tuple[float, float]], list[tuple[str, tuple[float, float]]]] | None:
    """同一页上按锚点顺序沿绿网走。失败返回 None。"""
    hits: list[tuple[str, tuple[float, float]]] = []
    for name in anchors:
        rects = page.search_for(name)
        if not rects:
            continue
        r = rects[0]
        hits.append((name, ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)))
    if len(hits) < 2:
        return None
    adj = _green_graph(page)
    if len(adj) > 2500:
        return None
    snapped: list[tuple[float, float]] = []
    prefer: set[tuple[float, float]] | None = None
    for _name, pt in hits:
        node = _nearest(adj, pt, prefer=prefer)
        if node is None:
            return None
        snapped.append(node)
        prefer = _component(adj, node)
    poly: list[tuple[float, float]] = []
    for i in range(len(snapped) - 1):
        a, b = snapped[i], snapped[i + 1]
        seg = _bfs(adj, a, b)
        if not seg:
            return None
        if poly and seg[0] == poly[-1]:
            poly.extend(seg[1:])
        else:
            poly.extend(seg)
    poly = _simplify(_trace_once(poly))
    if len(poly) < 2:
        return None
    return poly, hits
