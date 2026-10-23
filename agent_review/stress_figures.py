# -*- coding: utf-8 -*-
"""深审工况 → 工况通路图。

一条 states[] 一张图。工况带 figure（页、通路名字、圆点文案）时，只按这些名字贴绿线。
通过 CAD 脚位和网表核对后才沿绿网画线；缺几何或缺通路时只圈注。
一个名字都定位不到时才记「图未生成」，原因写具体缺什么。

python -m agent_review.stress_figures --board boards/0918全量
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import fitz

from agent_review.aggregate import load_judge_cards, safe_object_name
from agent_review.stress_path_render import render_spec
from agent_review.presentation import business_text, primary_state_index
from agent_review.stress_snap import (
    _NET,
    _REF,
    _bfs,
    _component,
    _green_graph,
    _nearest,
    snap_page_path,
    tokens_from_state,
)
from kernel.util import out_dir, read_json, resolve_board_path, write_json

# 会自列工况、需要出图的检查项
FIGURE_RULES = frozenset({
    "P11-A01",
    "P11-A02",
    "P11-A03",
    "P11-A06",
    "P11-A07",
    "P12-A01",
    "P12-A02",
    "P21-A01",
    "P12-A04",
    "P12-A05",
    "P12-A06",
    "P12-A07",
})


def _slug(state: dict[str, Any], index: int) -> str:
    cid = str(state.get("case_id") or "").strip()
    if cid and re.fullmatch(r"[A-Za-z0-9_\-]+", cid):
        return cid
    return str(index)


def _fingerprint(state: dict[str, Any]) -> str:
    blob = json.dumps(
        {
            "name": state.get("name_zh"),
            "v": state.get("V", state.get("V_across")),
            "p": state.get("P"),
            "结论": state.get("结论") or state.get("conclusion"),
            "formula_lines": state.get("formula_lines"),
            "conditions": state.get("conditions"),
            "role": state.get("role"),
            "figure": state.get("figure") if state.get("figure") else None,
            "rev": 7 if state.get("figure") else 5,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _callout(state: dict[str, Any]) -> str:
    conc = str(state.get("结论") or state.get("conclusion") or "").strip()
    v = state.get("V", state.get("V_across"))
    if v is not None and conc:
        return f"{v} V · {conc}"
    return conc or str(state.get("name_zh") or "本工况")


def _result_callout(state: dict[str, Any]) -> str:
    """底栏写出这一态的端压或功耗，不单留「满足」。"""
    conc = str(state.get("结论") or state.get("conclusion") or "").strip()
    v = state.get("V", state.get("V_across"))
    parts: list[str] = []
    if isinstance(v, (int, float)):
        parts.append(f"{v:g} V")
    else:
        lines = state.get("formula_lines") or []
        if lines and isinstance(lines[-1], dict):
            expr = str(lines[-1].get("expr") or "").split("；")[0].strip()
            if expr:
                parts.append(expr[:40])
    if conc:
        parts.append(conc)
    return " · ".join(parts) or _callout(state)


def _has_name(text: str, name: str) -> bool:
    """整词匹配，避免 R7 命中 V_R7、Q1 命中 N_Q1_BASE。"""
    if not name:
        return False
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text) is not None


def _line_about(state: dict[str, Any], name: str) -> str:
    """公式里已经写到这个名字的那一句。"""
    for line in state.get("formula_lines") or []:
        if not isinstance(line, dict):
            continue
        expr = str(line.get("expr") or "").strip()
        src = str(line.get("src") or "").strip()
        if _has_name(expr, name):
            for part in re.split(r"[；;]", expr):
                if _has_name(part, name):
                    return part.strip()[:40]
            return expr[:40]
        if _has_name(src, name):
            return src[:40]
    return ""


def _is_device(name: str, comps: dict[str, Any]) -> bool:
    return bool(name) and (name in comps or bool(_REF.fullmatch(name)))


def _symbol_center(page: fitz.Page, text: fitz.Rect) -> tuple[float, float]:
    """位号文字附近的红色符号图形，取外框中心。没有符号时退回文字中心。"""
    cx = (text.x0 + text.x1) / 2
    cy = (text.y0 + text.y1) / 2
    boxes: list[fitz.Rect] = []
    for drawing in page.get_drawings():
        col = drawing.get("color") or ()
        if len(col) < 3 or col[0] < 0.4 or col[1] > 0.2:
            continue
        rect = drawing.get("rect")
        if rect is None:
            continue
        if max(rect.width, rect.height) > 36 and min(rect.width, rect.height) < 1.5:
            continue
        mx = (rect.x0 + rect.x1) / 2
        my = (rect.y0 + rect.y1) / 2
        if abs(mx - cx) + abs(my - cy) > 48:
            continue
        boxes.append(rect)
    if not boxes:
        return (cx, cy)
    return (
        (min(r.x0 for r in boxes) + max(r.x1 for r in boxes)) / 2,
        (min(r.y0 for r in boxes) + max(r.y1 for r in boxes)) / 2,
    )


def _marker_body(state: dict[str, Any], name: str, obj: str, comps: dict[str, Any]) -> str:
    line = _line_about(state, name)
    if name == obj:
        return line or str(state.get("name_zh") or "被测件")[:40]
    if _is_device(name, comps):
        return line or "本工况写到的器件"
    if line:
        return line
    return "只有网名，电位未在本工况写明，待人工核"


def _where(comps: dict[str, Any], name: str) -> str:
    comp = comps.get(name) if isinstance(comps, dict) else None
    if isinstance(comp, dict):
        for loc in comp.get("location") or []:
            if isinstance(loc, dict) and loc.get("pdf_page"):
                return f"{name}在第{loc.get('pdf_page')}页"
    return f"{name}不在本页"


def _spec_from_snap(
    board_pdf: Path,
    kg: dict[str, Any],
    card: dict[str, Any],
    *,
    rule_id: str,
    obj: str,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    tokens = tokens_from_state(state, obj)
    if len(tokens) < 2:
        return None
    doc = fitz.open(board_pdf)
    comps = (kg.get("components") or {}) if isinstance(kg, dict) else {}
    pages: dict[int, list[str]] = {}
    for tok in tokens:
        comp = comps.get(tok) if isinstance(comps, dict) else None
        page_no = None
        if isinstance(comp, dict):
            locs = comp.get("location") or []
            if locs and isinstance(locs, list) and isinstance(locs[0], dict):
                page_no = locs[0].get("pdf_page")
        if page_no is None:
            for i, page in enumerate(doc):
                if page.search_for(tok):
                    page_no = i + 1
                    break
        if page_no is None:
            continue
        pages.setdefault(int(page_no), [])
        if tok not in pages[int(page_no)]:
            pages[int(page_no)].append(tok)
    obj_page = None
    obj_comp = comps.get(obj) if isinstance(comps, dict) else None
    if isinstance(obj_comp, dict):
        for loc in obj_comp.get("location") or []:
            if isinstance(loc, dict) and loc.get("pdf_page"):
                obj_page = int(loc["pdf_page"])
                break
    page_specs = []
    step = 1
    ordered_pages = sorted(pages)
    for pi, pdf_page in enumerate(ordered_pages):
        names = pages[pdf_page]
        if len(names) < 2 and len(ordered_pages) == 1:
            return None
        if len(names) < 2:
            continue
        page = doc[pdf_page - 1]
        snapped = snap_page_path(page, names)
        if not snapped:
            if obj_page is not None and pdf_page != obj_page:
                continue
            return None
        poly, hits = snapped
        markers = []
        seen: list[str] = []

        def _add_marker(name: str, text: fitz.Rect | None, at: tuple[float, float] | None) -> None:
            if name in seen or len(markers) >= 8:
                return
            if text is not None and _is_device(name, comps):
                point = _symbol_center(page, text)
            elif at is not None:
                point = at
            elif text is not None:
                point = ((text.x0 + text.x1) / 2, (text.y0 + text.y1) / 2)
            else:
                return
            seen.append(name)
            markers.append(
                {
                    "at": [point[0], point[1]],
                    "title": name,
                    "body": _marker_body(state, name, obj, comps),
                    "leader": [point[0] + 36, point[1] - 28],
                }
            )

        for name, at in hits:
            rects = _word_hits(page, name)
            _add_marker(name, rects[0] if rects else None, at)
        for name in _annotate_tokens(card, state, kg, obj):
            if name in seen:
                continue
            rects = _word_hits(page, name)
            if rects:
                _add_marker(name, rects[0], None)
        absent = [n for n in _annotate_tokens(card, state, kg, obj) if n not in seen]
        source_note = ""
        if absent:
            source_note = "来源未画入本页：" + "、".join(_where(comps, n) for n in absent[:4])
            for marker in markers:
                if marker["title"] == obj:
                    marker["body"] = str(marker["body"])[:28] + "\n" + source_note[:42]
        page_name = _page_name(comps, obj, pdf_page)
        if not page_name:
            comp0 = comps.get(names[0]) if isinstance(comps, dict) else None
            if isinstance(comp0, dict):
                locs = comp0.get("location") or []
                if locs and isinstance(locs[0], dict) and locs[0].get("pdf_page") == pdf_page:
                    page_name = str(locs[0].get("pageName") or "")
        page_specs.append(
            {
                "pdf_page": pdf_page,
                "page_name": page_name,
                "step": step,
                "label": " → ".join(names),
                "takeaway": (str(state.get("name_zh") or "")[:36] + (" · " + source_note if source_note else ""))[:80],
                "pts": [list(p) for p in poly],
                "full_page": True,
                "anchors": names,
                "markers": markers,
                "continue_to": ordered_pages[pi + 1] if pi + 1 < len(ordered_pages) else None,
            }
        )
        step += 1
    if not page_specs:
        return None
    verdict = "不通过" if "不满足" in str(state.get("结论") or "") else "通过"
    if "满足" in str(state.get("结论") or "") and "不满足" not in str(state.get("结论") or ""):
        verdict = "通过"
    return {
        "object": obj,
        "rule_id": rule_id,
        "title": f"{obj} · {str(state.get('name_zh') or '工况')[:28]}",
        "verdict": verdict,
        "callout": _result_callout(state),
        "pages": page_specs,
    }


_RAIL = re.compile(r"^(GND|\+\d+V|VCC[A-Z0-9_]*)$")
_ALIAS = re.compile(r"\b(V_[A-Z][A-Z0-9_]*)\b")
_NEAR_RAIL = 120.0


def _add_tok(found: list[str], tok: str) -> None:
    tok = tok.strip()
    if not tok or tok in found or len(found) >= 8:
        return
    found.append(tok)


def _annotate_tokens(card: dict[str, Any], state: dict[str, Any], kg: dict[str, Any], obj: str) -> list[str]:
    """圈注用的名字：被测件、它的两端网，再加本工况文案里点到的位号和网名。"""
    found: list[str] = []
    _add_tok(found, obj)
    comps = (kg.get("components") or {}) if isinstance(kg, dict) else {}
    comp = comps.get(obj) if isinstance(comps, dict) else None
    if isinstance(comp, dict):
        for pin in comp.get("pins") or []:
            if isinstance(pin, dict):
                _add_tok(found, str(pin.get("net") or ""))
    blob = str(state.get("name_zh") or "")
    for line in state.get("formula_lines") or []:
        if isinstance(line, dict):
            blob += " " + str(line.get("expr") or "") + " " + str(line.get("src") or "")
    blob += " " + str(card.get("location_one_liner") or "")
    for m in _NET.finditer(blob):
        _add_tok(found, m.group(1))
    for m in _REF.finditer(blob):
        name = m.group(1)
        if name == obj or (isinstance(comps, dict) and name in comps):
            _add_tok(found, name)
    return found


def _aliases(state: dict[str, Any]) -> list[str]:
    blob = ""
    for line in state.get("formula_lines") or []:
        if isinstance(line, dict):
            blob += " " + str(line.get("expr") or "")
    out: list[str] = []
    for m in _ALIAS.finditer(blob):
        name = m.group(1)
        if name not in out:
            out.append(name)
    return out[:3]


def _word_hits(page: fitz.Page, tok: str) -> list[fitz.Rect]:
    def same(word: str) -> bool:
        return word == tok or word.startswith(tok + ".") or word.startswith(tok + "_")

    hits = [fitz.Rect(w[:4]) for w in page.get_text("words") if same(w[4])]
    if hits:
        return hits
    if "_" in tok or len(tok) >= 5:
        return list(page.search_for(tok))
    return []


def _page_name(comps: dict[str, Any], tok: str, pdf_page: int) -> str:
    comp = comps.get(tok) if isinstance(comps, dict) else None
    if not isinstance(comp, dict):
        return ""
    for loc in comp.get("location") or []:
        if isinstance(loc, dict) and loc.get("pdf_page") == pdf_page:
            return str(loc.get("pageName") or "")
    return ""


def _review_note(missing: list[str], aliases: list[str]) -> str:
    parts = ["未沿铜线连线，通路请人工对照原理图"]
    if aliases:
        parts.append("推算名 " + "、".join(aliases) + " 不是图上网名")
    if missing:
        parts.append("图上未定位到 " + "、".join(missing[:4]))
    return "。".join(parts)


def _takeaway(missing: list[str], aliases: list[str]) -> str:
    text = "未沿铜线连线，待人工核"
    if aliases:
        text += " · 推算名 " + "、".join(aliases)
    if missing:
        text += " · 未定位 " + "、".join(missing[:3])
    return text[:48]


def _comp_pins(comp: dict[str, Any] | None) -> list[tuple[str, str]]:
    if not isinstance(comp, dict):
        return []
    out: list[tuple[str, str]] = []
    for pin in comp.get("pins") or []:
        if not isinstance(pin, dict):
            continue
        num = str(pin.get("pin") or "").strip()
        net = str(pin.get("net") or "").strip()
        if net:
            out.append((num, net))
    return out


def _mentioned_refs(card: dict[str, Any], state: dict[str, Any], kg: dict[str, Any], obj: str) -> list[str]:
    comps = kg.get("components") if isinstance(kg.get("components"), dict) else {}
    return [name for name in _annotate_tokens(card, state, kg, obj) if name in comps]


def _netlist_nodes(
    kg: dict[str, Any],
    card: dict[str, Any],
    state: dict[str, Any],
    obj: str,
) -> list[dict[str, str]]:
    """被测件两端按网表延伸。只在唯一的两脚器件上继续走，多脚器件标在同网上。"""
    comps = kg.get("components") if isinstance(kg.get("components"), dict) else {}
    dut = comps.get(obj) if isinstance(comps, dict) else None
    pins = _comp_pins(dut if isinstance(dut, dict) else None)
    if len(pins) != 2:
        return []
    mentioned = _mentioned_refs(card, state, kg, obj)
    series: list[str] = []

    def hop(net: str, seen: set[str]) -> list[tuple[str, str]]:
        """从非电源网沿唯一两脚器件走到电源网或分叉。"""
        hops: list[tuple[str, str]] = []
        cur = net
        guard = 0
        while cur and not _RAIL.match(cur) and guard < 6:
            guard += 1
            cands: list[tuple[str, str]] = []
            named = []
            for ref, comp in comps.items():
                if ref in seen or ref == obj or not isinstance(comp, dict):
                    continue
                pair = _comp_pins(comp)
                if len(pair) != 2:
                    continue
                nets = [n for _, n in pair]
                if cur not in nets:
                    continue
                other = nets[1] if nets[0] == cur else nets[0]
                cands.append((ref, other))
                if ref in mentioned:
                    named.append((ref, other))
            pick = named if len(named) == 1 else (cands if len(cands) == 1 else [])
            if len(pick) != 1:
                break
            ref, other = pick[0]
            hops.append((ref, other))
            seen.add(ref)
            series.append(ref)
            cur = other
        return hops

    left = hop(pins[0][1], set())
    right = hop(pins[1][1], set(series))
    # 向外走得到 (器件, 远端网)。翻到左侧时，远端网在器件外侧。
    nodes: list[dict[str, str]] = []
    for ref, far in reversed(left):
        nodes.append({"kind": "net", "text": far})
        nodes.append({"kind": "device", "text": ref})
    nodes.append({"kind": "net", "text": pins[0][1]})
    nodes.append({"kind": "device", "text": obj, "dut": "1"})
    nodes.append({"kind": "net", "text": pins[1][1]})
    for ref, far in right:
        nodes.append({"kind": "device", "text": ref})
        nodes.append({"kind": "net", "text": far})
    return _orient_flow(nodes)


def _orient_flow(nodes: list[dict[str, str]]) -> list[dict[str, str]]:
    if len(nodes) < 3:
        return []
    cleaned: list[dict[str, str]] = []
    for n in nodes:
        if cleaned and cleaned[-1].get("text") == n.get("text") and cleaned[-1].get("kind") == n.get("kind"):
            continue
        cleaned.append(n)
    def supply(text: str) -> bool:
        return bool(re.match(r"^(\+\d|VCC)", text or ""))

    def ground(text: str) -> bool:
        return text in {"GND", "DGND", "AGND", "PGND", "VSS"}

    if supply(cleaned[-1].get("text") or "") and ground(cleaned[0].get("text") or ""):
        cleaned = list(reversed(cleaned))
        for n in cleaned:
            if n.get("dut"):
                n["dut"] = "1"
    return cleaned


def _attach_same_net_notes(
    nodes: list[dict[str, str]],
    kg: dict[str, Any],
    mentioned: list[str],
    obj: str,
) -> None:
    comps = kg.get("components") if isinstance(kg.get("components"), dict) else {}
    on_chain = {n.get("text") for n in nodes if n.get("kind") == "device"}
    for n in nodes:
        if n.get("kind") != "net" or _RAIL.match(str(n.get("text") or "")):
            continue
        notes: list[str] = []
        for ref in mentioned:
            if ref == obj or ref in on_chain:
                continue
            comp = comps.get(ref) if isinstance(comps, dict) else None
            for num, net in _comp_pins(comp if isinstance(comp, dict) else None):
                if net == n.get("text"):
                    notes.append(f"{ref} 脚{num}" if num else ref)
                    break
            if len(notes) >= 3:
                break
        if notes:
            n["note"] = "同网 " + "、".join(notes)


def _locate_token(
    page: fitz.Page,
    name: str,
    *,
    near: tuple[float, float] | None,
    comps: dict[str, Any],
) -> tuple[float, float] | None:
    rects = _word_hits(page, name)
    if not rects:
        return None
    chosen = rects[0]
    if near is not None and len(rects) > 1:
        chosen = min(
            rects,
            key=lambda r: abs((r.x0 + r.x1) / 2 - near[0]) + abs((r.y0 + r.y1) / 2 - near[1]),
        )
    if _is_device(name, comps):
        return _body_center(page, chosen)
    return ((chosen.x0 + chosen.x1) / 2, (chosen.y0 + chosen.y1) / 2)


def _body_center(page: fitz.Page, text: fitz.Rect) -> tuple[float, float]:
    """圈注落在符号本体上，不落在引线。"""
    cx = (text.x0 + text.x1) / 2
    cy = (text.y0 + text.y1) / 2
    bodies: list[fitz.Rect] = []
    for drawing in page.get_drawings():
        col = drawing.get("color") or ()
        if len(col) < 3 or col[0] < 0.4 or col[1] > 0.25:
            continue
        rect = drawing.get("rect")
        if rect is None or rect.width < 3 or rect.height < 3 or max(rect.width, rect.height) > 40:
            continue
        mx = (rect.x0 + rect.x1) / 2
        my = (rect.y0 + rect.y1) / 2
        if abs(mx - cx) + abs(my - cy) > 40:
            continue
        bodies.append(rect)
    if not bodies:
        return (cx, cy)
    rect = min(
        bodies,
        key=lambda r: abs((r.x0 + r.x1) / 2 - cx) + abs((r.y0 + r.y1) / 2 - cy),
    )
    return ((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)


def _on_segment(pt: tuple[float, float], a: tuple[float, float], b: tuple[float, float], limit: float = 28.0) -> bool:
    """点是否贴着 a—b 这条线段。贴不上就不写进「→」文案。"""
    ax, ay = a
    bx, by = b
    px, py = pt
    abx, aby = bx - ax, by - ay
    ab2 = abx * abx + aby * aby
    if ab2 < 1:
        return abs(px - ax) + abs(py - ay) <= limit
    t = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / ab2))
    qx, qy = ax + abx * t, ay + aby * t
    return abs(px - qx) + abs(py - qy) <= limit


def _span_voltage(state: dict[str, Any]) -> str:
    """被测件两端压差，图上只标这个数，不重复标称阻值。"""
    for row in state.get("formula_lines") or []:
        if not isinstance(row, dict):
            continue
        expr = str(row.get("expr") or "")
        if "之差" not in expr and "端压" not in expr:
            continue
        nums = re.findall(r"=\s*(\d+(?:\.\d+)?)\s*V\b", expr)
        if nums:
            return f"{nums[-1]} V"
    return ""


def _dut_caption(state: dict[str, Any], obj: str) -> str:
    """图上只留结论。计算链放查看页侧栏，避免挡住电路。"""
    del obj
    conc = str(state.get("结论") or state.get("conclusion") or "").strip()
    return conc[:80] if conc else ""


def _calc_chain(state: dict[str, Any]) -> list[dict[str, str]]:
    """图下计算链：一行「算式 | 出处」，供复核展开，不画进原理图。"""
    rows: list[dict[str, str]] = []
    for row in state.get("formula_lines") or []:
        if not isinstance(row, dict):
            continue
        expr = business_text(str(row.get("expr") or "").strip())
        if not expr:
            continue
        src = str(row.get("src") or "").strip()
        rows.append({"expr": expr[:120], "src": src[:160]})
    return rows


def _rail_endpoint(
    page: fitz.Page,
    name: str,
    *,
    near: tuple[float, float],
    comps: dict[str, Any],
) -> tuple[float, float]:
    """电源取器件上方这截绿线的外端，地取下方这截。不跨过器件本体去找另一侧。"""
    del comps
    adj = _green_graph(page)
    ground = name in {"GND", "DGND", "AGND", "PGND", "VSS"}
    column = [n for n in adj if abs(n[0] - near[0]) <= 8]
    side = [n for n in column if n[1] > near[1] + 2] if ground else [n for n in column if n[1] < near[1] - 2]
    if not side:
        snapped = _nearest(adj, near, limit=36)
        return snapped or near
    lead = min(side, key=lambda n: abs(n[1] - near[1]))
    chunk = [
        n
        for n in _component(adj, lead)
        if abs(n[0] - near[0]) <= 8 and ((n[1] > near[1]) if ground else (n[1] < near[1]))
    ]
    if not chunk:
        return lead
    return max(chunk, key=lambda n: n[1]) if ground else min(chunk, key=lambda n: n[1])


def _label_point(
    page: fitz.Page,
    name: str,
    near: tuple[float, float] | None,
) -> tuple[float, float] | None:
    rects = _word_hits(page, name)
    if not rects:
        return None
    chosen = rects[0]
    if near is not None and len(rects) > 1:
        chosen = min(
            rects,
            key=lambda r: abs((r.x0 + r.x1) / 2 - near[0]) + abs((r.y0 + r.y1) / 2 - near[1]),
        )
    return ((chosen.x0 + chosen.x1) / 2, (chosen.y0 + chosen.y1) / 2)


def _project_segment(
    pt: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> tuple[tuple[float, float], float]:
    ax, ay = a
    bx, by = b
    abx, aby = bx - ax, by - ay
    ab2 = abx * abx + aby * aby
    if ab2 < 1:
        return a, abs(pt[0] - ax) + abs(pt[1] - ay)
    t = max(0.0, min(1.0, ((pt[0] - ax) * abx + (pt[1] - ay) * aby) / ab2))
    q = (ax + abx * t, ay + aby * t)
    return q, abs(pt[0] - q[0]) + abs(pt[1] - q[1])


def _trace_net(
    page: fitz.Page,
    name: str,
    device_pt: tuple[float, float],
    comps: dict[str, Any],
) -> list[tuple[float, float]]:
    """网名先落到旁边的绿线段上，再沿绿线收到器件引脚。不从字形斜插进符号。"""
    label = _label_point(page, name, device_pt)
    adj = _green_graph(page)
    if label is None:
        return [_rail_endpoint(page, name, near=device_pt, comps=comps)]
    edges: list[tuple[float, tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    seen: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    for a, nbs in adj.items():
        for b in nbs:
            key = (a, b) if a <= b else (b, a)
            if key in seen:
                continue
            seen.add(key)
            proj, dist = _project_segment(label, a, b)
            edges.append((dist, proj, a, b))
    edges.sort(key=lambda row: row[0])
    for dist, proj, a, b in edges:
        if dist > 36:
            break
        comp = _component(adj, a)
        lead = None
        lead_d = 32.0
        for node in comp:
            d = abs(node[0] - device_pt[0]) + abs(node[1] - device_pt[1])
            if d < lead_d:
                lead_d = d
                lead = node
        if lead is None:
            continue
        via_a = _bfs(adj, a, lead, max_nodes=80) or []
        via_b = _bfs(adj, b, lead, max_nodes=80) or []
        if via_a and (not via_b or len(via_a) <= len(via_b)):
            path = via_a
        else:
            path = via_b
        if not path or len(path) > 16:
            continue
        if abs(path[0][0] - proj[0]) > 1 or abs(path[0][1] - proj[1]) > 1:
            path = [proj, *path]
        # 只收器件旁边这一截。整页地网、电源干线不算这条通路。
        far = max(abs(p[0] - device_pt[0]) + abs(p[1] - device_pt[1]) for p in path)
        if far > 100:
            continue
        return path
    if _RAIL.match(name):
        return [_rail_endpoint(page, name, near=device_pt, comps=comps)]
    snapped = _nearest(adj, label, limit=36)
    return [snapped or label]


def _segment_pts(
    page: fitz.Page,
    a: tuple[float, float],
    b: tuple[float, float],
) -> list[tuple[float, float]]:
    """沿绿线走。绿线在器件本体处断开时，只在同一列或同一行上把缺口补直，不拐出铜线。"""
    adj = _green_graph(page)
    na = _nearest(adj, a, limit=24)
    nb = _nearest(adj, b, limit=24)
    if na is not None and nb is not None:
        seg = _bfs(adj, na, nb, max_nodes=120)
        if seg and len(seg) >= 2:
            return seg
    return []


def _spec_from_verified_figure(board_pdf, kg, card, *, rule_id, obj, state):
    from agent_review.schematic_routes import route_branches, calibration
    fig = _agent_figure(state)
    if not fig:
        return None
    jlc_path = board_pdf.parent.parent / "jlc_project.json"
    instances = (read_json(jlc_path).get("instances") or []) if jlc_path.is_file() else []
    comps = kg.get("components") or {}
    pages = []
    with fitz.open(board_pdf) as doc:
        for raw in fig.get("pages") or [fig]:
            number = raw.get("pdf_page")
            if not isinstance(number, int) or not 1 <= number <= len(doc):
                continue
            page = doc[number-1]
            direction_audit = {}
            lines, bridges, missing = route_branches(page, raw, instances, comps, _locate_token, direction_audit)
            if direction_audit.get('ambiguous_edges') and fig.get('kind')!='cross_case_relations':
                missing.append('部分线路的电流方向声明冲突；冲突箭头未绘制，需核对本工况。')
            if raw.get('unknown_direction_paths'):
                # Do not turn traversal order into current direction for an unresolved flow.
                direction_audit['arrow_polylines'] = []
            transform = calibration(page, instances, comps, _locate_token)
            markers = []
            for mark in (raw.get("markers") or [])[:None if card.get('review_v1') else 8]:
                token = str(mark.get("on") or "")
                ref, _, pin_number = token.partition(".")
                inst = next((x for x in instances if x.get("ref") == ref), {})
                loc = next((p.get("location") for p in inst.get("pins") or []
                            if p.get("object") == token), None) if pin_number else None
                if not loc:
                    loc = next((l for l in inst.get("locations") or [] if any(
                        k.get("pageId") == l.get("pageId") and k.get("pdf_page") == number
                        for k in (comps.get(ref) or {}).get("location") or [])), None)
                at = transform(loc["x"], loc["y"]) if loc and transform else None
                if at is None and not pin_number:
                    # A named net has a PDF label rather than a CAD component
                    # instance. Its label is a valid annotation anchor.
                    at = _locate_token(page, token, near=None, comps=comps)
                if at is not None:
                    markers.append({"at": at,
                                    "kind": mark.get("kind") or "",
                                    "title": business_text(str(mark.get("title") or token))[:60],
                                    "body": business_text(str(mark.get("body") or ""))[:180]})
            # Equations belong to this state, alongside its circuit. Never infer
            # current or voltage from connectivity or another state's numbers.
            dut = next((x for x in instances if x.get("ref") == obj), {})
            dut_loc = next((l for l in dut.get("locations") or [] if any(
                k.get("pageId") == l.get("pageId") and k.get("pdf_page") == number
                for k in (comps.get(obj) or {}).get("location") or [])), None)
            chain = _calc_chain(state)
            if dut_loc and transform and chain:
                object_rows = [r for r in chain if re.search(
                    rf"\b[IPVU]\s*(?:\(\s*{re.escape(obj)}(?:[.)]|\s)|_{re.escape(obj)}\b)", r["expr"])]
                selected = chain[:2] + [r for r in object_rows if r not in chain[:2]]
                if not object_rows:
                    selected = chain[:6]
                power = state.get("P")
                relation={'upper_bound':'功耗上界 ≤','lower_bound':'功耗下界 ≥'}.get(state.get('stress_kind'),'功耗 ≈')
                calc_title = f"{obj} 计算过程"
                if type(power) in (int, float):
                    # Decimal conversion preserves the card's bound; rounding
                    # an upper bound down (or lower bound up) changes its claim.
                    milliwatts = format(Decimal(str(power)) * 1000, "f")
                    if "." in milliwatts:
                        milliwatts = milliwatts.rstrip("0").rstrip(".")
                    calc_title = f"{obj} {relation} {milliwatts} mW"
                markers.append({"at": transform(dut_loc["x"], dut_loc["y"]),
                                "title": calc_title,
                                "body": "\n".join(r["expr"] for r in selected[:6]),
                                "kind": "calculation"})
            points = [p for line in lines for p in line] + [p for b in bridges for p in b["via"]]
            # Show the complete traversed component, including all connector
            # pins, without adding any unselected current paths.
            routed_refs = {str(t).partition('.')[0] for path in [raw.get('path') or [],*(raw.get('paths') or [])] for t in path}
            for ref in routed_refs & comps.keys():
                for rect in _word_hits(page,ref):
                    points.extend([(rect.x0,rect.y0),(rect.x1,rect.y1)])
                if transform:
                    page_ids={l.get('pageId') for l in comps[ref].get('location',[]) if l.get('pdf_page')==number}
                    inst=next((i for i in instances if i.get('ref')==ref),{})
                    for pin in inst.get('pins',[]):
                        loc=pin.get('location') or {}
                        if loc.get('pageId') in page_ids and loc.get('x') is not None and loc.get('y') is not None:
                            points.append(transform(loc['x'],loc['y']))
            anchor_names = []
            if not points:
                for path in ([raw.get("path") or []] + list(raw.get("paths") or [])):
                    for token in path:
                        name = _geom_name(str(token), comps)
                        if name not in anchor_names and _word_hits(page, name):
                            anchor_names.append(name)
                            for rect in _word_hits(page, name):
                                points.extend([(rect.x0, rect.y0), (rect.x1, rect.y1)])
                if not points:
                    points = [(25, 65), (page.rect.width-25, page.rect.height-25)]
            crop = [max(0, min(p[0] for p in points)-55),
                    max(0, min(p[1] for p in points)-45),
                    min(page.rect.width, max(p[0] for p in points)+25),
                    min(page.rect.height, max(p[1] for p in points)+18)]
            note = "；".join(missing)
            pages.append({"pdf_page": number, "step": len(pages)+1,
                          "page_name": _page_name(comps, obj, number),
                          "label": str(raw.get('label') or state.get('name_zh') or '评审主工况'),
                          "phase": str(raw.get('label') or ''),
                          "_model_page_summary": '\n'.join([str(raw.get('route_summary') or ''),*(raw.get('_unanchored_notes') or [])]).strip(),
                          "takeaway": str(state.get('_route_summary') or state.get('route_summary') or state.get('name_zh') or ''),
                          "full_page": True,
                          "polylines": lines, "body_bridges": bridges,
                          "arrow_polylines": direction_audit.get("arrow_polylines", []),
                          "ambiguous_direction_edges": direction_audit.get("ambiguous_edges", 0),
                          "anchors": anchor_names, "mode": "path" if lines else "anchors",
                          "show_arrows": state.get("status") != "unmapped",
                          "markers": markers, "crop_box": crop,
                          "routing_complete": not missing, "routing_issues": missing})
    if not pages:
        return None
    decision = card.get("decision_basis") or {}
    callout = _dut_caption(state, obj)
    if card.get('review_v1'):
        from agent_review.review_contract import detail_view
        view = detail_view(card['review_v1'])
        callout = view['headline']
    if card.get("verdict_zh") == "否" and decision.get("kind") == "normal_design_model":
        callout = (f"最大功耗不满足限额：已识别适用工况核算 {decision['value_w'] * 1000:.2f} mW，"
                   f"高于限额 {decision['limit_w'] * 1000:.2f} mW；主图更重候选的成立条件另列")
    return {"object": obj, "rule_id": rule_id, "title": f"{obj} · {state.get('name_zh') or state.get('name') or state.get('label') or '工况'}",
            "verdict": card.get("verdict_zh") or "待确认", "callout": callout,
            "pages": pages, "banner": (("条件极值候选（前提待确认）" if state.get('status')=='conditional' else "已确认极值") if card.get('review_v1') else "条件最坏通路（前提待确认）" if state.get("role") == "worst_candidate"
                else "正常设计范围最坏通路" if state.get("role") == "worst"
                else "未建模通路" if state.get("status") == "unmapped" else "评审主工况" if state.get('role')=='primary' else "对照工况通路")}


def _agent_figure(state: dict[str, Any]) -> dict[str, Any] | None:
    """Agent 写的通路规格：只有名字和文案，没有坐标。"""
    fig = state.get("figure")
    if not isinstance(fig, dict):
        return None
    from agent_review.figure_contract import normalize
    fig=normalize(fig)
    pages = fig.get("pages")
    if isinstance(pages, list) and pages:
        return fig
    if fig.get("path"):
        return fig
    return None


def _geom_name(token: str, comps: dict[str, Any]) -> str:
    """R11.1 落到 R11。网名保持原样。"""
    base = token.split(".", 1)[0]
    if base != token and _is_device(base, comps):
        return base
    return token


def _spec_from_agent_figure(
    board_pdf: Path,
    kg: dict[str, Any],
    *,
    rule_id: str,
    obj: str,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """按 Agent 点名的页和通路贴绿线。对不上的名字不另拉斜线。"""
    fig = _agent_figure(state)
    if fig is None:
        return None
    comps = (kg.get("components") or {}) if isinstance(kg, dict) else {}
    raw_pages = fig.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        raw_pages = [fig]
    doc = fitz.open(board_pdf)
    page_specs: list[dict[str, Any]] = []
    step = 1
    for raw in raw_pages:
        if not isinstance(raw, dict):
            continue
        tokens = [str(x).strip() for x in (raw.get("path") or []) if str(x).strip()]
        if len(tokens) < 2:
            continue
        try:
            pdf_page = int(raw.get("pdf_page") or 0)
        except (TypeError, ValueError):
            pdf_page = 0
        if pdf_page < 1 or pdf_page > doc.page_count:
            continue
        page = doc[pdf_page - 1]
        names: list[str] = []
        for token in tokens:
            name = _geom_name(token, comps)
            if name and name not in names:
                names.append(name)
        loc: dict[str, tuple[float, float]] = {}
        traces: dict[str, list[tuple[float, float]]] = {}
        for name in names:
            if _is_device(name, comps):
                at = _locate_token(page, name, near=loc.get(obj), comps=comps)
                if at is not None:
                    loc[name] = at
        for i, name in enumerate(names):
            if name in loc:
                continue
            prev_pt = next((loc[names[j]] for j in range(i - 1, -1, -1) if names[j] in loc), None)
            next_pt = next((loc[names[j]] for j in range(i + 1, len(names)) if names[j] in loc), None)
            if _is_device(name, comps):
                continue
            if prev_pt and next_pt and not _RAIL.match(name):
                at = _locate_token(page, name, near=prev_pt, comps=comps)
                if at is not None and _on_segment(at, prev_pt, next_pt):
                    loc[name] = at
                continue
            neighbor = prev_pt or next_pt or loc.get(obj)
            if neighbor is None:
                continue
            trace = _trace_net(page, name, neighbor, comps)
            if trace:
                traces[name] = trace
                loc[name] = trace[-1]
        hit_names = [n for n in names if n in loc]
        if len(hit_names) < 2:
            continue
        poly: list[tuple[float, float]] = []
        for name in hit_names:
            chunk = list(traces.get(name) or [loc[name]])
            if poly and chunk:
                if abs(chunk[0][0] - poly[-1][0]) + abs(chunk[0][1] - poly[-1][1]) > abs(chunk[-1][0] - poly[-1][0]) + abs(chunk[-1][1] - poly[-1][1]):
                    chunk = list(reversed(chunk))
                bridge = _segment_pts(page, poly[-1], chunk[0])
                if bridge and bridge[0] == poly[-1]:
                    poly.extend(bridge[1:])
                elif bridge:
                    poly.extend(bridge)
                if poly and chunk and abs(chunk[0][0] - poly[-1][0]) < 0.5 and abs(chunk[0][1] - poly[-1][1]) < 0.5:
                    poly.extend(chunk[1:])
                else:
                    poly.extend(chunk)
            else:
                poly.extend(chunk)
        if len(poly) < 2:
            continue
        markers: list[dict[str, Any]] = []
        for item in raw.get("markers") or []:
            if not isinstance(item, dict) or len(markers) >= 6:
                continue
            on = str(item.get("on") or "").strip()
            if not on:
                continue
            dev = _geom_name(on, comps)
            at = loc.get(dev) or loc.get(on)
            if at is None and _is_device(dev, comps):
                at = _locate_token(page, dev, near=loc.get(obj), comps=comps)
            if at is None:
                continue
            title = str(item.get("title") or on).strip()
            body = str(item.get("body") or "").strip()
            markers.append(
                {
                    "at": [at[0], at[1]],
                    "title": title[:40],
                    "body": body[:80],
                    "leader": [at[0] + 56, at[1]],
                }
            )
        bridge_note = None
        bodies = [b for b in (raw.get("through_body") or []) if isinstance(b, dict)]
        if bodies:
            ref = str(bodies[0].get("ref") or "").strip()
            note = str(bodies[0].get("note") or "封装内").strip()
            at = loc.get(ref)
            if at is not None:
                bridge_note = {"text": note[:24], "at": [at[0], at[1]]}
        label = " → ".join(tokens)
        page_specs.append(
            {
                "pdf_page": pdf_page,
                "page_name": str(raw.get("page_name") or _page_name(comps, obj, pdf_page) or ""),
                "step": step,
                "label": label[:120],
                "takeaway": str(state.get("name_zh") or "")[:72],
                "pts": [list(p) for p in poly],
                "full_page": True,
                "anchors": hit_names,
                "markers": markers,
                "bridge_note": bridge_note,
                "calc_chain": [],
                "mode": "path",
            }
        )
        step += 1
    doc.close()
    if not page_specs:
        return None
    return {
        "object": obj,
        "rule_id": rule_id,
        "title": f"{obj} · {str(state.get('name_zh') or '工况')[:28]}",
        "verdict": str(state.get("verdict_zh") or ""),
        "callout": _dut_caption(state, obj),
        "banner": "工况通路图",
        "pages": page_specs,
    }


def _spec_from_series_path(
    board_pdf: Path,
    kg: dict[str, Any],
    card: dict[str, Any],
    *,
    rule_id: str,
    obj: str,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """网表串联 → 仍在原理图原图上画青蓝通路（C5 同款渲染）。"""
    nodes = _netlist_nodes(kg, card, state, obj)
    if not nodes or not any(n.get("dut") for n in nodes):
        return None
    _attach_same_net_notes(nodes, kg, _mentioned_refs(card, state, kg, obj), obj)
    names = [str(n.get("text") or "") for n in nodes if n.get("text")]
    if len(names) < 2:
        return None
    comps = (kg.get("components") or {}) if isinstance(kg, dict) else {}
    doc = fitz.open(board_pdf)
    obj_page = None
    obj_comp = comps.get(obj) if isinstance(comps, dict) else None
    if isinstance(obj_comp, dict):
        for loc in obj_comp.get("location") or []:
            if isinstance(loc, dict) and loc.get("pdf_page"):
                obj_page = int(loc["pdf_page"])
                break
    by_page: dict[int, list[str]] = {}
    for name in names:
        pages: list[int] = []
        comp = comps.get(name) if isinstance(comps, dict) else None
        if isinstance(comp, dict):
            for loc in comp.get("location") or []:
                if isinstance(loc, dict) and loc.get("pdf_page"):
                    pages.append(int(loc["pdf_page"]))
        if not pages:
            for i, page in enumerate(doc):
                if _word_hits(page, name):
                    pages.append(i + 1)
        if obj_page is not None:
            on_home = [p for p in pages if p == obj_page]
            pages = on_home or pages
        for p in pages[:1]:
            by_page.setdefault(p, [])
            if name not in by_page[p]:
                by_page[p].append(name)
    ordered_pages = sorted(by_page, key=lambda p: (0 if p == obj_page else 1, p))
    page_specs: list[dict[str, Any]] = []
    step = 1
    for pdf_page in ordered_pages:
        page_names = by_page[pdf_page]
        if len(page_names) < 2 and pdf_page != obj_page:
            continue
        page = doc[pdf_page - 1]
        # 器件落在符号本体。端点网先贴到绿线上再收到引脚，避免从网名字形斜插进符号。
        loc: dict[str, tuple[float, float]] = {}
        traces: dict[str, list[tuple[float, float]]] = {}
        for name in page_names:
            if _is_device(name, comps):
                at = _locate_token(page, name, near=loc.get(obj), comps=comps)
                if at is not None:
                    loc[name] = at
        for i, name in enumerate(page_names):
            if name in loc:
                continue
            prev_pt = next((loc[page_names[j]] for j in range(i - 1, -1, -1) if page_names[j] in loc), None)
            next_pt = next((loc[page_names[j]] for j in range(i + 1, len(page_names)) if page_names[j] in loc), None)
            if _is_device(name, comps):
                continue
            if prev_pt and next_pt and not _RAIL.match(name):
                at = _locate_token(page, name, near=prev_pt, comps=comps)
                # 网标不在两端器件的连线上，就不作为通路停点，页眉也不写它。
                if at is not None and _on_segment(at, prev_pt, next_pt):
                    loc[name] = at
                continue
            neighbor = prev_pt or next_pt or loc.get(obj)
            if neighbor is None:
                continue
            trace = _trace_net(page, name, neighbor, comps)
            if trace:
                traces[name] = trace
                loc[name] = trace[-1]
        hit_names = [n for n in page_names if n in loc]
        if len(hit_names) < 2:
            continue
        poly: list[tuple[float, float]] = []
        for name in hit_names:
            chunk = list(traces.get(name) or [loc[name]])
            if poly and chunk:
                if abs(chunk[0][0] - poly[-1][0]) + abs(chunk[0][1] - poly[-1][1]) > abs(chunk[-1][0] - poly[-1][0]) + abs(chunk[-1][1] - poly[-1][1]):
                    chunk = list(reversed(chunk))
                bridge = _segment_pts(page, poly[-1], chunk[0])
                if bridge and bridge[0] == poly[-1]:
                    poly.extend(bridge[1:])
                elif bridge:
                    poly.extend(bridge)
                if poly and chunk and abs(chunk[0][0] - poly[-1][0]) < 0.5 and abs(chunk[0][1] - poly[-1][1]) < 0.5:
                    poly.extend(chunk[1:])
                else:
                    poly.extend(chunk)
            else:
                poly.extend(chunk)
        markers: list[dict[str, Any]] = []
        for name in hit_names:
            at = loc[name]
            if len(markers) >= 6:
                break
            # 只圈通路上的器件。网名和公式留在页眉、报告里，不钉在线段中点。
            if not _is_device(name, comps):
                continue
            comp = comps.get(name) if isinstance(comps, dict) else None
            value = str((comp or {}).get("value") or "").strip()
            if name != obj:
                continue
            body = _dut_caption(state, obj) or value
            markers.append(
                {
                    "at": [at[0], at[1]],
                    "title": name,
                    "body": body,
                    "leader": [at[0] + 56, at[1]],
                }
            )
            span = _span_voltage(state)
            if span:
                markers.append(
                    {
                        "at": [at[0], at[1]],
                        "title": span,
                        "body": "",
                        "kind": "volt",
                    }
                )
        page_specs.append(
            {
                "pdf_page": pdf_page,
                "page_name": _page_name(comps, obj, pdf_page) or _page_name(comps, hit_names[0], pdf_page),
                "step": step,
                "label": " → ".join(hit_names),
                "takeaway": str(state.get("name_zh") or "")[:72],
                "pts": [list(p) for p in poly],
                "full_page": True,
                "anchors": hit_names,
                "markers": markers,
                "calc_chain": [],
                "continue_to": None,
            }
        )
        step += 1
    if not page_specs:
        return None
    for i, ps in enumerate(page_specs):
        ps["continue_to"] = page_specs[i + 1]["pdf_page"] if i + 1 < len(page_specs) else None
    verdict = "不通过" if "不满足" in str(state.get("结论") or "") else "通过"
    if "满足" in str(state.get("结论") or "") and "不满足" not in str(state.get("结论") or ""):
        verdict = "通过"
    return {
        "object": obj,
        "rule_id": rule_id,
        "title": f"{obj} · {str(state.get('name_zh') or '工况')[:28]}",
        "verdict": verdict,
        "callout": _result_callout(state),
        "banner": "工况通路图",
        "pages": page_specs,
    }


def _spec_from_annotate(
    board_pdf: Path,
    kg: dict[str, Any],
    card: dict[str, Any],
    *,
    rule_id: str,
    obj: str,
    state: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """绿线走不通时的圈注规格。返回 (规格, 说明)；一个点都没有时规格为 None。"""
    tokens = _annotate_tokens(card, state, kg, obj)
    aliases = _aliases(state)
    doc = fitz.open(board_pdf)
    comps = (kg.get("components") or {}) if isinstance(kg, dict) else {}
    located: dict[int, list[tuple[str, fitz.Rect]]] = {}
    missing: list[str] = []

    def pages_of(tok: str) -> list[int]:
        comp = comps.get(tok) if isinstance(comps, dict) else None
        prefer: list[int] = []
        if isinstance(comp, dict):
            for loc in comp.get("location") or []:
                if isinstance(loc, dict) and loc.get("pdf_page"):
                    prefer.append(int(loc["pdf_page"]))
        found: list[int] = []
        for i, page in enumerate(doc):
            if _word_hits(page, tok):
                found.append(i + 1)
        if prefer:
            on_comp = [p for p in prefer if p in found or _word_hits(doc[p - 1], tok)]
            if on_comp:
                return on_comp
        return found

    obj_pages = pages_of(obj) if obj in tokens else []
    fixed: dict[str, list[int]] = {}
    for tok in tokens:
        if _RAIL.match(tok):
            continue
        pages = pages_of(tok)
        if not pages:
            missing.append(tok)
            continue
        if tok != obj and obj_pages:
            on_obj = [p for p in pages if p in obj_pages]
            pages = on_obj or pages
        fixed[tok] = pages

    pin_nets = []
    comp = comps.get(obj) if isinstance(comps, dict) else None
    if isinstance(comp, dict):
        pin_nets = [str(p.get("net") or "") for p in (comp.get("pins") or []) if isinstance(p, dict)]
    home_pages = set(obj_pages)
    for net in pin_nets:
        home_pages.update(fixed.get(net) or [])
    for tok in tokens:
        if not _RAIL.match(tok):
            continue
        pages = [p for p in pages_of(tok) if p in home_pages]
        if not pages:
            missing.append(tok)
            continue
        fixed[tok] = pages

    for tok, pages in fixed.items():
        for pdf_page in pages:
            rects = _word_hits(doc[pdf_page - 1], tok)
            if not rects:
                continue
            chosen = rects[0]
            if len(rects) > 1:
                mates = [r for name, r in located.get(pdf_page, []) if name == obj or not _RAIL.match(name)]
                if not mates:
                    mates = [r for name, r in located.get(pdf_page, [])]
                if mates:
                    cx = sum((r.x0 + r.x1) / 2 for r in mates) / len(mates)
                    cy = sum((r.y0 + r.y1) / 2 for r in mates) / len(mates)
                    best = min(rects, key=lambda r: abs((r.x0 + r.x1) / 2 - cx) + abs((r.y0 + r.y1) / 2 - cy))
                    dist = abs((best.x0 + best.x1) / 2 - cx) + abs((best.y0 + best.y1) / 2 - cy)
                    if _RAIL.match(tok) and dist > _NEAR_RAIL:
                        if tok not in missing:
                            missing.append(tok)
                        continue
                    chosen = best
                elif _RAIL.match(tok):
                    if tok not in missing:
                        missing.append(tok)
                    continue
            located.setdefault(pdf_page, []).append((tok, chosen))

    if not located:
        return None, _review_note(missing or tokens, aliases) or "文案中的器件和网名未能在原理图上定位"

    note = _review_note(missing, aliases)
    page_specs = []
    ordered = sorted(located)
    for pi, pdf_page in enumerate(ordered):
        hits = located[pdf_page][:8]
        page = doc[pdf_page - 1]
        names = [name for name, _ in hits][:8]
        markers = []
        for name, rect in hits[:8]:
            if _is_device(name, comps):
                at = _symbol_center(page, rect)
            else:
                at = ((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
            body = _marker_body(state, name, obj, comps)
            if name == obj and (missing or aliases):
                body = body[:28] + "\n" + _takeaway(missing, aliases)[:42]
            markers.append(
                {
                    "at": [at[0], at[1]],
                    "title": name,
                    "body": body,
                    "leader": [at[0] + 36, at[1] - 28],
                }
            )
        pname = ""
        for name in names:
            pname = _page_name(comps, name, pdf_page)
            if pname:
                break
        page_specs.append(
            {
                "pdf_page": pdf_page,
                "page_name": pname,
                "step": pi + 1,
                "label": "圈注 · " + " · ".join(names),
                "takeaway": _takeaway(missing, aliases),
                "mode": "anchors",
                "full_page": True,
                "anchors": names,
                "markers": markers,
                "continue_to": ordered[pi + 1] if pi + 1 < len(ordered) else None,
            }
        )
    verdict = "不通过" if "不满足" in str(state.get("结论") or "") else "通过"
    if "满足" in str(state.get("结论") or "") and "不满足" not in str(state.get("结论") or ""):
        verdict = "通过"
    return (
        {
            "object": obj,
            "rule_id": rule_id,
            "title": f"{obj} · {str(state.get('name_zh') or '工况')[:28]}",
            "verdict": verdict,
            "callout": _result_callout(state),
            "banner": "工况圈注",
            "pages": page_specs,
            "review_note": note,
        },
        note,
    )


def _states_of(card: dict[str, Any]) -> list[dict[str, Any]]:
    raw = card.get("states")
    if not isinstance(raw, list) or not raw:
        sol = card.get("solution") if isinstance(card.get("solution"), dict) else {}
        raw = (sol or {}).get("states") or []
    out = []
    for st in raw:
        if isinstance(st, dict) and (st.get("name_zh") or st.get("name") or st.get('label')):
            # Older rule schemas use label for the model-authored state title.
            out.append(dict(st,name=st.get('name') or st.get('label')))
    return out


def _prepare_schematic_pdf(o: Path) -> tuple[Path, str]:
    """Prepare the drawing source before report generation on a cold workspace."""
    from kernel.report_workbench import _resolve_schematic_pdf, ensure_schematic_pdf

    source = _resolve_schematic_pdf(o)
    if source is None:
        raise FileNotFoundError("无法生成评审图：缺少可读取的原理图 PDF")
    try:
        with fitz.open(source) as doc:
            if not doc.is_pdf or doc.needs_pass or doc.page_count < 1:
                raise ValueError("invalid schematic PDF")
    except (OSError, ValueError, RuntimeError) as exc:
        raise ValueError("无法生成评审图：原理图 PDF 损坏、加密或没有可读取页面") from exc
    dest = o / "sch" / "schematic.pdf"
    try:
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        if not ensure_schematic_pdf(o, source):
            raise OSError("PDF copy failed")
        # The report helper uses size/mtime as a fast path; drawings must use
        # the selected source bytes even when an older copy has identical stats.
        if hashlib.sha256(dest.read_bytes()).hexdigest() != source_hash:
            import shutil
            shutil.copy2(source, dest)
            if hashlib.sha256(dest.read_bytes()).hexdigest() != source_hash:
                raise OSError("PDF source/copy hash mismatch")
    except OSError as exc:
        raise OSError("无法生成评审图：原理图 PDF 报告副本准备失败或内容不一致") from exc
    return dest, source_hash


def run(
    board: Path,
    *,
    rule_ids: list[str] | None = None,
    objects: list[str] | None = None,
) -> dict[str, Any]:
    o = out_dir(board)
    pdf = None
    pdf_hash = None
    kg_path = o / "board_kg.json"
    enum_path = o / "agent_enumeration.json"
    if not enum_path.is_file():
        return {"skipped": True, "reason": "缺枚举"}
    kg = read_json(kg_path) if kg_path.is_file() else {}
    enum = read_json(enum_path)
    want = set(rule_ids) if rule_ids else set(enum.get('rule_ids') or FIGURE_RULES)
    want_objs = {safe_object_name(x) for x in objects} if objects else None
    canonical={}
    if (o/'verdicts.json').is_file():
        canonical={(r['rule_id'],c['object']):dict(c,rule_id=r['rule_id']) for r in read_json(o/'verdicts.json')['results'] for c in r.get('object_cards',[])}
    index: dict[str, Any] = {"rules": {}}
    drawn = 0
    annotated = 0
    missed = 0
    for rid in enum.get("rule_ids") or []:
        if rid not in want:
            continue
        objs = list((enum.get("by_rule") or {}).get(rid) or [])
        cards = load_judge_cards(o, rid, objs)
        rule_bucket: dict[str, Any] = {}
        for card in cards:
            obj = str(card.get("object") or "")
            if not obj or obj == "(none)":
                continue
            if want_objs is not None and safe_object_name(obj) not in want_objs:
                continue
            if card.get('main_analysis'):
                from agent_review.main_analysis import drawing_states
                states=drawing_states(card)
            else:states = _states_of(card)
            if not states:
                continue
            if rid not in FIGURE_RULES and not any(isinstance(s.get('figure'),dict) for s in states):
                # Type/temperature/condition checks remain textual; an explicit
                # electrical analysis in any rule still receives its diagram.
                continue
            obj_bucket: dict[str, Any] = {}
            if card.get('main_analysis'):
                from agent_review.main_analysis import selected_index
                primary=selected_index(card)
            else:primary = primary_state_index(states)
            for i, st in enumerate(states):
                slug = _slug(st, i)
                if i != primary:
                    obj_bucket[slug] = {"status": "text_only", "state_index": i, "reason": "其他工况仅作文字比较"}
                    continue
                if pdf is None:
                    # Text-only scopes need no PDF. Both standalone drawing and
                    # aggregation must prepare it before the renderer reads sch/.
                    pdf, pdf_hash = _prepare_schematic_pdf(o)
                # The business caption is model prose, never router diagnostics.
                caption=str(st.get('route_summary') or '')
                # Direct model contracts already own their current wording.
                # Published legacy prose must not override a freshly rerun card.
                original=None if card.get('main_analysis') or card.get('analysis_contract')=='analysis.v1' or card.get('review_v1') else canonical.get((rid,obj))
                if original:
                    from agent_review.detail_view import scenario_rows
                    from agent_review.detail_prose import load
                    prose=load(o,original)
                    rows=scenario_rows(original)
                    if prose and i<len(rows) and prose.get('primary_ref')==rows[i]['id']:
                        caption=prose.get('route_summary') or caption
                    native=original.get('review_v1')
                    if native:
                        scenario=next((x for x in native['scenarios'] if x['id']==st.get('case_id')),None)
                        if scenario:caption=scenario['route_summary']
                dest = o / "stress_paths" / rid / safe_object_name(obj) / slug
                fp = hashlib.sha1((_fingerprint(st) + str(card.get("verdict_zh"))
                                   + json.dumps(card.get('review_v1',{}),ensure_ascii=False,sort_keys=True)
                                   + json.dumps([card.get('main_analysis'),card.get('decision_basis')],ensure_ascii=False,sort_keys=True)
                                   + json.dumps(kg, ensure_ascii=False, sort_keys=True)
                                   + caption + hashlib.sha256(Path(__file__).read_bytes()+(Path(__file__).parent/'stress_path_render.py').read_bytes()+(Path(__file__).parent/'figure_contract.py').read_bytes()).hexdigest()
                                   + pdf_hash).encode("utf-8")).hexdigest()[:16]
                meta_path = dest / "meta.json"
                if meta_path.is_file() and (dest / "flow.png").is_file():
                    try:
                        old = json.loads(meta_path.read_text(encoding="utf-8"))
                    except Exception:
                        old = {}
                    if (
                        old.get("fingerprint") == fp
                        and old.get("status") == "ok"
                        and old.get("source") == "verified"
                        and old.get("flow_rev") == 28
                    ):
                        rel = f"stress_paths/{rid}/{safe_object_name(obj)}/{slug}/flow.png"
                        obj_bucket[slug] = {"status": "ok", "flow": rel, "cached": True,
                                            "state_index": i, "source": old.get("source"),
                                            "routing_complete": old.get("routing_complete", False)}
                        if old.get("review_note"):
                            obj_bucket[slug].update(review_note=old["review_note"], reason=old["review_note"])
                        drawn += 1
                        continue
                spec = None
                source = ""
                review_note = ""
                # Never interpolate disconnected copper or infer branches from prose.
                spec = _spec_from_verified_figure(pdf, kg, card, rule_id=rid, obj=obj, state=st)
                source = "verified"
                if not spec and not card.get('review_v1'):
                    spec, review_note = _spec_from_annotate(pdf, kg, card, rule_id=rid, obj=obj, state=st)
                    source = "annotate"
                if spec:
                    spec["verdict"] = card.get("verdict_zh") or "待确认"
                if not spec:
                    reason = review_note or "文案中的器件和网名未能在原理图上定位"
                    obj_bucket[slug] = {"status": "ungenerated", "state_index": i, "reason": reason}
                    missed += 1
                    print(f"MISS\t{rid}\t{obj}\t{slug}\t{reason}")
                    continue
                relation_kind=(st.get('figure') or {}).get('kind') if isinstance(st.get('figure'),dict) else None
                if relation_kind=='cross_case_relations':
                    spec['banner']='跨工况关系分析 · 非同时导通'
                    for panel in spec['pages']:
                        panel['show_arrows']=False;panel['arrow_polylines']=[]
                        panel['_model_page_summary']=(panel.get('_model_page_summary') or caption)+'\n关系高亮不代表各状态同时导通，不声明实际电流方向。'
                if card.get('main_analysis'):
                    main=card['main_analysis']
                    spec['title']=main['title'];spec['callout']=main['route_summary']
                    relations=(main.get('figure') or {}).get('kind')=='cross_case_relations'
                    spec['banner']=('跨工况上界关系图 · 非同时导通' if main['proof_status']=='verified' else '证明待完成 · 跨工况关系示意') if relations else '正式判定依据工况'
                    for panel in spec['pages']:
                        if relations:
                            panel['show_arrows']=False;panel['arrow_polylines']=[]
                            panel['_model_page_summary']=(panel.get('_model_page_summary') or main['route_summary'])+'\n关系高亮不表示各状态同时导通；本图不声明实际电流方向。'
                for panel in spec['pages']:
                    panel['full_page']=True
                    panel['takeaway']=panel.get('_model_page_summary') or caption or str(st.get('name_zh') or panel.get('label') or '')
                    if panel.get('routing_issues'):
                        panel['takeaway']+='\n定位提示：部分连接尚未完成实线定位，请结合原图核对。'
                import gc
                gc.collect()
                render_spec(board, spec, dest)
                meta = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
                complete = all(p.get("routing_complete", False) for p in spec["pages"])
                issues = [issue for p in spec["pages"] for issue in p.get("routing_issues") or []]
                if issues:
                    review_note = "；".join(dict.fromkeys(issues))
                if card.get('main_analysis'):
                    from agent_review.detail_view import basis_source_hash
                    meta.update(basis_ref=card['main_analysis']['basis_ref'],basis_state_index=card['main_analysis']['state_index'],
                        analysis_kind=card['main_analysis']['kind'],basis_source_hash=basis_source_hash(card),
                        relation_diagram=(card['main_analysis'].get('figure') or {}).get('kind')=='cross_case_relations')
                meta["routing_complete"] = complete
                meta["fingerprint"] = fp
                meta["source_pdf_sha256"] = pdf_hash
                meta["status"] = "ok"
                meta["flow_rev"] = 28
                meta["source"] = source
                if card.get('review_v1'):
                    from agent_review.review_contract import source_hash
                    meta['review_source_hash'] = source_hash(card['review_v1'])
                meta["case"] = slug
                if review_note:
                    meta["review_note"] = review_note
                (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                rel = f"stress_paths/{rid}/{safe_object_name(obj)}/{slug}/flow.png"
                row: dict[str, Any] = {"status": "ok", "state_index": i, "flow": rel, "source": source,
                                       "routing_complete": complete}
                if review_note:
                    row["review_note"] = review_note
                    row["reason"] = review_note
                obj_bucket[slug] = row
                drawn += 1
                if source == "annotate":
                    annotated += 1
                print(f"DRAW\t{rid}\t{obj}\t{slug}\t{source}")
            if obj_bucket:
                rule_bucket[obj] = obj_bucket
        if rule_bucket:
            index["rules"][rid] = rule_bucket
    index["drawn"] = drawn
    index["annotated"] = annotated
    index["ungenerated"] = missed
    index_path = o / "stress_paths" / "index.json"
    if want_objs is not None and index_path.is_file():
        prev = read_json(index_path)
        merged = dict(prev.get("rules") or {})
        for rid, bucket in (index.get("rules") or {}).items():
            slot = dict(merged.get(rid) or {})
            slot.update(bucket)
            merged[rid] = slot
        index["rules"] = merged
    write_json(index_path, index)
    return index


def _figure_pages(o: Path, row: dict[str, Any]) -> list[dict[str, str]]:
    """分步图路径。一页一张，供报告里翻页。"""
    flow = str(row.get("flow") or "").replace("\\", "/")
    if not flow:
        return []
    meta_path = o / Path(flow).parent / "meta.json"
    base = Path(flow).parent.as_posix()
    pages: list[dict[str, str]] = []
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        for p in meta.get("pages") or []:
            if not isinstance(p, dict):
                continue
            panel = str(p.get("panel") or "")
            if not panel:
                continue
            page_no = p.get("pdf_page")
            label = f"第 {page_no} 页" if page_no else "电流通路"
            phase=str(p.get("phase") or "")
            if phase:label=f"图 {p.get('step')} · {phase} · "+label
            name = str(p.get("page_name") or "")
            if name:
                label += " · " + name
            if not p.get("routing_complete", False):
                label += "（含未定位或圈注）"
            pages.append({"src": f"{base}/{panel}", "label": label})
    return pages or [{"src": flow, "label": ""}]


def lookup(o: Path, rule_id: str, obj: str, index: int) -> dict[str, Any] | None:
    path = o / "stress_paths" / "index.json"
    if not path.is_file():
        return None
    data = read_json(path)
    bucket = ((data.get("rules") or {}).get(rule_id) or {}).get(obj) or {}
    if not isinstance(bucket, dict) or not bucket:
        return None
    # New media carry an explicit source index; a numeric case ID can otherwise
    # accidentally match a different state's position.
    indexed = [r for r in bucket.values() if isinstance(r, dict) and "state_index" in r]
    row = next((r for r in indexed if r["state_index"] == index), None)
    if not indexed:
        row = bucket.get(str(index))
    if row is None and not indexed:
        keys = list(bucket)
        if 0 <= index < len(keys):
            row = bucket[keys[index]]
    if not isinstance(row, dict):
        return None
    out = dict(row)
    if out.get("status") == "ok":
        out["pages"] = _figure_pages(o, out)
    return out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    import argparse

    ap = argparse.ArgumentParser(description="按深审工况出通路图")
    ap.add_argument("--board", required=True)
    ap.add_argument("--rule-ids", default="", help="逗号分隔，默认 P11-A01/A03/A07")
    ap.add_argument("--objects", default="", help="逗号分隔；只重画这些对象，并合并进已有索引")
    args = ap.parse_args(argv)
    board = resolve_board_path(args.board)
    rids = [x.strip() for x in str(args.rule_ids).split(",") if x.strip()] or None
    objs = [x.strip() for x in str(args.objects).split(",") if x.strip()] or None
    info = run(board, rule_ids=rids, objects=objs)
    print(
        f"OK\tstress_figures drawn={info.get('drawn')} "
        f"annotated={info.get('annotated')} ungenerated={info.get('ungenerated')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
