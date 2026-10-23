# -*- coding: utf-8 -*-
"""Boss slide: version iteration effect — layout matched to mechanical CAD slide."""

from __future__ import annotations

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt, Emu

OUT = r"K:\project\schematic-intake\docs\AI审图_电子_版本迭代效果.pptx"
FONT = "Microsoft YaHei"

# palette close to reference slide
NAVY = RGBColor(0x1A, 0x3A, 0x6B)
TITLE_BLUE = RGBColor(0x2F, 0x6F, 0xB5)
TEXT = RGBColor(0x1A, 0x23, 0x32)
MUTED = RGBColor(0x5B, 0x6B, 0x7C)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
ALT = RGBColor(0xF3, 0xF6, 0xFA)
LINE = RGBColor(0xC8, 0xD2, 0xE0)
LOGIC_BG = RGBColor(0xE8, 0xF0, 0xF8)
LOGIC_BORDER = RGBColor(0xB8, 0xD0, 0xE8)


def set_run(run, size: float, bold: bool = False, color=TEXT) -> None:
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = FONT
    r_pr = run._r.get_or_add_rPr()
    ea = r_pr.find("{http://schemas.openxmlformats.org/drawingml/2006/main}ea")
    if ea is None:
        ea = etree.SubElement(r_pr, "{http://schemas.openxmlformats.org/drawingml/2006/main}ea")
    ea.set("typeface", FONT)


def add_rect(slide, left, top, width, height, fill, line=None, line_w=Pt(1)):
    sh = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line
        sh.line.width = line_w
    return sh


def fill_cell(cell, lines, *, sizes, bolds, colors, align=PP_ALIGN.CENTER, fill=None):
    cell.text = ""
    tf = cell.text_frame
    tf.word_wrap = True
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        run = p.add_run()
        run.text = line
        set_run(run, sizes[i], bolds[i], colors[i])
    if fill is not None:
        cell.fill.solid()
        cell.fill.fore_color.rgb = fill
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE


