# -*- coding: utf-8 -*-
"""Build pending-vs-template materials comparison HTML for product-center review."""
from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from _paths import playbook_path


def load_json(p: Path) -> dict:
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8-sig"))


def e(s) -> str:
    return html.escape("" if s is None else str(s))


# Canonical material kinds used for gap / mismatch analysis
KIND_ALIASES = [
    ("原理图", ["原理图", "SCH", "图纸"]),
    ("BOM", ["BOM", "物料"]),
    ("逻辑网表", ["逻辑网表", "网表", "Netlist", "NET"]),
    ("DRC/ERC报告", ["DRC", "ERC"]),
    ("原理图源/EDA工程", ["源文件", "EDA", "eprj", "dsn", "opj", "规则配置"]),
    ("工况/应力注释", ["工况", "功耗", "工作电压", "工作电流", "功率工况", "电压工况", "电流工况", "电源轨", "供电网络电压", "温升", "短路电流", "高压工况", "最大工作电流"]),
    ("项目/接口需求文档", ["项目需求", "电源规格", "整机接口", "输入允许", "接口速率", "信号带宽", "采样要求", "威胁等级", "环境"]),
    ("规格书/器件手册", ["规格书", "手册", "datasheet", "Datasheet", "绝对最大"]),
    ("PCB/布局约束", ["PCB", "封装库", "屏蔽", "接地策略", "布局"]),
    ("豁免/关闭记录", ["豁免", "关闭记录"]),
    ("NetTie/跳线清单", ["NetTie", "0Ω", "跳线"]),
    ("装配变体/封装映射", ["装配变体", "封装映射"]),
    ("ESD策略文档", ["ESD策略"]),
]


def split_materials(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"[;；,，、|/]+", text)
    return [p.strip() for p in parts if p.strip()]


def kinds_from_text(text: str) -> list[str]:
    raw = text or ""
    found = []
    for kind, keys in KIND_ALIASES:
        if any(k.lower() in raw.lower() if k.isascii() else k in raw for k in keys):
            found.append(kind)
    return found


def kinds_from_template(mats: str) -> list[str]:
    if not (mats or "").strip():
        return []  # empty → AI自检，无专家强制材料
    kinds = []
    for part in split_materials(mats):
        ks = kinds_from_text(part)
        if ks:
            for k in ks:
                if k not in kinds:
                    kinds.append(k)
        else:
            # keep literal fragment as soft kind
            soft = f"其他:{part[:24]}"
            if soft not in kinds:
                kinds.append(soft)
    return kinds


def present_kinds(files: list[str]) -> dict[str, list[str]]:
    mapping = defaultdict(list)
    for f in files:
        u = f.upper()
        if f.lower().endswith(".pdf") or "SCH" in u:
            mapping["原理图"].append(f)
        elif "BOM" in u or f.lower().endswith((".xlsx", ".csv")):
            mapping["BOM"].append(f)
        elif f.lower().endswith(".tel") or "NET" in u:
            mapping["逻辑网表"].append(f)
        elif "DRC" in u:
            mapping["DRC/ERC报告"].append(f)
        elif f.lower().endswith((".eprj2", ".dsn", ".opj")):
            mapping["原理图源/EDA工程"].append(f)
    return dict(mapping)


