# -*- coding: utf-8 -*-
"""Attach *region-level* schematic screenshots to EvidencePacks.

Not just a full-page dump: for each check item, locate the design block /
refs actually under review (e.g. 隔离485、通信/供电接口、MCU、U12、Q5),
crop + highlight that area into evidence/shots/{rule_id}_focus.png, and
write pages_checked with a precise page/region label.

Usage:
  python attach_schematic_shots.py --out-dir <review_out> [--dpi 160]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyMuPDF required: pip install pymupdf") from exc

# Named blocks — keep legacy labels for older boards; SCB24 uses sheet names below.
REGION_LABELS = [
    "通信/供电接口",
    "隔离485",
    "光耦+TRAIC",
    "+5V转+3.3V",
    "MCU",
    "LED指示灯",
    "过孔焊线",
    "电机接口",
    # SCB24 / 本板常见图页名
    "P01_POWER",
    "P02",
    "P03",
    "P04",
    "P05",
    "P06",
    "P07",
    "P08",
    "P09",
    "P10",
]

# Sheet-oriented seeds for product-center sensor boards (SCB24 class)
SHEET_FOCUS: dict[str, list[str]] = {
    "power": ["CN1", "F1", "D2", "D3", "U1", "C1", "C2", "C3", "+24V", "+12V", "L78L12"],
    "input": ["SPDL", "DS_ORI", "C17", "C18", "D8", "D9", "D10", "U4"],
    "compare": ["U2", "U3", "U7", "SIG_ORI", "R38"],
    "output": ["OUT_ORI", "Q1", "Q2", "Q3", "Q4", "H1", "CN2", "LED1"],
    "switch": ["U5", "CD4066"],
    "timing": ["U6", "MC14584", "C11"],
}

# parent_id → default region / keyword seeds (legacy boards). Prefer FAMILY_SEEDS for product-center.
PARENT_FOCUS: dict[int, list[str]] = {
    1: ["创建日期", "原理图", "板子", "版本"],
    4: ["+24V", "+12V", "CN1", "SIG_ORI"],
    5: ["C1", "C2", "C18", "U1"],
    7: ["+24V", "+12V", "U1", "CN1"],
    9: ["H1", "R38", "CN1"],
    11: ["C1", "C2", "C18", "U1", "+24V", "+12V"],
    12: ["CN1", "CN2", "U1", "Q1"],
    14: ["CN1", "F1", "D2", "D3", "+24V", "U1"],
    16: ["C1", "C2", "U1", "+12V"],
    18: ["晶振", "Y1", "X1"],
    19: ["RESET", "NRST"],
    25: ["CN1", "CN2", "D8", "D10", "F1"],
    26: ["继电器", "电机", "风扇"],
    32: ["CN2", "H1", "调试"],
    42: ["C18", "U1"],
}

REF_TO_REGION = {
    "CN1": "P01_POWER",
    "F1": "P01_POWER",
    "U1": "P01_POWER",
    "C1": "P01_POWER",
    "C2": "P01_POWER",
    "C3": "P01_POWER",
    "+24V": "P01_POWER",
    "+12V": "P01_POWER",
    "U4": "P02",
    "DS_ORI": "P08",
    "C18": "P08",
    "D8": "P08",
    "D10": "P08",
    "U5": "P03",
    "U2": "P04",
    "U3": "P05",
    "U7": "P05",
    "R38": "P06",
    "U6": "P07",
    "H1": "P09",
    "Q1": "P09",
    "OUT_ORI": "P09",
    "LED1": "P10",
    "CN2": "P01_POWER",
}

REF_RE = re.compile(r"\b([A-Z]{1,3}\d{1,4})\b")
MPN_HINT_RE = re.compile(
    r"(STM32\w+|SGM2028[\w/\-]*|TD541S485H|MOC3063[\w()]*|ACST1235[\w\-]*|"
    r"ESD3Z[\w.]*|CFSH05[\w\s\-]*|L78L12[\w\-]*|LM3\d{2}[\w/]*|CD4066[\w]*|"
    r"MC14584[\w]*|1N5241B|LL4148|JK-SMD0805[\w\-]*)",
    re.I,
)

# Title-block / revision noise — never treat as resistor R1 etc.
TITLEBLOCK_NOISE_RE = re.compile(
    r"(重建[-_]?R\d+|版本|Version|Rev\.?|Sheet|页\s*\d|PAGE\s*\d|A4|AUTOMATION|创建日期|更新日期|图页|尺寸)",
    re.I,
)

FAMILY_SEEDS: dict[str, list[str]] = {
    "P03": ["原理图", "P00", "功能"],
    "P04": ["+24V", "+12V", "CN1", "SIG_ORI", "GND"],
    "P05": ["C18", "C1", "C2", "C3", "47uF", "4.7uF", "U1"],
    "P07": ["+24V", "+12V", "U1", "CN1", "F1"],
    "P09": ["H1", "R38", "CN1", "OUT_ORI"],
    "P11": ["C1", "C2", "C3", "C18", "U1", "+24V", "+12V"],
    "P12": ["CN1", "CN2", "U1", "Q1", "Q3"],
    "P14": ["CN1", "F1", "D2", "D3", "+24V", "U1"],
    "P16": ["C1", "C2", "C4", "U1", "+12V"],
    "P18": ["Y1", "X1", "晶振"],
    "P19": ["RESET", "NRST"],
    "P21": ["U1", "U2", "+12V", "+24V"],
    "P25": ["CN1", "CN2", "D8", "D9", "D10", "F1"],
    "P26": ["继电器", "电机", "风扇"],
    "P28": ["CN1", "D8", "D10", "SPDL", "DS_ORI"],
    "P32": ["CN2", "H1", "调试"],
    "P42": ["C18", "U1", "C1"],
}

# Rule-specific focus overrides (hardware-meaningful objects)
RULE_FOCUS: dict[str, list[str]] = {
    # Resistor Value check — do not inherit P05 capacitor family seeds into caption/crop
    "P05-A01": ["R17", "R38", "R10", "R21", "10K", "100k"],
    "P05-A06": ["C18", "C1", "C2", "C3", "47uF", "4.7uF", "100nF"],
    "P05-A08": ["C1", "C2", "C3", "C18", "47uF", "4.7uF", "50V", "25V"],
    "P05-A09": ["C1", "C7", "C11", "C14", "C18", "X7R", "X5R"],
    "P11-A01": ["R10", "R17", "R21", "R38", "0603", "+12V"],
    "P11-A03": ["C1", "C2", "C3", "C18", "+24V", "+12V"],
    "P11-A04": ["C1", "C2", "C3", "U1", "L78L12", "+12V"],
    "P14-A06": ["CN1", "F1", "D2", "D3", "+24V", "U1"],
    "P14-A05": ["CN1", "F1", "D2", "D10", "SPDL"],
    "P12-A08": ["CN1", "CN2", "DB128V", "PH2.0"],
    "P04-A04": ["+24V", "+12V", "SIG_ORI", "CN1"],
    "P25-A02": ["CN1", "CN2", "D8", "D9", "D10"],
}


def collect_pack_keywords(pack: dict) -> list[str]:
    """Refs / nets this check actually cares about — drives page selection."""
    note = str(pack.get("note") or "")
    name = str(pack.get("name") or "")
    rid = str(pack.get("rule_id") or "")
    note_refs = REF_RE.findall(note + "\n" + name)
    bits: list[str] = [note, name, str(pack.get("why") or ""), str(pack.get("remediation") or "")]
    for s in pack.get("steps") or []:
        if isinstance(s, dict):
            bits.append(str(s.get("observation") or ""))
            bits.append(str(s.get("action") or ""))
    for row in pack.get("extracted") or []:
        if isinstance(row, dict):
            bits.append(str(row.get("value") or ""))
            bits.append(str(row.get("object_id") or ""))
    blob = "\n".join(bits)
    refs = REF_RE.findall(blob)
    mpns = MPN_HINT_RE.findall(blob)
    out: list[str] = []
    seen = set()

    def add(u: str) -> None:
        u = (u or "").strip()
        if not u or u.upper() in seen:
            return
        # Never seed from bare revision-like tokens alone when pulled from generic note
        if u.upper() in {"A4", "P00", "R1"} and "电阻" not in name and rid not in RULE_FOCUS:
            # Allow R1 only if rule focus explicitly wants resistors and note mentions 电阻位号
            if u.upper() == "R1" and ("电阻" in name or "电阻" in note):
                pass
            elif u.upper() == "R1":
                return
            else:
                return
        seen.add(u.upper())
        out.append(u)

    # Rule-specific hardware focus first
    for x in RULE_FOCUS.get(rid) or []:
        add(x)
    for x in note_refs:
        add(x)
    for x in refs + list(mpns):
        add(str(x))
    fam = rid.split("-")[0] if rid else ""
    for seed in FAMILY_SEEDS.get(fam) or []:
        add(seed)
    return out[:24]


def titleblock_zone(page: fitz.Page) -> fitz.Rect:
    """Approximate title-block / border zone (bottom-right strip on A4 schematics)."""
    pr = page.rect
    return fitz.Rect(pr.x0 + pr.width * 0.45, pr.y0 + pr.height * 0.72, pr.x1, pr.y1)


def is_titleblock_noise_hit(page: fitz.Page, rect: fitz.Rect, kw: str) -> bool:
    """Reject hits that are revision/page-label text, not real refdes."""
    zone = titleblock_zone(page)
    cx = (rect.x0 + rect.x1) / 2
    cy = (rect.y0 + rect.y1) / 2
    in_tb = zone.x0 <= cx <= zone.x1 and zone.y0 <= cy <= zone.y1
    # Expand context around the hit
    ctx = fitz.Rect(rect.x0 - 80, rect.y0 - 14, rect.x1 + 80, rect.y1 + 14)
    try:
        txt = page.get_text("text", clip=ctx) or ""
    except Exception:
        txt = ""
    if TITLEBLOCK_NOISE_RE.search(txt):
        # R1 inside "重建-R1" / 版本 cell
        if re.fullmatch(r"R\d{1,2}", kw, re.I) or re.fullmatch(r"P\d{1,2}", kw, re.I):
            return True
        if in_tb and re.fullmatch(r"[A-Z]{1,3}\d{1,4}", kw, re.I):
            # refdes-looking token inside noisy title context
            if re.search(rf"(重建[-_]?|版本\s*|Version\s*|Rev\.?\s*){re.escape(kw)}", txt, re.I):
                return True
    # Bare R1/P01 in titleblock without nearby capacitor/resistor value → noise
    if in_tb and re.fullmatch(r"R\d{1,2}", kw, re.I):
        if not re.search(r"(\d+\s*[kKmMuU]?[ΩohmΩ])|(\d+\s*[npuµu]F)", txt, re.I):
            return True
    return False


_REFDES_KW_RE = re.compile(r"^[A-Z]{1,3}\d{1,4}$", re.I)
_VALUE_WORD_RE = re.compile(
    r"^("
    r"\d+(\.\d+)?\s*[kKmMuUnNpPµu][fFwWvVΩ]?"  # 47uF / 4.7u / 10k
    r"|\d+(\.\d+)?\s*([ΩFfWwVv]|ohm)"  # with explicit unit
    r"|\d+[kKmM]"  # 10K
    r"|DNP|NC"
    r")$",
    re.I,
)


def is_refdes_keyword(kw: str) -> bool:
    return bool(_REFDES_KW_RE.fullmatch((kw or "").strip()))


def expand_highlight_context(page: fitz.Page, seed: fitz.Rect, *, pad: float = 3.0) -> fitz.Rect:
    """Tight box: symbol (usually left of OrCAD label) + refdes + value/MPN."""
    # Fixed-point pull left/down so the graphic is inside, not just the text
    r = fitz.Rect(
        seed.x0 - 34.0,
        seed.y0 - 6.0,
        seed.x1 + 10.0,
        seed.y1 + 26.0,
    )
    cx = (seed.x0 + seed.x1) / 2
    cy = (seed.y0 + seed.y1) / 2
    try:
        words = page.get_text("words") or []
    except Exception:
        words = []
    for w in words:
        if len(w) < 5:
            continue
        wx0, wy0, wx1, wy1, txt = float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4])
        if not txt or len(txt) > 28:
            continue
        wcx, wcy = (wx0 + wx1) / 2, (wy0 + wy1) / 2
        if abs(wcx - cx) > 55 or abs(wcy - cy) > 48:
            continue
        wr = fitz.Rect(wx0, wy0, wx1, wy1)
        if wr.intersects(seed) or _VALUE_WORD_RE.match(txt):
            r |= wr
        elif is_refdes_keyword(txt) and abs(wcx - cx) <= 20 and abs(wcy - cy) <= 22:
            r |= wr
        elif 3 <= len(txt) <= 28 and re.search(r"[A-Za-z].*\d|\d.*[A-Za-z]", txt):
            if 0 <= (wcy - cy) <= 44 and abs(wcx - cx) <= 48:
                r |= wr
    max_w, max_h = 64.0, 52.0
    if r.width > max_w:
        # Keep left of label (symbol side) when shrinking
        left = seed.x0 - 32.0
        r = fitz.Rect(left, r.y0, left + max_w, r.y1)
    if r.height > max_h:
        top = seed.y0 - 5.0
        r = fitz.Rect(r.x0, top, r.x1, top + max_h)
    return clamp_rect(r, page)


def merge_tight_highlights(rects: list[fitz.Rect], *, merge_dist: float = 8.0) -> list[fitz.Rect]:
    """Collapse near-duplicate boxes only; keep separate parts as separate frames."""
    if not rects:
        return []
    remaining = [fitz.Rect(r) for r in rects]
    merged: list[fitz.Rect] = []
    while remaining:
        cur = remaining.pop(0)
        changed = True
        while changed:
            changed = False
            nxt = []
            for other in remaining:
                ox = (other.x0 + other.x1) / 2
                oy = (other.y0 + other.y1) / 2
                cx = (cur.x0 + cur.x1) / 2
                cy = (cur.y0 + cur.y1) / 2
                inter = cur & other
                heavy = (not inter.is_empty) and inter.get_area() >= 0.4 * min(cur.get_area(), other.get_area())
                near_dup = abs(ox - cx) <= merge_dist and abs(oy - cy) <= merge_dist
                if heavy or near_dup:
                    cur |= other
                    changed = True
                else:
                    nxt.append(other)
            remaining = nxt
        merged.append(cur)
    return merged[:10]


def score_page_for_keywords(page: fitz.Page, keywords: list[str], priority: list[str] | None = None) -> tuple[int, list[str]]:
    if not keywords:
        return 0, []
    try:
        text = page.get_text("text") or ""
    except Exception:
        text = ""
    pri = {k.upper() for k in (priority or [])}
    hits = []
    score = 0
    for kw in keywords:
        if not kw or len(kw) < 2:
            continue
        found_raw = page.search_for(kw) or []
        found = [r for r in found_raw if not is_titleblock_noise_hit(page, r, kw)]
        w = 8 if kw.upper() in pri else 3
        if found:
            score += w + min(3, len(found))
            hits.append(kw)
        elif kw in text and not TITLEBLOCK_NOISE_RE.search(text[max(0, text.find(kw) - 20) : text.find(kw) + 40] if kw in text else ""):
            # textual presence but may be title noise — discount heavily for R#
            if re.fullmatch(r"R\d{1,2}", kw, re.I):
                continue
            score += max(1, w - 2)
            hits.append(kw)
    return score, hits


def pick_best_page_index(
    doc: fitz.Document,
    pack: dict,
    prefer_refs: list[str] | None = None,
    forbid_page: int | None = None,
) -> tuple[int, list[str], int]:
    kws = collect_pack_keywords(pack)
    note_refs = REF_RE.findall(str(pack.get("note") or "") + "\n" + str(pack.get("name") or ""))
    note_refs = [r for r in note_refs if not (r.upper() == "R1" and "电阻" not in str(pack.get("name") or ""))]
    if prefer_refs:
        note_refs = list(prefer_refs) + [r for r in note_refs if r not in prefer_refs]
        for r in reversed(prefer_refs):
            if r not in kws:
                kws.insert(0, r)
    best_i, best_score, best_hits = 0, -1, []
    for i in range(doc.page_count):
        if forbid_page is not None and (i + 1) == int(forbid_page):
            continue
        sc, hits = score_page_for_keywords(doc[i], kws, priority=note_refs)
        if prefer_refs:
            text_hits = []
            for r in prefer_refs:
                raw = doc[i].search_for(r) or []
                ok = [x for x in raw if not is_titleblock_noise_hit(doc[i], x, r)]
                if ok:
                    text_hits.append(r)
            sc += 20 * len(text_hits)
        # Penalize page-1 overview unless it's the only hit
        if i == 0 and doc.page_count > 1 and sc > 0 and not prefer_refs:
            sc = max(0, sc - 3)
        if sc > best_score:
            best_i, best_score, best_hits = i, sc, hits
    return best_i, best_hits, best_score



def vote_region(names: list[str], regions: list[dict], focus: fitz.Rect | None) -> str | None:
    """Pick a region label only if it was actually discovered on this page."""
    present = {r.get("name") for r in (regions or []) if r.get("name")}
    if not present:
        return None
    for n in names:
        mapped = REF_TO_REGION.get(n) or REF_TO_REGION.get(str(n).upper())
        if mapped and mapped in present:
            return mapped
        if n in present:
            return n
    if focus is not None:
        near = nearest_region(regions, focus)
        if near in present:
            return near
    return None



def load_json(p: Path) -> dict:
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8-sig"))


def save_json(p: Path, obj: object) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _pick_best_pdf(pdfs: list[Path]) -> Path | None:
    """Prefer 拆分/分頁 SCH over generic; then multi-page; then largest."""
    if not pdfs:
        return None

    def score(p: Path) -> tuple:
        name = p.name
        prefer = 0
        if "拆分" in name or "fenye" in name.lower() or "split" in name.lower():
            prefer += 20
        if name.upper().startswith("SCH") or "原理图" in name or "SCH_" in name.upper():
            prefer += 10
        if "原版" in name and "拆分" not in name:
            prefer -= 5
        try:
            pages = __import__("fitz").open(str(p)).page_count
        except Exception:
            pages = 0
        return (prefer, pages, p.stat().st_size)

    return sorted(pdfs, key=score, reverse=True)[0]


def _path_ok(raw: object, kind: str = "file") -> bool:
    """旧产物里的网络路径换机后可能连不上；Windows 会抛 OSError，按不存在处理。"""
    try:
        p = Path(str(raw))
        return p.is_dir() if kind == "dir" else p.exists()
    except (OSError, ValueError):
        return False


def find_schematic_pdf(out_dir: Path) -> Path | None:
    # 1) expert-html style inventory
    inv = load_json(out_dir / "facts" / "inventory.json")
    files = inv.get("files") or {}
    if isinstance(files, dict):
        cand = files.get("pdf")
        if cand and _path_ok(cand):
            return Path(str(cand))
    input_dir = inv.get("input_dir")
    if input_dir and _path_ok(input_dir, "dir"):
        picked = _pick_best_pdf(sorted(Path(str(input_dir)).glob("*.pdf")))
        if picked:
            return picked

    # 2) product-center input_inventory.json
    pin = load_json(out_dir / "input_inventory.json")
    pin_dir = pin.get("input_dir")
    if pin_dir and _path_ok(pin_dir, "dir"):
        picked = _pick_best_pdf(sorted(Path(str(pin_dir)).glob("*.pdf")))
        if picked:
            return picked

    # 3) board_facts.json may carry input_dir / pdf
    bf = load_json(out_dir / "facts" / "board_facts.json")
    for key in ("input_dir", "board_dir", "source_dir"):
        d = bf.get(key)
        if d and _path_ok(d, "dir"):
            picked = _pick_best_pdf(sorted(Path(str(d)).glob("*.pdf")))
            if picked:
                return picked
    for key in ("pdf", "schematic_pdf", "sch_pdf"):
        cand = bf.get(key)
        if cand and _path_ok(cand):
            return Path(str(cand))

    # 4) default: sibling of review_out (materials folder)
    parent = out_dir.parent
    if parent.is_dir():
        picked = _pick_best_pdf(sorted(parent.glob("*.pdf")))
        if picked:
            return picked
    return None


def discover_regions(page: fitz.Page) -> list[dict]:
    """Find named design-block titles on the page."""
    regions = []
    for label in REGION_LABELS:
        hits = page.search_for(label)
        if not hits:
            # try loose match without punctuation
            loose = label.replace("+", "").replace("/", "")
            hits = page.search_for(loose) if loose != label else []
        if not hits:
            continue
        # title may be split across adjacent rects — take union of first cluster
        r0 = hits[0]
        for h in hits[1:]:
            if abs(h.y0 - r0.y0) < 30 and abs(h.x0 - r0.x1) < 80:
                r0 |= h
        regions.append({"name": label, "title_rect": fitz.Rect(r0)})
    return regions


def union_rects(rects: list[fitz.Rect], pad: float = 0.0) -> fitz.Rect | None:
    if not rects:
        return None
    u = fitz.Rect(rects[0])
    for r in rects[1:]:
        u |= r
    if pad:
        u = fitz.Rect(u.x0 - pad, u.y0 - pad, u.x1 + pad, u.y1 + pad)
    return u


def clamp_rect(r: fitz.Rect, page: fitz.Page) -> fitz.Rect:
    pr = page.rect
    return fitz.Rect(
        max(pr.x0, r.x0),
        max(pr.y0, r.y0),
        min(pr.x1, r.x1),
        min(pr.y1, r.y1),
    )


def expand_to_block(page: fitz.Page, seed: fitz.Rect, *, min_w: float = 150, min_h: float = 110, pad: float = 22) -> fitz.Rect:
    """Grow a seed hit into a readable local crop (tight; never into title block)."""
    pr = page.rect
    tb = titleblock_zone(page)
    cx = (seed.x0 + seed.x1) / 2
    cy = (seed.y0 + seed.y1) / 2
    half_w = max(min_w / 2, (seed.width / 2) + pad + 16)
    half_h = max(min_h / 2, (seed.height / 2) + pad + 16)
    # Slight bias downward (values often under refdes); keep crop local
    r = fitz.Rect(cx - half_w, cy - pad * 0.55, cx + half_w, cy + half_h * 1.25)
    # Cap crop — prefer local parts over half-page panels
    max_w, max_h = pr.width * 0.42, pr.height * 0.36
    if r.width > max_w:
        mid = (r.x0 + r.x1) / 2
        r = fitz.Rect(mid - max_w / 2, r.y0, mid + max_w / 2, r.y1)
    if r.height > max_h:
        r = fitz.Rect(r.x0, r.y0, r.x1, r.y0 + max_h)
    r = clamp_rect(r, page)
    if r.y1 > tb.y0 - 2:
        r = fitz.Rect(r.x0, r.y0, r.x1, max(r.y0 + 40, tb.y0 - 4))
    return clamp_rect(r, page)


def filter_circuit_rects(page: fitz.Page, rects: list[fitz.Rect]) -> list[fitz.Rect]:
    """Drop highlights that land in the title-block / border metadata zone."""
    tb = titleblock_zone(page)
    out = []
    for r in rects:
        cx = (r.x0 + r.x1) / 2
        cy = (r.y0 + r.y1) / 2
        if tb.x0 <= cx <= tb.x1 and tb.y0 <= cy <= tb.y1:
            continue
        out.append(r)
    return out


def densest_cluster(rects: list[fitz.Rect], max_span: float = 280.0) -> list[fitz.Rect]:
    """Pick a spatially tight subset so one ref's distant duplicates don't widen the crop."""
    if len(rects) <= 1:
        return list(rects)
    best: list[fitz.Rect] = [rects[0]]
    best_score = -1.0
    for i, a in enumerate(rects):
        cluster = [a]
        for j, b in enumerate(rects):
            if i == j:
                continue
            if abs(((a.x0 + a.x1) / 2) - ((b.x0 + b.x1) / 2)) <= max_span and abs(
                ((a.y0 + a.y1) / 2) - ((b.y0 + b.y1) / 2)
            ) <= max_span:
                cluster.append(b)
        # score: more points, smaller bbox
        u = union_rects(cluster)
        area = max(1.0, u.width * u.height) if u else 1.0
        score = len(cluster) * 10000.0 / area
        if score > best_score:
            best_score = score
            best = cluster
    return best


def search_keywords(page: fitz.Page, keywords: list[str]) -> tuple[list[fitz.Rect], list[str]]:
    found_rects: list[fitz.Rect] = []
    found_names: list[str] = []
    seen = set()
    for kw in keywords:
        kw = (kw or "").strip()
        if not kw or len(kw) < 2:
            continue
        key = kw.upper()
        if key in seen:
            continue
        hits = page.search_for(kw)
        if not hits and " " in kw:
            hits = page.search_for(kw.split()[0])
        if not hits and "/" in kw:
            hits = page.search_for(kw.split("/")[0])
        if not hits:
            continue
        clean = [r for r in hits if not is_titleblock_noise_hit(page, r, kw)]
        if not clean:
            continue
        seen.add(key)
        found_names.append(kw)
        found_rects.extend(densest_cluster(list(clean)[:12]))
    return found_rects, found_names


def keywords_for_pack(pack: dict, parent_id: int | None) -> tuple[list[str], list[str]]:
    """Return (primary_keywords, fallback_keywords).

    Primary = extracted refs + regions mentioned in this pack's text.
    Fallback = parent defaults, used only when primary misses on the page.
    """
    primary: list[str] = []
    rid = str(pack.get("rule_id") or "")
    for kw in RULE_FOCUS.get(rid) or []:
        primary.append(kw)
    for kw in pack.get("_shot_prefer_keywords") or []:
        if kw:
            primary.append(str(kw))
    prefer = collect_pack_keywords(pack)
    for kw in prefer:
        primary.append(kw)
    for ex in pack.get("extracted") or []:
        oid = str(ex.get("object_id") or "").strip()
        val = str(ex.get("value") or "").strip()
        if oid and oid.lower() not in {"board", "n/a", "na", "c*", "focus"}:
            for part in re.split(r"[/|,，、\s]+", oid):
                part = part.strip()
                if part and len(part) >= 2 and part.upper() not in {"C*", "R*", "U*"}:
                    primary.append(part)
        for m in REF_RE.findall(val):
            if m.upper() != "R1" or "电阻" in str(pack.get("name") or ""):
                primary.append(m)
        for m in MPN_HINT_RE.findall(val):
            primary.append(m)

    step_bits = []
    for s in pack.get("steps") or []:
        if isinstance(s, dict):
            step_bits.append(str(s.get("observation") or ""))
            step_bits.append(str(s.get("action") or ""))
            step_bits.append(str(s.get("decision") or ""))
        else:
            step_bits.append(str(s))
    blob = " ".join(
        [
            str(pack.get("why") or ""),
            str(pack.get("note") or ""),
            str(pack.get("standard") or ""),
            str(pack.get("remediation") or ""),
            " ".join(step_bits),
        ]
    )
    regions_mentioned = [label for label in REGION_LABELS if label in blob]
    for label in regions_mentioned:
        primary.insert(0, label)
    for m in REF_RE.findall(blob):
        if m not in primary and not (m.upper() == "R1" and "电阻" not in str(pack.get("name") or "")):
            primary.append(m)
    for m in MPN_HINT_RE.findall(blob):
        if m not in primary:
            primary.append(m)

    # Dedup preserve order
    seen = set()
    prim_out = []
    for k in primary:
        ku = k.upper()
        if ku in seen:
            continue
        seen.add(ku)
        prim_out.append(k)

    fallback = list(PARENT_FOCUS.get(int(parent_id) if parent_id is not None else -1, []))
    fam = rid.split("-")[0] if rid else ""
    fallback = list(FAMILY_SEEDS.get(fam) or []) + fallback
    # Semantic bias for this board
    if any(x in blob for x in ("钽", "耐压", "电容", "C18", "C2", "降额")):
        fallback = SHEET_FOCUS["power"] + fallback
    if any(x in blob for x in ("负载开关", "软启动", "限流", "可插拔", "F1", "浪涌", "反接")):
        fallback = SHEET_FOCUS["power"] + fallback
    if any(x in blob for x in ("连接器", "防反插", "CN1", "CN2", "ESD")):
        fallback = ["CN1", "CN2"] + SHEET_FOCUS["power"] + fallback
    if any(x in blob for x in ("电阻", "功耗", "上拉")):
        fallback = ["R10", "R17", "R21", "R38", "+12V"] + fallback
    if any(x in blob for x in ("SPDL", "钳位", "DS_ORI", "输入")):
        fallback = SHEET_FOCUS["input"] + fallback
    if any(x in blob for x in ("晶振", "时钟")):
        fallback = ["Y1", "X1", "晶振"] + fallback
    if any(x in blob for x in ("复位", "RESET", "MCU")):
        fallback = ["RESET", "NRST"] + fallback

    fb_out = []
    seen2 = set(x.upper() for x in prim_out)
    for k in fallback:
        ku = k.upper()
        if ku in seen2:
            continue
        seen2.add(ku)
        fb_out.append(k)
    return prim_out[:14], fb_out[:10]


def nearest_region(regions: list[dict], focus: fitz.Rect) -> str | None:
    if not regions:
        return None
    cx = (focus.x0 + focus.x1) / 2
    cy = (focus.y0 + focus.y1) / 2
    best = None
    best_d = 1e18
    for reg in regions:
        tr: fitz.Rect = reg["title_rect"]
        dx = cx - (tr.x0 + tr.x1) / 2
        dy = cy - (tr.y0 + tr.y1) / 2
        d = (dx * dx) + (dy * dy) * 0.6
        if dy < -40:
            d += 80000
        if d < best_d:
            best_d = d
            best = reg["name"]
    return best


def render_focus(
    pdf_path: Path,
    page_index: int,
    out_path: Path,
    clip: fitz.Rect,
    highlights: list[fitz.Rect],
    *,
    dpi: int,
    caption: str,
) -> dict:
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_index]
        for rect in highlights[:10]:
            # Thin precise frames on the part — not panel-sized boxes
            page.draw_rect(rect, color=(1, 0.12, 0.12), width=0.9)
        if caption:
            # Compact caption strip — do not span empty crop width
            cap_w = min(210.0, max(90.0, clip.width * 0.55))
            box = fitz.Rect(
                clip.x0 + 3,
                max(clip.y0 + 2, 2),
                min(clip.x0 + cap_w, clip.x1 - 3),
                clip.y0 + 16,
            )
            page.draw_rect(box, color=(1, 1, 0.85), fill=(1, 1, 0.85), width=0.4)
            try:
                page.insert_textbox(box, caption[:48], fontsize=7, fontname="china-s", color=(0.15, 0.1, 0.1))
            except Exception:
                page.insert_textbox(box, caption[:48], fontsize=7, color=(0.15, 0.1, 0.1))
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(out_path))
        return {
            "screenshot": f"evidence/shots/{out_path.name}",
            "width": pix.width,
            "height": pix.height,
            "clip": [round(clip.x0, 1), round(clip.y0, 1), round(clip.x1, 1), round(clip.y1, 1)],
        }
    finally:
        doc.close()


def render_full_pages(pdf_path: Path, shots_dir: Path, dpi: int) -> list[dict]:
    shots_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    pages = []
    try:
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        for i in range(doc.page_count):
            pix = doc[i].get_pixmap(matrix=mat, alpha=False)
            name = f"schematic_page_{i + 1:02d}.png"
            out_png = shots_dir / name
            pix.save(str(out_png))
            pages.append(
                {
                    "page": f"PAGE{i + 1}",
                    "page_index": i + 1,
                    "pdf": pdf_path.name,
                    "screenshot": f"evidence/shots/{name}",
                    "width": pix.width,
                    "height": pix.height,
                }
            )
    finally:
        doc.close()
    return pages


def build_focus_for_pack(
    pack: dict,
    pdf_path: Path,
    page: fitz.Page,
    page_index: int,
    regions: list[dict],
    shots_dir: Path,
    dpi: int,
    *,
    out_suffix: str = "",
) -> dict:
    rid = pack.get("rule_id") or "UNKNOWN"
    parent_id = pack.get("parent_id")
    try:
        parent_id = int(parent_id) if parent_id is not None else None
    except Exception:
        parent_id = None

    primary, fallback = keywords_for_pack(pack, parent_id)
    hit_rects, hit_names = search_keywords(page, primary)
    used_fallback = False
    if len(hit_rects) < 1:
        hit_rects, hit_names = search_keywords(page, fallback)
        used_fallback = True
    elif len([n for n in hit_names if n not in REGION_LABELS]) < 1 and fallback:
        # only region title from primary — add a few concrete fallback refs nearby
        extra_r, extra_n = search_keywords(page, fallback[:4])
        hit_rects = hit_rects + extra_r
        hit_names = hit_names + [n for n in extra_n if n not in hit_names]

    region_name = None
    for name in hit_names:
        if name in REGION_LABELS:
            region_name = name
            break
    if not region_name:
        region_name = vote_region(hit_names, regions, None)

    # Prefer cropping around concrete *refdes* (not values like 47uF — those inflate frames)
    concrete = []
    concrete_names = []
    # Prefer-list first: when set, frame those parts only (avoids C* pollution on resistor items)
    prefer_first = []
    for kw in pack.get("_shot_prefer_keywords") or []:
        if is_refdes_keyword(str(kw)) or re.fullmatch(r"CN\d+", str(kw) or "", re.I):
            prefer_first.append(str(kw))
    prefer_first = list(dict.fromkeys(prefer_first))
    name_order = prefer_first + [n for n in hit_names if n not in prefer_first]
    for kw in name_order:
        if kw in REGION_LABELS:
            continue
        if not is_refdes_keyword(kw) and not re.fullmatch(r"CN\d+", kw or "", re.I):
            continue
        rs, _ = search_keywords(page, [kw])
        concrete.extend(rs)
        if rs:
            concrete_names.append(kw)
        # If prefer list is set, stop after those that hit (plus at most 2 extras)
        if prefer_first and len([n for n in concrete_names if n.upper() in {p.upper() for p in prefer_first}]) >= len(prefer_first):
            break
        if prefer_first and len(concrete_names) >= max(len(prefer_first), 4):
            break
    # If prefer set but none found on this page, fall through to any refdes hits
    if prefer_first and not concrete_names:
        for kw in hit_names:
            if kw in REGION_LABELS:
                continue
            if not is_refdes_keyword(kw) and not re.fullmatch(r"CN\d+", kw or "", re.I):
                continue
            rs, _ = search_keywords(page, [kw])
            concrete.extend(rs)
            if rs:
                concrete_names.append(kw)
    # If no refdes hits, fall back to any non-region keyword (nets / values) for page location only
    locator_rects: list[fitz.Rect] = []
    if not concrete:
        for kw in hit_names:
            if kw in REGION_LABELS:
                continue
            rs, _ = search_keywords(page, [kw])
            locator_rects.extend(rs)
            if rs and kw not in concrete_names:
                concrete_names.append(kw)
        concrete = locator_rects

    # If we know the target region, keep only hits near that region title
    if region_name and region_name in REGION_LABELS and concrete:
        title_hits, _ = search_keywords(page, [region_name])
        if title_hits:
            tx = (title_hits[0].x0 + title_hits[0].x1) / 2
            ty = (title_hits[0].y0 + title_hits[0].y1) / 2
            near = []
            for r in concrete:
                cx = (r.x0 + r.x1) / 2
                cy = (r.y0 + r.y1) / 2
                if abs(cx - tx) < 320 and -40 < (cy - ty) < 380:
                    near.append(r)
            if near:
                concrete = near

    if concrete:
        # One tight frame per unique refdes; multi-hit refs snap to peer cluster
        per_ref: list[fitz.Rect] = []
        placed_centers: list[tuple[float, float]] = []
        seen_ref = set()

        def _pick_hit(rs: list[fitz.Rect]) -> fitz.Rect:
            if len(rs) == 1:
                return rs[0]

            def nearby_value(r: fitz.Rect) -> bool:
                try:
                    words = page.get_text(
                        "words",
                        clip=fitz.Rect(r.x0 - 50, r.y0 - 20, r.x1 + 70, r.y1 + 45),
                    ) or []
                except Exception:
                    words = []
                for w in words:
                    if len(w) >= 5 and _VALUE_WORD_RE.match(str(w[4])):
                        return True
                return False

            def score(r: fitz.Rect) -> tuple:
                cx = (r.x0 + r.x1) / 2
                cy = (r.y0 + r.y1) / 2
                # Prefer refdes that sit next to a value (real part), not net-label collisions
                val = 0 if nearby_value(r) else 1
                if placed_centers:
                    peer = min((cx - px) ** 2 + (cy - py) ** 2 for px, py in placed_centers)
                    # Soft y-align with peers (same row of caps)
                    y_pen = min(abs(cy - py) for _, py in placed_centers)
                else:
                    peer, y_pen = 0.0, 0.0
                return (val, peer + y_pen * 40.0)

            return min(rs, key=score)

        # Unique-hit refs first so multi-hit (e.g. C2) can snap next to C1/C3
        unique_first: list[str] = []
        multi_hit: list[str] = []
        for kw in concrete_names:
            if not is_refdes_keyword(kw) and not re.fullmatch(r"CN\d+", kw or "", re.I):
                continue
            rs = filter_circuit_rects(page, list(search_keywords(page, [kw])[0]))
            (unique_first if len(rs) <= 1 else multi_hit).append(kw)

        for kw in unique_first + multi_hit:
            ku = (kw or "").upper()
            if ku in seen_ref:
                continue
            rs, _ = search_keywords(page, [kw])
            rs = filter_circuit_rects(page, list(rs))
            if not rs:
                continue
            seen_ref.add(ku)
            pick = _pick_hit(rs)
            box = expand_highlight_context(page, pick)
            per_ref.append(box)
            placed_centers.append(((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2))
        if not per_ref:
            ref_seeds = densest_cluster(concrete[:16], max_span=220.0)
            per_ref = [expand_highlight_context(page, r) for r in ref_seeds[:8]]
        expanded = merge_tight_highlights(filter_circuit_rects(page, per_ref))
        if not expanded:
            expanded = merge_tight_highlights([expand_highlight_context(page, concrete[0])])
        seed = union_rects(expanded) or expanded[0]
        # Reject crop seed entirely inside titleblock — shift to page center circuitry
        tb = titleblock_zone(page)
        scx = (seed.x0 + seed.x1) / 2
        scy = (seed.y0 + seed.y1) / 2
        if tb.x0 <= scx <= tb.x1 and tb.y0 <= scy <= tb.y1:
            alt = []
            for kw in (primary or fallback)[:10]:
                if not is_refdes_keyword(kw or "") and not re.fullmatch(r"CN\d+", kw or "", re.I):
                    continue
                if re.fullmatch(r"R1", kw or "", re.I):
                    continue
                rs, _ = search_keywords(page, [kw])
                for r in rs:
                    cx = (r.x0 + r.x1) / 2
                    cy = (r.y0 + r.y1) / 2
                    if not (tb.x0 <= cx <= tb.x1 and tb.y0 <= cy <= tb.y1):
                        alt.append(expand_highlight_context(page, r))
            alt = merge_tight_highlights(filter_circuit_rects(page, alt))
            if alt:
                expanded = alt[:8]
                seed = union_rects(expanded) or expanded[0]
                concrete_names = [n for n in (primary or hit_names) if is_refdes_keyword(n) and n.upper() != "R1"][:8]
        # Crop = union of all framed parts + small margin (do NOT densest-shrink
        # multi-ref prefer sets — that dropped C2/C3 and left only C1).
        margin = 16.0
        clip = union_rects(expanded, pad=margin) or expand_to_block(page, seed, min_w=110, min_h=80, pad=12)
        if clip.width < 88:
            mid = (clip.x0 + clip.x1) / 2
            clip = fitz.Rect(mid - 44, clip.y0, mid + 44, clip.y1)
        if clip.height < 68:
            mid = (clip.y0 + clip.y1) / 2
            clip = fitz.Rect(clip.x0, mid - 34, clip.x1, mid + 34)
        pr = page.rect
        # Only collapse when the union is absurdly large AND we have no multi-prefer intent
        prefer_n = sum(1 for n in concrete_names if is_refdes_keyword(n))
        if prefer_n <= 1 and clip.width > pr.width * 0.45:
            tight = densest_cluster(expanded, max_span=150.0)
            clip = union_rects(tight, pad=margin) or clip
        if prefer_n <= 1 and clip.height > pr.height * 0.40:
            tight = densest_cluster(expanded, max_span=130.0)
            clip = union_rects(tight, pad=margin) or clip
        clip = clamp_rect(clip, page)
        if not region_name:
            region_name = vote_region(concrete_names + hit_names, regions, clip)
        highlight = filter_circuit_rects(page, expanded[:8])
    elif hit_rects:
        ref_only = [r for r, n in zip(hit_rects, hit_names) if is_refdes_keyword(n)] or hit_rects[:4]
        expanded = merge_tight_highlights(
            filter_circuit_rects(page, [expand_highlight_context(page, r) for r in ref_only[:8]])
            or [expand_highlight_context(page, hit_rects[0])]
        )
        seed = union_rects(expanded) or expanded[0]
        clip = union_rects(expanded, pad=18) or expand_to_block(page, seed, min_w=110, min_h=80, pad=12)
        clip = clamp_rect(clip, page)
        if not region_name:
            region_name = vote_region(hit_names, regions, clip)
        highlight = filter_circuit_rects(page, expanded[:8])
    else:
        # Prefer upper/left circuitry, not titleblock
        pr = page.rect
        tb = titleblock_zone(page)
        clip = fitz.Rect(pr.x0 + 40, pr.y0 + 40, pr.x0 + pr.width * 0.55, min(pr.y0 + pr.height * 0.5, tb.y0 - 4))
        region_name = "整页局部（未命中具体位号）"
        highlight = []

    # Never let clip swallow title block
    tb = titleblock_zone(page)
    if clip.y1 > tb.y0 - 2:
        clip = fitz.Rect(clip.x0, clip.y0, clip.x1, max(clip.y0 + 50, tb.y0 - 4))
    clip = clamp_rect(clip, page)
    highlight = merge_tight_highlights(filter_circuit_rects(page, highlight))
    # Drop highlights that swallowed most of the crop (panel-sized mistakes)
    tight_hl = []
    for h in highlight:
        if clip.width > 1 and (h.width > clip.width * 0.92 or h.height > clip.height * 0.92):
            continue
        if h.width > 120 or h.height > 95:
            continue
        tight_hl.append(h)
    highlight = tight_hl or highlight[:4]

    # If region known (legacy Chinese titles only), bias clip to include region title
    if region_name in REGION_LABELS and region_name not in {
        "P01_POWER", "P02", "P03", "P04", "P05", "P06", "P07", "P08", "P09", "P10"
    }:
        tr, _ = search_keywords(page, [region_name])
        if tr:
            clip = clamp_rect(union_rects([clip, expand_to_block(page, tr[0], min_w=140, min_h=100, pad=14)]) or clip, page)
            if clip.y1 > tb.y0 - 2:
                clip = fitz.Rect(clip.x0, clip.y0, clip.x1, tb.y0 - 4)

    # Caption must only list refs whose glyphs fall inside the final crop
    # (avoids "R17 / C1 / C2 / C3" when crop only contains R17).
    candidates = [
        n
        for n in (concrete_names[:12] or [n for n in hit_names if n not in REGION_LABELS][:12])
        if n.upper() != "R1" and n not in REGION_LABELS
    ]
    show: list[str] = []
    for n in candidates:
        rs, _ = search_keywords(page, [n])
        if any(r.intersects(clip) for r in rs):
            show.append(n)
        if len(show) >= 4:
            break
    caption = f"{rid} · {region_name or '局部'}"
    if show:
        caption = f"{rid} · {'/'.join(show[:4])}"
    suffix = (out_suffix or "").strip()
    if suffix and not suffix.startswith("_"):
        suffix = "_" + suffix
    out_png = shots_dir / f"{rid}_focus{suffix}.png"
    meta = render_focus(
        pdf_path,
        page_index,
        out_png,
        clip,
        highlight,
        dpi=dpi,
        caption=caption,
    )

    focus_label = region_name or "设计局部"
    note_bits = []
    if region_name and not str(region_name).startswith("整页"):
        note_bits.append(f"功能区「{region_name}」")
    if show:
        note_bits.append("定位：" + "、".join(show))
    if used_fallback and not primary:
        note_bits.append("按大类默认区定位")
    note = "；".join(note_bits) if note_bits else "按本项线索裁切原理图局部"

    return {
        "page": f"PAGE{page_index + 1} / {focus_label}",
        "status": pack.get("verdict") or "INSUFFICIENT_EVIDENCE",
        "note": note,
        "screenshot": meta["screenshot"],
        "pdf": pdf_path.name,
        "page_index": page_index + 1,
        "region": focus_label,
        "focus_keywords": ([region_name] if region_name and region_name in REGION_LABELS else []) + show,
        "clip": meta["clip"],
        "crop": True,
    }


MAX_FOCUS_CROPS = 5  # local crops per item (plus optional full-page index)


def _collect_pack_refdes(pack: dict) -> list[str]:
    prefer = list(pack.get("_shot_prefer_keywords") or [])
    blob = " ".join(
        [
            str(pack.get("note") or ""),
            str(pack.get("name") or ""),
            json.dumps(pack.get("steps") or [], ensure_ascii=False)[:2000],
            " ".join(str(x) for x in prefer),
        ]
    )
    # Avoid sucking rule-id tails like P05-A10 → A10
    blob = re.sub(r"\bP\d{2}-A\d{2}\b", " ", blob)
    refs = []
    for m in re.finditer(
        r"\b((?:CN|TP|FB|SW|LED|RV|RT|BZ|BT)[0-9]{1,3}|[RUCLDQFJHYXTK][0-9]{1,3})\b",
        blob,
        re.I,
    ):
        refs.append(m.group(1).upper())
    for r in prefer:
        rs = str(r)
        if is_refdes_keyword(rs) or re.fullmatch(r"CN\d+", rs or "", re.I):
            refs.append(rs.upper())
    seen = set()
    out = []
    for r in refs:
        u = r.upper()
        # Skip lone revision-like / titleblock-prone R1 unless explicitly preferred
        if u == "R1" and "R1" not in {str(x).upper() for x in prefer}:
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def cluster_refs_on_page(
    page: fitz.Page,
    refs: list[str],
    *,
    max_span: float = 200.0,
    max_clusters: int = MAX_FOCUS_CROPS,
) -> list[list[str]]:
    """Spatially cluster refdes hits so each cluster can get its own crop."""
    points: list[tuple[str, float, float]] = []
    for ref in refs:
        rs, _ = search_keywords(page, [ref])
        rs = filter_circuit_rects(page, list(rs)[:8])
        if not rs:
            continue
        cluster_rs = densest_cluster(rs, max_span=max_span)
        r = cluster_rs[0]
        points.append((ref, (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
    if not points:
        return []
    clusters: list[list[tuple[str, float, float]]] = []
    for ref, cx, cy in points:
        placed = False
        for cl in clusters:
            if any(abs(cx - x) <= max_span and abs(cy - y) <= max_span for _r, x, y in cl):
                cl.append((ref, cx, cy))
                placed = True
                break
        if not placed:
            clusters.append([(ref, cx, cy)])
    clusters.sort(key=len, reverse=True)
    return [[t[0] for t in cl] for cl in clusters[:max_clusters]]


def region_targets_on_page(pack: dict, page: fitz.Page, regions: list[dict]) -> list[tuple[str, list[str]]]:
    """If multiple named schematic regions are relevant, return (region, nearby_refs)."""
    blob = f"{pack.get('note') or ''} {pack.get('name') or ''}"
    present = {r.get("name") for r in (regions or []) if r.get("name")}
    wanted = [lab for lab in REGION_LABELS if lab in blob and lab in present]
    if len(wanted) < 2:
        return []
    out: list[tuple[str, list[str]]] = []
    pack_refs = _collect_pack_refdes(pack)
    for lab in wanted[:MAX_FOCUS_CROPS]:
        title_hits, _ = search_keywords(page, [lab])
        if not title_hits:
            out.append((lab, []))
            continue
        tx = (title_hits[0].x0 + title_hits[0].x1) / 2
        ty = (title_hits[0].y0 + title_hits[0].y1) / 2
        near = []
        for ref in pack_refs:
            rs, _ = search_keywords(page, [ref])
            for r in rs:
                cx = (r.x0 + r.x1) / 2
                cy = (r.y0 + r.y1) / 2
                if abs(cx - tx) < 340 and -50 < (cy - ty) < 400:
                    near.append(ref)
                    break
        out.append((lab, near[:6]))
    return out


def build_multi_focus_rows(
    pack: dict,
    pdf_path: Path,
    page: fitz.Page,
    page_index: int,
    regions: list[dict],
    shots_dir: Path,
    dpi: int,
) -> list[dict]:
    """Build 1..N local crops covering spatially distinct check objects."""
    rid = pack.get("rule_id") or "UNKNOWN"
    rows: list[dict] = []

    region_tgts = region_targets_on_page(pack, page, regions)
    if region_tgts:
        for i, (lab, refs) in enumerate(region_tgts):
            p2 = dict(pack)
            seeds = refs or [lab]
            p2["_shot_prefer_keywords"] = list(dict.fromkeys(seeds))[:8]
            suffix = re.sub(r"[^\w]+", "", lab)[:16] or str(i + 1)
            row = build_focus_for_pack(
                p2, pdf_path, page, page_index, regions, shots_dir, dpi, out_suffix=suffix
            )
            row["page_index"] = page_index + 1
            row["region"] = lab
            row["note"] = f"功能区「{lab}」" + (f"；位号 {'/'.join(refs[:6])}" if refs else "")
            row["focus_keywords"] = list(dict.fromkeys(([lab] if lab else []) + refs))[:8]
            rows.append(row)
        return rows

    refs = _collect_pack_refdes(pack)
    clusters = cluster_refs_on_page(page, refs)
    if len(clusters) <= 1:
        row = build_focus_for_pack(pack, pdf_path, page, page_index, regions, shots_dir, dpi)
        row["page_index"] = page_index + 1
        return [row]

    for i, group in enumerate(clusters):
        p2 = dict(pack)
        p2["_shot_prefer_keywords"] = group[:8]
        suffix = f"{i+1}" if i else ""
        # first crop keeps classic name *_focus.png for compatibility
        row = build_focus_for_pack(
            p2,
            pdf_path,
            page,
            page_index,
            regions,
            shots_dir,
            dpi,
            out_suffix=("" if i == 0 else str(i + 1)),
        )
        row["page_index"] = page_index + 1
        row["note"] = f"焦点组{i+1}：{'/'.join(group[:8])}"
        row["focus_keywords"] = group[:8]
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Attach region-level schematic screenshots")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--dpi", type=int, default=160)
    ap.add_argument("--pdf", default="")
    ap.add_argument("--only-rules", default="", help="comma-separated rule_ids; empty=all")
    ap.add_argument("--rework-json", default="", help="adjudication rework_latest.json with force_by_rule")
    args = ap.parse_args()

    out_dir = Path(args.out_dir).resolve()
    evidence_dir = out_dir / "evidence"
    if not evidence_dir.is_dir():
        print(f"[error] evidence/ missing under {out_dir}")
        return 2

    only = {x.strip() for x in args.only_rules.split(",") if x.strip()}
    force_by_rule: dict = {}
    rework_path = Path(args.rework_json) if args.rework_json else out_dir / "adjudication" / "rework_latest.json"
    if rework_path.exists():
        force_by_rule = (load_json(rework_path).get("force_by_rule") or {})
        if not only and force_by_rule and args.rework_json:
            only = set(force_by_rule.keys())

    pdf_path = Path(args.pdf).resolve() if args.pdf else find_schematic_pdf(out_dir)
    if not pdf_path or not pdf_path.exists():
        print("[error] schematic PDF not found")
        save_json(out_dir / "facts" / "schematic_shots.json", {"ok": False, "error": "pdf_missing"})
        return 1

    shots_dir = evidence_dir / "shots"
    full_pages = render_full_pages(pdf_path, shots_dir, dpi=max(120, args.dpi - 20))

    doc = fitz.open(str(pdf_path))
    try:
        regions_by_page = {i: discover_regions(doc[i]) for i in range(doc.page_count)}
    finally:
        doc.close()

    patched = 0
    region_hist: dict[str, int] = {}
    for path in sorted(evidence_dir.glob("P*.json")):
        try:
            pack = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as e:
            print(f"[warn] skip {path.name}: {e}")
            continue

        rid = pack.get("rule_id") or path.stem
        if only and rid not in only:
            continue

        force = force_by_rule.get(rid) or {}
        prefer_refs = list(force.get("prefer_refs") or [])
        forbid_page = force.get("forbid_page")
        focus_rows: list[dict] = []
        page_index = 0

        doc = fitz.open(str(pdf_path))
        try:
            page_index, hit_kws, score = pick_best_page_index(
                doc, pack, prefer_refs=prefer_refs or None, forbid_page=forbid_page
            )
            page = doc[page_index]
            regions = regions_by_page.get(page_index) or discover_regions(page)
            pack = dict(pack)
            if prefer_refs:
                # Force list wins — do not append page hit pollution (C1/C2/…)
                clean = []
                for r in prefer_refs:
                    if is_refdes_keyword(r) or re.fullmatch(r"CN\d+", r or "", re.I) or r.startswith("+"):
                        clean.append(r)
                pack["_shot_prefer_keywords"] = list(dict.fromkeys(clean or prefer_refs))
            elif hit_kws:
                pack["_shot_prefer_keywords"] = [
                    k for k in hit_kws if is_refdes_keyword(k) or re.fullmatch(r"CN\d+", k or "", re.I)
                ][:6]
            focus_rows = build_multi_focus_rows(
                pack, pdf_path, page, page_index, regions, shots_dir, args.dpi
            )
            for fr in focus_rows:
                fr["page_index"] = page_index + 1
                fr["match_score"] = score
                fr["status"] = pack.get("verdict") or fr.get("status") or "INSUFFICIENT_EVIDENCE"
                if prefer_refs and not fr.get("focus_keywords"):
                    fr["focus_keywords"] = list(dict.fromkeys(prefer_refs))[:10]
                if score <= 0 and focus_rows and fr is focus_rows[0]:
                    fr["note"] = (fr.get("note") or "") + "（未强匹配位号，回退该页局部）"
        finally:
            doc.close()

        pages_checked = list(focus_rows)
        matched_overview = next((fp for fp in full_pages if fp.get("page_index") == page_index + 1), None)
        if not matched_overview and full_pages:
            for fp in full_pages:
                if str(fp.get("page") or "").upper() in {f"PAGE{page_index+1}", f"P{page_index+1}"}:
                    matched_overview = fp
                    break
            if not matched_overview and 0 <= page_index < len(full_pages):
                matched_overview = full_pages[page_index]
        if pages_checked and matched_overview:
            overview = dict(matched_overview)
            overview["page"] = f"PAGE{page_index+1} / 整页索引"
            overview["status"] = pack.get("verdict") or overview.get("status")
            n_focus = sum(1 for x in pages_checked if x.get("crop"))
            overview["note"] = (
                f"第{page_index+1}页整页索引（上方共 {n_focus} 张局部焦点裁切）"
            )
            overview["region"] = f"PAGE{page_index+1}整页"
            overview["crop"] = False
            overview["page_index"] = page_index + 1
            pages_checked.append(overview)

        pack["pages_checked"] = pages_checked
        if prefer_refs:
            pack["adjudication_shot_rework"] = {"prefer_refs": prefer_refs, "page": page_index + 1}
        save_json(path, pack)
        patched += 1
        for fr in pages_checked:
            if not fr.get("crop"):
                continue
            reg = str(fr.get("region") or "?")
            region_hist[reg] = region_hist.get(reg, 0) + 1

    index = {
        "ok": True,
        "pdf": str(pdf_path),
        "pdf_name": pdf_path.name,
        "mode": "multi_region_crop",
        "max_focus_crops": MAX_FOCUS_CROPS,
        "page_count": len(full_pages),
        "packs_patched": patched,
        "region_histogram": region_hist,
        "discovered_regions": [
            {"page": i + 1, "name": r["name"]}
            for i, regs in regions_by_page.items()
            for r in regs
        ],
        "full_pages": full_pages,
        "rework_applied": bool(force_by_rule),
    }
    save_json(out_dir / "facts" / "schematic_shots.json", index)
    print(json.dumps({
        "pdf": pdf_path.name,
        "packs_patched": patched,
        "region_histogram": region_hist,
        "rework_applied": bool(force_by_rule),
        "only_rules": sorted(only) if only else "all",
        "mode": "multi_region_crop",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
