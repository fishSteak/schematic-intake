# -*- coding: utf-8 -*-
"""C5 preview: single red flow path only (no context, no parallel nets)."""
from __future__ import annotations

import io
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
board = next(p for p in (ROOT / "boards").iterdir() if p.is_dir() and "0918" in p.name)
pdf = board / "review_out" / "sch" / "schematic.pdf"
out_crop = Path(__file__).with_name("c5_pdf_highlight_preview.png")
out_full = Path(__file__).with_name("c5_pdf_highlight_page5.png")
out_v3 = Path(__file__).with_name("c5_pdf_highlight_preview_v3.png")

doc = fitz.open(pdf)
page = doc[4]
zoom = 2.8
mat = fitz.Matrix(zoom, zoom)
pix = page.get_pixmap(matrix=mat, alpha=False)
base = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGBA")


def Z(x: float, y: float) -> tuple[float, float]:
    return (x * zoom, y * zoom)


# 唯一通路（正交折点，贴绿线；故意不用 y=291 那条平行线，避免「上下线串色」）
# 流向：POW/开漏脚 → R16 → N_U2_FILTER → C5 → GND
FLOW_PTS = [
    (474.8, 262.4),  # U2 开漏脚外侧
    (474.8, 280.4),
    (478.4, 280.4),
    (478.4, 284.0),
    (629.6, 284.0),  # 进入 R16（只用这一条水平干线）
    (629.6, 298.4),
    (658.4, 298.4),  # 过 R16 到滤波侧（短桥，对齐右侧绿线）
    (658.4, 345.2),
    (752.0, 345.2),  # C5 上端
    (752.0, 388.4),  # C5 → GND
]

overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
od = ImageDraw.Draw(overlay)

pts = [Z(x, y) for x, y in FLOW_PTS]
od.line(pts, fill=(255, 70, 70, 45), width=10)
od.line(pts, fill=(255, 255, 255, 150), width=6)
od.line(pts, fill=(200, 25, 25, 250), width=3)

# 擦掉画进 U2 封装的部分
for d in page.get_drawings():
    col = d.get("color") or ()
    if not col or abs(col[0] - 0.63) > 0.05:
        continue
    for it in d.get("items") or []:
        if it[0] != "re":
            continue
        r = it[1]
        if 400 < r.x0 < 430 and 250 < r.y0 < 280 and r.width > 40:
            body = (r.x0 * zoom - 1, r.y0 * zoom - 1, r.x1 * zoom + 1, r.y1 * zoom + 1)
            rr, gg, bb, aa = overlay.split()
            mask = Image.new("L", base.size, 255)
            ImageDraw.Draw(mask).rectangle(body, fill=0)
            aa = Image.composite(aa, Image.new("L", base.size, 0), mask)
            overlay = Image.merge("RGBA", (rr, gg, bb, aa))
            od = ImageDraw.Draw(overlay)
            break

# 流向箭头（沿折线中点）
for i in range(len(FLOW_PTS) - 1):
    x0, y0 = FLOW_PTS[i]
    x1, y1 = FLOW_PTS[i + 1]
    if abs(x1 - x0) + abs(y1 - y0) < 12:
        continue
    mx, my = Z((x0 + x1) / 2, (y0 + y1) / 2)
    if abs(x1 - x0) >= abs(y1 - y0):
        d = 1 if x1 > x0 else -1
        od.polygon([(mx + 7 * d, my), (mx - 5 * d, my - 6), (mx - 5 * d, my + 6)], fill=(200, 25, 25, 235))
    else:
        d = 1 if y1 > y0 else -1
        od.polygon([(mx, my + 7 * d), (mx - 6, my - 5 * d), (mx + 6, my - 5 * d)], fill=(200, 25, 25, 235))

for n in (FLOW_PTS[0], FLOW_PTS[-1]):
    cx, cy = Z(*n)
    od.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=(200, 25, 25, 255), outline=(255, 255, 255, 255))

cx, cy = Z(752.0, 356)
od.ellipse((cx - 22, cy - 18, cx + 22, cy + 30), outline=(200, 25, 25, 210), width=2)

try:
    font = ImageFont.truetype("msyh.ttc", 22)
    font_s = ImageFont.truetype("msyh.ttc", 16)
except Exception:
    font = ImageFont.load_default()
    font_s = font

sx, sy = Z(*FLOW_PTS[0])
od.text((sx - 40, sy - 18), "起", fill=(200, 25, 25, 255), font=font_s)
ex, ey = Z(*FLOW_PTS[-1])
od.text((ex + 10, ey - 6), "止·C5", fill=(200, 25, 25, 255), font=font_s)

call = [
    min(Z(560, 405)[0], base.width - 360),
    min(Z(560, 405)[1], base.height - 100),
    0,
    0,
]
call[2] = min(call[0] + 340, base.width - 10)
call[3] = min(call[1] + 78, base.height - 10)
od.rounded_rectangle(call, radius=8, fill=(200, 25, 25, 235))
od.text((call[0] + 14, call[1] + 12), "C5 端压峰值 ≈15.1V", fill=(255, 255, 255, 255), font=font)
od.text((call[0] + 14, call[1] + 44), "限额 12.5V  → 不通过", fill=(255, 240, 240, 255), font=font_s)

leg = (Z(500, 248)[0], Z(500, 248)[1], Z(500, 248)[0] + 400, Z(500, 248)[1] + 38)
od.rounded_rectangle(leg, radius=8, fill=(255, 255, 255, 240), outline=(180, 180, 180, 255), width=1)
od.line((leg[0] + 12, leg[1] + 19, leg[0] + 48, leg[1] + 19), fill=(200, 25, 25, 255), width=4)
od.text((leg[0] + 56, leg[1] + 10), "仅一条通路 · 箭头表示充电流向", fill=(25, 25, 25, 255), font=font_s)

composed = Image.alpha_composite(base, overlay)
xs = [p[0] * zoom for p in FLOW_PTS] + [call[0], call[2], leg[0], leg[2]]
ys = [p[1] * zoom for p in FLOW_PTS] + [call[1], call[3], leg[1], leg[3]]
pad = 56
crop = (
    max(0, int(min(xs) - pad)),
    max(0, int(min(ys) - pad)),
    min(composed.width, int(max(xs) + pad)),
    min(composed.height, int(max(ys) + pad)),
)
cropped = composed.crop(crop).convert("RGB")
cropped.save(out_crop, "PNG", optimize=True)
cropped.save(out_v3, "PNG", optimize=True)
composed.convert("RGB").save(out_full, "PNG", optimize=True)
print("wrote", out_crop, cropped.size)