def bucket_reason(note: str, rc: str | None, expert_kinds: list[str], claimed_kinds: list[str]) -> str:
    n = note or ""
    if rc == "check_impl_gap" or any(k in n for k in ("未抽取", "自动化未", "OCR", "未能自动", "实现")):
        return "能力缺口(图面/OCR/拓扑未抽全)"
    if any(k in n for k in ("规格书", "手册", "datasheet", "Datasheet")) and "工况" not in n:
        return "规格书未闭合"
    # AI asked beyond expert materials
    extra = [k for k in claimed_kinds if k not in expert_kinds and k not in {"规格书/器件手册"}]
    # datasheet is AI自决 by policy — not a template mismatch by itself
    if expert_kinds == ["原理图"] and any(k in claimed_kinds for k in ("BOM", "工况/应力注释", "项目/接口需求文档", "PCB/布局约束")):
        return "疑似超模版要材"
    if extra and expert_kinds:
        # if claimed has kinds not in expert list (except 规格书)
        real_extra = [k for k in claimed_kinds if k not in expert_kinds and k != "规格书/器件手册"]
        if real_extra:
            return "疑似超模版要材"
    if not expert_kinds:
        return "专家材料空(AI自检)仍缺输入"
    if any(k in n for k in ("未提供", "缺", "无独立", "材料不足", "主要依赖")):
        return "模版材料未齐"
    return "其他待确认"


def claim_kinds_from_note(note: str, reason_code: str | None) -> list[str]:
    """What the AI review implied it still needs, inferred from note wording."""
    n = note or ""
    claimed = kinds_from_text(n)
    # field-on-BOM phrasing
    if "BOM" in n and ("无独立" in n or "列" in n or "字段" in n):
        if "BOM" not in claimed:
            claimed.append("BOM")
    if any(k in n for k in ("OCR", "图面", "分区文字", "看图")):
        # still 原理图, but capability — mark as 原理图深度取证
        if "原理图" not in claimed:
            claimed.append("原理图")
    if any(k in n for k in ("功耗", "工况", "应力", "温升", "轨电压", "短路电流")):
        if "工况/应力注释" not in claimed:
            claimed.append("工况/应力注释")
    if any(k in n for k in ("项目需求", "威胁等级", "整机", "环境边界", "IO 阈值", "接口规范")):
        if "项目/接口需求文档" not in claimed:
            claimed.append("项目/接口需求文档")
    if "PCB" in n:
        if "PCB/布局约束" not in claimed:
            claimed.append("PCB/布局约束")
    if any(k in n for k in ("手册", "规格书", "绝对最大", "VIH", "VIL")):
        if "规格书/器件手册" not in claimed:
            claimed.append("规格书/器件手册")
    if "豁免" in n:
        if "豁免/关闭记录" not in claimed:
            claimed.append("豁免/关闭记录")
    return claimed


