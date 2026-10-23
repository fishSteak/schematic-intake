# -*- coding: utf-8 -*-
"""生产级：原理图原图工况通路高亮出图。

按一份通路规格在原理图原图上画青蓝高亮。规格来自 stress_figures，不在本文件按位号写死。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from kernel.util import out_dir

# 高亮：避开原理图绿线/红符；略粗 + 略透，盖不住铜线
PATH_RGB = (14, 165, 200)
PATH_ALPHA = 165
PATH_WIDTH = 4
# 出图像素倍率；内部再 ×AA 超采样后缩小，去掉折线/圆角锯齿
DISPLAY_ZOOM = 3.2
AA_SCALE = 2
# UI：浅色现代工程风（去厚重蓝条 / 旧式提示框）
UI_INK = (15, 23, 42)
UI_MUTED = (100, 116, 139)
UI_FAINT = (148, 163, 184)
UI_ACCENT = (14, 165, 200)
UI_ACCENT_SOFT = (224, 246, 252)
UI_BG = (241, 245, 249)
UI_CARD = (255, 255, 255)
UI_EDGE = (226, 232, 240)
UI_EDGE_STRONG = (203, 213, 225)
MARKER_FILL = (255, 255, 255)
MARKER_EDGE = (14, 165, 200)
VERDICT_OK = (16, 185, 129)
VERDICT_FAIL = (244, 63, 94)

# 通路规格由 stress_figures 生成后交给 render_spec。
# 不在这里按位号保存坐标。

def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    names = (
        ("msyhbd.ttc", "segoeuib.ttf", "arialbd.ttf", "msyh.ttc")
        if bold
        else ("msyh.ttc", "segoeui.ttf", "arial.ttf", "simhei.ttf")
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _z(pt: tuple[float, float], zoom: float) -> tuple[float, float]:
    return (pt[0] * zoom, pt[1] * zoom)


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.multiline_textbbox((0, 0), text, font=font, spacing=3)
    return int(box[2] - box[0]), int(box[3] - box[1])


def _ink_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    *,
    spacing: int = 3,
) -> tuple[float, float, float, float]:
    """返回墨迹宽、高，以及相对绘制原点的左/上偏移（用于真正居中）。"""
    if not text:
        return 0.0, 0.0, 0.0, 0.0
    x0, y0, x1, y1 = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing)
    return float(x1 - x0), float(y1 - y0), float(x0), float(y0)


def _round_paste(base: Image.Image, content: Image.Image, xy: tuple[int, int], radius: int = 12) -> None:
    """圆角裁切后贴到 base。"""
    mask = Image.new("L", content.size, 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, content.width - 1, content.height - 1), radius=radius, fill=255)
    base.paste(content, xy, mask)


def _soft_shadow(size: tuple[int, int], radius: int = 14, blur: int = 10, alpha: int = 36) -> Image.Image:
    w, h = size
    layer = Image.new("RGBA", (w + blur * 4, h + blur * 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(
        (blur * 2, blur * 2 + 2, blur * 2 + w, blur * 2 + h + 2),
        radius=radius,
        fill=(15, 23, 42, alpha),
    )
    return layer.filter(ImageFilter.GaussianBlur(blur))


def _draw_polyline(od: ImageDraw.ImageDraw, pts: list[tuple[float, float]], zoom: float, *, s: float = 1.0, arrows: bool = True, stroke: bool = True) -> None:
    if len(pts) < 2:
        return
    zpts = [_z(p, zoom) for p in pts]
    width = max(2, int(round(PATH_WIDTH * s)))
    if stroke:
        od.line(zpts, fill=(*PATH_RGB, 255), width=width, joint="curve")
    # 折点圆头，避免直角锯齿
    rad = width / 2
    for p in (pts if stroke else []):
        cx, cy = _z(p, zoom)
        od.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=(*PATH_RGB, 255))
    for i in (range(len(pts) - 1) if arrows else []):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        span = abs(x1 - x0) + abs(y1 - y0)
        if span < 24:
            continue
        mx, my = ((x0 + x1) / 2 * zoom, (y0 + y1) / 2 * zoom)
        mids = [(mx, my)]
        if span > 80:
            mids.append(((x0 * 0.28 + x1 * 0.72) * zoom, (y0 * 0.28 + y1 * 0.72) * zoom))
        for ax, ay in mids:
            if abs(x1 - x0) >= abs(y1 - y0):
                d = 1 if x1 > x0 else -1
                tip = (ax + 10 * s * d, ay)
                base_l = (ax - 7 * s * d, ay - 8 * s)
                base_r = (ax - 7 * s * d, ay + 8 * s)
                tip2 = (ax + 8 * s * d, ay)
                bl2 = (ax - 5 * s * d, ay - 6 * s)
                br2 = (ax - 5 * s * d, ay + 6 * s)
            else:
                d = 1 if y1 > y0 else -1
                tip = (ax, ay + 10 * s * d)
                base_l = (ax - 8 * s, ay - 7 * s * d)
                base_r = (ax + 8 * s, ay - 7 * s * d)
                tip2 = (ax, ay + 8 * s * d)
                bl2 = (ax - 6 * s, ay - 5 * s * d)
                br2 = (ax + 6 * s, ay - 5 * s * d)
            od.polygon([tip, base_l, base_r], fill=(255, 255, 255, 255))
            od.polygon([tip2, bl2, br2], fill=(*PATH_RGB, 255))
    # Do not add dots at every vector endpoint: CAD junction dots already
    # express connectivity, while extra dots obscure symbols and short wires.


def _draw_bridge(
    od: ImageDraw.ImageDraw,
    bridge_note: dict[str, Any],
    lines: list[list],
    zoom: float,
    *,
    s: float = 1.0,
    draw_text: bool = True,
) -> None:
    via = list(bridge_note.get("via") or [])
    if len(via) >= 2:
        bridge_pts = via
    elif len(lines) >= 2:
        bridge_pts = [lines[0][-1], lines[1][0]]
    else:
        bridge_pts = []
    for i in range(len(bridge_pts) - 1):
        a = _z(tuple(bridge_pts[i]), zoom)  # type: ignore[arg-type]
        b = _z(tuple(bridge_pts[i + 1]), zoom)  # type: ignore[arg-type]
        steps = 12
        for k in range(steps):
            if k % 2:
                continue
            t0, t1 = k / steps, min(1.0, (k + 1) / steps)
            od.line(
                [
                    (a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0),
                    (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1),
                ],
                fill=(*PATH_RGB, 255),
                width=max(2, int(round((PATH_WIDTH - 1) * s))),
            )
    if draw_text:
        _paint_bridge_label(od, bridge_note, zoom, (0.0, 0.0), s)


def _bridge_note_covered_by_markers(
    bridge_note: dict[str, Any] | None,
    markers: list[dict[str, Any]] | None,
) -> bool:
    """封装内说明若落在已有圆点旁边，再画一条会压住引线。圆点已经说明该器件。"""
    if not bridge_note or not bridge_note.get("at") or not markers:
        return False
    try:
        ax, ay = float(bridge_note["at"][0]), float(bridge_note["at"][1])
    except (TypeError, ValueError, IndexError):
        return False
    for m in markers:
        at = m.get("at") if isinstance(m, dict) else None
        if not isinstance(at, (list, tuple)) or len(at) < 2:
            continue
        try:
            mx, my = float(at[0]), float(at[1])
        except (TypeError, ValueError):
            continue
        if abs(mx - ax) + abs(my - ay) < 48:
            return True
    return False


def _paint_bridge_label(
    od: ImageDraw.ImageDraw,
    bridge_note: dict[str, Any] | None,
    zoom: float,
    origin: tuple[float, float],
    s: float,
) -> None:
    """封装内说明按最终像素直接画，避免随通路层一起缩小变糊。"""
    if not bridge_note or not bridge_note.get("at") or not bridge_note.get("text"):
        return
    bx, by = _z(tuple(bridge_note["at"]), zoom)  # type: ignore[arg-type]
    bx -= origin[0]
    by -= origin[1]
    note = str(bridge_note["text"])
    font = _font(max(12, int(round(13 * s))))
    tw, th = _text_size(od, note, font)
    pad = 9 * s
    od.rounded_rectangle(
        (bx - pad, by - th // 2 - pad, bx + tw + pad + 4 * s, by + th // 2 + pad),
        radius=int(10 * s),
        fill=(255, 255, 255, 242),
        outline=(*PATH_RGB, 255),
        width=max(1, int(round(s))),
    )
    od.multiline_text((bx, by - th // 2), note, fill=UI_INK, font=font, spacing=int(2 * s), align="left")


def _marker_title(raw: str, idx: int) -> str:
    title = str(raw or f"节点{idx}")
    for ch in ("①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧"):
        if title.startswith(ch):
            title = title[len(ch) :].lstrip(" ·.")
            break
    if title[:1].isdigit() and (len(title) == 1 or title[1] in " ·" or
                               (title[1] == "." and (len(title) < 3 or not title[2].isdigit()))):
        title = title[1:].lstrip(" .·")
    return title


def _wrap_marker_body(text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    """长算式按宽度折行；优先在逗号/等号处断行。"""
    text = str(text or "").strip()
    if not text:
        return ""
    if "\n" in text:
        return "\n".join(_wrap_marker_body(line, font, max_width) for line in text.splitlines())
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    if _text_size(probe, text, font)[0] <= max_width:
        return text
    chunks: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in "，,；;":
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)
    lines: list[str] = []
    cur = ""
    for chunk in chunks:
        trial = cur + chunk
        if cur and _text_size(probe, trial, font)[0] > max_width:
            lines.append(cur.rstrip())
            cur = chunk
        else:
            cur = trial
        # 单块仍超宽则按字符拆
        while _text_size(probe, cur, font)[0] > max_width and len(cur) > 1:
            cut = len(cur)
            while cut > 1 and _text_size(probe, cur[:cut], font)[0] > max_width:
                cut -= 1
            lines.append(cur[:cut].rstrip())
            cur = cur[cut:]
    if cur.strip():
        lines.append(cur.rstrip())
    return "\n".join(lines)


def _is_paper(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return r >= 245 and g >= 245 and b >= 240


def _region_busy(img: Image.Image, box: tuple[float, float, float, float]) -> float:
    """占用分：取局部小块最大墨迹比。细绿线不能被大片白底稀释掉。"""
    x0 = max(0, int(box[0]))
    y0 = max(0, int(box[1]))
    x1 = min(img.width, int(box[2]))
    y1 = min(img.height, int(box[3]))
    if x1 <= x0 or y1 <= y0:
        return 1.0
    region = img.crop((x0, y0, x1, y1)).convert("RGB")
    px = region.load()
    block = 20
    worst = 0.0
    for by in range(0, region.height, block):
        for bx in range(0, region.width, block):
            busy = 0
            total = 0
            for y in range(by, min(by + block, region.height), 2):
                for x in range(bx, min(bx + block, region.width), 2):
                    total += 1
                    if not _is_paper(px[x, y]):
                        busy += 1
            if total:
                worst = max(worst, busy / total)
            if worst >= 0.25:
                return worst
    return worst


def _rects_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float], gap: float = 8.0) -> bool:
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def _segment_busy(img: Image.Image, a: tuple[float, float], b: tuple[float, float], width: float = 6.0) -> float:
    """沿线采样：压到绿线/红符/高亮就加分。"""
    x0, y0 = a
    x1, y1 = b
    steps = max(4, int((abs(x1 - x0) + abs(y1 - y0)) / 8))
    busy = 0.0
    for i in range(steps + 1):
        t = i / steps
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t
        busy += _region_busy(img, (x - width, y - width, x + width, y + width))
    return busy / (steps + 1)


def _leader_polyline(
    img: Image.Image,
    ax: float,
    ay: float,
    rect: tuple[float, float, float, float],
) -> list[tuple[float, float]]:
    """正交引线，优先走空白，避免斜穿器件和铜线。"""
    cx0, cy0, cx1, cy1 = rect
    joins = [
        (cx0, (cy0 + cy1) / 2),
        (cx1, (cy0 + cy1) / 2),
        ((cx0 + cx1) / 2, cy0),
        ((cx0 + cx1) / 2, cy1),
    ]
    dodge = max(36.0, min(img.width, img.height) * 0.02)
    best: list[tuple[float, float]] | None = None
    best_score = 1e18
    for jx, jy in joins:
        routes = [
            [(ax, ay), (jx, ay), (jx, jy)],
            [(ax, ay), (ax, jy), (jx, jy)],
            [(ax, ay), (ax - dodge, ay), (ax - dodge, jy), (jx, jy)],
            [(ax, ay), (ax + dodge, ay), (ax + dodge, jy), (jx, jy)],
            [(ax, ay), (ax, ay - dodge), (jx, ay - dodge), (jx, jy)],
            [(ax, ay), (ax, ay + dodge), (jx, ay + dodge), (jx, jy)],
            # 贴着图框外侧走，不从电路中间穿
            [(ax, ay), (-12, ay), (-12, jy), (jx, jy)],
            [(ax, ay), (img.width + 12, ay), (img.width + 12, jy), (jx, jy)],
            [(ax, ay), (ax, -12), (jx, -12), (jx, jy)],
            [(ax, ay), (ax, img.height + 12), (jx, img.height + 12), (jx, jy)],
        ]
        for route in routes:
            score = 0.0
            for p, q in zip(route, route[1:]):
                if abs(p[0] - q[0]) < 1 and abs(p[1] - q[1]) < 1:
                    continue
                score += _segment_busy(img, p, q) * 100
                score += (abs(p[0] - q[0]) + abs(p[1] - q[1])) * 0.01
            if score < best_score:
                best_score = score
                best = route
    return best or [(ax, ay), ((cx0 + cx1) / 2, (cy0 + cy1) / 2)]


def _path_band(
    pts: list[tuple[float, float] | None],
    rect: tuple[float, float, float, float],
    pad: float,
) -> bool:
    """卡片是否压到通路折线段（含线宽余量）。"""
    x0, y0, x1, y1 = rect
    for a, b in zip(pts, pts[1:]):
        if a is None or b is None:
            continue
        ax, ay = a
        bx, by = b
        if abs(ax - bx) < 2:
            if x0 - pad <= ax <= x1 + pad and not (max(ay, by) < y0 - pad or min(ay, by) > y1 + pad):
                return True
        elif abs(ay - by) < 2:
            if y0 - pad <= ay <= y1 + pad and not (max(ax, bx) < x0 - pad or min(ax, bx) > x1 + pad):
                return True
        else:
            steps = 8
            for i in range(steps + 1):
                t = i / steps
                px, py = ax + (bx - ax) * t, ay + (by - ay) * t
                if x0 - pad <= px <= x1 + pad and y0 - pad <= py <= y1 + pad:
                    return True
    return False


def _pick_marker_card(
    img: Image.Image,
    *,
    ax: float,
    ay: float,
    card_w: float,
    card_h: float,
    taken: list[tuple[float, float, float, float]],
    avoid_pts: list[tuple[float, float]] | None = None,
    path_pts: list[tuple[float, float] | None] | None = None,
) -> tuple[float, float, float, float]:
    """在空白处放卡片：不压器件/连线；图内找不到就放到图外，稍后扩白边，禁止裁切。"""
    gap = 36.0
    margin = 20.0
    avoid = avoid_pts or []
    band = path_pts or []
    candidates: list[tuple[float, float]] = [
        (ax + gap, ay - card_h / 2),
        (ax + gap, ay - card_h - gap),
        (ax + gap, ay + gap),
        (ax - card_w - gap, ay - card_h / 2),
        (ax - card_w - gap, ay - card_h - gap),
        (ax - card_w - gap, ay + gap),
        (ax - card_w / 2, ay - card_h - gap),
        (ax - card_w / 2, ay + gap),
        (ax + gap * 3, ay - card_h - gap * 2),
        (ax - card_w - gap * 3, ay - card_h - gap * 2),
        (ax + gap * 3, ay + gap * 2),
        (ax - card_w - gap * 3, ay + gap * 2),
        # 图外侧空白（随后扩边），保证一定有不压电路的落点
        (ax - card_w - gap, -card_h - gap),
        (ax + gap, -card_h - gap),
        (ax - card_w - gap, img.height + gap),
        (ax + gap, img.height + gap),
        (-card_w - gap, ay - card_h / 2),
        (img.width + gap, ay - card_h / 2),
        (margin, -card_h - gap),
        (img.width - card_w - margin, -card_h - gap),
    ]
    best: tuple[float, float, float, float] | None = None
    best_score = 1e18
    for cx0, cy0 in candidates:
        rect = (cx0, cy0, cx0 + card_w, cy0 + card_h)
        if any(_rects_overlap(rect, t) for t in taken):
            continue
        # 卡片不得盖住锚点 / 通路折点
        if rect[0] - 12 <= ax <= rect[2] + 12 and rect[1] - 12 <= ay <= rect[3] + 12:
            continue
        if any(rect[0] - 6 <= px <= rect[2] + 6 and rect[1] - 6 <= py <= rect[3] + 6 for px, py in avoid):
            continue
        if band and _path_band(band, rect, pad=24):
            continue
        vis = (
            max(0.0, rect[0]),
            max(0.0, rect[1]),
            min(float(img.width), rect[2]),
            min(float(img.height), rect[3]),
        )
        if vis[2] > vis[0] and vis[3] > vis[1]:
            busy = _region_busy(img, vis)
        else:
            busy = 0.0
        # 局部小块一旦碰到铜线/符号就丢掉
        if busy > 0.08:
            continue
        outside = (
            max(0.0, -rect[0])
            + max(0.0, -rect[1])
            + max(0.0, rect[2] - img.width)
            + max(0.0, rect[3] - img.height)
        )
        dist = abs((cx0 + card_w / 2) - ax) + abs((cy0 + card_h / 2) - ay)
        score = busy * 8000 + outside * 0.35 + dist * 0.02
        if busy < 0.01 and outside == 0:
            score -= 300
        if score < best_score:
            best_score = score
            best = rect
    if best is None:
        # 保底：放到图上方外侧，扩白边后完整可见
        return (max(margin, ax - card_w / 2), -card_h - gap, max(margin, ax - card_w / 2) + card_w, -gap)
    return best


def _anchors_clustered(rows: list[dict[str, Any]], zoom: float) -> bool:
    """几个圆点落在同一只器件附近时，横排，避免卡片叠在一起、引线交叉。"""
    if len(rows) < 2:
        return False
    xs = [float(r["ax"]) for r in rows]
    ys = [float(r["ay"]) for r in rows]
    limit = 90.0 * zoom
    return (max(xs) - min(xs)) < limit and (max(ys) - min(ys)) < limit


def _assign_cluster_row(img: Image.Image, rows: list[dict[str, Any]]) -> bool:
    """同一只器件上的圆点排成一行，放到图框上沿之外，避免压住铜线和符号。"""
    ordered = sorted(rows, key=lambda r: float(r["ax"]))
    gap = 64.0
    total_w = sum(float(r["card_w"]) for r in ordered) + gap * (len(ordered) - 1)
    height = max(float(r["card_h"]) for r in ordered)
    left = min(float(r["ax"]) for r in ordered)
    right = max(float(r["ax"]) for r in ordered)
    x = (left + right) / 2 - total_w / 2
    y = -height - 36.0
    for r in ordered:
        w = float(r["card_w"])
        h = float(r["card_h"])
        r["rect"] = (x, y + (height - h), x + w, y + height)
        x += w + gap
    return True


def _cluster_leader(
    ax: float,
    ay: float,
    rect: tuple[float, float, float, float],
) -> list[tuple[float, float]]:
    """先竖直到卡片底边，再水平接入。锚点从左到右时，这些线不会交叉。"""
    cx0, cy0, cx1, cy1 = rect
    jx = (cx0 + cx1) / 2
    if ay >= cy1:
        jy = cy1
    elif ay <= cy0:
        jy = cy0
    else:
        jy = cy0 if abs(ay - cy0) < abs(ay - cy1) else cy1
    return [(ax, ay), (ax, jy), (jx, jy)]


def _draw_markers(
    img: Image.Image,
    markers: list[dict[str, Any]],
    zoom: float,
    origin: tuple[float, float],
    *,
    s: float = 1.0,
    path_pts: list[tuple[float, float] | None] | None = None,
) -> Image.Image:
    """编号胶囊 + 白卡片。字号随图幅放大；卡片避让元器件/连线；不够边则外扩，禁止裁切卡片。"""
    del s  # 字号改按图幅计算，不再跟 AA 缩放绑死
    if not markers:
        return img
    base = img.convert("RGB")
    # 圈注只标结论数字；卡片贴合内容，内外居中
    readable = max(1.2, base.width / 1500.0)
    font_title = _font(max(16, int(round(15 * readable))), bold=True)
    font_body = _font(max(14, int(round(13 * readable))))
    # 编号用西文粗体，避免中文字体把 1/2 画成怪符
    num_size = max(14, int(round(13 * readable)))
    try:
        font_num = ImageFont.truetype("segoeuib.ttf", num_size)
    except Exception:
        try:
            font_num = ImageFont.truetype("arialbd.ttf", num_size)
        except Exception:
            font_num = font_title
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    ox, oy = origin
    margin = max(14.0, 8.0 * readable)
    pad_x = max(16.0, 12.0 * readable)
    pad_y = max(14.0, 11.0 * readable)
    badge = max(22.0, 18.0 * readable)
    gap_badge = max(12.0, 9.0 * readable)
    gap_title_body = max(14.0, 10.0 * readable)
    max_body_w = min(int(base.width * 0.46), int(720 * readable))
    radius = max(10, int(round(8 * readable)))
    line_w = max(2, int(round(1.2 * readable)))
    dot_r = max(4.0, 3.2 * readable)

    planned: list[dict[str, Any]] = []
    taken: list[tuple[float, float, float, float]] = []
    pad_l = pad_t = pad_r = pad_b = 0.0
    avoid_pts = [
        (float(m["at"][0]) * zoom - ox, float(m["at"][1]) * zoom - oy)
        for m in markers
        if isinstance(m.get("at"), (list, tuple)) and len(m["at"]) >= 2
    ]
    marker_paths = [(p[0] * zoom - ox, p[1] * zoom - oy) if p is not None else None
                    for p in path_pts or ()]
    avoid_pts.extend(p for p in marker_paths if p is not None)

    for i, m in enumerate(markers):
        at = tuple(m["at"])  # type: ignore[arg-type]
        ax = at[0] * zoom - ox
        ay = at[1] * zoom - oy
        idx = i + 1
        volt = str(m.get("kind") or "") == "volt"
        title = str(m.get("title") or "").strip() if volt else _marker_title(str(m.get("title") or f"节点{idx}"), idx)
        body = "" if volt else _wrap_marker_body(str(m.get("body") or ""), font_body, max_body_w)
        spacing = max(2, int(3 * readable))
        title_font = font_title
        # 先画到透明层，按实际像素定卡片大小；编号与文字列垂直对齐
        tw, th, tox, toy = _ink_box(probe, title, title_font, spacing=spacing)
        if body:
            bw, bh, box, boy = _ink_box(probe, body, font_body, spacing=spacing)
        else:
            bw = bh = box = boy = 0.0
        text_h = th + ((gap_title_body + bh) if body else 0.0)
        if volt:
            layer_h = int(th + 16)
            layer_w = int(tw + 24)
        else:
            layer_h = int(max(badge, text_h) + 8)
            layer_w = int(badge + gap_badge + max(tw, bw) + 8)
        layer = Image.new("RGBA", (max(layer_w, 8), max(layer_h, 8)), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        if volt:
            ld.text((8 - tox, 6 - toy), title, fill=(*PATH_RGB, 255), font=font_title)
            content = layer.crop(layer.getbbox() or (0, 0, 1, 1))
            content_w, content_h = content.size
            card_w = content_w + 18 * readable
            card_h = content_h + 10 * readable
            planned.append({"ax": ax, "ay": ay, "content": content, "kind": "volt", "card_w": card_w, "card_h": card_h})
            continue
        mid = layer_h / 2
        bx = 0.0
        by = mid - badge / 2
        ld.ellipse((bx, by, bx + badge, by + badge), fill=(*UI_ACCENT, 255))
        # 按数字像素重心居中（外框会把「1」的头顶小钩算进去，竖线看起来偏）
        side = int(badge * 3)
        glyph = Image.new("L", (side, side), 0)
        gd = ImageDraw.Draw(glyph)
        ax0, ay0 = side / 2, side / 2
        gd.text((ax0, ay0), str(idx), fill=255, font=font_num, anchor="mm")
        pix = glyph.load()
        sx = sy = n = 0
        for yy in range(side):
            for xx in range(side):
                if pix[xx, yy] > 140:
                    sx += xx
                    sy += yy
                    n += 1
        if n:
            ax0 += side / 2 - sx / n
            ay0 += side / 2 - sy / n
        ld.text(
            (bx + badge / 2 + (ax0 - side / 2), by + badge / 2 + (ay0 - side / 2)),
            str(idx),
            fill=(255, 255, 255, 255),
            font=font_num,
            anchor="mm",
        )
        tx0 = badge + gap_badge
        ink_top = mid - text_h / 2
        ld.text((tx0 - tox, ink_top - toy), title, fill=(*UI_INK, 255), font=font_title)
        if body:
            ld.multiline_text(
                (tx0 - box, ink_top + th + gap_title_body - boy),
                body,
                fill=(*UI_MUTED, 255),
                font=font_body,
                spacing=spacing,
            )
        content = layer.crop(layer.getbbox() or (0, 0, 1, 1))
        content_w, content_h = content.size
        card_w = pad_x + content_w + pad_x
        card_h = pad_y + content_h + pad_y
        planned.append(
            {
                "ax": ax,
                "ay": ay,
                "card_w": card_w,
                "card_h": card_h,
                "content": content,
                "kind": "volt" if volt else "",
            }
        )

    clustered = _anchors_clustered(planned, zoom) and _assign_cluster_row(base, planned)
    foot_shift: dict[int, float] = {}
    if clustered and planned:
        xs = [float(r["ax"]) for r in planned]
        if max(xs) - min(xs) < 8:
            order = sorted(planned, key=lambda r: float(r["rect"][0]))
            mid = (len(order) - 1) / 2
            for i, r in enumerate(order):
                foot_shift[id(r)] = (i - mid) * 72.0
    if not clustered:
        for row in planned:
            rect = _pick_marker_card(
                base,
                ax=float(row["ax"]),
                ay=float(row["ay"]),
                card_w=float(row["card_w"]),
                card_h=float(row["card_h"]),
                taken=taken,
                avoid_pts=avoid_pts,
                path_pts=marker_paths,
            )
            row["rect"] = rect
            taken.append(rect)
    for row in planned:
        x0, y0, x1, y1 = row["rect"]
        pad_l = max(pad_l, max(0.0, -x0 + margin))
        pad_t = max(pad_t, max(0.0, -y0 + margin))
        pad_r = max(pad_r, max(0.0, x1 - base.width + margin))
        pad_b = max(pad_b, max(0.0, y1 - base.height + margin))

    pl, pt, pr, pb = int(pad_l), int(pad_t), int(pad_r), int(pad_b)
    if pl or pt or pr or pb:
        canvas = Image.new("RGB", (base.width + pl + pr, base.height + pt + pb), (255, 255, 255))
        canvas.paste(base, (pl, pt))
        base = canvas
        for row in planned:
            row["ax"] = float(row["ax"]) + pl
            row["ay"] = float(row["ay"]) + pt
            x0, y0, x1, y1 = row["rect"]
            row["rect"] = (x0 + pl, y0 + pt, x1 + pl, y1 + pt)

    out = base.convert("RGBA")
    overlay = Image.new("RGBA", out.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for row in planned:
        ax = float(row["ax"]) + foot_shift.get(id(row), 0.0)
        ay = float(row["ay"])
        cx0, cy0, cx1, cy1 = row["rect"]
        # 锚点小圆
        od.ellipse((ax - dot_r - 2, ay - dot_r - 2, ax + dot_r + 2, ay + dot_r + 2), outline=(255, 255, 255, 230), width=max(2, int(readable)))
        od.ellipse((ax - dot_r, ay - dot_r, ax + dot_r, ay + dot_r), fill=(*MARKER_EDGE, 230))
        if clustered:
            route = _cluster_leader(ax, ay, (cx0, cy0, cx1, cy1))
        else:
            route = _leader_polyline(base, ax, ay, (cx0, cy0, cx1, cy1))
        if len(route) >= 2:
            od.line(route, fill=(100, 116, 139, 190), width=line_w)
        pill = str(row.get("kind") or "") == "volt"
        od.rounded_rectangle((cx0 + 2, cy0 + 3, cx1 + 2, cy1 + 3), radius=radius, fill=(15, 23, 42, 28))
        od.rounded_rectangle(
            (cx0, cy0, cx1, cy1),
            radius=999 if pill else radius,
            fill=(255, 255, 255, 250) if not pill else (* (224, 246, 252), 250),
            outline=(*PATH_RGB, 255) if pill else (*UI_EDGE_STRONG, 255),
            width=max(1, int(round(readable))),
        )
        # 内容层贴到卡片正中，左右上下留白相等
        content = row["content"]
        if isinstance(content, Image.Image):
            px = int(round(cx0 + (cx1 - cx0 - content.width) / 2))
            py = int(round(cy0 + (cy1 - cy0 - content.height) / 2))
            overlay.paste(content, (px, py), content)

    out = Image.alpha_composite(out, overlay)
    return out.convert("RGB")


def _wrap_chain_text(text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    if _text_size(probe, text, font)[0] <= max_width:
        return text
    lines: list[str] = []
    buf = ""
    for ch in text:
        trial = buf + ch
        if buf and _text_size(probe, trial, font)[0] > max_width:
            lines.append(buf)
            buf = ch
        else:
            buf = trial
    if buf:
        lines.append(buf)
    return "\n".join(lines[:4])


def _draw_calc_chain(
    width: int,
    rows: list[dict[str, str]],
    *,
    ui: float,
) -> Image.Image:
    """图下计算链：左算式、右出处，复核时顺着往下看。"""
    def u(n: float) -> int:
        return int(round(n * ui))

    title_f = _font(u(13), bold=True)
    expr_f = _font(u(12), bold=True)
    src_f = _font(u(11))
    idx_f = _font(u(11), bold=True)
    pad = u(16)
    gap = u(10)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    inner_w = max(200, width - pad * 2)
    expr_w = int(inner_w * 0.46)
    src_w = inner_w - expr_w - u(24)
    blocks: list[tuple[str, str, str, int]] = []
    body_h = 0
    for i, row in enumerate(rows):
        expr = _wrap_chain_text(str(row.get("expr") or ""), expr_f, expr_w)
        src = _wrap_chain_text(str(row.get("src") or ""), src_f, src_w)
        eh = _text_size(probe, expr, expr_f)[1] if expr else u(12)
        sh = _text_size(probe, src, src_f)[1] if src else 0
        rh = max(eh, sh, u(18)) + u(12)
        blocks.append((str(i + 1), expr, src, rh))
        body_h += rh + gap
    head = u(36)
    h = head + body_h + pad
    img = Image.new("RGB", (width, h), UI_CARD)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, width - 1, h - 1), radius=u(12), outline=UI_EDGE, width=1)
    d.text((pad, u(10)), "计算链（算式 → 出处）", fill=UI_INK, font=title_f)
    y = head
    for num, expr, src, rh in blocks:
        d.rounded_rectangle((pad, y, width - pad, y + rh), radius=u(8), fill=(248, 250, 252), outline=UI_EDGE)
        bx0, by0 = pad + u(8), y + u(6)
        d.rounded_rectangle((bx0, by0, bx0 + u(18), by0 + u(18)), radius=999, fill=UI_ACCENT_SOFT)
        nw, nh = _text_size(d, num, idx_f)
        d.text((bx0 + (u(18) - nw) / 2, by0 + (u(18) - nh) / 2 - 1), num, fill=UI_ACCENT, font=idx_f)
        tx = bx0 + u(26)
        d.multiline_text((tx, y + u(6)), expr, fill=UI_INK, font=expr_f, spacing=u(2))
        if src:
            d.multiline_text((pad + expr_w + u(24), y + u(6)), src, fill=UI_MUTED, font=src_f, spacing=u(2))
        y += rh + gap
    return img


def _card_frame(
    content: Image.Image,
    *,
    step: int,
    meta: str,
    label: str,
    takeaway: str = "",
    ui: float = 1.0,
    calc_chain: list[dict[str, str]] | None = None,
) -> Image.Image:
    """浅色现代卡片。ui>1 时文字按最终像素绘制，放大查看仍清晰。"""
    def u(n: float) -> int:
        return int(round(n * ui))

    margin = u(28)
    view = content
    chain_img = None
    chain_gap = 0
    if calc_chain:
        chain_img = _draw_calc_chain(max(view.width, u(720)), calc_chain, ui=ui)
        chain_gap = u(18)
    frame_w = max(view.width + margin * 2, u(760))
    note_font = _font(u(13))
    note = _wrap_chain_text(takeaway, note_font, frame_w - u(36)) if takeaway else ""
    note_h = _text_size(ImageDraw.Draw(Image.new("RGB", (8, 8))), note, note_font)[1] if note else 0
    head_h = max(u(104 if note else 78), u(68) + note_h + u(16))
    frame_h = head_h + view.height + margin * 2
    if chain_img is not None:
        frame_h += chain_gap + chain_img.height + margin
    canvas = Image.new("RGB", (frame_w, frame_h), UI_CARD)
    d = ImageDraw.Draw(canvas)

    # 顶部分割细线（非实心蓝条）
    d.rectangle((0, 0, frame_w, head_h), fill=(255, 255, 255))
    d.line((0, head_h - 1, frame_w, head_h - 1), fill=UI_EDGE, width=max(1, u(1)))

    # 步骤胶囊
    badge_txt = f"STEP {step}"
    bf = _font(u(12), bold=True)
    bw, bh = _text_size(d, badge_txt, bf)
    bx0, by0 = u(18), u(14)
    d.rounded_rectangle((bx0, by0, bx0 + bw + u(16), by0 + bh + u(8)), radius=999, fill=UI_ACCENT_SOFT)
    d.text((bx0 + u(8), by0 + u(4)), badge_txt, fill=UI_ACCENT, font=bf)

    # 页码 meta
    mf = _font(u(12))
    d.text((bx0 + bw + u(28), by0 + u(5)), meta, fill=UI_FAINT, font=mf)

    # 路径标题
    lf = _font(u(15), bold=True)
    d.text((u(18), u(42)), label, fill=UI_INK, font=lf)
    if note:
        d.multiline_text((u(18), u(68)), note, fill=(14, 116, 144), font=note_font, spacing=3)

    # 视口水平垂直居中（页眉下方的内容区四边等距）
    vx = (frame_w - view.width) // 2
    vy = head_h + margin
    d.rounded_rectangle(
        (vx - 1, vy - 1, vx + view.width, vy + view.height),
        radius=u(14),
        outline=UI_EDGE_STRONG,
        width=max(1, u(1)),
    )
    _round_paste(canvas, view, (vx, vy), radius=u(14))
    if chain_img is not None:
        cx = (frame_w - chain_img.width) // 2
        cy = vy + view.height + chain_gap
        canvas.paste(chain_img, (cx, cy))
    # 外边
    d.rounded_rectangle((0, 0, frame_w - 1, frame_h - 1), radius=u(16), outline=UI_EDGE, width=max(1, u(1)))
    return canvas


def circuit_crop_box(page: fitz.Page) -> tuple[float, float, float, float]:
    """Union of ALL circuit wires and nearby device labels, never just a route.

    Green circuit copper excludes red drawing frames and the title table. If
    this export has no identifiable copper, keep the page rather than clipping
    an unknown circuit using board-specific coordinates.
    """
    drawings=page.get_drawings(); wires=[]
    for drawing in drawings:
        color=drawing.get('color')
        if color and len(color)>=3 and color[1]>color[0]+0.12 and color[1]>color[2]+0.12:
            wires.append(drawing['rect'])
    if not wires:return tuple(page.rect)
    # Horizontal/vertical PDF line rectangles have zero area; Rect union
    # discards them as empty, so accumulate extrema explicitly.
    bounds=fitz.Rect(min(r.x0 for r in wires),min(r.y0 for r in wires),max(r.x1 for r in wires),max(r.y1 for r in wires))
    vicinity=fitz.Rect(bounds.x0-28,bounds.y0-28,bounds.x1+28,bounds.y1+28)
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            for span in line.get('spans',[]):
                rect=fitz.Rect(span['bbox']);color=span.get('color',0)
                # Circuit annotations are coloured in these CAD exports; do not
                # pull in page titles, revision tables or branding.
                channels=((color>>16)&255,(color>>8)&255,color&255)
                red_frame_text=channels[0]>channels[1]+40 and channels[0]>channels[2]+40
                if not red_frame_text and max(channels)-min(channels)>40 and vicinity.intersects(rect):
                    bounds=fitz.Rect(min(bounds.x0,rect.x0),min(bounds.y0,rect.y0),max(bounds.x1,rect.x1),max(bounds.y1,rect.y1))
    padded=fitz.Rect(bounds.x0-18,bounds.y0-8,bounds.x1+18,bounds.y1+14)&page.rect
    # Padding may reach a nearby red sheet frame. Trim padding only, never the
    # union of circuit wires/device labels, using long axis-aligned frame edges.
    for drawing in drawings:
        color=drawing.get('color')
        if not color or color[0]<=color[1]+.12 or color[0]<=color[2]+.12:continue
        edges=[]
        for item in drawing.get('items',[]):
            if item[0]=='l':edges.append((item[1],item[2]))
            elif item[0]=='re':
                r=item[1];edges.extend([(r.tl,r.tr),(r.tr,r.br),(r.br,r.bl),(r.bl,r.tl)])
        for a,b in edges:
            if abs(a.x-b.x)<.5 and abs(a.y-b.y)>.5*page.rect.height:
                if bounds.x1<a.x<padded.x1:padded.x1=max(bounds.x1,a.x-1)
                elif padded.x0<a.x<bounds.x0:padded.x0=min(bounds.x0,a.x+1)
            if abs(a.y-b.y)<.5 and abs(a.x-b.x)>.5*page.rect.width:
                if bounds.y1<a.y<padded.y1:padded.y1=max(bounds.y1,a.y-1)
                elif padded.y0<a.y<bounds.y0:padded.y0=min(bounds.y0,a.y+1)
    return tuple(padded)


def render_page_panel(
    page: fitz.Page,
    *,
    pts: list[tuple[float, float]] | None,
    polylines: list[list[tuple[float, float]]] | None,
    anchors: list[str],
    step: int,
    label: str,
    page_name: str,
    pdf_page: int,
    zoom: float = DISPLAY_ZOOM,
    mode: str = "path",
    bridge_note: dict[str, Any] | None = None,
    markers: list[dict[str, Any]] | None = None,
    crop_box: tuple[float, float, float, float] | None = None,
    takeaway: str = "",
    full_page: bool = False,
    calc_chain: list[dict[str, str]] | None = None,
    body_bridges: list[dict[str, Any]] | None = None,
    show_arrows: bool = True,
    arrow_polylines: list | None = None,
) -> Image.Image:
    # 超采样绘制后再缩小，折线/圆/文字去锯齿
    aa = AA_SCALE
    z = zoom * aa
    pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
    base = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)

    boxes = []
    for t in anchors:
        for r in page.search_for(t):
            boxes.append((r.x0 * z, r.y0 * z, r.x1 * z, r.y1 * z))

    lines: list[list[tuple[float, float]]] = []
    if polylines:
        lines = [list(p) for p in polylines if p and len(p) >= 2]
    elif pts and len(pts) >= 2:
        lines = [list(pts)]

    if mode == "path" and lines:
        for pl in lines:
            _draw_polyline(od, pl, z, s=aa, arrows=show_arrows and arrow_polylines is None)
        if show_arrows and arrow_polylines is not None:
            for pl in arrow_polylines:
                _draw_polyline(od, pl, z, s=aa, arrows=True, stroke=False)
        if bridge_note:
            _draw_bridge(od, bridge_note, lines, z, s=aa, draw_text=False)
        for bridge in body_bridges or []:
            _draw_bridge(od, bridge, lines, z, s=aa, draw_text=False)
        r, g, b, a = overlay.split()
        a = a.point(lambda v: PATH_ALPHA if v > 0 else 0)
        overlay = Image.merge("RGBA", (r, g, b, a))
    else:
        for b in boxes:
            od.rounded_rectangle(
                (b[0] - 5, b[1] - 3, b[2] + 5, b[3] + 3),
                radius=3,
                outline=(*PATH_RGB, 220),
                width=2,
            )

    composed = Image.alpha_composite(base, overlay)

    # 图幅：full_page 取图框内整幅电路（不含标题栏）；否则 crop_box；再否则按通路扩边
    if full_page:
        x0, y0, x1, y1 = circuit_crop_box(page)
        box = (
            max(0, int(x0 * z)),
            max(0, int(y0 * z)),
            min(composed.width, int(x1 * z)),
            min(composed.height, int(y1 * z)),
        )
    elif crop_box:
        x0, y0, x1, y1 = crop_box
        box = (
            max(0, int(x0 * z)),
            max(0, int(y0 * z)),
            min(composed.width, int(x1 * z)),
            min(composed.height, int(y1 * z)),
        )
    else:
        xs, ys = [], []
        for pl in lines:
            xs += [p[0] * z for p in pl]
            ys += [p[1] * z for p in pl]
        for b in boxes:
            xs += [b[0], b[2]]
            ys += [b[1], b[3]]
        if not xs:
            box = (0, 0, composed.width, composed.height)
        else:
            pad = 110
            box = (
                max(0, int(min(xs) - pad)),
                max(0, int(min(ys) - pad)),
                min(composed.width, int(max(xs) + pad)),
                min(composed.height, int(max(ys) + pad)),
            )

    # 原理图与通路线保留超采样像素；文字在这一层直接绘制，不再缩小
    crop = composed.crop(box).convert("RGBA")
    label_layer = Image.new("RGBA", crop.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(label_layer)
    if not _bridge_note_covered_by_markers(bridge_note, markers):
        _paint_bridge_label(ld, bridge_note, z, (float(box[0]), float(box[1])), aa)
    crop = Image.alpha_composite(crop, label_layer).convert("RGB")
    path_for_markers: list[tuple[float, float] | None] = []
    for pl in lines:
        path_for_markers.extend(pl)
        path_for_markers.append(None)
    crop = _draw_markers(
        crop,
        list(markers or []),
        z,
        (float(box[0]), float(box[1])),
        s=aa,
        path_pts=path_for_markers,
    )

    return _card_frame(
        crop,
        step=step,
        meta=f"第 {pdf_page} 页  ·  {page_name}",
        label=label,
        takeaway=takeaway,
        ui=aa,
        calc_chain=calc_chain,
    )


def compose_storyboard(
    panels: list[Image.Image],
    *,
    title: str,
    callout: str,
    verdict: str,
    rule_id: str,
    continues: list[int | None],
    banner: str = "工况通路图",
) -> Image.Image:
    ui = AA_SCALE
    font = _font(int(30 * ui), bold=True)
    font_s = _font(int(14 * ui))
    font_m = _font(int(16 * ui))
    gap = int(28 * ui)
    cont_h = int(56 * ui)
    side = int(40 * ui)
    width = max(p.width for p in panels) + side * 2
    height = int(140 * ui) + sum(p.height + gap + cont_h for p in panels) + int(120 * ui)

    # 浅灰底 + 顶部极淡青晕
    def u(n: float) -> int:
        return int(round(n * ui))

    canvas = Image.new("RGB", (width, height), UI_BG)
    grad_h = u(180)
    grad = Image.new("RGB", (width, grad_h), UI_BG)
    gd = ImageDraw.Draw(grad)
    for i in range(grad_h):
        t = i / max(1, grad_h - 1)
        r = int(241 + (224 - 241) * (1 - t) * 0.55)
        g = int(245 + (246 - 245) * (1 - t) * 0.55)
        b = int(249 + (252 - 249) * (1 - t) * 0.55)
        gd.line((0, i, width, i), fill=(r, g, b))
    canvas.paste(grad, (0, 0))
    d = ImageDraw.Draw(canvas)

    d.rounded_rectangle((side - u(8), u(20), width - side + u(8), u(108)), radius=u(18), fill=UI_CARD, outline=UI_EDGE, width=1)
    rule_f = _font(u(12), bold=True)
    rw, rh = _text_size(d, rule_id, rule_f)
    d.rounded_rectangle((side + u(12), u(36), side + u(12) + rw + u(18), u(36) + rh + u(8)), radius=999, fill=UI_ACCENT_SOFT)
    d.text((side + u(21), u(40)), rule_id, fill=UI_ACCENT, font=rule_f)
    d.text((side + u(12) + rw + u(30), u(40)), banner, fill=UI_FAINT, font=font_s)
    d.text((side + u(12), u(64)), title, fill=UI_INK, font=font)

    y = u(128)
    for i, panel in enumerate(panels):
        panel_x = (width-panel.width)//2
        sh = _soft_shadow((panel.width, panel.height), radius=u(16), blur=u(12), alpha=28)
        canvas.paste(sh, (panel_x - u(20), y - u(18)), sh)
        sh.close()
        canvas.paste(panel, (panel_x, y))
        y += panel.height + u(14)
        if i < len(panels) - 1:
            nxt = continues[i]
            cx = width // 2
            d.line((cx, y - u(6), cx, y + u(10)), fill=UI_EDGE_STRONG, width=max(2, u(2)))
            label = f"续至第 {nxt} 页" if nxt is not None else "续接下页"
            lf = _font(u(13), bold=True)
            lw, lh = _text_size(d, label, lf)
            bx0 = cx - (lw + u(36)) // 2
            by0 = y + u(10)
            d.rounded_rectangle((bx0, by0, bx0 + lw + u(36), by0 + lh + u(12)), radius=999, fill=UI_CARD, outline=UI_EDGE_STRONG, width=1)
            d.ellipse((bx0 + u(10), by0 + u(6), bx0 + u(22), by0 + u(18)), fill=UI_ACCENT)
            d.text((bx0 + u(28), by0 + u(6)), label, fill=UI_INK, font=lf)
            y += cont_h
        else:
            y += gap

    ok = verdict in ("通过", "是")
    failed = verdict in ("不通过", "否", "不满足")
    accent = VERDICT_OK if ok else VERDICT_FAIL if failed else (180, 120, 20)
    soft = (220, 252, 231) if ok else (255, 228, 230) if failed else (254, 243, 199)
    d.rounded_rectangle((side, y, width - side, y + u(84)), radius=u(16), fill=UI_CARD, outline=UI_EDGE, width=1)
    d.rounded_rectangle((side, y, side + u(6), y + u(84)), radius=u(3), fill=accent)
    vf = _font(u(14), bold=True)
    tag = "PASS" if ok else "FAIL" if failed else "PENDING"
    tw, th = _text_size(d, tag, vf)
    d.rounded_rectangle((side + u(22), y + u(16), side + u(22) + tw + u(16), y + u(16) + th + u(8)), radius=999, fill=soft)
    d.text((side + u(30), y + u(20)), tag, fill=accent, font=vf)
    d.text((side + u(22) + tw + u(28), y + u(18)), f"判定：{verdict}", fill=UI_INK, font=_font(u(22), bold=True))
    d.text((side + u(22), y + u(52)), callout, fill=UI_MUTED, font=font_m)
    result = canvas.crop((0, 0, width, y + u(110)))
    canvas.close()
    grad.close()
    return result


def render_spec(board: Path, spec: dict[str, Any], dest: Path, *, zoom: float = DISPLAY_ZOOM) -> Path:
    """按一份通路规格出图。dest 目录写入 flow.png 与分步图。"""
    o = out_dir(board)
    pdf_path = o / "sch" / "schematic.pdf"
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    doc = fitz.open(pdf_path)
    dest.mkdir(parents=True, exist_ok=True)

    panels: list[Image.Image] = []
    continues: list[int | None] = []
    for page_index,page_spec in enumerate(spec["pages"]):
        page_i = int(page_spec["pdf_page"]) - 1
        page = doc[page_i]
        has_path = bool(page_spec.get("pts") or page_spec.get("polylines"))
        crop = page_spec.get("crop_box")
        crop_box = tuple(crop) if crop else None  # type: ignore[arg-type]
        panel = render_page_panel(
            page,
            pts=page_spec.get("pts"),
            polylines=page_spec.get("polylines"),
            anchors=list(page_spec.get("anchors") or []),
            step=int(page_spec["step"]),
            label=str(page_spec["label"]),
            page_name=str(page_spec["page_name"]),
            pdf_page=int(page_spec["pdf_page"]),
            zoom=zoom,
            mode=str(page_spec.get("mode") or ("path" if has_path else "anchors")),
            bridge_note=page_spec.get("bridge_note"),
            markers=list(page_spec.get("markers") or []),
            crop_box=crop_box,  # type: ignore[arg-type]
            takeaway=str(page_spec.get("takeaway") or ""),
            full_page=True,
            calc_chain=list(page_spec.get("calc_chain") or []) or None,
            body_bridges=list(page_spec.get("body_bridges") or []),
            show_arrows=bool(page_spec.get("show_arrows", True)),
            arrow_polylines=page_spec.get("arrow_polylines"),
        )
        panel_path = dest / f"step{page_spec['step']}_p{page_spec['pdf_page']}.png"
        panel.save(panel_path, "PNG", optimize=True)
        # Keep each full-resolution page on disk; the combined overview need
        # not retain multiple supersampled circuit pages in memory at once.
        preview_width = 2200
        if panel.width > preview_width:
            preview = panel.resize((preview_width, max(1, round(panel.height * preview_width / panel.width))), Image.Resampling.LANCZOS)
            panel.close()
            panel = preview
        panels.append(panel)
        next_page = spec['pages'][page_index+1]['pdf_page'] if page_index+1<len(spec['pages']) else None
        continues.append(page_spec.get("continue_to") or next_page)

    board_img = compose_storyboard(
        panels,
        title=str(spec["title"]),
        callout=str(spec["callout"]),
        verdict=str(spec["verdict"]),
        rule_id=str(spec["rule_id"]),
        continues=continues,
        banner=str(spec.get("banner") or "工况通路图"),
    )
    out_path = dest / "flow.png"
    board_img.save(out_path, "PNG", optimize=True)

    meta = {
        "object": spec.get("object") or dest.name,
        "rule_id": spec["rule_id"],
        "title": spec["title"],
        "verdict": spec["verdict"],
        "callout": spec["callout"],
        "pages": [
            {
                "pdf_page": p["pdf_page"],
                "page_name": p["page_name"],
                "step": p["step"],
                "label": p["label"],
                "phase": p.get("phase", ""),
                "takeaway": p.get("takeaway", ""),
                "crop_box": list(circuit_crop_box(doc[int(p["pdf_page"])-1])),
                "crop_mode": "whole_circuit",
                "panel": f"step{p['step']}_p{p['pdf_page']}.png",
                "markers": [m.get("title") for m in (p.get("markers") or [])],
                "routing_complete": p.get("routing_complete", False),
                "routing_issues": p.get("routing_issues") or [],
                "arrows_enabled": p.get("show_arrows", True),
                "copper_segments": len(p.get("polylines") or []),
                "body_bridges": len(p.get("body_bridges") or []),
                "ambiguous_direction_edges": p.get("ambiguous_direction_edges", 0),
            }
            for p in spec["pages"]
        ],
        "flow": "flow.png",
    }
    (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    doc.close()
    board_img.close()
    for panel in panels:panel.close()
    import gc
    gc.collect()
    return out_path


def main(argv: list[str] | None = None) -> int:
    del argv
    print(
        "工况图由 python -m agent_review.stress_figures 按工况 JSON 出，不再按位号读金样表。",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
