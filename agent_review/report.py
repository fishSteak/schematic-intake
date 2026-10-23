# -*- coding: utf-8 -*-
"""Build engineer_review.html — single complete review report (7 chapters)."""

from __future__ import annotations

import html
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kernel.util import read_json, write_json


def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s))


def _sev_class(sev: str) -> str:
    return {"严重": "sev-crit", "高": "sev-high", "中": "sev-mid", "低": "sev-low"}.get(sev, "sev-mid")


def build_report_data(
    board_name: str,
    enumeration: dict[str, Any],
    verdicts: dict[str, Any],
    findings: list[dict[str, Any]],
    *,
    materials: dict[str, Any] | None = None,
    context_meta: dict[str, Any] | None = None,
    gate: dict[str, Any] | None = None,
    datasheet_miss: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sm = verdicts.get("summary") or {}
    one_liner = (
        f"不通过项 {sm.get('否', 0)}，待确认项 {sm.get('INSUFFICIENT', 0)}，"
        f"通过项 {sm.get('是', 0)}，不适用 {sm.get('不适用', 0)}；"
        f"优先处理对象 {len(findings)} 条。"
    )
    pending_docs = []
    if datasheet_miss:
        for m in (datasheet_miss.get("miss") or [])[:80]:
            pending_docs.append({"kind": "datasheet", "key": m, "affects": []})

    rules_sections = []
    for r in verdicts.get("results") or []:
        cards = list(r.get("object_cards") or [])
        # problem first
        order = {"否": 0, "待确认": 1, "INSUFFICIENT": 1, "是": 2, "不适用": 3}
        cards.sort(key=lambda c: order.get(str(c.get("verdict_zh")), 9))
        rules_sections.append(
            {
                "rule_id": r.get("rule_id"),
                "title": r.get("title"),
                "severity": r.get("severity"),
                "verdict_zh": r.get("verdict_zh"),
                "intent": r.get("intent"),
                "reasoning_overview": r.get("reasoning_overview") or [],
                "coverage_n": r.get("coverage_n"),
                "objects": cards,
            }
        )

    return {
        "board": board_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "template": "product_center_agent_review",
        "summary": sm,
        "one_liner": one_liner,
        "gate_passed": bool((gate or {}).get("passed")),
        "context": context_meta or {},
        "priority": findings,
        "rules": rules_sections,
        "pending_docs": pending_docs,
        "materials": {
            "paths": (materials or {}).get("paths") or (materials or {}).get("resolved") or {},
            "rule_ids": (materials or {}).get("rule_ids") or enumeration.get("rule_ids"),
        },
        "meta": {
            "enumeration_stats": enumeration.get("stats"),
            "review_scope": enumeration.get("review_scope"),
            "source": verdicts.get("source"),
        },
    }


def render_html(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    # escape for script tag
    payload = payload.replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>原理图评审报告 — {_esc(data.get('board'))}</title>
<style>
:root {{
  --bg: #f4f6f8;
  --card: #fff;
  --ink: #1a2332;
  --muted: #5c6b7a;
  --line: #d8dee6;
  --crit: #b42318;
  --high: #c4320a;
  --mid: #b54708;
  --ok: #067647;
  --pend: #175cd3;
  --na: #667085;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; font-family: "IBM Plex Sans", "Segoe UI", "PingFang SC", sans-serif;
  background: linear-gradient(180deg, #e8eef5 0%, var(--bg) 240px);
  color: var(--ink); line-height: 1.5;
}}
header.hero {{
  padding: 2rem 1.5rem 1.25rem; max-width: 1100px; margin: 0 auto;
}}
header.hero h1 {{ margin: 0 0 .35rem; font-size: 1.75rem; letter-spacing: -0.02em; }}
header.hero .sub {{ color: var(--muted); font-size: .95rem; }}
.wrap {{ max-width: 1100px; margin: 0 auto; padding: 0 1.5rem 3rem; }}
nav.toc {{
  display: flex; flex-wrap: wrap; gap: .5rem; margin: 1rem 0 1.5rem;
}}
nav.toc a {{
  text-decoration: none; color: var(--ink); background: var(--card);
  border: 1px solid var(--line); padding: .35rem .7rem; border-radius: 6px; font-size: .85rem;
}}
section.block {{
  background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 1.1rem 1.25rem; margin-bottom: 1rem;
}}
section.block h2 {{ margin: 0 0 .75rem; font-size: 1.15rem; }}
.stats {{ display: flex; flex-wrap: wrap; gap: .6rem; margin: .75rem 0; }}
.pill {{
  padding: .35rem .7rem; border-radius: 999px; font-size: .85rem; font-weight: 600;
  background: #eef2f6;
}}
.pill.ok {{ background: #dcfae6; color: var(--ok); }}
.pill.bad {{ background: #fee4e2; color: var(--crit); }}
.pill.pend {{ background: #d1e9ff; color: var(--pend); }}
.pill.na {{ background: #f2f4f7; color: var(--na); }}
table {{ width: 100%; border-collapse: collapse; font-size: .88rem; }}
th, td {{ border-bottom: 1px solid var(--line); padding: .45rem .4rem; text-align: left; vertical-align: top; }}
th {{ color: var(--muted); font-weight: 600; }}
.sev-crit {{ color: var(--crit); font-weight: 700; }}
.sev-high {{ color: var(--high); font-weight: 700; }}
.sev-mid {{ color: var(--mid); }}
.v-否 {{ color: var(--crit); font-weight: 700; }}
.v-待确认, .v-INSUFFICIENT {{ color: var(--pend); font-weight: 600; }}
.v-是 {{ color: var(--ok); }}
.v-不适用 {{ color: var(--na); }}
details.obj {{ border-top: 1px solid var(--line); padding: .6rem 0; }}
details.obj summary {{ cursor: pointer; font-weight: 600; }}
ol.steps {{ margin: .4rem 0; padding-left: 1.2rem; }}
.muted {{ color: var(--muted); font-size: .85rem; }}
.rule-head {{ display: flex; flex-wrap: wrap; gap: .5rem; align-items: baseline; margin-bottom: .5rem; }}
footer.meta {{ margin-top: 2rem; color: var(--muted); font-size: .8rem; }}
</style>
</head>
<body>
<header class="hero">
  <h1>原理图评审报告</h1>
  <div class="sub" id="hero-sub"></div>
</header>
<div class="wrap">
  <nav class="toc">
    <a href="#ch1">1 封面与总评</a>
    <a href="#ch2">2 优先处理清单</a>
    <a href="#ch3">3 检查项全册</a>
    <a href="#ch4">4 对象详情</a>
    <a href="#ch5">5 待确认与缺资料</a>
    <a href="#ch6">6 材料与规格书</a>
    <a href="#ch7">7 附录</a>
  </nav>
  <div id="app"></div>
</div>
<script>
const DATA = {payload};
function esc(s) {{
  if (s && typeof s === 'object') {{
    s = s.eq || s.observation || s.action || s.expr || JSON.stringify(s);
  }}
  return String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}
function vClass(v) {{
  const x = String(v||'');
  if (x === '否') return 'v-否';
  if (x === '是') return 'v-是';
  if (x === '不适用') return 'v-不适用';
  return 'v-待确认';
}}
function sevClass(s) {{
  return ({{'严重':'sev-crit','高':'sev-high','中':'sev-mid','低':'sev-low'}})[s] || 'sev-mid';
}}
function render() {{
  const d = DATA;
  const sm = d.summary || {{}};
  document.getElementById('hero-sub').textContent =
    `${{d.board}} · 产品中心 Agent 全量评审 · 生成 ${{d.generated_at || ''}}`;
  const pri = d.priority || [];
  const rules = d.rules || [];
  let objDetails = '';
  for (const r of rules) {{
    for (const c of (r.objects || [])) {{
      const id = `obj-${{esc(r.rule_id)}}-${{esc(c.object)}}`.replace(/[^\\w\\-]/g,'_');
      const steps = (c.reasoning_steps || []).map(s => `<li>${{esc(s)}}</li>`).join('');
      objDetails += `
      <details class="obj" id="${{id}}">
        <summary><span class="${{vClass(c.verdict_zh)}}">${{esc(c.verdict_zh)}}</span>
          ${{esc(r.rule_id)}} · ${{esc(c.object)}} — ${{esc(c.location_one_liner || '')}}</summary>
        <p><strong>问题在哪</strong>：${{esc(c.location_one_liner || '')}}</p>
        <p><strong>推理过程</strong></p>
        <ol class="steps">${{steps || '<li class="muted">无</li>'}}</ol>
        <p><strong>现象</strong>：${{esc(c.phenomenon || '')}}</p>
        <p><strong>计算</strong>：${{esc(c.calc || '')}}</p>
        <p><strong>卡点</strong>：${{esc(c.stuck || '')}}</p>
        <p><strong>结论</strong>：${{esc(c.conclusion || c.summary || '')}}</p>
        <p><strong>建议动作</strong>：${{esc(c.action || '—')}}</p>
      </details>`;
    }}
  }}
  const priRows = pri.map(f => `
    <tr>
      <td class="${{sevClass(f.severity)}}">${{esc(f.severity)}}</td>
      <td>${{esc(f.rule_id)}}<div class="muted">${{esc(f.title)}}</div></td>
      <td>${{esc(f.object)}}</td>
      <td class="${{vClass(f.verdict_zh)}}">${{esc(f.verdict_zh)}}</td>
      <td>${{esc(f.location_one_liner)}}</td>
      <td>${{esc(f.summary)}}</td>
      <td>${{esc((f.reasoning_steps||[])[0] || '')}}</td>
      <td>${{esc(f.action)}}</td>
    </tr>`).join('');
  const ruleBlocks = rules.map(r => {{
    const rows = (r.objects || []).map(c => {{
      const oid = ('obj-' + r.rule_id + '-' + c.object).replace(/[^\\w\\-]/g, '_');
      return `<tr>
        <td><a href="#${{oid}}">${{esc(c.object)}}</a></td>
        <td class="${{vClass(c.verdict_zh)}}">${{esc(c.verdict_zh)}}</td>
        <td>${{esc(c.location_one_liner || '')}}</td>
        <td>${{esc((c.reasoning_steps||[])[0] || '')}}</td>
        <td>${{esc(c.summary || c.conclusion || '')}}</td>
      </tr>`;
    }}).join('');
    const ov = (r.reasoning_overview || []).map(s => `<li>${{esc(s)}}</li>`).join('');
    return `
    <div class="block" style="margin-top:.75rem;border:none;padding:0">
      <div class="rule-head">
        <strong>${{esc(r.rule_id)}}</strong>
        <span>${{esc(r.title)}}</span>
        <span class="${{vClass(r.verdict_zh)}}">${{esc(r.verdict_zh)}}</span>
        <span class="muted">覆盖 ${{esc(r.coverage_n)}} · ${{esc(r.severity)}}</span>
      </div>
      <p class="muted">查什么：${{esc(r.intent)}}</p>
      <p><strong>本项推理总览</strong></p>
      <ol class="steps">${{ov || '<li class="muted">—</li>'}}</ol>
      <table>
        <thead><tr><th>对象</th><th>结论</th><th>位置</th><th>推理摘要</th><th>说明</th></tr></thead>
        <tbody>${{rows || '<tr><td colspan="5" class="muted">无对象（不适用）</td></tr>'}}</tbody>
      </table>
    </div>`;
  }}).join('');
  const pend = (d.pending_docs || []).map(p =>
    `<li>${{esc(p.kind)}} · ${{esc(typeof p.key==='string'?p.key:JSON.stringify(p.key))}}</li>`).join('');
  document.getElementById('app').innerHTML = `
  <section class="block" id="ch1">
    <h2>1 封面与总评</h2>
    <p>${{esc(d.one_liner)}}</p>
    <div class="stats">
      <span class="pill ok">通过 ${{sm['是']||0}}</span>
      <span class="pill bad">不通过 ${{sm['否']||0}}</span>
      <span class="pill pend">待确认 ${{sm['INSUFFICIENT']||0}}</span>
      <span class="pill na">不适用 ${{sm['不适用']||0}}</span>
      <span class="pill">门禁 ${{d.gate_passed ? '通过' : '未通过'}}</span>
    </div>
  </section>
  <section class="block" id="ch2">
    <h2>2 优先处理清单</h2>
    <p class="muted">否与待确认对象，按严重度排序；不点详情也能定位问题。</p>
    <table>
      <thead><tr>
        <th>严重度</th><th>检查项</th><th>对象</th><th>结论</th>
        <th>电路位置</th><th>问题一句话</th><th>推理摘要</th><th>建议动作</th>
      </tr></thead>
      <tbody>${{priRows || '<tr><td colspan="8" class="muted">无优先项</td></tr>'}}</tbody>
    </table>
  </section>
  <section class="block" id="ch3">
    <h2>3 检查项全册</h2>
    ${{ruleBlocks}}
  </section>
  <section class="block" id="ch4">
    <h2>4 对象详情</h2>
    <p class="muted">位置 → 逐步推理 → 结论与改法</p>
    ${{objDetails || '<p class="muted">无对象卡</p>'}}
  </section>
  <section class="block" id="ch5">
    <h2>5 待确认与缺资料</h2>
    <ul>${{pend || '<li class="muted">无额外缺资料记录</li>'}}</ul>
    <p class="muted">优先清单中的「待确认」行即待闭环对象。</p>
  </section>
  <section class="block" id="ch6">
    <h2>6 材料与规格书快照</h2>
    <pre class="muted" style="white-space:pre-wrap">${{esc(JSON.stringify(d.materials||{{}}, null, 2))}}</pre>
  </section>
  <details class="block" id="ch7">
    <summary><h2 style="display:inline">7 附录</h2></summary>
    <pre class="muted" style="white-space:pre-wrap">${{esc(JSON.stringify({{context:d.context, meta:d.meta, generated_at:d.generated_at}}, null, 2))}}</pre>
  </details>
  <footer class="meta">Agent 全量判定编排 · 唯一主交付 engineer_review.html</footer>`;
}}
render();
</script>
</body>
</html>
"""


def write_report(out_dir: Path, data: dict[str, Any], *, bundle: bool = True) -> Path:
    write_json(out_dir / "report_data.json", data)
    html_path = out_dir / "engineer_review.html"
    html_path.write_text(render_html(data), encoding="utf-8")
    if bundle:
        zpath = out_dir / "engineer_review_bundle.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(html_path, "engineer_review.html")
            zf.write(out_dir / "report_data.json", "report_data.json")
            findings = out_dir / "findings.json"
            if findings.is_file():
                zf.write(findings, "findings.json")
    return html_path