def build_report(out_dir: Path) -> str:
    pb = load_json(playbook_path())
    items = {it["rule_id"]: it for it in (pb.get("items") or [])}
    inv = load_json(out_dir / "input_inventory.json")
    facts = load_json(out_dir / "facts" / "board_facts.json")
    board = facts.get("board") or "本板"

    parent = out_dir.parent
    files = sorted(p.name for p in parent.iterdir() if p.is_file()) if parent.exists() else list(inv.get("files") or [])
    present = present_kinds(files)

    # template coverage across 85
    template_kind_items: dict[str, list[str]] = defaultdict(list)
    empty_mat_items = []
    for it in pb.get("items") or []:
        rid = it["rule_id"]
        mats = (it.get("required_materials") or "").strip()
        if not mats:
            empty_mat_items.append(rid)
            continue
        for k in kinds_from_template(mats):
            template_kind_items[k].append(rid)

    # load packs
    verdict_c = Counter()
    pending_rows = []
    all_rows_mat = []  # for full 85 consistency view

    for path in sorted((out_dir / "evidence").glob("P*-A*.json")):
        pack = load_json(path)
        rid = pack.get("rule_id") or path.stem
        it = items.get(rid) or {}
        v = pack.get("verdict") or "INSUFFICIENT_EVIDENCE"
        verdict_c[v] += 1
        mats = (it.get("required_materials") or "").strip()
        expert_kinds = kinds_from_template(mats)
        note = (pack.get("note") or "").strip()
        claimed = claim_kinds_from_note(note, pack.get("reason_code"))
        # missing relative to present
        missing_vs_present = [k for k in expert_kinds if k not in present and not k.startswith("其他:")]
        # AI extras beyond expert (excluding 规格书 — AI自决政策)
        ai_extra = [
            k
            for k in claimed
            if k not in expert_kinds
            and k != "规格书/器件手册"
            and (mats != "" or k not in {"原理图", "BOM", "逻辑网表"})  # AI自检 may freely need basics
        ]
        # For empty expert mats (AI自检), extras are expected — classify separately
        if not mats:
            ai_extra = [k for k in claimed if k not in present and k != "规格书/器件手册"]

        row = {
            "rule_id": rid,
            "name": (it.get("name") or pack.get("name") or "").split("\n")[0][:72],
            "category": it.get("category") or pack.get("category") or "",
            "verdict": v,
            "mats": mats or "（空→AI自检）",
            "expert_kinds": expert_kinds,
            "claimed_kinds": claimed,
            "missing_vs_present": missing_vs_present,
            "ai_extra": ai_extra,
            "note": note,
            "reason_code": pack.get("reason_code"),
            "ai_self_check": bool(it.get("ai_self_check") or pack.get("ai_self_check")),
            "bucket": "",
        }
        all_rows_mat.append(row)
        if v in {"INSUFFICIENT_EVIDENCE", "REVIEW_REQUIRED"}:
            row["bucket"] = bucket_reason(note, pack.get("reason_code"), expert_kinds, claimed)
            pending_rows.append(row)

    # file-type gap to close ALL pending (union of missing expert kinds + justified claimed gaps)
    gap_kind_to_items: dict[str, list[str]] = defaultdict(list)
    for r in pending_rows:
        # what would unblock: expert missing first
        targets = list(r["missing_vs_present"])
        if not targets:
            # expert materials present but still pending → capability or deeper artifact
            if r["bucket"] == "能力缺口(图面/OCR/拓扑未抽全)":
                targets = ["（非缺文件）图面OCR/分区抽取能力"]
            elif r["bucket"] == "规格书未闭合":
                targets = ["规格书/器件手册"]
            elif r["bucket"].startswith("疑似超模版"):
                targets = [f"超模版诉求:{x}" for x in r["ai_extra"]] or ["复核AI要材是否合理"]
            else:
                # use claimed not present
                targets = [k for k in r["claimed_kinds"] if k not in present] or ["复核取证深度"]
        for t in targets:
            gap_kind_to_items[t].append(r["rule_id"])

    # mismatch rows: expert materials present OR AI asks beyond template
    mismatch_rows = [
        r
        for r in pending_rows
        if r["bucket"] == "疑似超模版要材"
        or (
            r["expert_kinds"]
            and not r["missing_vs_present"]
            and r["ai_extra"]
            and r["bucket"] != "能力缺口(图面/OCR/拓扑未抽全)"
        )
    ]
    # also: AI cites BOM/工况 while expert only 原理图
    for r in pending_rows:
        if r in mismatch_rows:
            continue
        if r["expert_kinds"] == ["原理图"] and any(
            k in r["claimed_kinds"] for k in ("BOM", "工况/应力注释", "项目/接口需求文档", "PCB/布局约束", "豁免/关闭记录")
        ):
            if r["bucket"] != "能力缺口(图面/OCR/拓扑未抽全)":
                r["bucket"] = "疑似超模版要材"
                mismatch_rows.append(r)

    bucket_c = Counter(r["bucket"] for r in pending_rows)

    # unique gap kinds (real files, not capability)
    real_gap_kinds = sorted(
        k
        for k in gap_kind_to_items
        if not k.startswith("（非缺文件）") and not k.startswith("超模版") and k != "复核取证深度" and k != "复核AI要材是否合理"
    )

    def pill(v: str) -> str:
        cls = {
            "PASS": "ok",
            "FAIL": "bad",
            "INSUFFICIENT_EVIDENCE": "warn",
            "NOT_APPLICABLE": "na",
            "REVIEW_REQUIRED": "warn",
        }.get(v, "")
        lab = {
            "PASS": "通过",
            "FAIL": "不通过",
            "INSUFFICIENT_EVIDENCE": "待确认",
            "NOT_APPLICABLE": "不适用",
            "REVIEW_REQUIRED": "待确认",
        }.get(v, v)
        return f"<span class='pill {cls}'>{e(lab)}</span>"

    # sections HTML
    present_html = "".join(
        f"<li><b>{e(k)}</b>：{e('、'.join(vs))}</li>" for k, vs in sorted(present.items())
    ) or "<li>无</li>"

    gap_rows_html = []
    for kind in sorted(gap_kind_to_items.keys(), key=lambda x: (-len(gap_kind_to_items[x]), x)):
        rids = gap_kind_to_items[kind]
        gap_rows_html.append(
            f"<tr><td>{e(kind)}</td><td>{len(rids)}</td><td class='mono'>{e(', '.join(rids))}</td></tr>"
        )

    template_rows = []
    for kind, rids in sorted(template_kind_items.items(), key=lambda kv: -len(kv[1])):
        have = "已提供" if kind in present else ("—政策AI自搜" if kind == "规格书/器件手册" else "未提供")
        cls = "ok" if kind in present or kind == "规格书/器件手册" else "bad"
        template_rows.append(
            f"<tr><td>{e(kind)}</td><td>{len(rids)}</td><td><span class='pill {cls}'>{e(have)}</span></td>"
            f"<td class='mono'>{e(', '.join(rids[:12]))}{'…' if len(rids)>12 else ''}</td></tr>"
        )

    pending_table = []
    for r in pending_rows:
        extra = "、".join(r["ai_extra"]) if r["ai_extra"] else "—"
        miss = "、".join(r["missing_vs_present"]) if r["missing_vs_present"] else "—"
        exp = "、".join(r["expert_kinds"]) if r["expert_kinds"] else "（空→AI自检）"
        claim = "、".join(r["claimed_kinds"]) if r["claimed_kinds"] else "—"
        flag = ""
        if r["bucket"] == "疑似超模版要材":
            flag = "<span class='flag'>超模版</span>"
        rid = e(r["rule_id"])
        pending_table.append(
            f"<tr class='{'mismatch' if r in mismatch_rows else ''}'>"
            f"<td class='mono'>{rid}</td>"
            f"<td>{e(r['name'])}</td>"
            f"<td>{e(r['bucket'])}{flag}</td>"
            f"<td>{e(r['mats'])}</td>"
            f"<td>{e(exp)}</td>"
            f"<td>{e(claim)}</td>"
            f"<td>{e(miss)}</td>"
            f"<td>{e(extra)}</td>"
            f"<td class='note'>{e(r['note'])}</td>"
            f"<td><div class='anno' contenteditable='true' data-rid='{rid}' data-ph='点击填写批注…'></div></td>"
            f"</tr>"
        )

    mismatch_table = []
    for r in mismatch_rows:
        mismatch_table.append(
            f"<tr>"
            f"<td class='mono'>{e(r['rule_id'])}</td>"
            f"<td>{e(r['name'])}</td>"
            f"<td>{e(r['mats'])}</td>"
            f"<td>{e('、'.join(r['expert_kinds']) or '（空）')}</td>"
            f"<td class='badtxt'>{e('、'.join(r['ai_extra']) or '、'.join(r['claimed_kinds']))}</td>"
            f"<td class='note'>{e(r['note'])}</td>"
            f"</tr>"
        )

    bucket_html = "".join(
        f"<div class='stat'><div class='n'>{n}</div><div class='l'>{e(b)}</div></div>"
        for b, n in bucket_c.most_common()
    )

    verdict_html = "".join(
        f"<div class='stat'><div class='n'>{verdict_c.get(k,0)}</div><div class='l'>{lab}</div></div>"
        for k, lab in [
            ("PASS", "通过"),
            ("FAIL", "不通过"),
            ("INSUFFICIENT_EVIDENCE", "待确认"),
            ("NOT_APPLICABLE", "不适用"),
        ]
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>待确认 vs 模版材料对照 · {e(board)}</title>
<style>
:root {{
  --bg:#f4f7f6; --card:#fff; --ink:#1a2b2a; --muted:#5b6e6c;
  --teal:#0f766e; --line:#d7e3e0; --bad:#b91c1c; --warn:#b45309; --ok:#047857;
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; font:15px/1.55 "IBM Plex Sans","Noto Sans SC","Microsoft YaHei",sans-serif;
  color:var(--ink); background:linear-gradient(180deg,#e8f3f1 0%, var(--bg) 220px);
}}
.wrap {{ max-width:1480px; margin:0 auto; padding:28px 20px 64px; }}
h1 {{ font-size:1.55rem; margin:0 0 6px; color:var(--teal); }}
.sub {{ color:var(--muted); margin:0 0 22px; }}
.card {{
  background:var(--card); border:1px solid var(--line); border-radius:14px;
  padding:18px 20px; margin:0 0 16px; box-shadow:0 1px 0 rgba(15,118,110,.04);
}}
h2 {{ font-size:1.1rem; margin:0 0 12px; }}
h3 {{ font-size:1rem; margin:18px 0 8px; color:var(--teal); }}
.stats {{ display:flex; flex-wrap:wrap; gap:10px; }}
.stat {{
  min-width:110px; background:#f0faf8; border:1px solid var(--line);
  border-radius:12px; padding:10px 12px;
}}
.stat .n {{ font-size:1.4rem; font-weight:700; color:var(--teal); }}
.stat .l {{ font-size:.78rem; color:var(--muted); }}
table {{ width:100%; border-collapse:collapse; font-size:.88rem; }}
th, td {{ border-bottom:1px solid var(--line); padding:8px 8px; vertical-align:top; text-align:left; }}
th {{ background:#eef7f5; color:#345; font-weight:600; position:sticky; top:0; }}
tr.mismatch {{ background:#fff7ed; }}
.mono {{ font-family:"IBM Plex Mono",Consolas,monospace; font-size:.82rem; }}
.note {{ color:#334; max-width:360px; }}
.anno {{
  min-width:180px; max-width:280px; min-height:52px; padding:6px 8px;
  border:1px dashed #99b8b3; border-radius:8px; background:#fffeeb;
  color:#1a2b2a; font-size:.84rem; line-height:1.45; outline:none;
}}
.anno:focus {{ border-color:var(--teal); background:#fff; box-shadow:0 0 0 2px rgba(15,118,110,.15); }}
.anno:empty:before {{ content:attr(data-ph); color:#9aa8a6; }}
.pill {{
  display:inline-block; padding:2px 8px; border-radius:999px; font-size:.75rem; font-weight:600;
  background:#e5e7eb; color:#374151;
}}
.pill.ok {{ background:#d1fae5; color:var(--ok); }}
.pill.bad {{ background:#fee2e2; color:var(--bad); }}
.pill.warn {{ background:#ffedd5; color:var(--warn); }}
.pill.na {{ background:#e5e7eb; color:#6b7280; }}
.flag {{
  margin-left:6px; background:#9a3412; color:#fff; font-size:.68rem;
  padding:1px 6px; border-radius:6px; vertical-align:middle;
}}
.badtxt {{ color:var(--bad); font-weight:600; }}
ul.compact li {{ margin:4px 0; }}
.callout {{
  background:#fff7ed; border-left:4px solid #ea580c; padding:10px 12px; border-radius:0 8px 8px 0;
  margin:10px 0; font-size:.92rem;
}}
.callout.ok {{ background:#ecfdf5; border-left-color:var(--ok); }}
.callout.info {{ background:#f0fdfa; border-left-color:var(--teal); }}
.footer {{ color:var(--muted); font-size:.8rem; margin-top:24px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>待确认项 × 产品中心85模版 · 材料对照</h1>
  <p class="sub">本板 <b>{e(board)}</b> · 生成 {date.today().isoformat()} ·
    模版 85 项 · 待确认 {len(pending_rows)} ·
    目的：弄清「还缺几种文件才能评估完」以及「AI 要材是否超出专家模版」</p>

  <div class="card">
    <h2>1. 本板判定总览</h2>
    <div class="stats">{verdict_html}</div>
    <h3>待确认原因分布</h3>
    <div class="stats">{bucket_html}</div>
  </div>

  <div class="card">
    <h2>2. 已提供材料（用户侧）</h2>
    <ul class="compact">{present_html}</ul>
    <p class="sub">目录文件：{e('、'.join(files))}</p>
  </div>

  <div class="card">
    <h2>3. 要评估完待确认项，还缺什么？</h2>
    <div class="callout info">
      <b>结论摘要：</b>待确认 <b>{len(pending_rows)}</b> 项。
      若只计「真实缺文件/资料种类」（不含 OCR 能力、不含超模版争议）：约 <b>{len(real_gap_kinds)}</b> 类。
      专家模版材料列为空的项共 <b>{len(empty_mat_items)}</b> 项（走 AI自检），其中落入待确认的见下表。
    </div>
    <table>
      <thead><tr><th>缺口种类</th><th>卡住项数</th><th>检查项 ID</th></tr></thead>
      <tbody>{''.join(gap_rows_html)}</tbody>
    </table>
  </div>

  <div class="card">
    <h2>4. 专家模版 85 项 · 所需材料种类覆盖</h2>
    <p class="sub">按 playbook <code>required_materials</code> 归类；空材料 = AI自检（{len(empty_mat_items)} 项）</p>
    <table>
      <thead><tr><th>材料种类</th><th>模版涉及项数</th><th>本板是否已有</th><th>涉及 ID（节选）</th></tr></thead>
      <tbody>{''.join(template_rows)}</tbody>
    </table>
  </div>

  <div class="card">
    <h2>5. 重点：疑似「超模版要材」对照</h2>
    <div class="callout">
      下列项：专家 <code>所需材料</code> 已写明范围，但 AI 待确认备注里还在要额外种类
      （规格书按政策允许 AI 自搜，<b>不</b>算超模版；BOM/工况/需求文档/PCB 等若专家未写，则列入）。
      共 <b>{len(mismatch_rows)}</b> 项，建议人工裁定：是模版漏写，还是 AI 过严。
    </div>
    <table>
      <thead>
        <tr>
          <th>ID</th><th>检查项</th><th>专家所需材料原文</th><th>专家归类</th>
          <th>AI多要的</th><th>待确认备注</th>
        </tr>
      </thead>
      <tbody>{''.join(mismatch_table) if mismatch_table else '<tr><td colspan="6">无（或未识别到超模版）</td></tr>'}</tbody>
    </table>
  </div>

  <div class="card">
    <h2>6. 全部待确认项明细（vs 模版）</h2>
    <p class="sub">最右列「批注」可直接填写；默认存在本机浏览器 localStorage。请点下方「导出批注」保存成 JSON，方便汇总进 skill 改进。</p>
    <p style="margin:8px 0 12px">
      <button type="button" id="btn-export-annos" style="margin-right:8px;padding:6px 12px;border-radius:8px;border:1px solid #0f766e;background:#0f766e;color:#fff;cursor:pointer">导出批注 JSON</button>
      <button type="button" id="btn-copy-annos" style="margin-right:8px;padding:6px 12px;border-radius:8px;border:1px solid #99b8b3;background:#fff;color:#0f766e;cursor:pointer">复制批注到剪贴板</button>
      <span id="anno-export-status" class="sub"></span>
    </p>
    <table id="pending-detail">
      <thead>
        <tr>
          <th>ID</th><th>检查项</th><th>原因桶</th>
          <th>专家材料原文</th><th>专家归类</th><th>AI备注暗示需要</th>
          <th>相对已提供仍缺(专家侧)</th><th>AI额外</th><th>备注</th><th>批注</th>
        </tr>
      </thead>
      <tbody>{''.join(pending_table)}</tbody>
    </table>
  </div>

  <div class="card">
    <h2>7. 阅读建议</h2>
    <ul class="compact">
      <li><b>模版材料未齐</b>：优先补工况表、项目/接口需求、PCB约束、豁免记录等专家已写明的输入。</li>
      <li><b>能力缺口</b>：文件其实有（多为仅原理图），缺的是 OCR/分区/复位网络等抽取能力，补文件也未必立刻变「是」。</li>
      <li><b>超模版要材</b>：先别急着补 AI 多要的东西；先改判定逻辑或回写专家材料列，避免两边标准漂移。</li>
      <li><b>规格书</b>：产品中心政策为 AI 自搜下载，不要求专家材料列写「规格书」。</li>
    </ul>
  </div>

  <p class="footer">来源：product_playbook.json（85） + review_out/evidence/*.json · 脚本 build_pending_materials_report.py</p>
</div>
<script>
(function () {{
  var KEY = "pending_vs_template_annos::" + location.pathname;
  var store = {{}};
  try {{ store = JSON.parse(localStorage.getItem(KEY) || "{{}}") || {{}}; }} catch (e) {{ store = {{}}; }}
  function syncFromDom() {{
    document.querySelectorAll(".anno[data-rid]").forEach(function (el) {{
      var rid = el.getAttribute("data-rid");
      store[rid] = (el.innerText || "").trim();
    }});
    try {{ localStorage.setItem(KEY, JSON.stringify(store)); }} catch (err) {{}}
  }}
  function rowsFromStore() {{
    syncFromDom();
    return Object.keys(store).sort().map(function (rid) {{
      return {{ rule_id: rid, annotation: store[rid] || "" }};
    }}).filter(function (r) {{ return r.annotation; }});
  }}
  document.querySelectorAll(".anno[data-rid]").forEach(function (el) {{
    var rid = el.getAttribute("data-rid");
    if (store[rid]) el.textContent = store[rid];
    el.addEventListener("input", function () {{
      store[rid] = el.innerText.trim();
      try {{ localStorage.setItem(KEY, JSON.stringify(store)); }} catch (err) {{}}
    }});
  }});
  var statusEl = document.getElementById("anno-export-status");
  var btnExport = document.getElementById("btn-export-annos");
  if (btnExport) btnExport.addEventListener("click", function () {{
    var rows = rowsFromStore();
    var blob = new Blob([JSON.stringify({{ exported_at: new Date().toISOString(), count: rows.length, annotations: rows }}, null, 2)], {{ type: "application/json" }});
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "user_annotations_pending.json";
    a.click();
    URL.revokeObjectURL(a.href);
    if (statusEl) statusEl.textContent = "已导出 " + rows.length + " 条批注 → 请放到 review_out/ 后告诉我";
  }});
  var btnCopy = document.getElementById("btn-copy-annos");
  if (btnCopy) btnCopy.addEventListener("click", function () {{
    var rows = rowsFromStore();
    var text = JSON.stringify({{ exported_at: new Date().toISOString(), count: rows.length, annotations: rows }}, null, 2);
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(text).then(function () {{
        if (statusEl) statusEl.textContent = "已复制 " + rows.length + " 条到剪贴板";
      }}).catch(function () {{
        if (statusEl) statusEl.textContent = "复制失败，请用导出 JSON";
      }});
    }} else if (statusEl) statusEl.textContent = "剪贴板不可用，请用导出 JSON";
  }});
}})();
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out_dir = Path(args.out_dir).resolve()
    doc = build_report(out_dir)
    dest = out_dir / "pending_vs_template_materials.html"
    dest.write_text(doc, encoding="utf-8")
    print(f"wrote {dest} ({len(doc)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