def main() -> None:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])

    add_rect(slide, 0, 0, prs.slide_width, prs.slide_height, WHITE)

    # Title: 01 VERSION ITERATION 版本迭代效果：规范类 48% → 88%
    box = slide.shapes.add_textbox(Inches(0.45), Inches(0.28), Inches(12.4), Inches(0.55))
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    r1 = p.add_run()
    r1.text = "01  "
    set_run(r1, 26, True, TITLE_BLUE)
    r2 = p.add_run()
    r2.text = "VERSION ITERATION  "
    set_run(r2, 22, True, TITLE_BLUE)
    r3 = p.add_run()
    r3.text = "版本迭代效果："
    set_run(r3, 22, True, TITLE_BLUE)
    r4 = p.add_run()
    r4.text = "规范类准确率 48% → 88%"
    set_run(r4, 22, True, TITLE_BLUE)

    # experiment note
    note = slide.shapes.add_textbox(Inches(0.45), Inches(0.85), Inches(12.4), Inches(0.35))
    tf = note.text_frame
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = (
        "实验说明：机械成型机产品中心原理图样本（检查清单约 85 项），按检查大类统计准确率；"
        "业务交 6 类材料，优先检 19 项 / 约 94 器件 / 自动对照约 44 份规格书。"
    )
    set_run(run, 11, False, TEXT)

    # table 4 rows x 5 cols (header + 3 versions)
    nrows, ncols = 4, 5
    table_left = Inches(0.45)
    table_top = Inches(1.3)
    table_width = Inches(12.4)
    table_height = Inches(4.35)
    shape = slide.shapes.add_table(nrows, ncols, table_left, table_top, table_width, table_height)
    table = shape.table

    widths = [Inches(2.55), Inches(1.35), Inches(1.35), Inches(1.2), Inches(5.95)]
    for i, w in enumerate(widths):
        table.columns[i].width = w

    # header
    headers = [
        ["方案 / 版本"],
        ["图纸规范与", "工程文件"],
        ["器件选型与", "物料可靠性"],
        ["其他类别"],
        ["做了什么改变"],
    ]
    for j, lines in enumerate(headers):
        fill_cell(
            table.cell(0, j),
            lines,
            sizes=[12] * len(lines),
            bolds=[True] * len(lines),
            colors=[WHITE] * len(lines),
            align=PP_ALIGN.CENTER if j else PP_ALIGN.LEFT,
            fill=NAVY,
        )

    rows = [
        {
            "name": ["v0.1", "大模型直审"],
            "m1": ["综合", "48%"],
            "m2": ["—"],
            "m3": ["—"],
            "chg": [
                "按规则与器件手册直接审查，输出带证据结论；",
                "缺工程结构化真值底座，结论易漂、难回归。",
            ],
            "alt": False,
            "merge_metrics": True,
        },
        {
            "name": ["v0.2", "算法引擎 + 智能复核"],
            "m1": ["88%"],
            "m2": ["46%"],
            "m3": ["探索中"],
            "chg": [
                "电路解析器 → 原理图知识图谱；算法引擎按器件/通路",
                "做确定性核验，智能复核为辅；规格书自动对照。",
            ],
            "alt": True,
            "merge_metrics": False,
        },
        {
            "name": ["v0.3", "Agent 智能审图主链路"],
            "m1": ["验证中"],
            "m2": ["验证中"],
            "m3": ["验证中"],
            "chg": [
                "混合智能架构 + 路由：简单项算法、复杂项 Agent；",
                "算法保留旁证与门禁；同类器件分片并行。待业务确认准确率。",
            ],
            "alt": False,
            "merge_metrics": False,
        },
    ]

    for i, row in enumerate(rows):
        r = i + 1
        bg = ALT if row["alt"] else WHITE

        fill_cell(
            table.cell(r, 0),
            row["name"],
            sizes=[12, 11],
            bolds=[True, False],
            colors=[TEXT, MUTED],
            align=PP_ALIGN.LEFT,
            fill=bg,
        )

        if row["merge_metrics"]:
            table.cell(r, 1).merge(table.cell(r, 3))
            fill_cell(
                table.cell(r, 1),
                ["综合准确率 48%", "首轮样本 · 全项汇总"],
                sizes=[14, 10],
                bolds=[True, False],
                colors=[TEXT, MUTED],
                align=PP_ALIGN.CENTER,
                fill=bg,
            )
        else:
            for j, key in enumerate(["m1", "m2", "m3"]):
                val = row[key]
                big = len(val) == 1 and val[0].endswith("%")
                fill_cell(
                    table.cell(r, j + 1),
                    val,
                    sizes=[16 if big else 12],
                    bolds=[True],
                    colors=[TEXT],
                    align=PP_ALIGN.CENTER,
                    fill=bg,
                )

        fill_cell(
            table.cell(r, 4),
            row["chg"],
            sizes=[11, 11],
            bolds=[False, False],
            colors=[TEXT, TEXT],
            align=PP_ALIGN.LEFT,
            fill=bg,
        )

    # iteration logic box
    logic_top = Inches(5.85)
    logic_h = Inches(1.25)
    add_rect(slide, Inches(0.45), logic_top, Inches(12.4), logic_h, LOGIC_BG, LOGIC_BORDER, Pt(1.25))

    logic = slide.shapes.add_textbox(Inches(0.6), logic_top + Inches(0.18), Inches(12.1), Inches(1.0))
    tf = logic.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = "迭代逻辑："
    set_run(run, 13, True, TEXT)

    p2 = tf.add_paragraph()
    p2.space_before = Pt(6)
    parts = [
        ("v0.1 ", True, TEXT),
        ("大模型直审，缺工程真值", False, TEXT),
        ("  →  ", True, TITLE_BLUE),
        ("v0.2 ", True, TEXT),
        ("引知识图谱 + 算法引擎，规范类大幅提升", False, TEXT),
        ("  →  ", True, TITLE_BLUE),
        ("v0.3 ", True, TEXT),
        ("Agent 主导复杂判定，算法作旁证与门禁", False, TEXT),
    ]
    for text, bold, color in parts:
        run = p2.add_run()
        run.text = text
        set_run(run, 13, bold, color)

    prs.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
