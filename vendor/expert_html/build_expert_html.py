# -*- coding: utf-8 -*-
"""Build expert_review.html — list → detail SPA for hardware expert audit.

Usage:
  python build_expert_html.py --out-dir <review_out>
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from collections import Counter, OrderedDict
from datetime import date
from pathlib import Path

from _paths import playbook_path, product_root
from portable_paths import (
    is_abs_path_str,
    make_pack_portable,
    portable_input_dir,
    portable_path,
)

_ROOT = product_root()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from kernel.report_workbench import fmt_num, fmt_power, load_workbench, pack_devices  # noqa: E402

VERDICT_LABEL = {
    "PASS": "通过",
    "FAIL": "不通过",
    "INSUFFICIENT_EVIDENCE": "待确认",
    "NOT_APPLICABLE": "不适用",
    "REVIEW_REQUIRED": "待确认",
    "SKIPPED_OUT_OF_SCOPE": "不适用",
}
VERDICT_CHIP = {
    "PASS": "PASS",
    "FAIL": "FAIL",
    "INSUFFICIENT_EVIDENCE": "BLOCKED",
    "NOT_APPLICABLE": "NOT_APPLICABLE",
    "REVIEW_REQUIRED": "BLOCKED",
    "SKIPPED_OUT_OF_SCOPE": "NOT_APPLICABLE",
}
RISK_BY_CATEGORY = {
    "器件选型与物料可靠性": "严重",
    "电源系统PI专项检查": "严重",
    "接口、ESD、EMC、可靠性强制检查": "严重",
    "时钟、复位、时序电路": "严重",
    "高速总线&存储": "严重",
    "仿真与风险评估": "一般",
    "调试、测试、可生产性设计": "一般",
    "图纸规范与工程文件检查": "一般",
    "最终闭环输出检查": "提示",
}

CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500&family=Noto+Sans+SC:wght@400;500;700&display=swap');
:root {
  --nav-w: 300px;
  --ink: #24312e;
  --muted: #5d6c67;
  --line: #dde8e3;
  --paper: #f3f7f5;
  --card: #ffffff;
  --teal: #2b7d6f;
  --teal-soft: #e8f4f0;
  --fail: #c4413a;
  --fail-bg: #fdf4f3;
  --pass: #2a7a4a;
  --pass-bg: #eef8f1;
  --blocked: #b54708;
  --blocked-bg: #fff8ee;
  --na: #667085;
  --na-bg: #f3f5f4;
  --shadow: 0 8px 24px rgba(36,49,46,.05);
  --radius: 14px;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  color: var(--ink);
  background: var(--paper);
  font-family: "IBM Plex Sans", "Noto Sans SC", "Microsoft YaHei", sans-serif;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.chrome {
  background: #fff;
  color: var(--ink);
  padding: 10px 20px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  align-items: baseline;
  justify-content: space-between;
  flex: 0 0 auto;
  border-bottom: 1px solid var(--line);
}
.chrome h1 { margin: 0; font-size: 15px; font-weight: 650; color: var(--ink); }
.chrome .who { font-size: 12px; color: var(--muted); }
.layout { flex: 1; min-height: 0; display: flex; }
.sidenav {
  width: var(--nav-w, 300px);
  min-width: 200px;
  max-width: 520px;
  background: #fff;
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.nav-hd { padding: 10px 12px 8px; border-bottom: 1px solid var(--line); }
.nav-hd .board { font-size: 13px; font-weight: 700; }
.nav-hd .sum { font-size: 11.5px; color: var(--muted); margin-top: 4px; }
.nav-all {
  display: block; margin-top: 8px; font-size: 12.5px; font-weight: 650;
  color: var(--teal); text-decoration: none;
}
.nav-body { flex: 1; overflow: auto; }
.nav-cat { border-bottom: 1px solid #eef6f3; }
.nav-cat > summary {
  list-style: none; cursor: pointer; padding: 8px 12px;
  font-size: 12px; font-weight: 700; color: #334155;
  display: flex; justify-content: space-between; gap: 8px;
  background: #f7fbf9;
}
.nav-cat > summary::-webkit-details-marker { display: none; }
.nav-cat > summary .cnt { font-weight: 500; color: var(--muted); }
.nav-item {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px 8px;
  padding: 8px 10px 8px 14px;
  cursor: pointer;
  border-left: 3px solid transparent;
}
.nav-item:hover { background: #f7fbf9; }
.nav-item.on { background: var(--teal-soft); border-left-color: var(--teal); }
.nav-item .rid { font-family: "IBM Plex Mono", monospace; font-size: 11.5px; color: var(--teal); }
.nav-item .nm {
  grid-column: 1 / -1;
  font-size: 12px; color: var(--muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.nav-item .pill { font-size: 10.5px; padding: 1px 6px; justify-self: end; }
.splitter { flex: 0 0 6px; cursor: col-resize; background: #e4eee9; }
.splitter:hover, .splitter.drag { background: #9ec9bf; }
.main { flex: 1; min-width: 0; overflow: auto; }
.report { max-width: 1180px; margin: 0 auto; padding: 0 18px 72px; }

.toolbar {
  display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  margin: 16px 0 12px;
}
.search {
  flex: 1; min-width: 240px;
  border: 1px solid var(--line); background: #fff;
  border-radius: 12px; padding: 11px 14px;
  font: inherit; outline: none;
}
.search:focus { border-color: var(--teal); box-shadow: 0 0 0 3px rgba(15,118,110,.12); }
.btn {
  border: 1px solid var(--line); background: #fff; color: var(--ink);
  border-radius: 12px; padding: 10px 14px; font: inherit; cursor: pointer;
}
.btn.primary { background: var(--teal); border-color: var(--teal); color: #fff; }
.btn:hover { filter: brightness(1.03); }

.panel {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  overflow: hidden;
}
.panel-hd {
  padding: 14px 18px;
  border-bottom: 1px solid var(--line);
  display: flex; justify-content: space-between; align-items: center;
  background: linear-gradient(#fff, #f8fafc);
}
.panel-hd h2 { margin: 0; font-size: 1rem; }
.ds-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
  padding: 14px 16px 18px;
}
.ds-card {
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px;
  background: #fbfdff;
}
.ds-card .mpn { font-family: "IBM Plex Mono", monospace; font-size: .82rem; color: var(--teal); }
.ds-card .meta { font-size: .78rem; color: var(--muted); margin: 4px 0 8px; }
.badge {
  display: inline-flex; align-items: center; gap: 4px;
  border-radius: 999px; padding: 2px 8px; font-size: .72rem; font-weight: 600;
}
.badge.ok { background: var(--pass-bg); color: var(--pass); }
.badge.miss { background: var(--blocked-bg); color: var(--blocked); }
.dl {
  display: inline-block; margin-right: 6px; padding: 4px 9px;
  border-radius: 8px; border: 1px solid var(--teal); color: var(--teal);
  text-decoration: none; font-size: .75rem; background: #fff;
}
.dl:hover { background: var(--teal-soft); }
.dl.preview { background: var(--teal-soft); color: var(--teal); border-color: #b7ddd4; }
.dl.preview:hover { background: #d7eee8; color: var(--teal); }
.dl.miss { border-color: #c8d0d8; color: var(--muted); background: #f8fafc; }
.ds-inline {
  margin-top: 12px; padding: 12px 14px; border-radius: 12px;
  background: #f0fdfa; border: 1px solid #cdeedf;
}
.ds-inline > .lab {
  font-size: .78rem; font-weight: 700; color: #134e4a; margin-bottom: 8px;
}
.ds-chip {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  padding: 8px 10px; margin-top: 6px; background: #fff;
  border: 1px solid #d9ebe4; border-radius: 10px; font-size: .82rem;
}
.ds-chip .name { font-family: "IBM Plex Mono", monospace; color: var(--teal); font-weight: 600; }
.ds-chip .hint { color: var(--muted); font-size: .75rem; }
.wr-block {
  margin: 10px 0 14px; padding: 12px 14px; border-radius: 12px;
  background: #fff7ed; border: 1px solid #fed7aa;
}
.wr-block > .lab { font-size: .8rem; font-weight: 700; color: #9a3412; margin-bottom: 8px; }
.wr-card {
  background: #fff; border: 1px solid #fde68a; border-radius: 10px;
  padding: 10px 12px; margin-top: 8px; font-size: .82rem;
}
.wr-card .q { font-weight: 700; color: #9a3412; }
.wr-card .kv { margin-top: 4px; color: #405261; }
.wr-card .tag {
  display: inline-block; margin-top: 6px; margin-right: 6px;
  padding: 2px 8px; border-radius: 999px; font-size: .72rem; font-weight: 600;
}
.wr-card .tag.use { background: #dcfce7; color: #166534; }
.wr-card .tag.drop { background: #fee2e2; color: #991b1b; }
.cmp-table { margin: 10px 0 14px; }
.step .obs { white-space: pre-wrap; }

.list-wrap { margin-top: 16px; }
.cat-bar {
  padding: 10px 16px; background: #eef6f5; border-bottom: 1px solid var(--line);
  font-size: .86rem; font-weight: 600; color: #134e4a;
  position: sticky; top: 0; z-index: 2;
}
.cat-bar small { font-weight: 400; color: var(--muted); margin-left: 8px; }
.row {
  display: grid;
  grid-template-columns: 108px 72px 1fr 88px 72px 36px;
  gap: 10px; align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #eef2f6;
  cursor: pointer;
  transition: background .12s;
}
.row:hover { background: var(--teal-soft); }
.row.on { outline: 2px solid #b7ddd4; outline-offset: -2px; }
.row .rid { font-family: "IBM Plex Mono", monospace; font-size: .8rem; color: var(--teal); font-weight: 500; }
.row .name { font-size: .9rem; font-weight: 500; }
.row .parent { font-size: .75rem; color: var(--muted); margin-top: 2px; }
.pill {
  justify-self: start;
  border-radius: 999px; padding: 3px 9px; font-size: .72rem; font-weight: 700;
}
.pill.FAIL { background: var(--fail-bg); color: var(--fail); }
.pill.PASS { background: var(--pass-bg); color: var(--pass); }
.pill.BLOCKED, .pill.INSUFFICIENT_EVIDENCE { background: var(--blocked-bg); color: var(--blocked); }
.pill.NOT_APPLICABLE { background: var(--na-bg); color: var(--na); }
.risk-tag { font-size: .72rem; color: var(--muted); }
.chev { color: #94a3b8; font-size: 1.1rem; }

#view-detail { display: none; }
body.mode-detail #view-list { display: none; }
body.mode-detail #view-detail { display: block; }

.back {
  display: inline-flex; align-items: center; gap: 6px;
  margin-bottom: 14px; color: var(--teal); text-decoration: none; font-weight: 600;
}
.detail {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: var(--shadow);
  padding: 22px 24px 18px;
}
.detail.shallow { outline: 2px dashed #d4a017; }
.head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.title { font-size: 1.28rem; font-weight: 700; margin: 6px 0; }
.ridline { font-family: "IBM Plex Mono", monospace; color: var(--teal); font-size: .85rem; }
.stamp {
  display: inline-block; border: 3px solid var(--fail); color: var(--fail);
  font-weight: 800; padding: 10px 12px; border-radius: 10px;
  transform: rotate(-8deg); background: rgba(180,35,24,.04); line-height: 1.2;
}
.stamp.pass { border-color: var(--pass); color: var(--pass); background: rgba(6,118,71,.05); }
.stamp.blocked { border-color: var(--blocked); color: var(--blocked); background: rgba(181,71,8,.06); }
.stamp.na { border-color: var(--na); color: var(--na); background: #f8fafc; transform: none; }
.ident {
  display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; margin: 16px 0;
}
.ident .box {
  background: var(--teal-soft); border: 1px solid #cdeedf; border-radius: 12px;
  padding: 10px 12px; font-size: .8rem;
}
.ident .box b { display: block; margin-bottom: 4px; }
.sec { margin: 18px 0; }
.sec h3 {
  display: flex; align-items: center; gap: 8px;
  font-size: .98rem; margin: 0 0 10px;
}
.num {
  width: 24px; height: 24px; border-radius: 50%;
  background: var(--teal-soft); color: var(--teal); font-size: .75rem; font-weight: 700;
  display: inline-flex; align-items: center; justify-content: center;
}
table { width: 100%; border-collapse: collapse; font-size: .8rem; }
th, td { border: 1px solid var(--line); padding: 7px 8px; vertical-align: top; text-align: left; }
th { background: #eef6f5; }
.pages {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 14px;
  align-items: start;
}
.pages.compact {
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 8px 10px;
}
.pages-fold {
  border: 1px solid var(--line);
  border-radius: 12px;
  background: #f8fafc;
  padding: 0 12px 12px;
  margin: 0 0 8px;
}
.pages-fold > summary {
  cursor: pointer;
  list-style: none;
  padding: 12px 4px 10px;
  font-size: .88rem;
  font-weight: 700;
  color: #0f172a;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  user-select: none;
}
.pages-fold > summary::-webkit-details-marker { display: none; }
.pages-fold > summary::after {
  content: "展开 ▾";
  font-size: .75rem;
  font-weight: 600;
  color: #64748b;
  white-space: nowrap;
}
.pages-fold[open] > summary::after { content: "收起 ▴"; }
.pages-fold .fold-hint {
  font-weight: 500;
  color: #64748b;
  font-size: .78rem;
}
.page-card {
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fff;
  overflow: hidden;
  padding: 0;
}
.page-card.fail { border-color: #fecdca; }
.page-shot {
  background: #f8fafc;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 96px;
  max-height: 260px;
  overflow: hidden;
}
.pages.compact .page-shot {
  min-height: 72px;
  max-height: 140px;
}
.page-shot img {
  display: block;
  width: 100%;
  height: auto;
  max-height: 260px;
  object-fit: contain;
  cursor: zoom-in;
}
.pages.compact .page-shot img {
  max-height: 140px;
}
/* In-page image / PDF.js preview (no new tab) */
.img-lb {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: none;
  align-items: center;
  justify-content: center;
  background: rgba(36, 49, 46, .45);
  padding: 16px;
  box-sizing: border-box;
}
.img-lb.open { display: flex; }
.img-lb-panel {
  position: relative;
  max-width: min(96vw, 1400px);
  max-height: 92vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}
.img-lb-panel.pdf-mode {
  max-width: min(98vw, 1600px);
  width: min(98vw, 1600px);
  max-height: 96vh;
  height: 96vh;
  gap: 0;
  background: #1f2933;
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 12px 40px rgba(36,49,46,.28);
}
.img-lb-panel img {
  display: block;
  max-width: 96vw;
  max-height: calc(92vh - 48px);
  width: auto;
  height: auto;
  object-fit: contain;
  border-radius: 8px;
  box-shadow: 0 12px 40px rgba(36,49,46,.18);
  background: #fff;
  cursor: default;
}
.img-lb-panel.pdf-mode img { display: none; }
.pdf-lb-frame {
  display: none;
  width: 100%;
  height: calc(96vh - 44px);
  border: 0;
  border-radius: 0;
  background: #525659;
  box-shadow: none;
}
.img-lb-panel.pdf-mode .pdf-lb-frame { display: block; flex: 1; min-height: 0; }
.img-lb-cap {
  color: var(--ink);
  font-size: .85rem;
  text-align: center;
  max-width: 90vw;
  line-height: 1.4;
}
.img-lb-panel.pdf-mode .img-lb-cap { display: none; }
.img-lb-tools {
  position: absolute;
  top: -8px;
  right: -8px;
  z-index: 2;
  display: flex;
  gap: 8px;
  align-items: center;
}
.img-lb-panel.pdf-mode .img-lb-tools {
  position: static;
  flex-shrink: 0;
  width: 100%;
  box-sizing: border-box;
  justify-content: flex-end;
  gap: 10px;
  padding: 8px 10px;
  background: #24312e;
  border-bottom: 1px solid rgba(255,255,255,.08);
}
.img-lb-newtab {
  display: none;
  border: 0;
  border-radius: 8px;
  background: #fff;
  color: #0f172a;
  font-size: .78rem;
  font-weight: 650;
  line-height: 1;
  padding: 8px 12px;
  cursor: pointer;
  box-shadow: none;
  white-space: nowrap;
}
.img-lb-panel.pdf-mode .img-lb-newtab { display: inline-flex; }
.img-lb-newtab:hover { background: #e8f4f0; }
.img-lb-close {
  width: 36px;
  height: 36px;
  border: 0;
  border-radius: 999px;
  background: #fff;
  color: #0f172a;
  font-size: 1.25rem;
  line-height: 1;
  cursor: pointer;
  box-shadow: 0 2px 10px rgba(0,0,0,.25);
}
.img-lb-panel.pdf-mode .img-lb-close {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  box-shadow: none;
}
.img-lb-close:hover { background: #f1f5f9; }
.sheet-open-tab {
  margin-left: 8px;
  font-size: 11px;
  font-weight: 650;
  color: var(--teal);
  background: transparent;
  border: 0;
  padding: 0;
  cursor: pointer;
  text-decoration: underline;
}
.serve-banner {
  display: none;
  margin: 0 0 10px;
  padding: 10px 12px;
  border-radius: 10px;
  background: #fff8ee;
  border: 1px solid #f0d9b5;
  color: #7a3e00;
  font-size: .85rem;
  line-height: 1.45;
}
.serve-banner.show { display: block; }
.serve-banner code { font-size: .8rem; }
.page-shot button.shot-open,
.shot button.shot-open {
  display: block;
  width: 100%;
  margin: 0;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: zoom-in;
}
.shot img { cursor: zoom-in; }
.page-cap {
  padding: 8px 10px 10px;
  font-size: .78rem;
  line-height: 1.45;
  color: #334155;
}
.pages.compact .page-cap {
  padding: 6px 8px 8px;
  font-size: .72rem;
}
.page-cap .cap-title {
  font-weight: 700;
  color: #0f172a;
  margin: 0 0 2px;
}
.page-cap .cap-meta {
  color: #64748b;
  font-size: .72rem;
  margin: 0 0 4px;
}
.pages.compact .page-cap .cap-meta { font-size: .68rem; }
.page-cap .cap-note {
  color: #405261;
  margin: 0;
}
.pages.compact .page-cap .cap-note {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.page-missing {
  margin: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #fef3f2;
  border: 1px solid #fecdca;
  color: #b42318;
  font-size: .8rem;
  font-weight: 600;
}
.pages-banner {
  background: #fef3f2; border: 1px solid #fecdca; color: #912018;
  padding: 10px 12px; border-radius: 10px; font-size: .86rem; margin-bottom: 10px;
}
@media (max-width: 900px) {
  .pages { grid-template-columns: 1fr; }
  .pages.compact { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
.step { margin: 0 0 12px; font-size: .88rem; padding: 10px 12px; background: #f8fafc; border-radius: 10px; border: 1px solid #eef2f6; }
.step .obs, .step .dec { color: #405261; font-size: .82rem; margin-top: 4px; }
.shot img { max-width: min(560px, 100%); border: 1px solid var(--line); border-radius: 8px; margin-top: 8px; display: block; }
.formula {
  background: #f0fdfa; border: 1px solid #cdeedf; padding: 10px 12px;
  border-radius: 10px; font-size: .84rem; margin-bottom: 8px;
}
details.fold {
  border: 1px solid #cdeedf;
  border-radius: 10px;
  background: #f8fffc;
  margin: 0 0 12px;
  overflow: hidden;
}
details.fold > summary {
  cursor: pointer;
  list-style: none;
  padding: 10px 12px;
  font-size: .86rem;
  font-weight: 700;
  color: #134e4a;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  user-select: none;
}
details.fold > summary::-webkit-details-marker { display: none; }
details.fold > summary .fold-hint {
  font-weight: 500;
  font-size: .75rem;
  color: #64748b;
}
details.fold > summary .fold-btn {
  flex: 0 0 auto;
  font-size: .72rem;
  font-weight: 600;
  color: #0f766e;
  background: #ecfdf5;
  border: 1px solid #99f6e4;
  border-radius: 999px;
  padding: 2px 10px;
}
details.fold[open] > summary .fold-btn::after { content: "收起"; }
details.fold:not([open]) > summary .fold-btn::after { content: "展开"; }
details.fold .fold-body { padding: 0 12px 12px; }
details.fold .fold-body .formula:last-child { margin-bottom: 0; }

.banner {
  background: #fffbeb; border: 1px solid #f5d0a0; color: #7a4d00;
  padding: 8px 10px; border-radius: 10px; font-size: .82rem; margin: 10px 0;
}
.verdict-card {
  background: var(--card);
  border: 1px solid var(--line);
  border-left: 4px solid var(--fail);
  border-radius: 10px;
  padding: 18px 20px 16px;
  margin-top: 8px;
}
.verdict-card.wait { border-left-color: var(--blocked); }
.verdict-card.pass { border-left-color: var(--pass); }
.verdict-card.na { border-left-color: var(--na); }
.vrow { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.action-tag { font-size: 12.5px; color: var(--muted); }
.one { margin: 10px 0 14px; font-size: 17px; font-weight: 650; line-height: 1.45; }
.rule-summary {
  margin: 12px 0 0;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid var(--line);
  border-left: 3px solid var(--teal);
  background: var(--teal-soft);
  font-size: 14.5px;
  font-weight: 650;
  line-height: 1.5;
  color: var(--ink, #14241f);
}
.verdict-card.wait .rule-summary {
  border-left-color: var(--blocked);
  background: var(--blocked-bg);
  color: #7a4d00;
}
.verdict-card.na .rule-summary {
  border-left-color: var(--na);
  background: var(--na-bg);
  color: #3f4a45;
}
.verdict-card.fail .rule-summary {
  border-left-color: var(--fail);
  background: #fef2f2;
  color: #7f1d1d;
}
.kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0,1fr));
  gap: 8px;
}
.kpi {
  background: var(--teal-soft);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 12px;
}
.kpi b { display: block; font-size: 22px; font-variant-numeric: tabular-nums; font-weight: 700; }
.kpi.hot b { color: var(--fail); }
.kpi.wait b { color: var(--blocked); }
.kpi span { font-size: 11.5px; color: var(--muted); }
.filters { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 8px; align-items: center; }
.filt {
  border: 1px solid var(--line); background: #fff; color: var(--ink);
  border-radius: 999px; padding: 4px 10px; font-size: 12.5px; cursor: pointer;
}
.filt.on { background: var(--teal-soft); color: var(--teal); border-color: #b7ddd4; }
tr.device { cursor: pointer; }
tr.device:hover { background: #f4fbf8; }
tr.device.fail td:first-child { box-shadow: inset 3px 0 0 var(--fail); }
tr.device.wait td:first-child { box-shadow: inset 3px 0 0 var(--blocked); }
tr.device.pass td:first-child { box-shadow: inset 3px 0 0 var(--pass); }
.btn-link {
  border: 0; background: transparent; color: var(--teal);
  cursor: pointer; font-size: 12.5px; padding: 0; font-weight: 650;
}
.obj-pager {
  display: flex; align-items: center; flex-wrap: wrap; gap: 10px;
  margin: 10px 0 0; font-size: 12.5px; color: var(--muted);
}
.obj-pager button {
  border: 1px solid var(--line); background: #fff; border-radius: 8px;
  padding: 4px 10px; cursor: pointer; font-size: 12.5px;
}
.obj-pager button:disabled { opacity: .45; cursor: default; }
.obj-pager .page-size {
  margin-left: auto;
  display: inline-flex; align-items: center; gap: 6px;
}
.obj-pager .pg-size {
  border: 1px solid var(--line); background: #fff; border-radius: 8px;
  padding: 4px 8px; font-size: 12.5px; color: var(--ink);
}
.fix-list {
  margin: 0; padding: 0; list-style: none;
  display: grid; gap: 8px;
}
.fix-item {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px 14px;
}
.fix-n {
  width: 36px; height: 36px; border-radius: 10px;
  background: var(--fail-bg); color: var(--fail);
  font-weight: 700; font-size: 13px;
  display: flex; align-items: center; justify-content: center;
}
.fix-ref {
  display: inline-block;
  font-family: "IBM Plex Mono", Consolas, monospace;
  font-size: 13px; font-weight: 650;
  color: var(--fail); background: var(--fail-bg);
  border-radius: 6px; padding: 1px 8px; margin-bottom: 6px;
}
.fix-list .t { font-weight: 650; font-size: 14px; margin-bottom: 4px; }
.fix-list .s { font-size: 13px; color: #3d4a46; line-height: 1.65; margin: 0; }
.flow-box {
  margin: 0;
  padding: 14px 16px 10px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: #f7fbf9;
}
.flow-lead {
  margin: 0 0 14px;
  padding: 10px 12px;
  line-height: 1.65;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 10px;
}
.flow-tl { list-style: none; margin: 0; padding: 0; }
.flow-tl > li {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr);
  gap: 12px;
  position: relative;
  padding-bottom: 14px;
}
.flow-tl > li:last-child { padding-bottom: 2px; }
.flow-tl > li::before {
  content: "";
  position: absolute;
  left: 13px;
  top: 28px;
  bottom: 0;
  width: 2px;
  background: #c5e0d8;
}
.flow-tl > li:last-child::before { display: none; }
.flow-n {
  width: 28px; height: 28px; border-radius: 50%;
  background: var(--teal); color: #fff;
  font-size: 12px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  position: relative; z-index: 1;
  box-shadow: 0 0 0 4px #f7fbf9;
}
.flow-card {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 8px 12px 10px;
  min-width: 0;
}
.flow-name { font-weight: 650; font-size: 13.5px; }
.flow-body { display: block; margin-top: 4px; color: #3d4a46; font-size: 13px; line-height: 1.6; }
.modal-bg {
  position: fixed; inset: 0; z-index: 80;
  background: rgba(36, 49, 46, .28);
  display: none; overflow: auto; padding: 20px 12px 40px;
}
.modal-bg.open { display: block; }
.modal {
  background: #fff; border-radius: 12px;
  width: min(1440px, 100%); margin: 0 auto; overflow: hidden;
}
.modal-hd {
  position: sticky; top: 0; z-index: 2;
  background: #fff; border-bottom: 1px solid var(--line);
  padding: 12px 16px; display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-start;
}
.modal-hd .ttl { flex: 1 1 320px; min-width: 0; }
.modal-hd .ttl h3, .modal-hd .ttl .hint {
  white-space: normal; overflow-wrap: anywhere; overflow: visible;
}
.modal-hd > .icon-btn { flex: 0 0 auto; }
.modal-hd h3 { margin: 0; font-size: 16px; }
.icon-btn {
  border: 1px solid var(--line); background: #fff; border-radius: 8px;
  padding: 6px 10px; cursor: pointer; font-size: 13px; white-space: nowrap;
}
.sheet { margin: 12px 0 18px; }
.sheet .cap { font-size: 12.5px; font-weight: 650; margin: 0 0 6px; color: var(--muted); }
.sheet .cap a { margin-left: 8px; }
.sheet-grid {
  display: grid;
  gap: 10px;
  margin: 0 0 12px;
}
.sheet-grid .sheet { margin: 0; min-width: 0; }
.sheet-grid .cap { font-size: 11px; line-height: 1.35; }
img.page-full {
  width: 100%; height: auto; display: block;
  border: 1px solid var(--line); border-radius: 8px; background: #f7fbf9;
  cursor: zoom-in;
}
@media (max-width: 900px) {
  .sheet-grid { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; }
}
@media (max-width: 560px) {
  .sheet-grid { grid-template-columns: minmax(0, 1fr) !important; }
}
.pdf-frame {
  width: 100%; height: 78vh; border: 1px solid var(--line); border-radius: 8px; background: #f7fbf9;
}
.page-jump {
  display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 10px;
  position: sticky; top: 52px; z-index: 3;
  background: #fff; padding: 8px 0;
}
.page-jump button {
  border: 1px solid var(--line); background: #fff; border-radius: 8px;
  padding: 4px 8px; font-size: 12px; cursor: pointer; text-align: left;
}
.page-jump button.on { background: var(--teal-soft); color: var(--teal); border-color: #b7ddd4; }
.page-jump .why { display: block; font-size: 10.5px; color: var(--muted); }
.page-jump button.on .why { color: var(--teal); }
pre.formula {
  font-family: "IBM Plex Mono", Consolas, monospace;
  font-size: 12.5px; line-height: 1.65;
  background: #f7fbf9; color: var(--ink);
  border: 1px solid var(--line); border-radius: 8px;
  padding: 8px 10px; white-space: pre; overflow: auto; margin: 0;
}
pre.formula.blocked { background: #fff8f3; border-color: #f3e0d0; }
.obj-blurb {
  margin: 0 0 14px;
  padding: 12px 14px;
  border-radius: 12px;
  border: 1px solid #b7ddd4;
  background: linear-gradient(180deg, #f2fbf7 0%, #eef8f4 100%);
  border-left: 4px solid var(--teal);
}
.obj-blurb .blurb-k {
  display: block;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .04em;
  color: var(--teal);
  margin-bottom: 6px;
}
.obj-blurb .blurb-v {
  margin: 0;
  font-size: 14.5px;
  font-weight: 650;
  line-height: 1.55;
  color: var(--ink);
}
.sol-wait-callout {
  margin: 0 0 12px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid #f0d4b0;
  background: #fff8ee;
  color: var(--blocked);
  font-size: 12.5px;
  line-height: 1.55;
}
.sol-wait-callout b { font-weight: 700; }
.quad-formula {
  display: grid;
  gap: 8px;
}
.quad-formula .qf-row {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 10px 12px;
}
.quad-formula .qf-k {
  display: block;
  font-size: 11px;
  font-weight: 700;
  color: var(--muted);
  margin-bottom: 4px;
}
.quad-formula .qf-v {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink);
  white-space: pre-wrap;
}
.sheet.sol-wrap { margin: 10px 0 16px; }
.sol { font-size: 12px; line-height: 1.5; }
.sol.blocked .sol-state { background: #fff8f3; border-color: #f3e0d0; }
.sol-given {
  width: 100%; border-collapse: collapse; margin: 0 0 14px;
  font-size: 12px;
  table-layout: fixed;
}
.sol-given th, .sol-given td {
  padding: 7px 10px; border-bottom: 1px solid var(--line);
  text-align: left; vertical-align: top; line-height: 1.45;
}
.sol-given th { font-weight: 600; color: var(--muted); font-size: 11px; }
.sol-given th:nth-child(1), .sol-given td:nth-child(1) { width: 12%; }
.sol-given th:nth-child(2), .sol-given td:nth-child(2) { width: 58%; }
.sol-given th:nth-child(3), .sol-given td:nth-child(3) { width: 30%; min-width: 7.5em; }
.sol-given .sym-cell {
  white-space: nowrap;
  font-family: "Cambria Math", "STIX Two Text", "Times New Roman", Cambria, serif;
}
.sol-given .val-cell {
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: break-word;
  font-family: "Cambria Math", "STIX Two Text", "Times New Roman", Cambria, serif;
}
.sol-given td:nth-child(3) {
  overflow-wrap: anywhere;
  word-break: break-word;
}
.sol-given .sym-cell .sym { font-style: italic; }
.sol-given .sol-n, .sol-given .unit, .sol-given .plain { font-style: normal; }
.sol-src { color: var(--ink); }
.sol-note { color: var(--muted); font-size: 11px; }
.sol-src + .sol-note { margin-left: 6px; }
.sol-bar {
  display: flex; align-items: center; gap: 8px;
  margin: 0 0 8px;
}
.sol-bar .sol-count {
  margin-right: auto; color: var(--muted); font-size: 12px;
}
.sol-bar button {
  border: 1px solid var(--line); background: #fff; color: var(--ink);
  border-radius: 999px; padding: 4px 12px; font-size: 12px;
  cursor: pointer; line-height: 1.4;
}
.sol-bar button:hover { border-color: var(--teal); color: var(--teal); }
.sol-states { display: flex; flex-direction: column; gap: 8px; margin: 0 0 10px; }
.sol-state {
  background: #fff; border: 1px solid var(--line);
  border-radius: 10px; padding: 0; min-width: 0; overflow: hidden;
}
.sol-state > summary.sol-st-hd {
  list-style: none; cursor: pointer; user-select: none;
  font-size: 13px; font-weight: 650; margin: 0;
  display: flex; align-items: center; gap: 8px;
  padding: 11px 14px;
}
.sol-state > summary.sol-st-hd::-webkit-details-marker { display: none; }
.sol-state > summary.sol-st-hd:hover { background: #f4faf8; }
.sol-state[open] > summary.sol-st-hd { border-bottom: 1px solid var(--line); }
.sol-st-name {
  min-width: 0;
  flex: 1 1 auto;
  white-space: normal;
  overflow-wrap: anywhere;
  line-height: 1.35;
  font-weight: 600;
}
.sol-badge {
  flex-shrink: 0; font-size: 11px; font-weight: 650;
  padding: 1px 8px; border-radius: 999px; line-height: 1.7;
}
.sol-badge.pass { background: #e7f6ee; color: var(--pass); }
.sol-badge.fail { background: #fdeceb; color: var(--fail); }
.sol-badge.wait { background: #f6f1e6; color: #8a6a2f; }
.sol-chev {
  margin-left: auto; flex-shrink: 0; width: 16px; height: 16px;
  color: var(--muted); transition: transform .15s ease;
}
.sol-chev::before { content: "▾"; font-size: 12px; }
.sol-state[open] .sol-chev { transform: rotate(180deg); }
.sol-st-body { padding: 10px 14px 12px; }
.sol-stress-cap { margin: 10px 0 6px; font-size: 12px; font-weight: 650; color: var(--teal); }
.stress-cover { position: relative; display: block; width: 100%; margin: 0 0 10px; padding: 0; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; background: #f4f7f8; cursor: zoom-in; text-align: left; }
.stress-cover img { display: block; width: 100%; max-height: 420px; object-fit: contain; object-position: top center; background: #fff; }
.stress-cover-mask { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; background: rgba(15, 23, 42, .45); color: #fff; font-size: 14px; font-weight: 650; letter-spacing: .04em; opacity: 0; transition: opacity .15s ease; }
.stress-cover:hover .stress-cover-mask, .stress-cover:focus-visible .stress-cover-mask { opacity: 1; }
.stress-preview { position: fixed; inset: 0; z-index: 10050; background: #f3f7f5; display: flex; flex-direction: column; color: #24312e; }
.stress-preview[hidden] { display: none !important; }
.stress-preview .pv-top { background: #fff; border-bottom: 1px solid var(--line); padding: 10px 16px; display: flex; align-items: center; gap: 12px; }
.stress-preview .pv-top .who { font-size: 12px; color: var(--muted); }
.stress-preview .pv-back, .stress-preview .pv-tools button, .stress-preview .pv-zoom button, .stress-preview .pv-full .bar button { border: 1px solid var(--line); background: #fff; border-radius: 999px; font: inherit; font-size: 12px; cursor: pointer; }
.stress-preview .pv-back { padding: 6px 10px; border-radius: 8px; }
.stress-preview .pv-main { flex: 1; min-height: 0; display: grid; grid-template-columns: 1fr 8px var(--side-w, 380px); }
.stress-preview .pv-stage { min-width: 0; min-height: 0; display: grid; grid-template-rows: auto 1fr auto; }
.stress-preview .pv-rail { display: flex; align-items: center; gap: 8px; padding: 10px 16px; overflow: auto; }
.stress-preview .pv-step { display: flex; gap: 8px; align-items: center; min-width: 180px; text-align: left; border: 1px solid var(--line); background: #fff; border-radius: 12px; padding: 8px 10px; cursor: pointer; font: inherit; color: inherit; }
.stress-preview .pv-step.on { border-color: var(--teal); box-shadow: 0 0 0 3px rgba(31,111,98,.12); }
.stress-preview .pv-step i { width: 22px; height: 22px; border-radius: 999px; display: grid; place-items: center; background: #d7ebe6; color: var(--teal); font-style: normal; font-size: 12px; font-weight: 700; flex: none; }
.stress-preview .pv-step.on i { background: var(--teal); color: #fff; }
.stress-preview .pv-join { color: var(--muted); font-size: 12px; }
.stress-preview .pv-tools { margin-left: auto; display: flex; gap: 6px; }
.stress-preview .pv-tools button { padding: 6px 10px; }
.stress-preview .pv-tools button.on { background: var(--teal); color: #fff; border-color: var(--teal); }
.stress-preview .pv-view { min-height: 0; display: grid; gap: 10px; padding: 0 16px; }
.stress-preview .pv-view.split { grid-template-columns: 1fr 1fr; }
.stress-preview .pv-pane { position: relative; border: 1px solid var(--line); border-radius: 14px; background: #fff; overflow: hidden; min-height: 0; }
.stress-preview .pv-fullbtn { position: absolute; z-index: 2; right: 10px; top: 10px; border: 1px solid var(--line); background: rgba(255,255,255,.94); border-radius: 999px; padding: 4px 10px; font: inherit; font-size: 12px; cursor: pointer; }
.stress-preview .pv-canvas { position: absolute; inset: 0; cursor: grab; touch-action: none; }
.stress-preview .pv-canvas.dragging { cursor: grabbing; }
.stress-preview .pv-sharp { position: absolute; user-select: none; }
.stress-preview .pv-zoom { display: flex; align-items: center; gap: 8px; margin: 8px 16px 12px; width: max-content; padding: 6px 8px; border: 1px solid var(--line); border-radius: 999px; background: #fff; }
.stress-preview .pv-zoom button { width: 32px; height: 28px; padding: 0; }
.stress-preview .pv-hint { color: var(--muted); font-size: 12px; }
.stress-preview .pv-bar { cursor: col-resize; position: relative; }
.stress-preview .pv-bar::before { content: ""; position: absolute; top: 0; bottom: 0; left: 3px; width: 2px; background: var(--line); }
.stress-preview .pv-side { background: #fff; border-left: 1px solid var(--line); padding: 16px; overflow: auto; }
.stress-preview .pv-side h2 { margin: 0 0 8px; font-size: 15px; }
.stress-preview .pv-side p { margin: 0 0 12px; font-size: 15px; line-height: 1.55; }
.stress-preview .pv-chain { display: flex; flex-direction: column; gap: 10px; }
.stress-preview .pv-chain article { background: #f7fbf9; border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; }
.stress-preview .pv-chain .n { display: inline-grid; place-items: center; width: 22px; height: 22px; margin-right: 8px; border-radius: 999px; background: var(--teal); color: #fff; font-size: 12px; font-weight: 700; }
.stress-preview .pv-chain .expr { font-size: 15px; font-weight: 650; line-height: 1.45; }
.stress-preview .pv-chain .src { margin-top: 6px; color: var(--muted); font-size: 13px; line-height: 1.5; }
.stress-preview .pv-hand { margin-top: 16px; padding: 12px; border-radius: 12px; background: #e7f4f0; color: #134e48; font-size: 13px; line-height: 1.5; }
.stress-preview .pv-full { position: absolute; inset: 0; z-index: 5; background: rgba(20,28,26,.72); display: none; flex-direction: column; }
.stress-preview .pv-full.open { display: flex; }
.stress-preview .pv-full .bar { display: flex; align-items: center; gap: 10px; padding: 10px 14px; color: #fff; }
.stress-preview .pv-full .bar .spacer { flex: 1; }
.stress-preview .pv-full .bar button { border-color: rgba(255,255,255,.35); background: transparent; color: #fff; padding: 6px 12px; font-size: 13px; }
.stress-preview .pv-fullstage { flex: 1; min-height: 0; position: relative; cursor: grab; touch-action: none; }
.sol-stress-miss { margin: 8px 0 4px; padding: 8px 10px; font-size: 12px; color: var(--muted); background: #f6f1e6; border-radius: 8px; }
.sol-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.sol-dot.pass { background: var(--pass); }
.sol-dot.fail { background: var(--fail); }
.sol-dot.wait { background: var(--blocked); }
.sol-lead {
  margin: 0 0 14px; padding: 10px 12px;
  background: #f7fbf9; border: 1px solid var(--line); border-radius: 8px;
  color: var(--ink); font-size: 13px; line-height: 1.65;
}
.sol-reason { margin: 0 0 8px; color: #3d4a46; font-size: 12.5px; line-height: 1.55; }
.premise { margin: 0 0 14px; }
.premise p { margin: 0 0 8px; line-height: 1.65; font-size: 13px; }
.premise p:last-child { margin-bottom: 0; }
.premise { padding: 16px 18px; border: 1px solid var(--line); border-radius: 12px; background: #f8fbfa; }
.premise > .cap { color: var(--ink); font-size: 14px; margin-bottom: 12px; }
.premise-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 24px; }
.premise-label { color: var(--muted); font-size: 12px; margin-bottom: 6px; }
.premise-reason { grid-column: 1 / -1; border-top: 1px solid var(--line); padding-top: 10px; }
@media(max-width:700px) { .premise-grid { grid-template-columns: 1fr; } }
.why-flow { margin: 0 0 12px; padding: 10px 12px; background: #f7fbf9; border: 1px solid var(--line); border-radius: 8px; }
.why-flow p { margin: 6px 0 0; line-height: 1.65; font-size: 13px; }
.why-flow ol { margin: 8px 0 0; padding-left: 1.2em; }
.why-flow li { margin: 4px 0; font-size: 12.5px; line-height: 1.55; }
.sol-src-list { list-style: none; margin: 0; padding: 0; }
.sol-src-list li {
  display: grid; grid-template-columns: minmax(0, 1fr) minmax(140px, 42%);
  gap: 8px; padding: 4px 0; border-bottom: 1px dashed var(--line);
}
.sol-src-list .expr { font-variant-numeric: tabular-nums; }
.sol-src-list .src { color: var(--muted); font-size: 11.5px; }
.sol-step { margin: 0 0 10px; }
.sol-step:last-child { margin-bottom: 0; }
.sol-eq {
  font-family: "Cambria Math", "STIX Two Text", "Times New Roman", Cambria, serif;
  font-size: 13px; line-height: 1.5; margin: 0; color: var(--ink);
  font-style: normal;
}
.sol-eq .sym { font-style: italic; }
.sol-eq .sol-n, .sol-eq .unit, .sol-eq .plain { font-style: normal; }
.sol-eq sub { font-size: .72em; font-style: italic; }
.sol-eq sup { font-size: .68em; font-style: normal; }
.sol .sol-n, .sol .num {
  display: inline; width: auto; height: auto; min-width: 0;
  border-radius: 0; background: none; color: inherit;
  font-size: inherit; font-weight: 400; font-style: normal;
  line-height: inherit; padding: 0; margin: 0;
}
.sol-cite {
  font-family: "IBM Plex Sans", "Noto Sans SC", sans-serif;
  display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
  font-size: 11px; color: var(--muted); line-height: 1.4;
  margin: 5px 0 2px; padding: 2px 0 2px 8px;
  border-left: 2px solid var(--teal);
}
.sol-cite::before {
  content: "注";
  flex-shrink: 0;
  font-size: 10px; font-weight: 650; letter-spacing: .04em;
  color: var(--teal); background: var(--teal-soft);
  border-radius: 4px; padding: 0 5px; line-height: 1.7;
}
.sol-calc-box { margin-top: 4px; }
.sol-calc-box > summary {
  cursor: pointer; list-style: none;
  font-family: "IBM Plex Sans", "Noto Sans SC", sans-serif;
  font-size: 11px; font-weight: 650; color: var(--teal);
  user-select: none;
}
.sol-calc-box > summary::-webkit-details-marker { display: none; }
.sol-calc-box > summary::after { content: " ▾"; font-weight: 400; }
.sol-calc-box[open] > summary::after { content: " ▴"; }
.sol-calc-body {
  margin-top: 6px; padding: 8px 10px;
  background: #f7fbf9; border: 1px solid var(--line); border-radius: 6px;
}
.sol-calc-body .sol-eq { margin: 0 0 6px; font-size: 12.5px; }
.sol-calc-body .sol-eq:last-child { margin-bottom: 0; }
.sol-calc-k {
  width: 100%; border-collapse: collapse; margin: 0 0 8px; font-size: 11px;
  table-layout: fixed;
}
.sol-calc-k th, .sol-calc-k td {
  padding: 4px 6px; border-bottom: 1px solid var(--line);
  text-align: left; vertical-align: top;
}
.sol-calc-k th { font-weight: 600; color: var(--muted); font-size: 10px; }
.sol-calc-k th:nth-child(1), .sol-calc-k td:nth-child(1) { width: 28%; }
.sol-calc-k th:nth-child(2), .sol-calc-k td:nth-child(2) { width: 42%; }
.sol-calc-k th:nth-child(3), .sol-calc-k td:nth-child(3) { width: 30%; min-width: 6em; }
.sol-calc-k .sym-cell {
  white-space: nowrap;
  font-family: "Cambria Math", "STIX Two Text", "Times New Roman", Cambria, serif;
}
.sol-calc-k .val-cell,
.sol-calc-k td:nth-child(3) {
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: break-word;
  font-family: "Cambria Math", "STIX Two Text", "Times New Roman", Cambria, serif;
}
.sol-foot {
  font-size: 12px; color: var(--muted); margin: 8px 0 0; line-height: 1.45;
}
.sheet.reason-wrap { margin: 10px 0 12px; }
.reason-list {
  margin: 0; padding: 8px 12px 10px 28px; font-size: 12.5px; line-height: 1.55;
}
.reason-list li { margin: 0 0 4px; }
.js-devpack { display: none; }
.expert {
  margin-top: 14px; border-top: 1px dashed var(--line); padding-top: 12px; font-size: .84rem;
}
.expert textarea {
  width: 100%; min-height: 64px; margin-top: 8px; border-radius: 10px;
  border: 1px solid var(--line); padding: 8px 10px; font: inherit;
}
details.tech { margin-top: 10px; font-size: .78rem; color: var(--muted); }
details.tech pre {
  white-space: pre-wrap; background: #f7fbf9; color: var(--ink);
  border: 1px solid var(--line);
  padding: 12px; border-radius: 10px; max-height: 280px; overflow: auto;
}
.detail.workbench {
  background: transparent;
  border: 0;
  box-shadow: none;
  padding: 0;
  border-radius: 0;
}
.detail.workbench .title { font-size: 1.08rem; margin: 4px 0 0; }
.hint { color: var(--muted); font-size: .8rem; margin: 8px 0 0; }
.obj-table-wrap { overflow-x: auto; max-height: 520px; overflow-y: auto; border: 1px solid var(--line); border-radius: 10px; }
.tbl-wrap { overflow-x: auto; }
.tbl-wrap table.obj-cards { width: max-content; min-width: 100%; }
.tbl-wrap table.obj-cards th,
.tbl-wrap table.obj-cards td {
  white-space: nowrap;
  vertical-align: middle;
}
.tbl-wrap table.obj-cards td.explain {
  max-width: 28em;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tbl-wrap table.obj-cards .pill { white-space: nowrap; display: inline-block; }
.tbl-wrap table.obj-cards code { white-space: nowrap; word-break: normal; }
table.obj-cards { font-size: .78rem; }
table.obj-cards th { position: sticky; top: 0; background: #eef2f6; z-index: 1; }
table.obj-cards .mono { font-family: "IBM Plex Mono", Consolas, monospace; font-size: .72rem; word-break: break-all; }
.empty { padding: 28px; text-align: center; color: var(--muted); }
@media (max-width: 900px) {
  .sidenav { min-width: 160px; }
  .row { grid-template-columns: 90px 1fr 72px 24px; }
  .row .risk-tag, .row .seq { display: none; }
  .ident { grid-template-columns: 1fr; }
}
"""


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def e(s) -> str:
    return html.escape("" if s is None else str(s))


def risk_of(item: dict, pack: dict) -> str:
    if pack.get("risk") in {"严重", "一般", "提示"}:
        return pack["risk"]
    return RISK_BY_CATEGORY.get(item.get("category") or "", "一般")


def is_shallow(pack: dict) -> bool:
    steps = pack.get("steps") or []
    obs_bits = []
    for s in steps:
        if isinstance(s, str):
            obs_bits.append(s)
        elif isinstance(s, dict):
            obs_bits.append(str(s.get("observation") or s.get("action") or ""))
    obs = " ".join(obs_bits)
    if len(steps) < 2 or len(obs) < 80:
        return True
    if not pack.get("extracted") and not pack.get("calculations") and len(obs) < 160:
        return True
    return False


def board_from_facts(out_dir: Path) -> dict:
    facts = load_json(out_dir / "facts" / "board_facts.json")
    inv = load_json(out_dir / "facts" / "inventory.json")
    used = load_json(out_dir / "product_playbook_used.json")
    files = []
    if inv.get("files"):
        for key in ("dsn", "pdf", "bom", "netlist", "drc", "power", "opj", "eprj2"):
            p = (inv.get("files") or {}).get(key)
            if p:
                files.append(Path(str(p)).name)
    if not files:
        # fall back: parent input dir listing from pipeline / inventory sidecar
        inv2 = load_json(out_dir / "input_inventory.json")
        files = list(inv2.get("files") or [])
    if not files:
        parent = out_dir.parent
        if parent.exists():
            files = sorted(
                p.name
                for p in parent.iterdir()
                if p.is_file() and p.suffix.lower() in {".pdf", ".xlsx", ".tel", ".txt", ".eprj2", ".dsn", ".opj"}
            )
    board_name = facts.get("board") or "产品中心本板"
    return {
        "name": board_name,
        "input_dir": portable_input_dir(out_dir, inv.get("input_dir") or str(out_dir.parent)),
        "files": files,
        "present": inv.get("present")
        or {
            "bom": any("BOM" in f.upper() for f in files),
            "pdf": any(f.lower().endswith(".pdf") for f in files),
            "netlist": any(f.lower().endswith(".tel") or "net" in f.lower() for f in files),
            "drc": any("drc" in f.lower() for f in files),
        },
        "template": (used.get("template") if isinstance(used, dict) else None) or "产品中心",
        "item_count": (used.get("item_count") if isinstance(used, dict) else None) or 85,
    }


def load_product_items() -> list[dict]:
    pb = load_json(playbook_path())
    items = []
    for it in pb.get("items") or []:
        items.append(
            {
                "rule_id": it.get("rule_id"),
                "name": it.get("name"),
                "category": it.get("category"),
                "severity": it.get("severity"),
                "standard_text": it.get("huawei_standard") or "",
                "parent_name": it.get("category") or "",
                "parent_id": (it.get("rule_id") or "P??").split("-")[0].replace("P", "") or "",
                "child_id": (it.get("rule_id") or "A??").split("-")[-1].replace("A", "") or "",
                "atomic_seq": it.get("seq"),
                "in_scope": True,
                "huawei_standard": it.get("huawei_standard") or "",
                "required_materials": it.get("required_materials") or "",
                "expert_check_method": it.get("expert_check_method") or "",
                "result_format": it.get("result_format") or "",
                "check_process": it.get("check_process") or "",
                "playbook_filled": bool(it.get("playbook_filled")),
                "ai_self_check": bool(it.get("ai_self_check")),
            }
        )
    return items


def normalize(item: dict, pack: dict, board: dict, out_dir: Path | None = None) -> dict:
    pack = dict(pack or {})
    pack.setdefault("rule_id", item.get("rule_id"))
    pack.setdefault("why", item.get("name") or "")
    pack.setdefault("standard", item.get("standard_text") or item.get("huawei_standard") or "")
    pack.setdefault("layer", "AI")
    pack.setdefault("verdict", "INSUFFICIENT_EVIDENCE")
    pack.setdefault("risk", item.get("severity") or RISK_BY_CATEGORY.get(item.get("category") or "", "一般"))
    b = pack.get("board") if isinstance(pack.get("board"), dict) else {}
    in_dir = b.get("input_dir") or board["input_dir"]
    if out_dir is not None:
        in_dir = portable_input_dir(out_dir, in_dir)
    pack["board"] = {
        "name": b.get("name") or board["name"],
        "input_dir": in_dir,
        "files": b.get("files") or board["files"],
    }
    xb = pack.get("cross_board") if isinstance(pack.get("cross_board"), dict) else {}
    pack["cross_board"] = {
        "applies_to_all_schematic_reviews": xb.get("applies_to_all_schematic_reviews", True),
        "in_scope_this_phase": xb.get("in_scope_this_phase", item.get("in_scope", True)),
        "import_sample_verdict": xb.get("import_sample_verdict"),
        "import_sample_note": xb.get("import_sample_note")
        or "产品中心 85 项模版；历史样例板结论不得改写本板判定。",
        "used_sample_as_this_board": bool(xb.get("used_sample_as_this_board", False)),
    }
    if not pack.get("pages_checked"):
        pack["pages_checked"] = [
            {
                "page": "SCH_原版拆分 / PAGE1",
                "status": pack.get("verdict"),
                "note": "本跑次未挂原理图截图；请结合 PDF 人工复核图面。",
            }
        ]
    # normalize references to {path, role}
    refs = pack.get("references") or []
    norm_refs = []
    for r in refs:
        if isinstance(r, str):
            norm_refs.append({"path": r, "role": "输入材料"})
        elif isinstance(r, dict):
            norm_refs.append(r)
    pack["references"] = norm_refs
    # normalize steps to dict form
    steps = pack.get("steps") or []
    norm_steps = []
    for i, s in enumerate(steps, 1):
        if isinstance(s, str):
            norm_steps.append(
                {"step": i, "action": f"步骤{i}", "observation": s, "decision": ""}
            )
        elif isinstance(s, dict):
            d = dict(s)
            d.setdefault("step", i)
            d.setdefault("action", d.get("action") or f"步骤{i}")
            d.setdefault("observation", d.get("observation") or "")
            d.setdefault("decision", d.get("decision") or "")
            norm_steps.append(d)
    pack["steps"] = norm_steps
    # expert_playbook present/filled for UI
    g = pack.get("expert_playbook") if isinstance(pack.get("expert_playbook"), dict) else {}
    if not g:
        g = {
            "required_materials": item.get("required_materials") or "",
            "expert_check_method": item.get("expert_check_method") or "",
            "result_format": item.get("result_format") or "",
            "check_process": item.get("check_process") or "",
            "need_datasheet_query": "AI自决",
            "need_human": "AI自决",
        }
    mats = (g.get("required_materials") or "").strip()
    proc = (g.get("check_process") or "").strip()
    g["present"] = True
    g["filled"] = bool(mats and proc) and not bool(pack.get("ai_self_check") or item.get("ai_self_check"))
    g["severity"] = pack.get("severity") or item.get("severity") or ""
    g["note"] = (
        "专家过程/材料未填齐 → AI自检"
        if pack.get("ai_self_check") or item.get("ai_self_check")
        else "按专家过程执行"
    )
    g.setdefault("need_datasheet_query", "AI自决")
    g.setdefault("need_human", "AI自决")
    pack["expert_playbook"] = g
    pack.setdefault("remediation", "")
    if pack.get("verdict") == "FAIL" and not pack.get("remediation"):
        pack["remediation"] = pack.get("note") or "按判定说明整改后复测。"
    return pack


def stamp_class(verdict: str) -> str:
    if verdict == "PASS":
        return "pass"
    if verdict in {"INSUFFICIENT_EVIDENCE", "REVIEW_REQUIRED"}:
        return "blocked"
    if verdict in {"NOT_APPLICABLE", "SKIPPED_OUT_OF_SCOPE"}:
        return "na"
    return ""


def render_extracted(pack: dict) -> str:
    rows = pack.get("extracted") or []
    if not rows:
        return "<p class='hint'>本项未提供结构化抽取表；请结合下方推导步骤与技术附录核对。</p>"
    body = "".join(
        "<tr>"
        f"<td>{e(r.get('source_file'))}</td><td><code>{e(r.get('extractor'))}</code></td>"
        f"<td>{e(r.get('object_id') or r.get('object'))}</td><td>{e(r.get('field'))}</td><td>{e(r.get('value'))}</td>"
        "</tr>"
        for r in rows
    )
    return (
        "<table><thead><tr><th>源文件</th><th>抽取器</th><th>对象</th><th>字段</th><th>抽取值</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


_BLOCK_ZH = {
    "miss_datasheet": "缺手册/额定",
    "extract_fail": "手册未抽出",
    "miss_stress": "缺应力",
    "topology_open": "拓扑开路",
    "miss_taskbook": "缺任务书/工况",
    "source_conflict": "多源冲突",
    "device_irregular": "器件不规范",
    "not_in_scope": "本项不适用字段",
    "rule_gap": "规则不足以闭合",
}


def render_object_cards(pack: dict) -> str:
    """逐对象四格表：现象 / 计算 / 卡点 / 结论（+定位/身份/手册；应力类附加原先→推理）。"""
    cards = pack.get("object_cards") or []
    if not cards:
        return (
            "<p class='hint'>本项暂无逐对象卡（object_cards）。"
            "应力类检查应列出位号；网名类列出网名。</p>"
        )
    card_class = pack.get("card_class") or ""
    show_stress = card_class == "stress_calc" or any(
        c.get("formula") or c.get("computed") or c.get("before") for c in cards
    )
    head = (
        "<tr><th>对象</th><th>定位</th><th>身份</th><th>手册抽出</th>"
        + ("<th>原先→推理后</th><th>公式/限额</th>" if show_stress else "")
        + "<th>现象</th><th>计算</th><th>卡点</th><th>结论</th><th>判定</th><th>原因码</th></tr>"
    )
    kind_zh = {
        "operating_point": "工作点",
        "p_max_envelope": "P_max包络",
    }
    body_parts = []
    for c in cards:
        loc = c.get("location") or {}
        ident = c.get("identity") or {}
        ds = c.get("datasheet") or {}
        loc_s = " / ".join(
            str(x)
            for x in (
                f"p{loc.get('pdf_page')}" if loc.get("pdf_page") is not None else "",
                loc.get("page_name") or "",
                loc.get("module_title") or "",
            )
            if x
        ) or "—"
        id_s = " · ".join(
            str(x)
            for x in (
                ident.get("mpn") or "",
                ident.get("value_bom") or ident.get("value_pdf") or "",
                ident.get("footprint") or "",
            )
            if x
        ) or "—"
        ext = ds.get("extracted") or {}
        ds_s = " · ".join(f"{k}={v}" for k, v in ext.items()) or (ds.get("extract_note") or "—")
        if ds.get("local_path"):
            ds_s += f"<br/><span class='hint'>{e(Path(str(ds['local_path'])).name)}</span>"
        stress_cols = ""
        if show_stress:
            before = c.get("before") or {}
            after = c.get("after") or {}
            # Compact before→after: prefer voltages over raw JSON dump
            def _brief_node(nd: dict) -> str:
                if not isinstance(nd, dict):
                    return ""
                v = nd.get("value")
                st = nd.get("status") or ""
                if v is not None:
                    return f"{v}V/{st}" if st else f"{v}V"
                return str(st or "—")

            if before:
                before_s = e(
                    " · ".join(f"{k}={_brief_node(v)}" for k, v in before.items())[:220]
                )
            else:
                before_s = "—"
            after_bits = []
            if after.get("v") is not None:
                after_bits.append(f"V={after.get('v')}")
            if after.get("R") is not None:
                after_bits.append(f"R={after.get('R')}")
            if after.get("i") is not None:
                after_bits.append(f"I={after.get('i')}")
            sk = after.get("stress_kind") or c.get("stress_kind")
            if sk:
                after_bits.append(kind_zh.get(sk, sk))
            after_s = e(" · ".join(str(x) for x in after_bits)) if after_bits else (
                e(json.dumps(after, ensure_ascii=False)[:120]) if after else "—"
            )
            lim = c.get("limit")
            comp = c.get("computed") or {}
            formula = e(c.get("formula") or "")
            sk2 = c.get("stress_kind") or sk
            sk_s = kind_zh.get(sk2, sk2) if sk2 else ""
            lim_parts = []
            if formula:
                lim_parts.append(formula)
            if sk_s:
                lim_parts.append(f"口径={sk_s}")
            if comp.get("P_max_w") is not None:
                lim_parts.append(f"P_max={comp.get('P_max_w')}")
            if lim is not None:
                lim_parts.append(f"限额={lim}")
            lim_s = "<br/>".join(lim_parts) if lim_parts else "—"
            stress_cols = (
                f"<td><div class='mono'>{before_s}</div>→"
                f"<div class='mono'>{after_s}</div></td><td>{lim_s}</td>"
            )
        vzh = c.get("verdict_zh") or ""
        bc = c.get("block_class") or ""
        bc_zh = _BLOCK_ZH.get(bc, bc) if bc else "—"
        phen = c.get("phenomenon") or ""
        calc = c.get("calc") or c.get("formula") or ""
        stuck = c.get("stuck") or ("无" if vzh in {"是", "否", "不适用"} else "—")
        conclusion = c.get("conclusion") or c.get("summary") or vzh or ""
        # 待确认：卡点列必须可见网/脚/字段
        if vzh in {"待确认", "INSUFFICIENT"} and (not stuck or stuck == "—"):
            stuck = bc_zh if bc else "未写卡点"
        body_parts.append(
            "<tr>"
            f"<td><code>{e(c.get('object'))}</code></td>"
            f"<td>{e(loc_s)}</td>"
            f"<td>{e(id_s)}</td>"
            f"<td>{ds_s}</td>"
            f"{stress_cols}"
            f"<td>{e(phen)}</td>"
            f"<td>{e(calc)}</td>"
            f"<td class='stuck'>{e(stuck)}</td>"
            f"<td>{e(conclusion)}</td>"
            f"<td>{e(vzh)}</td>"
            f"<td>{e(bc_zh)}</td>"
            "</tr>"
        )
    filt = (
        "<p class='hint'>共 "
        f"{len(cards)} 个对象 · 四格=现象/计算/卡点/结论"
        + (f" · card_class={e(card_class)}" if card_class else "")
        + "。待确认须能看出卡在哪一网/脚/字段。</p>"
    )
    return (
        filt
        + "<div class='obj-table-wrap'><table class='obj-cards'>"
        + f"<thead>{head}</thead><tbody>{''.join(body_parts)}</tbody></table></div>"
    )


def datasheet_local_ok(ds: dict, out_dir: Path) -> tuple[bool, str]:
    """Resolve a usable local PDF path for a datasheet entry.

    Tries explicit local_path first, then MPN/title fuzzy match under datasheets/.
    """
    candidates: list[str] = []
    local = (ds.get("local_path") or "").replace("\\", "/")
    if local:
        if is_abs_path_str(local):
            local = portable_path(out_dir, local)
        candidates.append(local)
    mpn = str(ds.get("mpn") or "")
    title = str(ds.get("title") or "")
    keys = []
    for raw in (mpn, title):
        s = raw.strip()
        if not s:
            continue
        keys.append(s)
        keys.append(s.split()[0])
        # ACST1235-7GTR -> ACST1235
        if "-" in s:
            keys.append(s.split("-")[0])
    ds_dir = out_dir / "datasheets"
    if ds_dir.exists():
        pdfs = list(ds_dir.glob("*.pdf"))
        for k in keys:
            k_u = k.upper()
            for p in pdfs:
                if k_u in p.stem.upper():
                    candidates.append(f"datasheets/{p.name}")
    seen = set()
    for rel in candidates:
        if not rel or rel in seen:
            continue
        seen.add(rel)
        p = out_dir / rel
        if p.exists() and p.read_bytes()[:4] == b"%PDF":
            return True, rel
    return False, local


def render_ds_actions(ds: dict, out_dir: Path) -> str:
    """Preview + download links for one datasheet entry."""
    ok, local = datasheet_local_ok(ds, out_dir)
    url = ds.get("source_url") or ""
    if ok:
        return (
            f"<a class='dl preview' href='{e(local)}' target='_blank' rel='noopener'>预览</a>"
            f"<a class='dl' href='{e(local)}' download>下载</a>"
        )
    if url:
        return (
            f"<a class='dl preview' href='{e(url)}' target='_blank' rel='noopener'>预览源站</a>"
            f"<a class='dl miss' href='{e(url)}' target='_blank' rel='noopener'>打开源 URL</a>"
        )
    return "<span class='dl miss'>未下载到本地</span>"


def render_conclusion_datasheets(pack: dict, out_dir: Path) -> str:
    """Inline datasheet chips under最终结论 — next to '可追溯' claims."""
    sheets = pack.get("datasheets") or []
    if not sheets:
        return ""
    chips = []
    for ds in sheets:
        label = e(ds.get("title") or ds.get("mpn") or "规格书")
        mpn = e(ds.get("mpn") or "")
        ok, _ = datasheet_local_ok(ds, out_dir)
        status = "本地可追溯" if ok else "缺本地 PDF"
        chips.append(
            f"<div class='ds-chip'><span class='name'>{label}</span>"
            f"<span class='hint'>{mpn} · {status}</span>"
            f"{render_ds_actions(ds, out_dir)}</div>"
        )
    return (
        "<div class='ds-inline'><div class='lab'>本结论引用的规格书（预览 / 下载）</div>"
        + "".join(chips)
        + "</div>"
    )


def render_refs(pack: dict, out_dir: Path) -> str:
    bits = ["<ul>"]
    for r in pack.get("references") or []:
        bits.append(f"<li>{e(r.get('role'))}：{e(r.get('path'))}</li>")
    for ds in pack.get("datasheets") or []:
        label = e(ds.get("title") or ds.get("mpn") or "规格书")
        ok, _ = datasheet_local_ok(ds, out_dir)
        miss = "" if ok else f" — {e(ds.get('download_error') or '本地无效/缺失')}"
        bits.append(
            f"<li>规格书 {label}（{e(ds.get('mpn'))}）"
            f"{render_ds_actions(ds, out_dir)}"
            f"{miss}</li>"
        )
    if len(bits) == 1:
        bits.append("<li>未列出引用文件</li>")
    bits.append("</ul>")
    return "".join(bits)


def render_web_research(pack: dict) -> str:
    rows = pack.get("web_research") or []
    if not rows:
        return ""
    parts = [
        "<div class='wr-block'><div class='lab'>联网 / 规格书检索发现（查了什么 · 抽到什么 · 用了什么 · 没用什么）</div>"
    ]
    for i, r in enumerate(rows, 1):
        srcs = r.get("sources") or []
        if isinstance(srcs, list):
            src_bits = []
            for x in srcs:
                s = str(x)
                if s.startswith("http"):
                    src_bits.append(f"<a href='{e(s)}' target='_blank' rel='noopener'>{e(s)}</a>")
                else:
                    src_bits.append(e(s))
            src_s = "；".join(src_bits)
        else:
            src_s = e(srcs)
        ext = r.get("extracted") or {}
        if isinstance(ext, dict):
            ext_rows = "".join(
                f"<tr><td><code>{e(k)}</code></td><td>{e(v)}</td></tr>" for k, v in ext.items()
            )
            ext_html = (
                f"<table class='cmp-table'><thead><tr><th>抽取字段</th><th>值</th></tr></thead>"
                f"<tbody>{ext_rows}</tbody></table>"
                if ext_rows
                else "<div class='kv'>（无结构化抽取）</div>"
            )
        else:
            ext_html = f"<div class='kv'>{e(ext)}</div>"
        useful = r.get("useful_for_decision") or r.get("useful") or ""
        not_used = r.get("not_used") or ""
        page = r.get("pages_or_section") or ""
        parts.append(
            f"<div class='wr-card'><div class='q'>Q{i}. {e(r.get('query'))}</div>"
            f"<div class='kv'><b>来源：</b>{src_s}</div>"
            + (f"<div class='kv'><b>手册位置：</b>{e(page)}</div>" if page else "")
            + f"<div class='kv'><b>抽取结果：</b></div>{ext_html}"
            + (f"<span class='tag use'>有用：{e(useful)}</span>" if useful else "")
            + (f"<span class='tag drop'>未采用：{e(not_used)}</span>" if not_used else "")
            + "</div>"
        )
    parts.append("</div>")
    return "".join(parts)


def render_comparisons(pack: dict) -> str:
    rows = pack.get("comparisons") or []
    if not rows:
        return ""
    body = "".join(
        "<tr>"
        f"<td>{e(r.get('left'))}<div class='hint'>{e(r.get('left_value'))}</div></td>"
        f"<td><code>{e(r.get('op'))}</code></td>"
        f"<td>{e(r.get('right'))}<div class='hint'>{e(r.get('right_value'))}</div></td>"
        f"<td>{'满足' if r.get('pass') is True else ('不满足' if r.get('pass') is False else '—')}</td>"
        "</tr>"
        for r in rows
    )
    return (
        "<div class='formula'><b>对照表（本板值 vs 手册/标准）</b>"
        "<table class='cmp-table'><thead><tr><th>本板侧</th><th>关系</th><th>手册/标准侧</th><th>结果</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def render_calcs(pack: dict) -> str:
    calcs = pack.get("calculations") or []
    if not calcs:
        return (
            "<p class='hint'>本项无数值不等式。"
            "若属存在性/标注类检查，对照过程见步骤与检索卡片，不编造工况电流。</p>"
        )
    parts = []
    for c in calcs:
        ok = c.get("pass")
        ok_s = "满足" if ok is True else ("不满足" if ok is False else "无法判定（缺输入）")
        inputs = c.get("inputs") or {}
        if isinstance(inputs, dict):
            inp_rows = "".join(
                f"<tr><td><code>{e(k)}</code></td><td>{e(v)}</td></tr>" for k, v in inputs.items()
            )
            inp_html = (
                f"<table><thead><tr><th>代入量</th><th>取值/来源</th></tr></thead>"
                f"<tbody>{inp_rows}</tbody></table>"
            )
        else:
            inp_html = e(inputs)
        parts.append(
            f"<div class='formula'><b>{e(c.get('name'))}</b><br/>"
            f"公式：<code>{e(c.get('formula'))}</code><br/>"
            f"{inp_html}"
            f"结果：{e(c.get('result'))} {e(c.get('unit'))}<br/>"
            f"判据：{e(c.get('criterion'))} → <b>{ok_s}</b>"
            f"<div>{e(c.get('note'))}</div></div>"
        )
    return "".join(parts)


def render_calc_fold(pack: dict) -> str:
    """对照表 + 计算明细：默认可收起，减少详情页冗余。"""
    cmp_html = render_comparisons(pack)
    calc_html = render_calcs(pack)
    # 仅 hint、无实质明细时不包折叠
    has_cmp = bool(pack.get("comparisons"))
    has_calc = bool(pack.get("calculations"))
    if not has_cmp and not has_calc:
        return calc_html
    n = len(pack.get("comparisons") or []) + len(pack.get("calculations") or [])
    summary = (
        "<summary>"
        "<span>计算 / 对照明细"
        f"<span class='fold-hint'>（{n} 条，默认收起）</span></span>"
        "<span class='fold-btn'></span>"
        "</summary>"
    )
    return (
        f"<details class='fold'>{summary}"
        f"<div class='fold-body'>{cmp_html}{calc_html if has_calc else ''}</div>"
        f"</details>"
    )


def render_steps(pack: dict, out_dir: Path) -> str:
    parts = []
    for i, s in enumerate(pack.get("steps") or [], 1):
        if isinstance(s, str):
            obs = s  # no length cap — hardware review narration may be long
            parts.append(
                f"<div class='step'><strong>Step {i}</strong>"
                f"<div class='obs'>{e(obs)}</div></div>"
            )
            continue
        if not isinstance(s, dict):
            continue
        img = ""
        shot = s.get("screenshot")
        if shot:
            rel = resolve_shot_rel(out_dir, str(shot)) or ""
            if rel:
                img = (
                    f"<div class='shot'>"
                    f"<button type='button' class='shot-open' data-lb-src='{e(rel)}' data-lb-cap='步骤截图' title='页内预览'>"
                    f"<img src='{e(rel)}' alt='step screenshot'/></button></div>"
                )
        obs = str(s.get("observation") or "")
        act = str(s.get("action") or f"步骤{i}")
        parts.append(
            f"<div class='step'><strong>{e(s.get('step') or i)} · {e(act)}</strong>"
            f"<div class='obs'>{e(obs)}</div>"
            f"<div class='dec'>{e(s.get('decision') or '')}</div>{img}</div>"
        )
    return "".join(parts) or "<p class='hint'>无核心推导步骤</p>"


def resolve_shot_rel(out_dir: Path, shot: str | None) -> str | None:
    if not shot:
        return None
    rel = portable_path(out_dir, str(shot)) if is_abs_path_str(str(shot)) else str(shot).replace("\\", "/")
    p = out_dir / rel
    if not p.exists():
        alt = out_dir / "evidence" / "shots" / Path(rel).name
        if alt.exists():
            return f"evidence/shots/{alt.name}"
        return None
    return rel.replace("\\", "/")


def render_pages(pack: dict, out_dir: Path) -> str:
    rows = pack.get("pages_checked") or []
    if not rows:
        return (
            "<div class='pages-banner'>⚠ 未填写检查页，且无原理图截图 — "
            "按交付纪律视为「未检查原理图」。</div>"
        )
    has_img = any(resolve_shot_rel(out_dir, p.get("screenshot")) for p in rows)
    bits: list[str] = []
    if not has_img:
        bits.append(
            "<div class='pages-banner'>⚠ 本项 pages_checked 无可用截图文件 — "
            "专家应视为「未实际检图」。请重新跑 attach_schematic_shots.py。</div>"
        )
    n_img = sum(1 for p in rows if resolve_shot_rel(out_dir, p.get("screenshot")))
    n_focus = sum(1 for p in rows if p.get("crop") and resolve_shot_rel(out_dir, p.get("screenshot")))
    compact = n_img >= 3
    # Always expanded — experts need shots visible without an extra click
    open_attr = " open"
    summary = (
        f"检图证据 · 共 {n_img} 张"
        + (f"（局部焦点 {n_focus} + 整页索引）" if n_focus else "")
        + f"<span class='fold-hint'>点击图页内预览；多图已缩小缩略显示</span>"
    )
    grid_cls = "pages compact" if compact else "pages"
    bits.append(f"<details class='pages-fold'{open_attr}><summary>{summary}</summary>")
    bits.append(f"<div class='{grid_cls}'>")
    for p in rows:
        st = VERDICT_LABEL.get(p.get("status") or "", p.get("status") or "")
        cls = "fail" if (p.get("status") or "") == "FAIL" else ""
        title = str(p.get("page") or p.get("region") or "原理图局部")
        meta_parts = [st]
        if p.get("region") and str(p.get("region")) not in title:
            meta_parts.append(str(p.get("region")))
        if p.get("pdf"):
            meta_parts.append(str(p.get("pdf")))
        if p.get("page_index"):
            meta_parts.append(f"第{p.get('page_index')}页")
        kws = p.get("focus_keywords") or []
        if kws:
            meta_parts.append("定位 " + "、".join(str(x) for x in kws[:6]))
        note = (p.get("note") or "").strip() or "—"
        rel = resolve_shot_rel(out_dir, p.get("screenshot"))
        if rel:
            cap = " · ".join(x for x in [title, note] if x and x != "—")
            media = (
                f"<div class='page-shot'>"
                f"<button type='button' class='shot-open' data-lb-src='{e(rel)}' data-lb-cap='{e(cap)}' title='页内预览'>"
                f"<img src='{e(rel)}' alt='{e(title)}'/></button></div>"
            )
        else:
            media = (
                "<div class='page-missing'>无截图（screenshot 缺失或文件不存在）—"
                "不能证明已检查该原理图局部。</div>"
            )
        bits.append(
            f"<figure class='page-card {cls}'>"
            f"{media}"
            f"<figcaption class='page-cap'>"
            f"<div class='cap-title'>{e(title)}</div>"
            f"<div class='cap-meta'>{e(' · '.join(meta_parts))}</div>"
            f"<p class='cap-note'>{e(note)}</p>"
            f"</figcaption></figure>"
        )
    bits.append("</div></details>")
    return "".join(bits)


def render_playbook(pack: dict) -> str:
    g = pack.get("expert_playbook") or {}
    if not g or not g.get("present"):
        return (
            "<p class='hint'>本项暂无专家 playbook 条目；按原子检查项 + 本板 facts 判定，"
            "禁止编造检查过程。</p>"
        )
    if not g.get("filled"):
        return (
            f"<div class='formula'><b>专家 playbook</b>（过程/材料未填齐 → <b>AI自检</b>）<br/>"
            f"严重度：{e(g.get('severity'))}；{e(g.get('note'))}<br/>"
            f"所需材料：{e(g.get('required_materials') or '（空）')}<br/>"
            f"检查方式：{e(g.get('expert_check_method') or '（空）')}</div>"
        )
    return (
        "<div class='formula'><b>专家规定的检查方法（产品中心 playbook）</b><br/>"
        f"所需材料：{e(g.get('required_materials'))}<br/>"
        f"检查方式：{e(g.get('expert_check_method'))}<br/>"
        f"规格书策略：{e(g.get('need_datasheet_query') or 'AI自决')}｜人工复核：{e(g.get('need_human') or 'AI自决')}<br/>"
        f"<b>检查过程：</b><pre style='white-space:pre-wrap;margin:6px 0 0;font:inherit'>"
        f"{e(g.get('check_process'))}</pre>"
        f"<div class='hint'>期望输出：{e(g.get('result_format'))}</div></div>"
    )


ACTION_TAG = {
    "FAIL": "必须改",
    "INSUFFICIENT_EVIDENCE": "等资料",
    "REVIEW_REQUIRED": "等资料",
    "PASS": "已闭合",
    "NOT_APPLICABLE": "不适用",
    "SKIPPED_OUT_OF_SCOPE": "不适用",
}


def _pill_chip(bucket: str) -> str:
    if bucket == "通过":
        return "PASS"
    if bucket == "不通过":
        return "FAIL"
    if bucket == "不适用":
        return "NOT_APPLICABLE"
    return "BLOCKED"


def _row_cls(bucket: str) -> str:
    if bucket == "通过":
        return "pass"
    if bucket == "不通过":
        return "fail"
    return "wait"


_READ_ZH = {
    "tel": "网表",
    "bom": "BOM",
    "jlc": "嘉立创工程",
    "pdf": "原理图",
    "design_task": "设计任务书",
    "datasheet": "规格书",
    "oc": "工况",
}

_TOKEN_ZH = (
    ("pages_checked[]", "不通过对象所在页"),
    ("user_nets[]", "用户网名"),
    ("internal[]", "内部网名"),
    ("illegal[]", "不合规网名"),
    ("entries[]", "入口清单"),
    ("power_pins[]", "电源脚清单"),
    ("object_cards[]", "逐颗结果"),
    ("object_cards", "逐颗卡片"),
    ("R_list[]", "装配电阻清单"),
    ("C_list[]", "装配电容清单"),
    ("raw[]", "原始网名"),
    ("fix[]", "整改条目"),
    ("dup[]", "重复项"),
    ("parsed|missing", "解析结果"),
    ("netname_voltage", "网名上的电压"),
    ("recipe_id", "命名算法"),
    ("stress_kind", "应力类型"),
    ("user_nets", "用户网名"),
    ("illegal", "不合规"),
    ("footprint", "封装"),
    ("nets.b", "另一端网"),
)


def _clean_flow_text(raw: str) -> str:
    t = str(raw or "").strip()
    if not t:
        return ""
    for src, dst in _TOKEN_ZH:
        t = t.replace(src, dst)
    t = t.replace("[]", "")
    t = t.replace("≠", "不算")
    return re.sub(r"\s+", " ", t).strip(" ，；")


def _reads_zh(reads) -> str:
    if isinstance(reads, str):
        reads = [x.strip() for x in reads.replace("，", ",").split(",") if x.strip()]
    names = []
    for item in reads or []:
        key = str(item or "").strip()
        if not key:
            continue
        names.append(_READ_ZH.get(key, key))
    return "、".join(names)


def _flow_sentence(step: dict) -> tuple[str, str]:
    action = str(step.get("action") or "").strip() or "检查"
    reads = _reads_zh(step.get("read") or [])
    take = _clean_flow_text(step.get("take") or "")
    compare = _clean_flow_text(step.get("compare") or "")
    produce = _clean_flow_text(step.get("produce") or "")
    bits: list[str] = []
    if reads and take:
        bits.append(f"从{reads}取出{take}")
    elif reads:
        bits.append(f"查看{reads}")
    elif take:
        bits.append(f"整理{take}")
    if compare:
        bits.append(f"对照：{compare}")
    if produce:
        bits.append(f"得到{produce}")
    body = "。".join(bits)
    if body and not body.endswith("。"):
        body += "。"
    return action, body or action


# 15 项检查流程的业务说法。键是检查项，值是（这项在查什么, [(步骤名, 说明)]）。
_FLOW_BUSINESS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "P04-A01": (
        "图纸上人工起的网名，只允许字母、数字和下划线。软件自动生成的内部网名单独列出，不拿来判错。",
        [
            ("收集网名", "只收原理图上的导线 NET 和网络标识。BOM、规格书、仅出现在网表里的网名不进对象名单。"),
            ("分开内部网名", "以 N$ 开头、没有名字、或软件自动编号的，算内部网名；其余算设计人员起的网名。"),
            ("核对字符", "设计人员起的网名，默认只能含字母、数字、下划线。任务书写明电源网可以用别的符号时，只对写明的那类网生效。"),
            ("逐条判定", "通过和不通过的用户网名都逐条列出。通过写明只含字母、数字、下划线，或写明命中任务书允许的符号。不通过写明含有的非法字符。"),
            ("改名", "按原来的网名给出改法，不另编一个图上没有的功能名。"),
        ],
    ),
    "P04-A03": (
        "图上同名网标出现几次。只出现 1 次，电路仍可能连通，按漏标标出。出现 2 次及以上通过。",
        [
            ("收集图面网标", "只收原理图上的导线 NET 和网络标识。BOM、规格书、仅出现在网表里的网名不进对象名单。"),
            ("点同名网标", "所有网名同一规则：一个网络标识符号算 1 处；同一页导线 NET 与标识不重复算；该页没有标识时导线 NET 算 1 处。"),
            ("判定", "1 处标出漏标。2 处及以上通过。连通情况不改这条判定。卡上只写次数。"),
        ],
    ),
    "P05-A01": (
        "每颗装配电阻都要有能读出来的阻值、精度和封装。这项不查功耗。",
        [
            ("列出装配电阻", "从 BOM、网表、嘉立创工程、原理图列出位号、料号、阻值、封装。不装配的不列入。"),
            ("读三项", "把 4R7、10k 这类写法换算成欧姆，并看有没有精度和封装。"),
            ("判定", "三项都有、阻值能读出来，才通过。缺一项就不通过。"),
            ("不通过才对图", "只标出缺标注的电阻所在页。"),
            ("补标注", "只填有出处的精度或封装，不猜测。"),
        ],
    ),
    "P05-A06": (
        "原理图上每颗电容，图面标注要同时有容值、额定耐压、介质和封装。BOM、规格书不补这四项。这项不查耐压够不够用。",
        [
            ("列出图面电容", "只收原理图上的电容位号。只出现在 BOM 里的不进名单。"),
            ("读图面标注", "容值、耐压、介质、封装只认位号旁边的图面文字和嘉立创 Value。料号、手册、BOM 描述不算。"),
            ("判定", "四项都在图上且容值能读出来，才通过。缺一项就不通过。"),
        ],
    ),
    "P07-A05": (
        "任务书写明不准连在一起的网络，不能被直接短接。0Ω、NetTie、跳线只有写了依据才算受控。没有依据的短接算未受控。",
        [
            ("标出禁止连在一起的网络", "电源域、差分、隔离、互斥输出，以任务书为准。"),
            ("沿实际连线看是否连通", "跨页同名、层次端口、0Ω 都算通路。"),
            ("核对受控清单", "0Ω、NetTie、跳线必须有书面依据。没有依据，不能从问题里剔除。"),
            ("不通过才对图", "短接两端都要在图上看得见。"),
            ("处理", "拆开网络，或补上允许短接的依据。"),
        ],
    ),
    "P11-A01": (
        "电阻上的功耗不超过额定功率的一半。温度和安装方式的降额不在这项里判。",
        [
            ("列出装配电阻", "列出位号、料号、封装。"),
            ("查额定功率", "取规格书 70℃ 表上的额定值，或额定栏里的功率。不拿降额曲线当额定功率。"),
            ("取电阻两端电压", "纯电阻网络按电路求解。电源网名上的电压要有别的资料印证才采用。开路比较器或可以被拉到地的网，按几种状态里跨压最大的算。同一通道的跟随器，输出抄同相输入。跨压是两端电压差的绝对值。不能把一只脚上的电源电压当成跨压，不能把没连上的另一端当成地。开路的几种状态都要列出来。没有对上已有的算法时，不能判通过或不通过。"),
            ("算功耗", "功耗等于跨压的平方除以电阻。多种状态时取最大跨压。不超过一半额定功率才通过。"),
            ("不通过才对图", "只标超限的电阻。"),
            ("选型", "可以加大功率或换封装。阻值所承担的功能保持不变，不指定料号。"),
        ],
    ),
    "P11-A03": (
        "电容两端最大电压不超过额定耐压的一半，并核对极性。不能只靠网名上的电压就判通过。",
        [
            ("列出两端网络和极性", "记下每颗电容两端接到哪根网，以及有没有极性。"),
            ("查额定耐压", "从规格书取额定耐压。没有抽出就标缺手册。"),
            ("取工作电压", "优先用工况里的电源表，其次图上标注和稳压器输出，最后才是网名上的电压。网名上的电压必须有别的资料印证。几种来源打架时，待确认。"),
            ("比较", "超过一半耐压，或极性接反，判不通过。工作电压取不到，待确认。"),
            ("不通过才对图", "只标超限或接反的电容。"),
            ("选型", "可以换更高耐压，不指定料号。"),
        ],
    ),
    "P11-A07": (
        "电感要同时满足两道门：饱和电流不小于工作电流的 1.5 倍；温升裕量要大于 10℃。温升裕量等于最高温度减去环境温度，再减去温升。两道分别出结果，总结果取更严的。",
        [
            ("列出全部装配电感", "网表、BOM、工程三处对过。不装配的剔除。"),
            ("查饱和电流和温升", "从规格书取饱和电流、温升曲线和最高温度，并记下页码和适用条件。读不出曲线上的点，就按缺手册。"),
            ("比饱和电流", "比较用的电流要和手册定义一致。不能用有效值电流去比直流饱和电流。"),
            ("算温升裕量", "发热电流用有效值，环境温度用工况。裕量等于 10℃ 算不通过。没有曲线时，不能口头估一个温升。"),
            ("不通过才对图", "只标不通过的电感。缺手册的不拿空白图充数。"),
            ("选型只给条件", "写出工作电流、频率、电感量、环境温度。参数不全时不编料号。"),
        ],
    ),
    "P12-A01": (
        "每颗 IC 的供电脚，稳态电压要在手册推荐工作范围内；该计入的瞬态不超过绝对最大值。不能把绝对最大值当成推荐工作范围。取电压的方法和电容耐压相同。",
        [
            ("列出供电脚", "列出供电脚、所在网络和上游电源。"),
            ("查推荐范围和绝对最大值", "推荐工作电压和绝对最大值分开读，上电和掉电也分开。规格书没有抽出就标缺手册。"),
            ("取实际供电", "和电容耐压用同一套优先级。稳态和瞬态分开。"),
            ("比较", "稳态要落在推荐范围内。瞬态不超过绝对最大值。"),
            ("不通过才对图", "只标这颗 IC 的电源脚。"),
            ("处理", "改供电电压或换器件，不指定料号。"),
        ],
    ),
    "P12-A02": (
        "数字信号发送端的高低电平，要和接收端的输入阈值在全工况下兼容。缺阈值只能待确认，不能用 0.7×VCC 冒充。与 P21-A01 共用同一套阈值证据。",
        [
            ("找出发送端和接收端", "按网络方向区分推挽、开漏和双向。"),
            ("查输出和输入阈值", "在最坏供电、温度和负载下取 VOH_min、VOL_max、VIH_min、VIL_max 及输入耐压。"),
            ("比较", "核对 VOH_min≥VIH_min、VOL_max≤VIL_max，并看输入耐压。噪声裕量按项目要求。"),
            ("缺阈值", "手册或工况缺阈值时记待确认，不编造门限。"),
            ("不通过才对图", "标出发送/接收脚和相关网络。"),
            ("处理", "改电平、加缓冲或换器件，只写条件不编料号。"),
        ],
    ),
    "P12-A03": (
        "有源器件的工作温度等级要盖住项目环境温度；还要看自热后的结温。不能拿存储温度代替工作温度。",
        [
            ("列出有源器件", "记下完整型号和温度后缀。"),
            ("查环境与手册范围", "项目最低/最高环境温度，手册工作温、壳温、结温分开读。"),
            ("比较", "温度等级覆盖环境；另核自热后结温。存储温度不能当工作温度。"),
            ("缺环境边界", "任务书没写温度上下限时记待确认。"),
            ("不通过才对图", "只标不覆盖的器件。"),
            ("选型", "只写温度条件，不编料号。"),
        ],
    ),
    "P12-A04": (
        "MOS 管漏源峰值电压不超过额定 VDS 的 70%。缺启动/关断等瞬态峰值时不能判通过。",
        [
            ("找出 MOSFET", "记下漏源连接和开关工况。"),
            ("查额定 VDS", "取匹配温度条件的额定值。"),
            ("取峰值", "启动、关断、负载变化时的漏源峰值。"),
            ("比较", "VDS_peak≤0.70×VDS_rated。缺峰值记待确认。"),
            ("不通过才对图", "只标超限的管子。"),
            ("处理", "换更高 VDS 或加钳位，不编料号。"),
        ],
    ),
    "P12-A05": (
        "MOS 管持续漏极电流不超过额定 ID 的 70%；脉冲和线性区另按 SOA 与温升核对。",
        [
            ("找出电流工况", "持续、峰值、脉宽和重复率。"),
            ("查额定 ID 与 SOA", "对应壳温、栅压、封装，不要误读成 VDS 字段。"),
            ("比较持续电流", "I_D,max≤0.70×ID_rated。"),
            ("脉冲另查 SOA", "按 VDS、ID、脉宽、温度核对。"),
            ("不通过才对图", "只标超限的管子。"),
            ("处理", "换更大电流或改驱动，不编料号。"),
        ],
    ),
    "P12-A06": (
        "TVS：正常电压低于 VRWM；浪涌/ESD 钳位加过冲低于后级适用限制。两类瞬态分开判，不能互代。",
        [
            ("找出 TVS 与后级", "保护路径和后级器件。"),
            ("分浪涌与 ESD", "各自的波形、电流和后级瞬态依据。"),
            ("比较", "正常电压低于 VRWM；保护后峰值低于后级限制。浪涌 VC 不能证明 ESD 通过。"),
            ("缺依据", "缺匹配瞬态数据的子项记待确认。"),
            ("不通过才对图", "标出保护网络。"),
            ("处理", "只写钳位与能量条件，不编料号。"),
        ],
    ),
    "P12-A07": (
        "连接器的电流、插拔寿命、防护等级要分别覆盖整机需求。缺正式需求时待确认，不自行补统一比例。",
        [
            ("列出连接器", "型号、使用触点和使用场景。"),
            ("查需求与手册", "持续/峰值电流、插拔次数、防护等级及厂家降额。"),
            ("分项比较", "电流、寿命、防护分别判定，不拼凑统一门槛。"),
            ("缺需求", "项目没写正式需求时记待确认。"),
            ("不通过才对图", "只标不满足的接口。"),
            ("处理", "只写接口条件，不编料号。"),
        ],
    ),
    "P21-A01": (
        "接口发送端输出电平与接收端 VIH/VIL 兼容。与 P12-A02 共用证据，避免同一连线结论打架。",
        [
            ("找出接口方向", "发送脚、接收脚和实际信号方向。"),
            ("查匹配工况阈值", "VOH/VOL、VIH/VIL 及耐压。"),
            ("比较", "VOH_min≥VIH_min、VOL_max≤VIL_max，并看噪声裕量。"),
            ("复用证据", "与 P12-A02 相同对象共用阈值证据。"),
            ("不通过才对图", "标出接口网络。"),
            ("处理", "改电平或加缓冲，不编料号。"),
        ],
    ),
    "P14-A02": (
        "有反接风险的入口，正接要能供电，反接后后级不能吃到反向电压；或者任务书有条款号批准豁免。有 MOS 不算已经通过。熔丝不是防反。",
        [
            ("找可能接反的入口", "从连接器、任务书接口和网表看插入后的极性，以及后级接到哪里。不是把所有带电压的网名都当成入口。"),
            ("正接、反接各推一次", "正接要能供电。反接时后级不承受反向电压，并核算损耗。"),
            ("查豁免", "只认任务书里的条款号，不能口头说豁免。"),
            ("不通过才对图", "标出入口到后级的路径。"),
            ("整改条件", "只写二极管或理想二极管要满足的条件，不编料号。"),
        ],
    ),
    "P14-A05": (
        "浪涌保护器件的工作电压、钳位和能量要匹配端口威胁。不能只比额定电压，也不能外推成 ESD 合格。",
        [
            ("找出保护器件和后级", "输入浪涌路径与受保护网络。"),
            ("取威胁与器件数据", "波形、源阻抗、VRWM/VC、脉冲能量及温度条件。"),
            ("核算", "钳位、过冲、能量分配与后级应力；重复脉冲与温度降额一并看。"),
            ("缺模型", "缺匹配动态数据时记待确认或要求仿真/测试。"),
            ("不通过才对图", "标出保护网络。"),
            ("处理", "只写威胁与钳位条件，不编料号。"),
        ],
    ),
    "P14-A06": (
        "每个外部可插拔供电口，负载开关、限流、软启动要分开判。电源一直带着、再插上负载时，只靠使能脚上电时的软启动，不能证明这一次插入是受控的。",
        [
            ("给全部接口分类", "是不是供电口、电源是进还是出、能不能插拔、插入时带不带电。"),
            ("沿供电和使能通路看", "开关能不能关掉；有没有限流；软启动是不是作用在这一次插入上。"),
            ("分项统计", "缺了哪一项就记哪一项。不能因为板上有别的芯片，就当成三项都有。"),
            ("不通过才对图", "只标缺功能的口。"),
            ("整改条件", "只写三项要满足的条件，不编料号。"),
        ],
    ),
    "P16-A01": (
        "每个 IC 电源脚，或手册允许共用的一组电源脚，要有高频去耦。手册有要求按手册，否则默认 0.1µF。原理图只查连没连上、容值对不对。靠不靠得近留给 PCB，不在原理图里判通过。同一根网上的一只电容，不能默认盖住所有脚。",
        [
            ("列出电源脚", "列出电源脚和所在网络，按电源轨分开。"),
            ("查去耦目标", "手册写了用手册，没写用 0.1µF。只有手册允许时，一组脚才能共用一只电容。"),
            ("逐脚对电容", "看同一根网上已装配电容的容值。"),
            ("靠不靠近", "没有 PCB 时，不证明电容就近。标成转到 PCB 确认，不因此判原理图不通过。"),
            ("没连上才对图", "只标没有去耦的 IC。"),
            ("补电容", "按手册容值补，不编料号。"),
        ],
    ),
    "P16-A05": (
        "模拟电源要按噪声预算用 RC 或磁珠隔离。没有模拟电源则本项不适用。不能用分地代替电源滤波。",
        [
            ("找出模拟电源", "供电路径和负载。"),
            ("查噪声预算与滤波件", "RC/磁珠参数和压降。"),
            ("核对", "隔离是否到位，谐振与压降是否可接受。"),
            ("无模拟电源", "记不适用。"),
            ("不通过才对图", "标出模拟电源网络。"),
            ("处理", "只写滤波条件，不编料号。"),
        ],
    ),
    "P18-A02": (
        "晶体负载电容按公式算：两只外接电容并联后，再加上寄生电容。芯片内部电容只加一次。结果要落在手册的负载电容范围内。寄生电容没有约定时，不用 3pF 代替。",
        [
            ("找到晶体和两端电容", "确认两只电容接到晶体的振荡器脚。"),
            ("查手册负载电容", "读取负载电容范围和芯片内部电容。没有就标缺手册。"),
            ("取寄生电容", "任务书或工况写了就用。没写就缺约定，不估算。"),
            ("代入公式", "按容差最坏情况算，内部电容只加一次，再和手册范围比。"),
            ("不通过才对图", "标出晶体所在的网络。"),
            ("改外接电容", "只按公式反推容值，不编料号。"),
        ],
    ),
    "P19-A03": (
        "复位信号的上拉/下拉电压和默认状态要满足器件要求。没有复位网则不适用。",
        [
            ("找出复位线", "上下拉、内部偏置和外部驱动。"),
            ("查阈值与漏电流", "电源范围、输入阈值和驱动方式。"),
            ("核算默认电平", "复位有效电平、争用和掉电反灌。"),
            ("无复位网", "记不适用。"),
            ("不通过才对图", "标出复位网络。"),
            ("处理", "只写偏置条件，不编料号。"),
        ],
    ),
    "P42-A01": (
        "实际装配的位号不能重复。一个器件拆成多个单元，不算重复。不能用嘉立创里的实例分组数量，代替位号是否唯一。",
        [
            ("取出全部位号", "记下位号在哪一层、是第几个单元。"),
            ("区分单元和实体", "软件把一个器件拆成多门时，合法的同一位号先剔除。"),
            ("查重复", "同一层次、同一位号冲突的，列出所在页。"),
            ("不通过才对图", "冲突两边都要看得到。"),
            ("改位号", "改掉冲突的位号。"),
        ],
    ),
    "P42-A04": (
        "同一版本下，列出只在原理图、只在 BOM、以及两边都有的位号。两边都有的，再比料号和数值。不能因为大部分对得上，就忽略整颗缺失。",
        [
            ("分成三类", "只在原理图、只在 BOM、两边都有。工程文件一并纳入。"),
            ("处理允许的差异", "不装配、机械件、测试点必须有排除规则。没有规则时，不能一律判通过，也不能一律判不通过。"),
            ("比料号和数值", "两边都有的位号，比料号和数值。单位要统一，后缀不能丢。"),
            ("不通过才对图", "只列数值冲突，或没有依据却只出现在一处的位号。"),
            ("补齐", "补料或改标注，使原理图、BOM、工程文件一致。"),
        ],
    ),
}


def render_check_flow(pack: dict) -> str:
    """项级检查流程：一块轻度样式的编号文本，用业务说法。"""
    rid = str(pack.get("rule_id") or "")
    spec = _FLOW_BUSINESS.get(rid)
    if spec:
        intent, steps = spec
    else:
        g = pack.get("expert_playbook") if isinstance(pack.get("expert_playbook"), dict) else {}
        detail = g.get("rule_detail") if isinstance(g.get("rule_detail"), dict) else {}
        intent = _clean_flow_text(g.get("intent") or detail.get("intent") or "")
        steps = []
        for step in g.get("steps") or []:
            if isinstance(step, dict):
                action, body = _flow_sentence(step)
                steps.append((action, body))
    parts: list[str] = []
    if intent:
        parts.append(f"<p class='flow-lead'>这项在查：{e(intent)}</p>")
    if not steps:
        parts.append("<p class='flow-lead'>这项没有写下检查流程。</p>")
    else:
        lis = []
        for i, (action, body) in enumerate(steps, 1):
            lis.append(
                f"<li><span class='flow-n'>{i}</span>"
                f"<div class='flow-card'><span class='flow-name'>{e(action)}</span>"
                f"<span class='flow-body'>{e(body)}</span></div></li>"
            )
        parts.append(f"<ol class='flow-tl'>{''.join(lis)}</ol>")
    return f"<div class='flow-box'>{''.join(parts)}</div>"


def _join_refs(refs: list[str], limit: int = 8) -> str:
    show = [r for r in refs if r and not str(r).startswith("(")]
    if not show:
        return "本项"
    text = "、".join(show[:limit])
    if len(show) > limit:
        text += f" 等 {len(show)} 个"
    return text


def _page_name(device: dict) -> str:
    for p in device.get("pages") or []:
        if isinstance(p, dict) and p.get("page_name"):
            return str(p["page_name"])
    return ""


def _pair_nets(phen: str) -> str:
    m = re.search(r"两端网\s+([^；。，]+)", phen or "")
    return m.group(1).strip() if m else ""


def _miss_names(phen: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"缺([^，。；、]+)", phen or "")))


def _advice_rows(rule_id: str, devices: list[dict]) -> list[tuple[str, str]]:
    fails = [d for d in devices if d.get("bucket") == "不通过"]
    waits = [d for d in devices if d.get("bucket") == "待确认"]
    fn = _ADVICE_ROWS.get(rule_id)
    if fn:
        return fn(fails, waits)
    return _advice_fallback(fails, waits)


def _advice_p04_a01(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    plus: list[tuple[str, str]] = []
    other: list[str] = []
    for d in fails:
        net = str(d.get("ref") or "")
        m = re.fullmatch(r"\+(\d+(?:\.\d+)?)V", net, re.I)
        if m:
            plus.append((net, f"V{m.group(1)}P"))
        elif net:
            other.append(net)
    if plus:
        bits = "、".join(f"「{a}」改为「{b}」" for a, b in plus)
        rows.append((
            _join_refs([a for a, _ in plus]),
            f"网名{bits}。原理图、网表、工程文件一起改。若任务书明确允许电源网使用「+」，则只在任务书写下该例外，网名可以不改。",
        ))
    for net in other:
        rows.append((
            net,
            f"网名「{net}」去掉不允许的字符，改成只含字母、数字、下划线的名字。原理图、网表、工程文件一起改。",
        ))
    return rows


def _advice_p04_a03(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    nets = [str(d.get("ref") or "") for d in fails if d.get("ref")]
    if nets:
        rows.append(
            (
                _join_refs(nets),
                "图上同名网标只有 1 处。在跨页或跨段的另一端再放一处同名网标。若该网只在这一段出现，在报告里写明「单段、不跨页」，网标保持 1 处。",
            )
        )
    for d in waits:
        ref = str(d.get("ref") or "")
        if ref:
            rows.append((ref, f"先把网 {ref} 的网标所在页写进报告。页码没写上之前，不改网名。"))
    return rows


def _advice_p05_a01(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    groups: dict[str, list[tuple[str, str]]] = {}
    order: list[str] = []
    for d in fails:
        phen = str(d.get("phenomenon") or "")
        names = _miss_names(phen) or ["标注"]
        key = "、".join(names)
        val = ""
        m = re.search(r"阻值=([^，]+)", phen)
        if m:
            val = m.group(1).strip()
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((str(d.get("ref") or ""), val))
    rows = []
    verb = {
        "精度": "阻值后补精度，例如「{ex} 1%」",
        "封装": "补封装名",
        "阻值": "把阻值写成能读出欧姆的形式，例如 4R7 或 10k",
    }
    for key in order:
        items = groups[key]
        example = next((v for _, v in items if v), "10kΩ")
        bits = []
        for name in key.split("、"):
            tmpl = verb.get(name, "补上{name}")
            bits.append(tmpl.format(ex=example, name=name))
        rows.append((
            _join_refs([r for r, _ in items]),
            "。".join(bits) + "。写在原理图位号旁边，BOM 改成同一行。没有出处的不填。",
        ))
    return rows


def _advice_p05_a06(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for d in fails + waits:
        phen = str(d.get("phenomenon") or "")
        names = _miss_names(phen)
        if "TBD" in phen and "容值" not in "、".join(names):
            names.append("容值")
        if not names:
            continue
        key = "、".join(names)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(str(d.get("ref") or ""))
    verb = {
        "介质": "容值后补介质，例如 X7R 或 C0G",
        "额定耐压": "补额定耐压，写在容值后面，例如 50V",
        "封装": "补封装名",
        "容值": "先确定容值，去掉 TBD",
    }
    rows = []
    for key in order:
        bits = []
        for name in key.split("、"):
            bits.append(verb.get(name, f"补上{name}"))
        rows.append((
            _join_refs(groups[key]),
            "。".join(bits) + "。写在原理图位号旁边，BOM 改成同一行。只写在图上的才算改完。",
        ))
    return rows


def _advice_p07_a05(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    rows = []
    if waits:
        rows.append((
            "任务书",
            "在任务书写一张禁止短接表，写明 +12V、+24V、VCC、VCC24V 各自能不能连在一起。表没写上之前，不拆 F1、D2。",
        ))
    for d in fails:
        ref = str(d.get("ref") or "短接")
        rows.append((ref, f"把 {ref} 从短接改成断开。若保留短接，把允许依据写进任务书。"))
    return rows


_THICK_FILM_W = (
    ("0603", 0.10),
    ("0805", 0.125),
    ("1206", 0.25),
    ("1210", 0.50),
    ("2010", 0.75),
    ("2512", 1.0),
)
_CAP_VR = (6.3, 10, 16, 25, 35, 50, 100)


def _chip_resistor_fix(ref: str, value: str, nets: str, p_w: float, limit: float | None) -> str:
    """功耗超限时写出可执行的换封装或拆电阻。额定仍按一半比较。"""
    need = 2.0 * float(p_w)
    cur = 2.0 * float(limit) if limit is not None else None
    val = value or "现阻值"
    where = f"两端仍接 {nets}。" if nets else ""
    head = f"把 {ref} 的额定功率提高到大于 {fmt_power(need)}。"
    if cur is not None:
        head += f"现在是 {fmt_power(cur)}。"
    ok = [(n, w) for n, w in _THICK_FILM_W if w > need + 1e-9]
    short = [(n, w) for n, w in _THICK_FILM_W if (cur is None or w > cur + 1e-9) and w <= need + 1e-9]
    if ok:
        name, watt = ok[0]
        pack = f"换成 {name}、额定 {fmt_power(watt)} 的 {val}。"
        if short:
            names = "、".join(f"{n}（{fmt_power(w)}）" for n, w in short)
            pack += f"{names} 的一半额定仍小于 {fmt_power(p_w)}，不能用。"
        n_pcs = max(2, int((float(p_w) / 0.05) + 0.999999))
        if n_pcs <= 6:
            pack += f"也可以拆成至少 {n_pcs} 只 0603 并联或串联，等效阻值仍是 {val}，每只功耗小于 50 mW。"
    else:
        pack = "0603 到 2512 的常见厚膜片阻最大约 1 W，都不够。换成额定功率大于该值的功率电阻。"
    return f"{head}{pack}阻值保持 {val}。{where}"


def _cap_vr_fix(ref: str, v: float, limit: float | None) -> str:
    need = 2.0 * float(v)
    cur = 2.0 * float(limit) if limit is not None else None
    higher = [x for x in _CAP_VR if x > need + 1e-9]
    pick = higher[0] if higher else None
    cur_s = f"现在是 {fmt_num(cur, 'V')}。" if cur is not None else ""
    if pick is not None:
        short = [x for x in _CAP_VR if (cur is None or x > cur + 1e-9) and x <= need + 1e-9]
        extra = ""
        if short:
            extra = "、".join(fmt_num(x, "V") for x in short) + " 的一半仍小于工作电压，不能用。"
        return (
            f"把 {ref} 的额定耐压由现挡改为 {fmt_num(pick, 'V')}。"
            f"{cur_s}工作电压 {fmt_num(v, 'V')}，一半额定耐压必须盖住它，所以额定耐压要大于 {fmt_num(need, 'V')}。"
            f"{extra}容值不改，两端不调换。同封装没有这一挡时，加大封装。"
        )
    return (
        f"把 {ref} 换成额定耐压大于 {fmt_num(need, 'V')} 的同容值电容。"
        f"{cur_s}常用挡 6.3 V 到 100 V 都不够时，改用更高耐压的电容。容值不改，两端不调换。"
    )


def _advice_p11_a01(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    rows = []
    for d in fails:
        ref = str(d.get("ref") or "")
        if d.get("action"):
            rows.append((ref, str(d["action"])))
            continue
        nets = _pair_nets(str(d.get("phenomenon") or ""))
        net_s = f"两端网 {nets}。" if nets else ""
        p_w = d.get("p_w")
        limit = d.get("limit")
        val = str(d.get("value") or "").strip()
        if p_w is None:
            rows.append((ref, f"先把 {ref} 的两端电压算出来并写进报告。电压没写上之前，不换电阻、不改阻值。"))
            continue
        rows.append((ref, _chip_resistor_fix(ref, val, nets, float(p_w), float(limit) if limit is not None else None)))
    for d in waits:
        ref = str(d.get("ref") or "")
        if d.get("action"):
            rows.append((ref, str(d["action"])))
            continue
        stuck = str(d.get("stuck") or "")
        nets = _pair_nets(str(d.get("phenomenon") or ""))
        net_s = f"两端网 {nets}。" if nets else ""
        if "未挂" in stuck or "比较器" in stuck or "OC" in stuck:
            body = f"在原理图上标出 {ref} 两端是否接到比较器开漏输出。没标出之前，不换这只电阻。"
        else:
            body = f"先把 {ref} 两端的对地电压算出来并写进报告。电压没写上之前，不换电阻、不改阻值。"
        rows.append((ref, body))
    return rows


def _advice_p11_a03(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    rows = []
    for d in fails:
        ref = str(d.get("ref") or "")
        phen = str(d.get("phenomenon") or "")
        nets = _pair_nets(phen)
        net_s = f"两端网 {nets}。" if nets else ""
        if "极性" in phen and "反" in phen:
            rows.append((ref, f"对调 {ref} 两端。正端改接到高电位，负端改接到低电位。容值不改。"))
            continue
        v = d.get("v")
        limit = d.get("limit")
        if v is not None:
            rows.append((ref, _cap_vr_fix(ref, float(v), float(limit) if limit is not None else None)))
        else:
            rows.append((ref, f"先算出 {ref} 两端工作电压。算出来之后，把额定耐压换成大于该电压 2 倍的同容值电容。"))
    for d in waits:
        ref = str(d.get("ref") or "")
        rows.append((ref, "先从规格书抄出额定耐压，写到原理图该电容旁边。没写上之前不换料。写上之后若工作电压超过一半额定耐压，再换成更高耐压的同容值电容。"))
    return rows


def _advice_p11_a07(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    rows = []
    for d in fails:
        ref = str(d.get("ref") or "")
        rows.append((ref, f"把 {ref} 换成饱和电流不小于 1.5 倍工作电流、且按温升曲线裕量大于 10℃ 的同感值电感。规格书曲线达不到的不能留。不指定料号。"))
    for d in waits:
        ref = str(d.get("ref") or "")
        rows.append((ref, f"从 {ref} 的规格书抄出饱和电流和温升曲线，并在工况写下环境温度。数没写上之前不换电感。"))
    return rows


def _advice_p12_a01(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    rows = []
    for d in fails:
        ref = str(d.get("ref") or "")
        phen = str(d.get("phenomenon") or "")
        rows.append((ref, f"读 {ref} 该脚现在的电压。低于推荐下限时，把该脚所在电源抬进推荐范围；高于推荐上限时，把该电源降进推荐范围，或换成推荐范围能盖住该电压的器件。不指定料号。"))
    for d in waits:
        ref = str(d.get("ref") or "")
        stuck = str(d.get("stuck") or "")
        if "推荐" in stuck:
            rows.append((ref, f"从 {ref} 的规格书抄出推荐工作电压的下限和上限，写进报告。范围没写上之前不改供电。"))
        else:
            rows.append((ref, f"先把 {ref} 供电脚所在网的电压算出来并写进报告。电压没写上之前不改供电。"))
    return rows


def _advice_p14_ports(waits: list[dict], fails: list[dict], power_body: str) -> list[tuple[str, str]]:
    power, signal = [], []
    for d in waits + fails:
        ref = str(d.get("ref") or "")
        phen = str(d.get("phenomenon") or "")
        if "没有供电针" in phen:
            signal.append(ref)
        elif ref and not ref.startswith("("):
            power.append(ref)
    rows = []
    if power:
        rows.append((_join_refs(power), power_body))
    if signal:
        rows.append((_join_refs(signal), "任务书填「非供电口」。"))
    return rows


def _advice_p14_a02(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    return _advice_p14_ports(
        waits,
        fails,
        "在任务书给该口各填一格：允许反接，或不允许反接。填不允许时，在入口串一只防反二极管或理想二极管，通过电流不小于入口工作电流，反向耐压大于入口最大电压。格没填之前不加二极管。",
    )


def _advice_p14_a06(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    return _advice_p14_ports(
        waits,
        fails,
        "任务书填两格：能否插拔；插入时是否带电。两格都为「是」时，该口要能关断、能重复限流，软启动要作用在这一次插入上。格没填之前不加负载开关。",
    )


def _advice_p16_a05(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    rows = []
    for d in waits:
        ref = str(d.get("ref") or "")
        rows.append((ref, "把噪声预算写进任务书。预算没写上之前，不加磁珠，也不加 RC。"))
    return rows


def _advice_p14_a05(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    rows = []
    for d in waits:
        ref = str(d.get("ref") or "")
        rows.append((ref, "把该口的威胁等级写进任务书，并从手册抄出工作电压、钳位电压和能量。这三项没写上之前，不改保护器件。齐纳的稳压电压不能拿来当浪涌能量。"))
    return rows


def _advice_p19_a03(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    rows = []
    for d in waits:
        ref = str(d.get("ref") or "")
        rows.append((ref, "从器件手册抄出复位阈值和上电默认电平，写进报告。没写上之前，不改上拉电阻，也不改下拉电阻。"))
    return rows


def _advice_p21_a01(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    rows = []
    for d in waits:
        ref = str(d.get("ref") or "")
        rows.append((ref, "从手册推荐栏抄出 VOH、VOL、VIH、VIL，写进报告。不用 0.7×VCC，也不用绝对最大额定。这四项没写上之前，不改连线。"))
    return rows


def _advice_p16_a01(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    rows = []
    for d in fails:
        ref = str(d.get("ref") or "")
        phen = str(d.get("phenomenon") or "")
        pins = re.findall(r"pin(\w+)\s*在网\s+(\S+)\s*上", phen)
        if pins:
            where = "、".join(f"pin{p}（网 {n}）" for p, n in pins)
            verb = "补一只" if len(pins) == 1 else "各补一只"
            body = f"{where}{verb} 0.1µF 到地。手册另有容值的用手册容值。"
        else:
            body = "缺去耦的电源脚同网各补一只 0.1µF 到地。手册另有容值的用手册容值。"
        rows.append((ref, body))
    for d in waits:
        ref = str(d.get("ref") or "")
        phen = str(d.get("phenomenon") or "")
        stuck = str(d.get("stuck") or "")
        if "共用" in phen or "共享" in stuck or "共用" in stuck:
            rows.append((ref, "向手册确认这些电源脚能否共用一只电容。手册没写允许之前，每个脚的同网各放一只目标电容。"))
        else:
            rows.append((ref, "从规格书或封装标出电源脚，再逐脚查同网去耦。脚没标出之前，不补电容。"))
    return rows


def _advice_p18_a02(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    rows = []
    for d in fails:
        ref = str(d.get("ref") or "")
        rows.append((ref, "按公式把两只外接电容的容值改到负载电容落进手册范围。只改容值，不写料号。"))
    for d in waits:
        ref = str(d.get("ref") or "")
        stuck = str(d.get("stuck") or "")
        if "寄生" in stuck or "C_par" in stuck:
            rows.append((ref, "把寄生电容写进任务书或工况。没写上之前，不用 3pF 代替，也不改外接电容。"))
        elif "旁边" in stuck:
            rows.append((ref, "在晶体两脚各放一只负载电容，并写上容值，再代入公式。"))
        else:
            rows.append((ref, "从规格书抄出负载电容范围，写进报告。范围没写上之前，不改外接电容。"))
    return rows


def _advice_p42_a01(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    rows = []
    for d in fails:
        ref = str(d.get("ref") or "")
        rows.append((ref, f"位号 {ref} 有两处实体。把其中一个改成还没占用的位号。"))
    if waits:
        rows.append(("位号", "先补上图符的单元身份，再核对有没有撞号。身份没补之前不改位号。"))
    return rows


def _advice_p42_a04(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    rows = []
    for d in fails:
        ref = str(d.get("ref") or "")
        page = _page_name(d)
        mpn = str(d.get("mpn") or "").strip()
        where = f"在 {page}，" if page else ""
        placeholder = f"，料号是 {mpn}" if mpn else ""
        rows.append((
            ref,
            f"{where}把原理图、BOM、工程文件三处写成同一个容值、耐压和介质{placeholder}。去掉 TBD 和占位料号。",
        ))
    for d in waits:
        ref = str(d.get("ref") or "")
        phen = str(d.get("phenomenon") or "")
        rows.append((ref, "在任务书写下这颗能否排除。不能排除的，把缺的那一处资料补进原理图、BOM 或工程文件。"))
    return rows


def _advice_fallback(fails: list[dict], waits: list[dict]) -> list[tuple[str, str]]:
    return []


_ADVICE_ROWS = {
    "P04-A01": _advice_p04_a01,
    "P04-A03": _advice_p04_a03,
    "P05-A01": _advice_p05_a01,
    "P05-A06": _advice_p05_a06,
    "P07-A05": _advice_p07_a05,
    "P11-A01": _advice_p11_a01,
    "P11-A03": _advice_p11_a03,
    "P11-A07": _advice_p11_a07,
    "P12-A01": _advice_p12_a01,
    "P14-A02": _advice_p14_a02,
    "P14-A06": _advice_p14_a06,
    "P16-A01": _advice_p16_a01,
    "P16-A05": _advice_p16_a05,
    "P14-A05": _advice_p14_a05,
    "P19-A03": _advice_p19_a03,
    "P21-A01": _advice_p21_a01,
    "P18-A02": _advice_p18_a02,
    "P42-A01": _advice_p42_a01,
    "P42-A04": _advice_p42_a04,
}


_REF_TITLE = re.compile(r"^[A-Za-z][A-Za-z0-9./_-]{0,24}$")


def _advice_items(rows: list[tuple[str, str]]) -> str:
    lis = []
    for i, (title, body) in enumerate(rows, 1):
        title_s = str(title or "").strip()
        if _REF_TITLE.fullmatch(title_s):
            head = f"<code class='fix-ref'>{e(title_s)}</code>"
        else:
            head = f"<div class='t'>{e(title_s)}</div>"
        lis.append(
            f"<li class='fix-item'><span class='fix-n'>{i}</span>"
            f"<div class='fix-main'>{head}<div class='s'>{e(body)}</div></div></li>"
        )
    return f"<ul class='fix-list'>{''.join(lis)}</ul>"


def render_advice(rule_id: str, devpack: dict) -> str:
    """整改建议写改哪一处、改成什么。通过、不适用不出这一栏。"""
    devices = [d for d in (devpack.get("devices") or []) if isinstance(d, dict)]
    rows = _advice_rows(rule_id, devices)
    if not rows:
        return ""
    return _advice_items(rows)


def render_fixes(pack: dict) -> str:
    items: list[tuple[str, str]] = []
    for x in pack.get("fix") or []:
        if isinstance(x, dict):
            t = x.get("condition") or x.get("from") or x.get("t") or "整改"
            s = x.get("need") or x.get("to") or x.get("note") or x.get("s") or ""
            items.append((str(t), str(s)))
        else:
            items.append(("整改", str(x)))
    rem = pack.get("remediation") or ""
    if rem and not items:
        items.append(("整改建议", str(rem)))
    if not items:
        return "<p class='hint'>本项无单独整改条目。</p>"
    return _advice_items(items)


def render_device_table(devpack: dict) -> str:
    devices = devpack.get("devices") or []
    if not devices:
        return "<p class='hint'>本项无逐对象卡。通过对象与待确认对象都应落卡；禁止只写摘要。</p>"
    counts = devpack.get("counts") or {}
    n = devpack.get("n") or len(devices)
    has_p = any(d.get("p_txt") for d in devices)
    keys = [("全部", n), ("待确认", counts.get("待确认") or 0), ("不通过", counts.get("不通过") or 0), ("通过", counts.get("通过") or 0)]
    filt = "".join(
        f"<button type='button' class='filt{' on' if k=='全部' else ''}' data-f='{e(k)}'>{e(k)} {c}</button>"
        for k, c in keys
        if k == "全部" or c
    )
    extra_h = "<th>P</th><th>限额</th>" if has_p else ""
    rows = []
    for i, d in enumerate(devices):
        nets = " / ".join(x for x in (d.get("nets") or []) if x)
        roles = "；".join(
            f"p{p.get('page')} {' / '.join(p.get('roles') or [])}" for p in (d.get("pages") or [])
        )
        extra = (
            f"<td>{e(d.get('p_txt') or '—')}</td><td>{e(d.get('limit_txt') or '—')}</td>" if has_p else ""
        )
        rows.append(
            f"<tr class='device {_row_cls(d.get('bucket') or '')}' data-i='{i}' data-bucket='{e(d.get('bucket'))}'>"
            f"<td><code>{e(d.get('ref'))}</code></td>"
            f"<td><span class='pill {_pill_chip(d.get('bucket') or '')}'>{e(d.get('bucket'))}</span></td>"
            f"<td>{e(d.get('value') or '')}</td>"
            f"<td><code>{e(nets)}</code></td>"
            f"{extra}"
            f"<td title='{e(roles)}'>{int(d.get('page_n') or 0)} 页</td>"
            f"<td class='explain' title='{e(d.get('stuck') or '')}'>{e(d.get('stuck'))}</td>"
            f"<td><button type='button' class='btn-link js-dev'>明细</button></td></tr>"
        )
    pager = (
        "<div class='obj-pager'>"
        "<button type='button' class='pg-prev'>上一页</button>"
        "<span class='pg-info'></span>"
        "<button type='button' class='pg-next'>下一页</button>"
        "<label class='page-size'>每页 "
        "<select class='pg-size'>"
        "<option value='10'>10</option>"
        "<option value='20'>20</option>"
        "<option value='50'>50</option>"
        "<option value='100'>100</option>"
        "<option value='0'>全部</option>"
        "</select></label>"
        "</div>"
    )
    return (
        f"<div class='filters'>{filt}</div>"
        "<div class='tbl-wrap'><table class='obj-cards'><thead><tr>"
        f"<th>对象</th><th>判定</th><th>取值</th><th>网络</th>{extra_h}<th>图幅</th><th>判定说明</th><th></th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        f"{pager}"
    )


def render_detail(item: dict, pack: dict, out_dir: Path, board: dict, wb: dict | None = None) -> str:
    pack = normalize(item, pack, board, out_dir)
    pack = make_pack_portable(pack, out_dir)
    rid = item.get("rule_id") or ""
    verdict = pack.get("verdict") or "INSUFFICIENT_EVIDENCE"
    chip = VERDICT_CHIP.get(verdict, verdict)
    label = VERDICT_LABEL.get(verdict, verdict)
    action = ACTION_TAG.get(verdict, "")
    card_cls = "fail" if chip == "FAIL" else ("wait" if chip == "BLOCKED" else ("pass" if chip == "PASS" else "na"))
    wb = wb or {}
    devpack = pack_devices(pack, wb) if wb.get("out_dir") else {"n": 0, "counts": {}, "devices": []}
    counts = devpack.get("counts") or {}
    n_obj = int(devpack.get("n") or 0)
    n_fail = int(counts.get("不通过") or 0)
    n_wait = int(counts.get("待确认") or 0)
    n_pass = int(counts.get("通过") or 0)
    kpis = [
        (n_obj, "对象", ""),
        (n_fail, "不通过", " hot" if n_fail else ""),
        (n_wait, "待确认", " wait" if n_wait else ""),
        (n_pass, "通过", ""),
    ]
    kpi_html = "".join(
        f"<div class='kpi{cls}'><b>{n}</b><span>{e(lab)}</span></div>" for n, lab, cls in kpis
    )
    note = str(pack.get("note") or "").strip()
    if not note or note.startswith("待 Agent"):
        bits = []
        if chip in {"NA", "SKIPPED"} or verdict in {"NOT_APPLICABLE", "SKIPPED_OUT_OF_SCOPE"}:
            bits.append("本项不适用")
        if n_fail:
            bits.append(f"{n_fail} 个对象不通过")
        if n_wait:
            bits.append(f"{n_wait} 个对象待确认，不得无补丁升绿")
        if n_pass and not bits:
            bits.append(f"{n_pass} 个对象已闭合")
        one = "；".join(bits) or (pack.get("why") or item.get("name") or label)
    else:
        one = note
    rule_summary = str(pack.get("rule_summary") or "").strip()
    from agent_review.selection_coverage import render as render_delivery_coverage
    scope_html=render_delivery_coverage(out_dir,rid)
    summary_html = (
        f"<div class='rule-summary'>{e(rule_summary)}</div>" if rule_summary else ""
    )
    advice_html = render_advice(str(rid), devpack)
    advice_sec = (
        f"<div class='sec'><h3>整改建议</h3>{advice_html}</div>" if advice_html else ""
    )
    flow_sec = "" if scope_html else f"<div class='sec'><h3>推导步骤</h3>{render_check_flow(pack)}</div>"
    pack_json = e(json.dumps(devpack, ensure_ascii=False))
    tech = json.dumps(pack, ensure_ascii=False, indent=2)
    return f"""
<section class="detail workbench" data-detail-id="{e(rid)}">
  <div class="head">
    <div>
      <div class="ridline">{e(rid)} · {e(item.get('parent_name') or '')} · #{e(item.get('atomic_seq'))}</div>
      <div class="title">{e(item.get('name'))}</div>
    </div>
  </div>
  <section class="verdict-card {card_cls}">
    <div class="vrow">
      <span class="pill {e(chip)}">{e(label)}</span>
      <span class="action-tag">{e(action)}</span>
    </div>
    <div class="one">{e(one)}</div>
    <div class="kpis">{kpi_html}</div>
    {summary_html}
    {scope_html}
  </section>
  <div class="sec">
    <h3>逐对象</h3>
    {render_device_table(devpack)}
    <textarea class="js-devpack" hidden>{pack_json}</textarea>
  </div>
  {advice_sec}
  {flow_sec}
  <details class="tech"><summary>技术附录（机器字段）</summary><pre>{e(tech)}</pre></details>
  <div class="expert">
    专家批注：
    <label><input type="radio" name="ex-{e(rid)}" value="ok" onchange="saveEx('{e(rid)}')"/> 认可</label>
    <label><input type="radio" name="ex-{e(rid)}" value="no" onchange="saveEx('{e(rid)}')"/> 不认可</label>
    <label><input type="radio" name="ex-{e(rid)}" value="more" onchange="saveEx('{e(rid)}')"/> 需补充</label>
    <textarea id="tx-{e(rid)}" placeholder="写明哪一步推错、哪份规格书页码不对、哪个公式代入错…" oninput="saveEx('{e(rid)}')"></textarea>
  </div>
</section>
"""


def install_pdfjs(out_dir: Path) -> bool:
    """Copy vendored Mozilla PDF.js viewer into review_out/pdfjs/."""
    src = Path(__file__).resolve().parent / "pdfjs"
    if not (src / "web" / "viewer.html").is_file():
        return False
    dest = out_dir / "pdfjs"
    try:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        return (dest / "web" / "viewer.html").is_file()
    except Exception:
        return False


def build_html(out_dir: Path) -> str:
    items = load_product_items()
    packs = {}
    ev = out_dir / "evidence"
    if ev.exists():
        for p in ev.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            # Rewrite absolute paths in evidence so packaged zip works on other PCs
            portable = make_pack_portable(data, out_dir)
            if portable != data:
                p.write_text(json.dumps(portable, ensure_ascii=False, indent=2), encoding="utf-8")
            packs[portable.get("rule_id") or p.stem] = portable
    board = board_from_facts(out_dir)
    wb = load_workbench(out_dir, _ROOT)
    has_pdfjs = install_pdfjs(out_dir)
    sch_pdf = str(wb.get("schematic_pdf") or "")
    if sch_pdf and not (out_dir / sch_pdf).is_file():
        sch_pdf = ""
    counts = Counter()
    grouped: OrderedDict[str, list] = OrderedDict()
    details = []
    for item in items:
        rid = item.get("rule_id") or ""
        pack = packs.get(rid) or {}
        v = pack.get("verdict") or "REVIEW_REQUIRED"
        chip = VERDICT_CHIP.get(v, v)
        counts[chip] += 1
        cat = item.get("category") or "未分类"
        grouped.setdefault(cat, []).append((item, pack, v, chip))
        details.append(
            f"<div class='detail-slot' id='slot-{e(rid)}' hidden>"
            f"{render_detail(item, pack, out_dir, board, wb)}</div>"
        )

    list_html = []
    nav_body = []
    for cat, rows in grouped.items():
        cc = Counter(ch for *_, ch in rows)
        list_html.append(
            f"<div class='cat-bar' data-cat='{e(cat)}' id='cat-{e(cat)}'>{e(cat)}"
            f"<small>共{len(rows)} · 不通过 {cc.get('FAIL',0)} · 待确认 {cc.get('BLOCKED',0)} · "
            f"通过 {cc.get('PASS',0)} · 不适用 {cc.get('NOT_APPLICABLE',0)}</small></div>"
        )
        nav_items = []
        for item, pack, v, chip in rows:
            rid = item.get("rule_id") or ""
            risk = risk_of(item, pack)
            note = (pack.get("note") or "")[:80]
            short = (item.get("name") or "").split("\n")[0]
            list_html.append(
                f"<div class='row' data-id='{e(rid)}' data-verdict='{e(v)}' data-chip='{e(chip)}' "
                f"data-text='{e(rid + ' ' + (item.get('name') or '') + ' ' + (pack.get('note') or ''))}' "
                f"onclick=\"openItem('{e(rid)}')\">"
                f"<div class='rid'>{e(rid)}</div>"
                f"<div class='seq'>#{e(item.get('atomic_seq'))}</div>"
                f"<div><div class='name'>{e(item.get('name'))}</div>"
                f"<div class='parent'>{e(item.get('parent_name'))} · {e(note)}</div></div>"
                f"<span class='pill {e(chip)}'>{e(VERDICT_LABEL.get(v, v))}</span>"
                f"<span class='risk-tag'>{e(risk)}</span>"
                f"<span class='chev'>›</span></div>"
            )
            nav_items.append(
                f"<div class='nav-item' data-id='{e(rid)}'>"
                f"<span class='rid'>{e(rid)}</span>"
                f"<span class='pill {e(chip)}'>{e(VERDICT_LABEL.get(v, v))}</span>"
                f"<span class='nm' title='{e(short)}'>{e(short)}</span></div>"
            )
        nav_body.append(
            f"<details class='nav-cat' open>"
            f"<summary><span>{e(cat)}</span>"
            f"<span class='cnt'>{len(rows)} · 否{cc.get('FAIL',0)} 待{cc.get('BLOCKED',0)}</span></summary>"
            + "".join(nav_items)
            + "</details>"
        )

    present = board.get("present") or {}
    mat = " · ".join(f"{k}{'✓' if v else '×'}" for k, v in present.items()) if present else ""
    files = "、".join(board.get("files") or [])
    n_all = sum(counts.values())
    nav_hd = (
        f"<div class='board'>{e(board.get('name'))}</div>"
        f"<div class='sum'>是 {counts.get('PASS',0)} · 否 {counts.get('FAIL',0)} · "
        f"待确认 {counts.get('BLOCKED',0)} · 不适用 {counts.get('NOT_APPLICABLE',0)}</div>"
        f"<a class='nav-all' href='#list'>全部检查</a>"
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>产品中心原理图评审 · {e(board.get('name'))}</title>
<style>{CSS}
/* Shared review.v1 hierarchy: ordinary headings must not rely on legacy summary styles. */
.rv1 {{ color:#273d49; font-size:14px; line-height:1.8; }}
.rv1 .rv-panel {{ margin:16px 0; padding:22px 24px; border:1px solid #dce5e9; border-radius:14px; background:#fff; overflow:hidden; }}
.rv1 p {{ margin:10px 0 0; overflow-wrap:anywhere; }}
.rv1 .rv-kicker {{ color:#607783; font-size:12px; font-weight:700; letter-spacing:.06em; margin-bottom:12px; }}
.rv1 .rv-decision {{ background:#f5f9fb; }}
.rv1 .rv-metrics {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:20px; margin-bottom:16px; }}
.rv1 .rv-metrics>div+div {{ border-left:1px solid #dce5e9; padding-left:24px; }}
.rv1 .rv-metrics small {{ display:block; color:#687d88; font-size:12px; }}
.rv1 .rv-metrics strong {{ display:block; font-size:23px; font-weight:650; color:#234758; margin-top:4px; }}
.rv1 .rv-heading {{ display:flex; align-items:center; flex-wrap:wrap; gap:8px 12px; }}
.rv1 h3 {{ font-size:18px; line-height:1.5; margin:0 4px 0 0; color:#213d4d; }}
.rv1 .rv-tag {{ display:inline-block; border-radius:6px; padding:2px 8px; font-size:11px; line-height:1.7; color:#93611c; background:#fff3dc; white-space:nowrap; }}
.rv1 .rv-tag-neutral {{ background:#eef3f6; color:#536e7d; }}
.rv1 .rv-route {{ padding:13px 16px; border-radius:8px; background:#edf6f7; color:#235a66; font-weight:550; margin:16px 0; }}
.rv1 .rv-condition {{ display:flex; gap:16px; padding:0 0 16px; font-size:13px; color:#6b7780; }}
.rv1 .rv-condition b {{ flex-shrink:0; color:#4d6572; }}
.rv1 .rv-calc-title {{ margin-top:20px; }}
.rv1 .rv-calcs {{ list-style:none; counter-reset:calc; padding:0; margin:0; border:1px solid #e4ebef; border-radius:9px; overflow:hidden; }}
.rv1 .rv-calcs li {{ counter-increment:calc; display:flex; gap:12px; padding:10px 14px; background:#f8fafb; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }}
.rv1 .rv-calcs li+li {{ border-top:1px solid #e4ebef; }}
.rv1 .rv-calcs li::before {{ content:counter(calc,decimal-leading-zero); color:#8a9da7; font-size:12px; padding-top:2px; }}
.rv1 .rv-comparisons article+article {{ border-top:1px solid #e6edf0; margin-top:16px; padding-top:16px; }}
.rv1 .rv-value {{ margin-left:auto; color:#325d70; font-variant-numeric:tabular-nums; }}
.rv1 .rv-comparisons p {{ font-size:13px; color:#607581; }}
.rv1 .rv-audit {{ padding:0; background:#fafcfd; }}
.rv1 .rv-audit>summary {{ cursor:pointer; padding:18px 24px; font-weight:650; }}
.rv1 .rv-audit>summary>span {{ font-size:12px; font-weight:400; color:#82939d; margin-left:16px; }}
.rv1 .rv-audit-body {{ border-top:1px solid #e1e9ed; padding:4px 24px 24px; }}
.rv1 .rv-audit-body>section {{ padding-top:20px; }}
.rv1 .rv-audit-body>section+section {{ border-top:1px solid #e2eaee; margin-top:24px; }}
.rv1 h4 {{ color:#456475; font-size:13px; margin:0 0 10px; }}
.rv1 .rv-note {{ background:#f0f5f8; border-radius:8px; padding:12px 16px; margin-top:14px; font-size:13px; }}
.rv1 .rv-audit-list {{ display:grid; gap:10px; }}
.rv1 .rv-audit-list article {{ background:white; border:1px solid #e3eaee; border-radius:9px; padding:14px 16px; }}
.rv1 .rv-audit-list p {{ color:#667a85; font-size:13px; margin-top:6px; }}
.premise .premise-grid {{ gap:18px 28px; }}
.premise .premise-label {{ color:#506d7b; }}
.premise .premise-reason li+li {{ margin-top:7px; }}
@media(max-width:700px) {{ .rv1 .rv-panel {{ padding:16px; }} .rv1 .rv-metrics {{ gap:10px; }} .rv1 .rv-metrics>div+div {{ padding-left:10px; }} .rv1 .rv-metrics strong {{ font-size:18px; }} .rv1 .rv-audit>summary>span {{ display:block; margin-left:0; }} }}

.rv-premise .premise-reason {{ display:grid; grid-template-columns:80px minmax(0,1fr); align-items:start; gap:16px; padding-top:16px; border-top:1px solid #e3eaee; }}
.rv-premise .premise-reason>.premise-label {{ margin:3px 0 0; font-size:12px; line-height:1.8; }}
.rv-premise .rv-reason-steps {{ list-style:none; counter-reset:reason; padding:0; margin:0; }}
.rv-premise .rv-reason-steps li {{ counter-increment:reason; display:grid; grid-template-columns:22px minmax(0,1fr); gap:10px; margin:0; padding:0; font-size:13px; font-weight:400; line-height:1.85; color:#526b78; }}
.rv-premise .rv-reason-steps li+li {{ margin-top:10px; }}
.rv-premise .rv-reason-steps li::before {{ content:counter(reason); display:block; text-align:center; height:22px; line-height:22px; font-size:11px; color:#63808d; background:#eaf1f4; border-radius:6px; margin-top:1px; }}
@media(max-width:700px) {{ .rv-premise .premise-reason {{ grid-template-columns:1fr; gap:10px; }} }}

/* One card surface and one internal heading system throughout the pilot detail. */
.review-detail {{ background:#f3f6f8; }}
.review-detail .modal-hd {{ background:#fff; }}
.review-detail .rv-summary, .review-detail .rv-premise, .review-detail .rv1 .rv-panel,
.review-detail .rv-extract, .review-detail .rv-sheets, .review-detail .ds-pdf {{ background:#fff; border:1px solid #dce5e9; border-radius:14px; margin:0 0 16px; padding:22px 24px; box-shadow:0 2px 5px #29485904; }}
.review-detail .rv-section-title {{ display:flex; align-items:center; gap:10px; color:#345b70; font-size:14px; font-weight:650; line-height:1.5; letter-spacing:0; margin:0 0 18px; }}
.review-detail .rv-section-title::before {{ content:''; flex:0 0 4px; height:16px; border-radius:3px; background:#6d9dab; }}
.review-detail .rv-summary p {{ font-size:14px; line-height:1.85; margin:0; color:#294b5d; }}
.review-detail .rv1 .rv-case-title {{ color:#263f4e; font-size:17px; line-height:1.6; margin:0 4px 0 0; }}
.review-detail .premise-label, .review-detail .rv-subtitle {{ color:#647d89; font-size:12px; font-weight:600; }}
.review-detail .rv1 .rv-calc-title {{ margin:22px 0 10px; }}
.review-detail .rv-extract>.sheet {{ border:0; margin:0; padding:0; background:transparent; }}
.review-detail .rv-extract>.sheet>.cap {{ display:flex; align-items:center; gap:10px; color:#345b70; font-size:14px; font-weight:650; margin:0 0 18px; }}
.review-detail .rv-extract>.sheet>.cap::before {{ content:''; width:4px; height:16px; border-radius:3px; background:#6d9dab; }}
.review-detail .rv1 .rv-audit {{ padding:0; }}
.review-detail .rv1 .rv-audit>summary, .review-detail .ds-pdf>summary {{ position:relative; padding:20px 24px 20px 38px; color:#345b70; font-size:14px; font-weight:650; background:transparent; list-style:none; }}
.review-detail .rv1 .rv-audit>summary::before, .review-detail .ds-pdf>summary::before {{ content:''; position:absolute; left:24px; top:23px; width:4px; height:16px; border-radius:3px; background:#6d9dab; }}
.review-detail .rv1 .rv-audit>summary::after {{ content:'＋'; float:right; color:#7996a3; }}
.review-detail .rv1 .rv-audit[open]>summary::after {{ content:'－'; }}
.review-detail .ds-pdf {{ padding:0; }}
.review-detail .rv-audit-body h4 {{ display:inline-block; background:#edf3f6; padding:4px 10px; border-radius:6px; }}
.review-detail .rv-sheets .sheet {{ background:#fff; border-color:#e1e9ee; border-radius:9px; }}
@media(max-width:700px) {{ .review-detail .rv-summary, .review-detail .rv-premise, .review-detail .rv1 .rv-panel, .review-detail .rv-extract, .review-detail .rv-sheets {{ padding:18px 16px; }} .review-detail .rv1 .rv-audit {{ padding:0; }} }}
{(Path(__file__).resolve().parents[2] / 'report_ui/src/review-detail.css').read_text(encoding='utf-8')}
</style>
</head>
<body>
<header class="chrome">
  <h1>原理图原子检查 · 专家审计报告（产品中心 85）</h1>
  <div class="who">{e(board.get('name'))} · {date.today().isoformat()} · 模版：产品中心 {e(board.get('item_count') or 85)} 项 · 共 {n_all} 项</div>
</header>
<div class="layout">
  <aside class="sidenav" id="sidenav">
    <div class="nav-hd">{nav_hd}</div>
    <div class="nav-body" id="nav-body">{''.join(nav_body)}</div>
  </aside>
  <div class="splitter" id="splitter" title="左右拖动调整导航宽度"></div>
  <div class="main">
    <div class="report">
  <div id="view-list">
    <div class="toolbar">
      <input class="search" id="q" placeholder="搜索规则 ID / 检查项 / 位号 / 料号…" oninput="filterList()"/>
      <button class="btn" type="button" onclick="exportFeedback()">导出专家批注</button>
    </div>
    {__import__("agent_review.selection_coverage",fromlist=["render"]).render(out_dir)}
    <p class="hint">材料：{e(mat)}　文件：{e(files)}</p>
    <div id="serve-banner" class="serve-banner" hidden>
      关联图幅 PDF 查看（缩放 / 搜索 / 划选）需要通过 HTTP 打开本报告。
      请在 intake 目录执行：
      <code>python -m agent_review.serve --board boards/&lt;板&gt;</code>
    </div>
    <p class="hint">图幅查看：{'PDF.js 已就绪（推荐 serve 打开）' if has_pdfjs and sch_pdf else '缩略图模式（缺 schematic.pdf 或 pdfjs）'} · PDF：{e(sch_pdf or '无')}</p>

    <div class="panel list-wrap">
      <div class="panel-hd"><h2>检查项列表 · 点击一行进入详情</h2><span class="hint">共 {len(items)} 项</span></div>
      <div id="list">{''.join(list_html)}</div>
      <div class="empty" id="empty" hidden>没有匹配的检查项</div>
    </div>
  </div>

  <div id="view-detail">
    <a class="back" href="#list" onclick="goList(''); return false;">← 返回列表</a>
    <div id="detail-root"></div>
  </div>
    </div>
  </div>
</div>
  <div id="detail-store" hidden>{''.join(details)}</div>
<div id="dev-modal-bg" class="modal-bg">
  <div class="modal" id="dev-modal"></div>
</div>
<div id="img-lb" class="img-lb" role="dialog" aria-modal="true" aria-label="图幅预览" hidden>
  <div class="img-lb-panel" id="img-lb-panel">
    <div class="img-lb-tools">
      <button type="button" class="img-lb-newtab" id="img-lb-newtab" title="在新标签页打开 PDF.js">新窗口打开</button>
      <button type="button" class="img-lb-close" id="img-lb-close" title="关闭" aria-label="关闭">×</button>
    </div>
    <img id="img-lb-img" src="" alt="预览"/>
    <iframe id="pdf-lb-frame" class="pdf-lb-frame" title="原理图 PDF" hidden></iframe>
    <div class="img-lb-cap" id="img-lb-cap"></div>
  </div>
</div>
<script>
const KEY='expert-html-feedback-v2';
const FB_API='/api/feedback';
const SCH_PDF={json.dumps(sch_pdf, ensure_ascii=False)};
const HAS_PDFJS={json.dumps(bool(has_pdfjs and sch_pdf))};
let _fbCache={{}};
let _fbShared=false;
let _fbTimer=null;
let _fbPoll=null;
let _fbDirty={{}};
function isFileProtocol(){{ return location.protocol==='file:'; }}
function canShareFeedback(){{ return !isFileProtocol() && (location.protocol==='http:'||location.protocol==='https:'); }}
function showServeBanner(){{
  const b=document.getElementById('serve-banner');
  if(!b) return;
  if(isFileProtocol() && HAS_PDFJS){{
    b.hidden=false;
    b.classList.add('show');
  }}
}}
showServeBanner();
function loadLocal(){{ try {{ return JSON.parse(localStorage.getItem(KEY)||'{{}}'); }} catch(e){{ return {{}}; }} }}
function saveLocal(all){{
  try {{ localStorage.setItem(KEY, JSON.stringify(all||{{}})); }} catch(e){{}}
}}
function loadAll(){{ return _fbCache && typeof _fbCache==='object' ? _fbCache : loadLocal(); }}
/** 详情是从 #detail-store 克隆的，页面上会有重复 id/name；读写只认可见的 #detail-root */
function feedbackRoot(root){{
  if(root && root.querySelector) return root;
  return document.getElementById('detail-root') || null;
}}
function applyFeedbackToDom(root, all){{
  const scope=feedbackRoot(root);
  if(!scope) return;
  scope.querySelectorAll(':scope > [data-detail-id], [data-detail-id]').forEach(sec=>{{
    // 跳过隐藏模板库，避免同名 radio 把可见区勾选冲掉
    if(sec.closest('#detail-store')) return;
    const id=sec.getAttribute('data-detail-id');
    const rec=all[id]; if(!rec) return;
    if(_fbDirty[id]) return;
    const mark=String(rec.mark||'');
    sec.querySelectorAll('input[name="ex-'+id+'"]').forEach(function(inp){{
      inp.checked = !!mark && inp.value===mark;
    }});
    const t=sec.querySelector('textarea#tx-'+id+', textarea[id="tx-'+id+'"]');
    if(t && document.activeElement!==t) t.value=rec.note||'';
  }});
}}
function restoreOne(root){{ applyFeedbackToDom(root, loadAll()); }}
function setFbStatus(msg, ok){{
  let el=document.getElementById('fb-sync-status');
  if(!el){{
    el=document.createElement('div');
    el.id='fb-sync-status';
    el.style.cssText='position:fixed;right:16px;bottom:16px;z-index:9999;padding:8px 12px;border-radius:8px;font-size:12px;box-shadow:0 4px 16px rgba(0,0,0,.12);background:#fff;color:#24312e;border:1px solid #dde8e3;max-width:280px';
    document.body.appendChild(el);
  }}
  el.textContent=msg;
  el.style.borderColor=ok===false?'#c4413a':(ok?'#2b7d6f':'#dde8e3');
  el.hidden=!msg;
}}
async function pullFeedback(){{
  if(!canShareFeedback()){{
    _fbCache=loadLocal();
    _fbShared=false;
    return _fbCache;
  }}
  try{{
    const res=await fetch(FB_API,{{cache:'no-store'}});
    if(!res.ok) throw new Error('HTTP '+res.status);
    const data=await res.json();
    let remote=(data && typeof data==='object' && !Array.isArray(data)) ? data : {{}};
    const local=loadLocal();
    // 服务端空、本机有旧批注 → 一次性上载，避免部署后丢本地内容
    if(!Object.keys(remote).length && Object.keys(local).length){{
      const up=await fetch(FB_API,{{
        method:'PUT',
        headers:{{'Content-Type':'application/json'}},
        body:JSON.stringify(local)
      }});
      if(up.ok) remote=local;
    }}
    _fbCache=remote;
    _fbShared=true;
    saveLocal(_fbCache);
    applyFeedbackToDom(document, _fbCache);
    return _fbCache;
  }}catch(err){{
    _fbCache=loadLocal();
    _fbShared=false;
    setFbStatus('批注未共享（仅本机）。请用 python -m agent_review.serve 打开', false);
    return _fbCache;
  }}
}}
async function pushOne(id){{
  const rec=_fbCache[id];
  if(!rec) return;
  if(!_fbShared && !canShareFeedback()){{
    saveLocal(_fbCache);
    return;
  }}
  try{{
    const res=await fetch(FB_API+'/'+encodeURIComponent(id),{{
      method:'PUT',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify(rec)
    }});
    if(!res.ok) throw new Error('HTTP '+res.status);
    _fbShared=true;
    delete _fbDirty[id];
    setFbStatus('批注已同步', true);
    setTimeout(function(){{ setFbStatus('', true); }}, 1200);
  }}catch(err){{
    saveLocal(_fbCache);
    setFbStatus('同步失败，已暂存本机', false);
  }}
}}
function saveEx(id){{
  const root=feedbackRoot();
  if(!root) return;
  const mark=[...root.querySelectorAll('input[name="ex-'+id+'"]')].find(x=>x.checked);
  const tx=root.querySelector('textarea#tx-'+id+', textarea[id="tx-'+id+'"]');
  const note=(tx && tx.value) || '';
  _fbCache=_fbCache||{{}};
  _fbCache[id]={{
    mark: mark? mark.value:'',
    note: note,
    updated_at: new Date().toISOString(),
    by: ''
  }};
  _fbDirty[id]=true;
  saveLocal(_fbCache);
  // 同步回隐藏模板，避免下次打开克隆到空表单
  const slot=document.getElementById('slot-'+id);
  if(slot){{
    slot.querySelectorAll('input[name="ex-'+id+'"]').forEach(function(inp){{
      inp.checked = !!(mark && inp.value===mark.value);
    }});
    const stx=slot.querySelector('textarea#tx-'+id+', textarea[id="tx-'+id+'"]');
    if(stx) stx.value=note;
  }}
  if(_fbTimer) clearTimeout(_fbTimer);
  _fbTimer=setTimeout(function(){{ pushOne(id); }}, 400);
}}
function exportFeedback(){{
  const blob=new Blob([JSON.stringify(loadAll(),null,2)],{{type:'application/json'}});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='expert_feedback.json'; a.click();
}}
function startFeedbackSync(){{
  pullFeedback().then(function(){{
    if(_fbShared) setFbStatus('批注已共享（全员同一份）', true);
    setTimeout(function(){{ if(_fbShared) setFbStatus('', true); }}, 2000);
  }});
  if(_fbPoll) clearInterval(_fbPoll);
  if(canShareFeedback()){{
    _fbPoll=setInterval(function(){{
      if(Object.keys(_fbDirty).length) return;
      pullFeedback();
    }}, 15000);
  }}
  window.addEventListener('focus', function(){{
    if(Object.keys(_fbDirty).length) return;
    pullFeedback();
  }});
}}
startFeedbackSync();
function filterList(){{
  const q=(document.getElementById('q').value||'').toLowerCase();
  let shown=0;
  document.querySelectorAll('#list .row').forEach(row=>{{
    const okQ = !q || (row.dataset.text||'').toLowerCase().includes(q);
    row.style.display = okQ ? '' : 'none';
    if(okQ) shown++;
  }});
  document.querySelectorAll('#list .cat-bar').forEach(bar=>{{
    let el=bar.nextElementSibling, any=false;
    while(el && !el.classList.contains('cat-bar')){{
      if(el.classList.contains('row') && el.style.display!=='none') any=true;
      el=el.nextElementSibling;
    }}
    bar.style.display = any ? '' : 'none';
  }});
  document.getElementById('empty').hidden = shown>0;
}}
function highlightRow(id){{
  document.querySelectorAll('#list .row').forEach(r=> r.classList.toggle('on', !!id && r.dataset.id===id));
  document.querySelectorAll('.nav-item').forEach(el=>{{
    const on = !!id && el.dataset.id===id;
    el.classList.toggle('on', on);
    if(on){{
      const cat=el.closest('details');
      if(cat) cat.open=true;
      el.scrollIntoView({{block:'nearest'}});
    }}
  }});
  if(id){{
    const row=document.querySelector('#list .row[data-id="'+id+'"]');
    if(row) row.scrollIntoView({{block:'center'}});
  }}
}}
function goList(id){{
  location.hash = id ? 'list/'+id : 'list';
}}
function openItem(id){{
  const slot=document.getElementById('slot-'+id);
  const root=document.getElementById('detail-root');
  if(!slot){{ return; }}
  closeDevice();
  root.innerHTML = slot.innerHTML;
  document.body.classList.add('mode-detail');
  if(location.hash !== '#item/'+id) location.hash = 'item/'+id;
  restoreOne(root);
  bindDev(root);
  highlightRow(id);
  const main=document.querySelector('.main');
  if(main) main.scrollTop=0;
}}
function closeItem(){{
  closeDevice();
  document.body.classList.remove('mode-detail');
  document.getElementById('detail-root').innerHTML='';
}}
function currentDevPack(){{
  const ta=document.querySelector('#detail-root .js-devpack');
  if(!ta) return {{devices:[]}};
  try {{ return JSON.parse(ta.value||'{{}}'); }} catch(e){{ return {{devices:[]}}; }}
}}
function hEsc(s){{
  return String(s==null?'':s).replace(/[&<>"']/g, c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}
function stepEq(step){{
  if(step==null) return '';
  if(typeof step==='object') return String(step.eq||step.expr||'').trim();
  let t=String(step).trim();
  // 防御：历史数据把 dict 形态的 eq 整段塞进 steps
  if(t.indexOf("'eq'")>=0 || t.indexOf('"eq"')>=0){{
    const colon=t.indexOf(':');
    if(colon>=0){{
      let rest=t.slice(colon+1).trim();
      if((rest.startsWith("'")&&rest.endsWith("'}}"))||(rest.startsWith('"')&&rest.endsWith('"}}'))){{
        rest=rest.slice(1, rest.length-2);
        return rest.trim();
      }}
    }}
  }}
  return t;
}}
function toMathHtml(raw){{
  const s=String(raw==null?'':raw);
  let i=0, out='', afterNum=false;
  const isUnit=function(t){{
    return /^(mW|mV|kΩ|mA|uA|µA|kV|Ω|ohm|V|W|A|R)$/i.test(t);
  }};
  while(i<s.length){{
    const rest=s.slice(i);
    const fn=rest.match(/^(V|I|P|R)\\(([^)]+)\\)/);
    if(fn){{
      out+='<i class="sym">'+hEsc(fn[1])+'</i><sub>'+hEsc(fn[2])+'</sub>';
      i+=fn[0].length; afterNum=false; continue;
    }}
    if(s[i]==='²'){{ out+='<sup>2</sup>'; i++; afterNum=false; continue; }}
    if(s[i]==='³'){{ out+='<sup>3</sup>'; i++; afterNum=false; continue; }}
    const num=rest.match(/^[0-9]+(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/);
    if(num){{
      out+='<span class="sol-n">'+hEsc(num[0])+'</span>';
      i+=num[0].length; afterNum=true; continue;
    }}
    const id=rest.match(/^[A-Za-zµΩ][A-Za-z0-9_µΩ]*/);
    if(id){{
      if(afterNum && isUnit(id[0])){{
        out+='<span class="unit">'+hEsc(id[0])+'</span>';
        i+=id[0].length; afterNum=false; continue;
      }}
      const bits=id[0].split('_');
      out+='<i class="sym">'+hEsc(bits[0])+'</i>';
      if(bits.length>1) out+='<sub>'+hEsc(bits.slice(1).join('_'))+'</sub>';
      i+=id[0].length; afterNum=false; continue;
    }}
    if(/[\\u4e00-\\u9fff]/.test(s[i])){{
      let j=i;
      while(j<s.length && /[\\u4e00-\\u9fff]/.test(s[j])) j++;
      out+='<span class="plain">'+hEsc(s.slice(i,j))+'</span>';
      i=j; afterNum=false; continue;
    }}
    if(s[i]===' ' || s[i]==='\\t'){{
      out+=' '; i++; continue;
    }}
    out+=hEsc(s[i]); i++; afterNum=false;
  }}
  return out;
}}
function srcNoteHtml(src, note, cite){{
  const a=String(src||'');
  let b=String(note||'');
  if(!b) b=String(cite||'');
  let html='';
  if(a) html+='<span class="sol-src">'+hEsc(a)+'</span>';
  if(b && b!==a) html+=(a?' ':'')+'<span class="sol-note">'+hEsc(b)+'</span>';
  return html;
}}
function renderCalc(c){{
  if(!c) return '';
  let html='<details class="sol-calc-box"><summary>展开算式</summary><div class="sol-calc-body">';
  const kn=c.knowns||[];
  if(kn.length){{
    html+='<table class="sol-calc-k"><thead><tr><th>用到的已知</th><th>取值</th><th>来源</th></tr></thead><tbody>';
    kn.forEach(function(k){{
      html+='<tr><td class="sym-cell">'+toMathHtml(k.sym)+'</td><td class="val-cell">'+toMathHtml(k.val)
        +'</td><td>'+srcNoteHtml(k.src, k.note, k.cite)+renderCalc(k.calc)+'</td></tr>';
    }});
    html+='</tbody></table>';
  }}
  const steps=c.steps||[];
  if(steps.length){{
    steps.forEach(function(s){{
      const eq=stepEq(s);
      if(!eq) return;
      html+='<div class="sol-eq">'+toMathHtml(eq)+'</div>';
    }});
  }} else {{
    if(c.eq) html+='<div class="sol-eq">'+toMathHtml(c.eq)+'</div>';
    if(c.subs) html+='<div class="sol-eq">代入：'+toMathHtml(c.subs)+'</div>';
    if(c.result) html+='<div class="sol-eq">= '+toMathHtml(c.result)+'</div>';
  }}
  html+='</div></details>';
  return html;
}}
function vDot(zh){{
  if(zh==='是') return 'pass';
  if(zh==='否') return 'fail';
  return 'wait';
}}
function stressViewHtml(st){{
  const pages=(st.figure_pages && st.figure_pages.length) ? st.figure_pages : [{{src: st.figure, label:''}}];
  const first=pages[0]||{{}};
  const relation=['cross_case_bound','incomplete_proof'].includes(st.kind);
  const diagramLabel=relation?'分析关系图':'工况分析图';
  const lines=(st.lines||[]).map(function(row){{ return {{expr: row.expr||'', src: row.src||''}}; }});
  return '<button type="button" class="stress-cover" data-pages="'+hEsc(JSON.stringify(pages))+'" data-lines="'+hEsc(JSON.stringify(lines))+'">'
    +'<img alt="'+diagramLabel+'" src="'+hEsc(first.src||st.figure)+'">'
    +'<span class="stress-cover-mask"><span>打开'+diagramLabel+' · 同一主分析 '+pages.length+' 幅图</span></span>'
    +'</button>';
}}
let _stressPages=[], _stressLines=[], _stressPage=0, _stressSplit=false, _stressPanes=[];
let _full={{z:1,x:0,y:0}}, _fullDrag=null, _fullDown=null;
function closeStressPreview(){{
  const root=document.getElementById('stress-preview');
  if(root) root.hidden=true;
}}
function _pairStart(){{
  if(_stressPages.length<2) return 0;
  return Math.min(_stressPage, _stressPages.length-2);
}}
function _visible(){{
  if(!_stressSplit || _stressPages.length<2) return [_stressPage];
  const a=_pairStart();
  return [a, a+1];
}}
function _paintZoom(){{
  const z=_stressPanes[0] ? _stressPanes[0].st.z : 1;
  const el=document.querySelector('#stress-preview .pv-zlabel');
  if(el) el.textContent=Math.round(z*100)+'%';
}}
function _bindSharp(host, src){{
  const board=document.createElement('canvas');
  board.className='pv-sharp';
  host.appendChild(board);
  const ctx=board.getContext('2d');
  const img=new Image();
  const st={{z:1,x:0,y:0,nw:0,nh:0,drawnZ:-1}};
  function fit(){{
    const r=host.getBoundingClientRect();
    if(!st.nw || r.width<2 || r.height<2) return 0;
    return Math.min(r.width/st.nw, r.height/st.nh);
  }}
  function place(){{
    const r=host.getBoundingClientRect();
    const scale=fit()*st.z;
    if(!scale) return;
    const cssW=st.nw*scale, cssH=st.nh*scale;
    board.style.width=Math.round(cssW)+'px';
    board.style.height=Math.round(cssH)+'px';
    board.style.left=Math.round(r.width/2-cssW/2+st.x)+'px';
    board.style.top=Math.round(r.height/2-cssH/2+st.y)+'px';
  }}
  function redraw(){{
    const dpr=window.devicePixelRatio||1;
    const scale=fit()*st.z;
    if(!scale) return;
    const cssW=st.nw*scale, cssH=st.nh*scale;
    const bw=Math.max(1, Math.round(cssW*dpr));
    const bh=Math.max(1, Math.round(cssH*dpr));
    if(board.width!==bw || board.height!==bh){{ board.width=bw; board.height=bh; }}
    ctx.imageSmoothingEnabled=bw<st.nw;
    ctx.imageSmoothingQuality='high';
    ctx.drawImage(img, 0, 0, bw, bh);
    st.drawnZ=st.z;
    place();
  }}
  function apply(){{ if(st.drawnZ!==st.z) redraw(); else place(); }}
  img.onload=function(){{ st.nw=img.naturalWidth; st.nh=img.naturalHeight; redraw(); }};
  img.src=src||'';
  return {{st:st, apply:apply}};
}}
let _fullSharp=null;
function _makePane(page, i, host){{
  const el=document.createElement('div');
  el.className='pv-pane';
  el.innerHTML='<button type="button" class="pv-fullbtn">全图</button><div class="pv-canvas"></div>';
  el.querySelector('.pv-fullbtn').addEventListener('click', function(ev){{ ev.stopPropagation(); _openFull(i); }});
  const canvas=el.querySelector('.pv-canvas');
  const sharp=_bindSharp(canvas, page.src||'');
  const st=sharp.st;
  function zoomAt(clientX, clientY, next){{
    const prev=st.z;
    const z=Math.min(6, Math.max(0.35, next));
    const rect=canvas.getBoundingClientRect();
    const mx=clientX-rect.left-rect.width/2;
    const my=clientY-rect.top-rect.height/2;
    st.x=mx-((mx-st.x)*z)/prev;
    st.y=my-((my-st.y)*z)/prev;
    st.z=z;
    sharp.apply();
    _paintZoom();
  }}
  canvas.addEventListener('wheel', function(ev){{
    ev.preventDefault();
    zoomAt(ev.clientX, ev.clientY, st.z*(ev.deltaY<0?1.12:1/1.12));
  }}, {{passive:false}});
  let drag=null;
  canvas.addEventListener('pointerdown', function(ev){{
    if(ev.button!==0) return;
    drag={{x:ev.clientX, y:ev.clientY, ox:st.x, oy:st.y}};
    canvas.classList.add('dragging');
    canvas.setPointerCapture(ev.pointerId);
  }});
  canvas.addEventListener('pointermove', function(ev){{
    if(!drag) return;
    st.x=drag.ox+(ev.clientX-drag.x);
    st.y=drag.oy+(ev.clientY-drag.y);
    sharp.apply();
  }});
  canvas.addEventListener('pointerup', function(){{ drag=null; canvas.classList.remove('dragging'); }});
  host.appendChild(el);
  return {{el:el, st:st, zoomAt:zoomAt, reset:function(){{ st.z=1; st.x=0; st.y=0; sharp.apply(); _paintZoom(); }}}};
}}
function _renderRail(){{
  const rail=document.querySelector('#stress-preview .pv-rail');
  if(!rail) return;
  const shown=new Set(_visible());
  let html='';
  _stressPages.forEach(function(p, i){{
    if(i) html+='<span class="pv-join">关联</span>';
    html+='<button type="button" class="pv-step'+(shown.has(i)?' on':'')+'" data-i="'+i+'"><i>'+(i+1)+'</i><span><strong>'+hEsc(p.label||'本页')+'</strong></span></button>';
  }});
  if(_stressPages.length>1){{
    html+='<div class="pv-tools"><button type="button" id="pv-one" class="'+(_stressSplit?'':'on')+'">一页跟着看</button><button type="button" id="pv-split" class="'+(_stressSplit?'on':'')+'">相邻两页对照</button></div>';
  }}
  rail.innerHTML=html;
}}
function _renderView(){{
  const view=document.querySelector('#stress-preview .pv-view');
  if(!view) return;
  const idxs=_visible();
  view.className='pv-view'+(idxs.length>1?' split':'');
  view.innerHTML='';
  _stressPanes.length=0;
  idxs.forEach(function(i){{ _stressPanes.push(_makePane(_stressPages[i]||{{}}, i, view)); }});
  _paintZoom();
}}
function _renderSide(){{
  const side=document.querySelector('#stress-preview .pv-side');
  if(!side) return;
  const page=_stressPages[_stressPage]||{{}};
  const next=_stressPages[_stressPage+1];
  let hand='';
  if(_stressSplit && _stressPages.length>1){{
    const a=_pairStart();
    hand='<div class="pv-hand">对照的是第 '+(a+1)+'、'+(a+2)+' 幅；页间关系以图中网名和标注为准。</div>';
  }} else if(next){{
    hand='<div class="pv-hand">继续查看关联图幅：<b>'+hEsc(next.label||'')+'</b></div>';
  }}
  const chain=_stressLines.length
    ? '<h2 style="margin-top:18px">计算链</h2><div class="pv-chain">'+_stressLines.map(function(row, i){{
        return '<article><div class="expr"><span class="n">'+(i+1)+'</span>'+hEsc(row.expr||'')+'</div>'+(row.src?'<div class="src">'+hEsc(row.src)+'</div>':'')+'</article>';
      }}).join('')+'</div>'
    : '';
  side.innerHTML='<h2>这一段</h2><p>'+hEsc(page.label||'本页')+'</p>'+chain+hand
    +'<div class="pv-hint" style="margin-top:14px">图上只留结论和端压。算式和出处在这一侧，不画在铜线上。</div>';
}}
function _stressShow(n){{
  if(!_stressPages.length) return;
  _stressPage=Math.max(0, Math.min(_stressPages.length-1, n));
  if(_stressPages.length<2) _stressSplit=false;
  _renderRail();
  _renderView();
  _renderSide();
}}
function _zoomAll(factor){{
  _stressPanes.forEach(function(p){{
    const r=p.el.getBoundingClientRect();
    p.zoomAt(r.left+r.width/2, r.top+r.height/2, p.st.z*factor);
  }});
}}
function _paintFull(){{
  const lab=document.querySelector('#stress-preview .pv-fz');
  if(_fullSharp){{
    _fullSharp.st.z=_full.z;
    _fullSharp.st.x=_full.x;
    _fullSharp.st.y=_full.y;
    _fullSharp.apply();
  }}
  if(lab) lab.textContent=Math.round(_full.z*100)+'%';
}}
function _zoomFull(clientX, clientY, next){{
  const stage=document.querySelector('#stress-preview .pv-fullstage');
  if(!stage) return;
  const prev=_full.z;
  const z=Math.min(8, Math.max(0.35, next));
  const rect=stage.getBoundingClientRect();
  const mx=clientX-rect.left-rect.width/2;
  const my=clientY-rect.top-rect.height/2;
  _full.x=mx-((mx-_full.x)*z)/prev;
  _full.y=my-((my-_full.y)*z)/prev;
  _full.z=z;
  _paintFull();
}}
function _openFull(i){{
  const page=_stressPages[i];
  if(!page) return;
  _full={{z:1,x:0,y:0}};
  const stage=document.querySelector('#stress-preview .pv-fullstage');
  const title=document.querySelector('#stress-preview .pv-ftitle');
  if(title) title.textContent=page.label||'全图';
  if(stage){{
    stage.innerHTML='';
    _fullSharp=_bindSharp(stage, page.src||'');
  }}
  _paintFull();
  const box=document.querySelector('#stress-preview .pv-full');
  if(box) box.classList.add('open');
}}
function _closeFull(){{
  const box=document.querySelector('#stress-preview .pv-full');
  if(box) box.classList.remove('open');
}}
function ensureStressPreview(){{
  let root=document.getElementById('stress-preview');
  if(root) return root;
  root=document.createElement('div');
  root.id='stress-preview';
  root.className='stress-preview';
  root.hidden=true;
  root.innerHTML=''
    +'<div class="pv-top"><button type="button" class="pv-back">← 回到明细</button><div class="who"></div></div>'
    +'<div class="pv-main">'
    +'<div class="pv-stage"><div class="pv-rail"></div><div class="pv-view"></div>'
    +'<div class="pv-zoom"><button type="button" class="pv-out">−</button><span class="pv-zlabel">100%</span><button type="button" class="pv-in">+</button><button type="button" class="pv-fit">适应</button><span class="pv-hint">滚轮缩放 · 拖动平移</span></div>'
    +'</div>'
    +'<div class="pv-bar" title="左右拖动"></div>'
    +'<aside class="pv-side"></aside>'
    +'</div>'
    +'<div class="pv-full"><div class="bar"><b class="pv-ftitle">全图</b><span class="pv-fz">100%</span><span class="spacer"></span>'
    +'<button type="button" class="pv-fout">−</button><button type="button" class="pv-fin">+</button><button type="button" class="pv-ffit">适应全图</button><button type="button" class="pv-fclose">关闭</button></div>'
    +'<div class="pv-fullstage"></div></div>';
  document.body.appendChild(root);
  root.querySelector('.pv-back').onclick=function(ev){{ ev.stopPropagation(); closeStressPreview(); }};
  root.querySelector('.pv-in').onclick=function(ev){{ ev.stopPropagation(); _zoomAll(1.25); }};
  root.querySelector('.pv-out').onclick=function(ev){{ ev.stopPropagation(); _zoomAll(1/1.25); }};
  root.querySelector('.pv-fit').onclick=function(ev){{ ev.stopPropagation(); _stressPanes.forEach(function(p){{ p.reset(); }}); }};
  root.addEventListener('click', function(ev){{
    const step=ev.target.closest('.pv-step');
    if(step){{ _stressShow(Number(step.dataset.i)); return; }}
    if(ev.target.id==='pv-split'){{ _stressSplit=true; _stressShow(_stressPage); }}
    if(ev.target.id==='pv-one'){{ _stressSplit=false; _stressShow(_stressPage); }}
  }});
  const bar=root.querySelector('.pv-bar');
  const main=root.querySelector('.pv-main');
  let barDrag=null;
  bar.addEventListener('pointerdown', function(ev){{
    if(ev.button!==0) return;
    barDrag=true;
    bar.setPointerCapture(ev.pointerId);
  }});
  bar.addEventListener('pointermove', function(ev){{
    if(!barDrag) return;
    const rect=main.getBoundingClientRect();
    const side=Math.min(rect.width-280, Math.max(240, rect.right-ev.clientX));
    main.style.setProperty('--side-w', side+'px');
  }});
  function endBar(){{ barDrag=null; }}
  bar.addEventListener('pointerup', endBar);
  bar.addEventListener('pointercancel', endBar);
  const stage=root.querySelector('.pv-fullstage');
  stage.addEventListener('wheel', function(ev){{
    ev.preventDefault();
    _zoomFull(ev.clientX, ev.clientY, _full.z*(ev.deltaY<0?1.12:1/1.12));
  }}, {{passive:false}});
  stage.addEventListener('pointerdown', function(ev){{
    if(ev.button!==0) return;
    _fullDown={{x:ev.clientX, y:ev.clientY, onImg:ev.target.classList && ev.target.classList.contains('pv-sharp')}};
    _fullDrag={{x:ev.clientX, y:ev.clientY, ox:_full.x, oy:_full.y}};
    stage.setPointerCapture(ev.pointerId);
  }});
  stage.addEventListener('pointermove', function(ev){{
    if(!_fullDrag) return;
    _full.x=_fullDrag.ox+(ev.clientX-_fullDrag.x);
    _full.y=_fullDrag.oy+(ev.clientY-_fullDrag.y);
    _paintFull();
  }});
  stage.addEventListener('pointerup', function(ev){{
    if(_fullDown){{
      const moved=Math.hypot(ev.clientX-_fullDown.x, ev.clientY-_fullDown.y);
      if(!_fullDown.onImg && moved<6) _closeFull();
    }}
    _fullDrag=null; _fullDown=null;
  }});
  root.querySelector('.pv-fin').onclick=function(ev){{ ev.stopPropagation(); const r=stage.getBoundingClientRect(); _zoomFull(r.left+r.width/2, r.top+r.height/2, _full.z*1.25); }};
  root.querySelector('.pv-fout').onclick=function(ev){{ ev.stopPropagation(); const r=stage.getBoundingClientRect(); _zoomFull(r.left+r.width/2, r.top+r.height/2, _full.z/1.25); }};
  root.querySelector('.pv-ffit').onclick=function(ev){{ ev.stopPropagation(); _full={{z:1,x:0,y:0}}; _paintFull(); }};
  root.querySelector('.pv-fclose').onclick=function(ev){{ ev.stopPropagation(); _closeFull(); }};
  return root;
}}
function openStressPreview(pages, index, lines){{
  _stressPages=pages||[];
  _stressLines=lines||[];
  _stressSplit=false;
  const root=ensureStressPreview();
  root.hidden=false;
  _closeFull();
  _stressShow(index||0);
}}
function bindStressViews(root){{
  if(!root) return;
  root.querySelectorAll('.stress-cover').forEach(function(btn){{
    if(btn.dataset.bound) return;
    btn.dataset.bound='1';
    btn.addEventListener('click', function(ev){{
      ev.preventDefault();
      ev.stopPropagation();
      let pages=[], lines=[];
      try {{ pages=JSON.parse(btn.dataset.pages||'[]'); }} catch (err) {{ pages=[]; }}
      try {{ lines=JSON.parse(btn.dataset.lines||'[]'); }} catch (err) {{ lines=[]; }}
      if(!pages.length) return;
      openStressPreview(pages, 0, lines);
    }});
  }});
}}
function renderContractPremises(v){{
  const items=v.analysis_steps||[];
  const pending=function(step){{return (step.assumption_refs||[]).some(function(id){{return (v.key_assumptions||[]).some(function(a){{return a.id===id&&a.status==='unresolved';}});}});}};
  return '<section class="sheet premise rv-premise" data-review-module="premises"><h3 class="rv-section-title">' + hEsc(v.module_titles.premises) + '</h3><div class="premise-grid rv-materials-only">'
    +'<div><div class="premise-label">材料参数</div>'+(!(v.materials||[]).length?'<p class="hint">原记录未单列参数</p>':'')+(v.materials||[]).map(function(m){{return '<p>'+hEsc(m.label)+'：'+hEsc(m.text)+(m.kind==='model_parameter'?'（模型参数）':'')+'</p>';}}).join('')+'</div></div>'
    +'<div class="premise-reason"><div class="premise-label">分析思路</div>'+(!items.length?'<p class="hint">当前记录缺少可追溯的分析内容，见判定证据中的资料状态。</p>':'')+'<ol class="rv-reason-steps">'+items.map(function(item){{return '<li><span>'+hEsc(item.text)+(pending(item)?' <b class="rv-assumption-pending">待证条件</b>':'')+'</span></li>';}}).join('')+'</ol></div></section>';
}}
function renderContractReview(v){{
  const p=v.primary; let html='<div class="sol rv1">';
  const rows=v.assessment_rows||[];
  html+='<section class="rv-panel rv-decision" data-review-module="assessment"><h3 class="rv-section-title">'+hEsc(v.assessment_title||'判定要点')+'</h3>';
  if(rows.length)html+='<div class="rv-metrics">'+rows.map(function(r){{return '<div><small>'+hEsc(r.label)+'</small><strong>'+hEsc(r.text)+'</strong></div>';}}).join('')+'</div>';
  if(v.assessment_text)html+='<p>'+hEsc(v.assessment_text)+'</p>';
  if(v.criterion_text && v.criterion_text!==v.assessment_text)html+='<p>'+hEsc(v.criterion_text)+'</p>';
  if((v.assessment_checks||[]).length)html+='<div class="rv-audit-list">'+v.assessment_checks.map(function(c){{return '<article><b>'+hEsc(c.name)+'</b><p>'+hEsc(c.detail||'')+'</p></article>';}}).join('')+'</div>';
  html+='</section>';
  if(p){{
    html+='<section class="rv-panel rv-primary" data-review-module="primary"><h3 class="rv-kicker rv-section-title">' + hEsc(v.primary_module_title||v.module_titles.primary) + '</h3><div class="rv-heading">'+(!p.route_summary?'<h4 class="rv-case-title">'+hEsc(p.title)+'</h4>':'')+'<span class="rv-tag">'+hEsc(v.primary_label)+'</span>'+ (p.comparison?'<span class="rv-tag rv-tag-neutral">'+hEsc(p.comparison)+'</span>':'') +'</div><p class="rv-route">'+hEsc(p.route_summary)+'</p>';
    if(v.main_analysis)html+='<div class="rv-basis-scope"><b>分析范围</b><p>'+hEsc(v.main_analysis.scope.included)+'</p><small>范围外：'+hEsc(v.main_analysis.scope.excluded)+'</small></div>';
    if((p.conditions||[]).length)html+='<div class="rv-condition"><b>成立条件</b><span>'+hEsc(p.conditions.join('；'))+'</span></div>';
    if(p.figure)html+=stressViewHtml({{kind:p.kind,figure:p.figure,figure_pages:p.figure_pages,lines:(p.calculations||[]).map(function(c){{return {{expr:c.expression,src:''}};}})}});
    else if(p.route_expected)html+='<div class="sol-stress-miss">通路图尚未通过连线校验</div>';
    if((p.calculations||[]).length)html+='<h4 class="rv-subtitle rv-calc-title">计算过程</h4><ol class="rv-calcs">'+(p.calculations||[]).map(function(c){{return '<li>'+hEsc(c.expression)+'</li>';}}).join('')+'</ol>';
    html+='</section>';
  }}
  if(v.comparisons && v.comparisons.length)html+='<section class="rv-panel" data-review-module="comparisons"><h3 class="rv-kicker rv-section-title">' + hEsc(v.module_titles.comparisons) + '</h3><div class="rv-comparisons">'+v.comparisons.map(function(s){{return '<article><div class="rv-heading"><b>'+hEsc(s.title)+'</b><span class="rv-tag rv-tag-neutral">'+hEsc(s.kind==='model_boundary'?'判定边界':s.comparison)+'</span><strong class="rv-value">'+hEsc(s.value_text)+'</strong></div><p>'+hEsc(s.reason)+'</p></article>';}}).join('')+'</div></section>';
  html+='<details class="rv-panel rv-audit" data-review-module="audit"><summary>判定证据与未解边界<span>适用依据 · 覆盖范围 · 待确认事项</span></summary><div class="rv-audit-body"><section><h4>01 · 判定依据</h4><p>'+hEsc(v.explanation)+'</p><div class="rv-note"><b>适用条件</b><p>'+hEsc(v.basis.applicability||'原记录未单列适用条件')+'</p></div></section><section><h4>02 · 覆盖范围与边界</h4><p>'+hEsc(v.basis.coverage_argument||'原记录未单列覆盖范围，详见证据索引')+'</p></section>';
  if(v.main_analysis){{
    const labels={{supported:'已核查',unresolved:'待完成',refuted:'不成立'}};
    html+='<section><h4>正式判定依据的证明核查</h4><div class="rv-audit-list">'+v.main_analysis.proof_checks.map(function(c){{return '<article><b>'+hEsc(labels[c.status])+' · '+hEsc(c.claim)+'</b><p>'+hEsc(c.reason)+'</p><small>'+hEsc(c.evidence_refs.join('；'))+'</small></article>';}}).join('')+'</div></section>';
  }}
  if((v.gaps||[]).length)html+='<section><h4>03 · 未闭合事项</h4><div class="rv-audit-list">'+v.gaps.map(function(g){{return '<article><b>'+hEsc(g.issue)+'</b><p>'+hEsc(g.reason)+'</p></article>';}}).join('')+'</div></section>';
  if((v.audit_scenarios||[]).length)html+='<section><h4>补充工况说明</h4><div class="rv-audit-list">'+v.audit_scenarios.map(function(s){{return '<article><div class="rv-heading"><b>'+hEsc(s.title)+'</b><span class="rv-tag rv-tag-neutral">'+(s.role==='excluded'?'不纳入稳态排名':'排名未闭合')+'</span></div><p>'+hEsc(s.reason)+'</p></article>';}}).join('')+'</div></section>';
  if((v.key_assumptions||[]).length){{
    const labels={{supported:'已支持',model_assumption:'模型假设',unresolved:'待证',recorded:'原记录'}};
    html+='<section><h4>关键假设与来源（追溯）</h4><div class="rv-audit-list">'+v.key_assumptions.map(function(a){{return '<article><b>'+hEsc(labels[a.status]||a.status)+'</b><p>'+hEsc(a.text)+'</p><small>'+hEsc(a.id)+' · '+hEsc((a.source||{{}}).field||'')+((a.evidence_refs||[]).length?' · 证据：'+hEsc(a.evidence_refs.join('、')):'')+'</small></article>';}}).join('')+'</div></section>';
  }}
  if((v.notes||[]).length)html+='<section><h4>展示资料状态</h4>'+v.notes.map(function(n){{return '<p>'+hEsc(n)+'</p>';}}).join('')+'</section>';
  if((v.evidence||[]).length)html+='<section class="rv-audit-list"><h4>证据索引</h4>'+v.evidence.map(function(e){{return '<article><b>'+hEsc(e.display_label||'来源记录')+'</b><p>'+hEsc(e.display_locator||'')+'</p><p>'+hEsc(e.display_text||'')+'</p></article>';}}).join('')+'</section>';
  return html+'</div></details></div>';
}}
function renderPremises(d){{ return d.review_detail?renderContractPremises(d.review_detail):''; }}
function renderFormula(d){{ return d.review_detail?renderContractReview(d.review_detail):'<p>详情展示数据尚未生成</p>'; }}

function bindDev(root){{
  if(!root) return;
  const SIZE_KEY='sr-obj-page-size';
  const SIZE_OK={{'0':1,'10':1,'20':1,'50':1,'100':1}};
  function readSize(){{
    try {{
      const v=localStorage.getItem(SIZE_KEY);
      if(v && SIZE_OK[v]) return +v;
    }} catch (err) {{}}
    return 10;
  }}
  let pageSize=readSize();
  let filter='全部';
  let page=1;
  const rows=Array.from(root.querySelectorAll('tr.device'));
  const pager=root.querySelector('.obj-pager');
  const sizeSel=root.querySelector('.pg-size');
  function vis(){{
    return rows.filter(tr=> filter==='全部' || tr.dataset.bucket===filter);
  }}
  function apply(){{
    const list=vis();
    const size=pageSize>0 ? pageSize : Math.max(list.length, 1);
    const pages=Math.max(1, Math.ceil(list.length / size));
    if(page>pages) page=pages;
    if(page<1) page=1;
    const start=(page-1)*size;
    const end=start+size;
    rows.forEach(tr=>{{ tr.style.display='none'; }});
    list.forEach((tr,i)=>{{
      tr.style.display = (i>=start && i<end) ? '' : 'none';
    }});
    if(pager){{
      const info=pager.querySelector('.pg-info');
      if(info) info.textContent = list.length ? ('第 '+page+'/'+pages+' 页 · '+list.length+' 条') : '0 条';
      const prev=pager.querySelector('.pg-prev');
      const next=pager.querySelector('.pg-next');
      if(prev) prev.disabled = page<=1;
      if(next) next.disabled = page>=pages;
    }}
  }}
  if(sizeSel){{
    sizeSel.value=String(pageSize);
    sizeSel.onchange=()=>{{
      pageSize=+sizeSel.value;
      try {{ localStorage.setItem(SIZE_KEY, String(pageSize)); }} catch (err) {{}}
      page=1;
      apply();
    }};
  }}
  root.querySelectorAll('.filt').forEach(btn=>{{
    btn.onclick=()=>{{
      filter=btn.dataset.f;
      page=1;
      root.querySelectorAll('.filt').forEach(x=>x.classList.toggle('on', x===btn));
      apply();
    }};
  }});
  if(pager){{
    const prev=pager.querySelector('.pg-prev');
    const next=pager.querySelector('.pg-next');
    if(prev) prev.onclick=()=>{{ page-=1; apply(); }};
    if(next) next.onclick=()=>{{ page+=1; apply(); }};
  }}
  root.querySelectorAll('tr.device').forEach(tr=>{{
    tr.onclick=ev=>{{
      if(ev.target.closest('button')) return;
      openDevice(+tr.dataset.i);
    }};
  }});
  root.querySelectorAll('.js-dev').forEach(b=>{{
    b.onclick=ev=>{{
      ev.stopPropagation();
      const tr=b.closest('tr');
      if(tr) openDevice(+tr.dataset.i);
    }};
  }});
  apply();
}}
function openDevice(i){{
  const pack=currentDevPack();
  const list=pack.devices||[];
  const d=list[i];
  if(!d) return;
  const pages=d.pages||[];
  const jumps=pages.map(function(p,n){{
    return '<button type="button" data-p="'+p.page+'" class="'+(n===0?'on':'')+'">'
      +(n+1)+'. p'+p.page+' '+hEsc(p.page_name||'')
      +'<span class="why">'+hEsc((p.roles||[]).join(' · '))+'</span></button>';
  }}).join('');
  let sheets='<p class="hint">无关联图幅页码。</p>';
  if(pages.length){{
    const bits=[];
    for(let i=0;i<pages.length;i+=4){{
      const chunk=pages.slice(i,i+4);
      const cells=chunk.map(function(p,k){{
        const n=i+k;
        const roles=(p.roles||[]).map(hEsc).join(' / ');
        const pdfPath=hEsc(p.pdf||pack.schematic_pdf||SCH_PDF||'');
        return '<div class="sheet" id="sheet-p'+p.page+'">'
          +'<div class="cap">'+(n+1)+' / '+pages.length+' · p'+p.page+' '+hEsc(p.page_name||'')
          +' '+hEsc(p.module_title||'')+' · '+roles
          +(pdfPath?' <button type="button" class="sheet-open-tab" data-pdf="'+pdfPath+'" data-page="'+p.page+'">新窗口打开</button>':'')
          +'</div>'
          +'<img class="page-full" src="'+hEsc(p.img)+'" alt="第'+p.page+'页"'
          +' data-full="'+hEsc(p.img)+'" data-pdf="'+pdfPath+'" data-page="'+p.page+'"/></div>';
      }}).join('');
      bits.push('<div class="sheet-grid" style="grid-template-columns:repeat('+chunk.length+',minmax(0,1fr))">'+cells+'</div>');
    }}
    sheets=bits.join('');
  }}
  const ds=d.datasheet
    ? '<details class="fold ds-pdf"><summary><span>规格书 PDF · '+hEsc(d.mpn)+' · 共 '+(d.datasheet_pages||'?')+' 页'
      +'<span class="fold-hint">（默认收起）</span></span><span class="fold-btn"></span></summary>'
      +'<div class="fold-body"><div class="sheet">'
      +'<div class="cap"><a href="'+hEsc(d.datasheet)+'" target="_blank" rel="noopener">新窗口打开</a></div>'
      +'<iframe class="pdf-frame" data-src="'+hEsc(d.datasheet)+'" title="规格书"></iframe>'
      +'</div></div></details>'
    : '<p class="hint">本地规格书 PDF 未下载。</p>';
  const erows=d.extract_rows||[];
  let extractHtml='';
  if(erows.length){{
    const trs=erows.filter(r=>r.key!=='extract_note').map(r=>
      '<tr><td>'+hEsc(r.label||r.key)+'</td><td><code>'+hEsc(r.value)+'</code></td><td>'+hEsc(r.unit||'')+'</td></tr>'
    ).join('');
    const note=erows.find(r=>r.key==='extract_note');
    extractHtml='<div class="sheet"><h3 class="rv-section-title">规格书资料 · 摘录参考 · '+hEsc(d.mpn)+'</h3>'
      +'<div class="tbl-wrap"><table class="obj-cards"><thead><tr><th>字段</th><th>资料摘录值（非本次评审采用值）</th><th>单位</th></tr></thead><tbody>'
      +trs+'</tbody></table></div>'
      +(note?'<p class="hint">抽出说明：'+hEsc(note.value)+'</p>':'')
      +'</div>';
  }} else {{
    extractHtml='<div class="sheet"><h3 class="rv-section-title">规格书资料 · 摘录参考</h3><p class="hint">未抽出 · 规格书参数尚未抽出。</p></div>';
  }}
  const sc=pack.sheet_scope||d.sheet_scope||'device';
  const scopeHint=pack.sheet_hint||({{
    device:'仅器件所在页。本项不跟两端网络跨页。',
    net:'仅该网络出现过的图幅。',
    nets:'器件所在页 + 两端网络出现过的图幅（GND 全板铺开不跟）。',
    peer_caps:'器件所在页 + 同网电容所在页（不跟电源网全板其它器件）。',
    path:'器件所在页 + 路径相关位号所在页（不跟电源网全板）。'
  }}[sc]||'仅器件所在页。本项不跟两端网络跨页。');
  const sheetTitle=sc==='nets'?'关联图幅（整页 · 含连线跨页）':'关联图幅（整页）';
  const modal=document.getElementById('dev-modal');
  const bg=document.getElementById('dev-modal-bg');
  modal.classList.add('review-detail');
  // 给人看：一句 headline。长 summary 不是对象评审摘要。
  let blurb='';
  [[(d.review_detail||{{}}).headline,false]].some(function(pair){{
    const t=String(pair[0]||'').trim();
    if(pair[1] && t.length>90) return false;
    if(t && t!=='无' && t!=='—'){{ blurb=t; return true; }}
    return false;
  }});
  const blurbHtml=blurb
    ? (d.review_detail ? '<section class="rv-summary" data-review-module="summary"><h3 class="rv-section-title">' + hEsc(d.review_detail.module_titles.summary) + '</h3><p>'+hEsc(blurb)+'</p></section>' : '<div class="obj-blurb"><span class="blurb-k">对象评审摘要</span><p class="blurb-v">'+hEsc(blurb)+'</p></div>')
    : '';
  modal.innerHTML='<div class="modal-hd"><div class="ttl"><h3><code>'+hEsc(d.ref)+'</code> · '+hEsc(d.bucket)+'</h3>'
    +'<dl class="rv-device-identity">'+[['型号',d.mpn],['参数',d.value],['封装',d.footprint],['网络',(d.nets||[]).filter(Boolean).join(' / ')]].filter(function(x){{return x[1];}}).map(function(x){{return '<div><dt>'+hEsc(x[0])+'</dt><dd>'+hEsc(x[1])+'</dd></div>';}}).join('')+'</dl></div>'
    +'<button type="button" class="icon-btn" id="dev-prev">上一颗</button>'
    +'<button type="button" class="icon-btn" id="dev-next">下一颗</button>'
    +'<button type="button" class="icon-btn" id="dev-close">关闭</button></div>'
    +'<div class="review-body rv1" style="padding:14px 16px 20px">'+blurbHtml+renderPremises(d)
    +renderFormula(d)
    +(extractHtml?'<section class="rv-extract" data-review-module="datasheets">'+extractHtml+ds+'</section>':'')
    +(d.review_detail ? '<section class="rv-sheets" data-review-module="schematics"><h3 class="rv-section-title">'+hEsc(sheetTitle)+'</h3>' : '<h3 style="margin:8px 0;font-size:13px;color:var(--muted)">'+hEsc(sheetTitle)+'</h3>')
    +'<p class="hint">'+hEsc(scopeHint)+' 本对象 '+pages.length+' 页。</p>'
    +'<div class="page-jump">'+jumps+'</div>'+sheets+(d.review_detail?'</section>':'')
    +'</div>';
  if(d.review_detail)modal.querySelectorAll('[data-review-module]').forEach(function(el){{el.style.order=(d.review_detail.module_order||[]).indexOf(el.dataset.reviewModule);}});
  bg.classList.add('open');
  document.body.style.overflow='hidden';
  document.getElementById('dev-close').onclick=closeDevice;
  document.getElementById('dev-prev').onclick=function(){{ openDevice(Math.max(0, i-1)); }};
  document.getElementById('dev-next').onclick=function(){{ openDevice(Math.min(list.length-1, i+1)); }};
  const pdfFold=modal.querySelector('details.ds-pdf');
  if(pdfFold){{
    pdfFold.addEventListener('toggle', function(){{
      if(!pdfFold.open) return;
      const iframe=pdfFold.querySelector('iframe.pdf-frame');
      if(iframe && iframe.dataset.src && !iframe.getAttribute('src')) iframe.src=iframe.dataset.src;
    }});
  }}
  bindStressViews(modal);
  modal.querySelectorAll('img.page-full').forEach(img=>{{
    img.onclick=function(){{
      const pdf=img.getAttribute('data-pdf')||'';
      const page=parseInt(img.getAttribute('data-page')||'0',10)||0;
      if(pdf && page){{
        openPdfViewer(pdf, page, img.alt||('第'+page+'页'), img.getAttribute('data-full')||'');
      }} else {{
        openLightbox(img.getAttribute('data-full'), img.alt||'原理图整页');
      }}
    }};
  }});
  modal.querySelectorAll('.sheet-open-tab').forEach(btn=>{{
    btn.onclick=function(ev){{
      ev.preventDefault();
      ev.stopPropagation();
      openPdfInNewTab(btn.getAttribute('data-pdf')||'', parseInt(btn.getAttribute('data-page')||'0',10)||1);
    }};
  }});
  modal.querySelectorAll('.page-jump button').forEach(btn=>{{
    btn.onclick=function(){{
      modal.querySelectorAll('.page-jump button').forEach(x=>x.classList.remove('on'));
      btn.classList.add('on');
      const el=document.getElementById('sheet-p'+btn.dataset.p);
      if(el) el.scrollIntoView({{block:'start'}});
    }};
  }});
}}
function closeDevice(){{
  const bg=document.getElementById('dev-modal-bg');
  if(!bg) return;
  bg.classList.remove('open');
  document.body.style.overflow='';
}}
function route(){{
  const h=(location.hash||'#list').replace(/^#/, '');
  if(h.startsWith('item/')) {{ openItem(h.slice(5)); return; }}
  closeItem();
  const id = h.startsWith('list/') ? h.slice(5) : '';
  highlightRow(id);
}}
function bindSplitter(){{
  const saved=localStorage.getItem('expert-nav-w');
  if(saved) document.documentElement.style.setProperty('--nav-w', saved+'px');
  const sp=document.getElementById('splitter');
  if(!sp) return;
  let dragging=false;
  sp.addEventListener('mousedown', ev=>{{ dragging=true; sp.classList.add('drag'); ev.preventDefault(); }});
  window.addEventListener('mousemove', ev=>{{
    if(!dragging) return;
    const w=Math.min(520, Math.max(200, ev.clientX));
    document.documentElement.style.setProperty('--nav-w', w+'px');
  }});
  window.addEventListener('mouseup', ()=>{{
    if(!dragging) return;
    dragging=false; sp.classList.remove('drag');
    const w=parseInt(getComputedStyle(document.documentElement).getPropertyValue('--nav-w'),10);
    if(w) localStorage.setItem('expert-nav-w', String(w));
  }});
}}
function bindNav(){{
  document.querySelectorAll('.nav-item').forEach(el=>{{
    el.onclick=()=> goList(el.dataset.id);
  }});
}}
let _pdfViewerUrl='';
function buildPdfViewerUrl(pdfRel, page){{
  const pageNum=parseInt(page,10)||1;
  const rel=String(pdfRel||SCH_PDF||'').replace(/^\\.\\//,'').replace(/^\\/+/, '');
  let pdfAbs;
  try {{ pdfAbs=new URL(rel, location.href).href; }}
  catch(e){{ pdfAbs=location.origin+'/'+rel; }}
  const fileParam=encodeURIComponent(pdfAbs);
  return 'pdfjs/web/viewer.html?file='+fileParam+'&_p='+pageNum+'&_t='+Date.now()+'#page='+pageNum;
}}
function openPdfInNewTab(pdfRel, page){{
  if(isFileProtocol() || !HAS_PDFJS){{
    showServeBanner();
    alert('新窗口打开 PDF 需要 HTTP 服务。\\n请执行：python -m agent_review.serve --board boards/<板>');
    return;
  }}
  const url=buildPdfViewerUrl(pdfRel, page);
  window.open(url, '_blank', 'noopener');
}}
function openLightbox(src, cap){{
  if(!src) return;
  const lb=document.getElementById('img-lb');
  const panel=document.getElementById('img-lb-panel');
  const img=document.getElementById('img-lb-img');
  const frame=document.getElementById('pdf-lb-frame');
  const capEl=document.getElementById('img-lb-cap');
  const newtab=document.getElementById('img-lb-newtab');
  if(panel) panel.classList.remove('pdf-mode');
  if(frame){{ frame.hidden=true; frame.removeAttribute('src'); }}
  _pdfViewerUrl='';
  if(newtab) newtab.onclick=null;
  img.hidden=false;
  img.src=src;
  img.alt=cap||'预览';
  capEl.textContent=cap||'';
  lb.hidden=false;
  lb.classList.add('open');
}}
function openPdfViewer(pdfRel, page, cap, fallbackImg){{
  const pageNum=parseInt(page,10)||1;
  const title=(cap||('第'+pageNum+'页'))+' · PDF.js（Ctrl+F 搜索）';
  if(isFileProtocol() || !HAS_PDFJS){{
    showServeBanner();
    if(fallbackImg){{
      openLightbox(fallbackImg, title+' · 请用 serve 打开以启用缩放/搜索');
      return;
    }}
    alert('关联图幅 PDF 查看需要 HTTP 服务。\\n请执行：python -m agent_review.serve --board boards/<板>');
    return;
  }}
  const lb=document.getElementById('img-lb');
  const panel=document.getElementById('img-lb-panel');
  const img=document.getElementById('img-lb-img');
  const frame=document.getElementById('pdf-lb-frame');
  const capEl=document.getElementById('img-lb-cap');
  const newtab=document.getElementById('img-lb-newtab');
  const viewerUrl=buildPdfViewerUrl(pdfRel, pageNum);
  _pdfViewerUrl=viewerUrl;
  if(panel) panel.classList.add('pdf-mode');
  img.hidden=true;
  img.removeAttribute('src');
  frame.hidden=false;
  frame.removeAttribute('src');
  frame.src=viewerUrl;
  if(newtab){{
    newtab.onclick=function(ev){{
      ev.preventDefault();
      ev.stopPropagation();
      window.open(_pdfViewerUrl || buildPdfViewerUrl(pdfRel, pageNum), '_blank', 'noopener');
    }};
  }}
  capEl.textContent=title;
  lb.hidden=false;
  lb.classList.add('open');
}}
function closeLightbox(){{
  const lb=document.getElementById('img-lb');
  const panel=document.getElementById('img-lb-panel');
  const img=document.getElementById('img-lb-img');
  const frame=document.getElementById('pdf-lb-frame');
  const newtab=document.getElementById('img-lb-newtab');
  lb.classList.remove('open');
  lb.hidden=true;
  if(panel) panel.classList.remove('pdf-mode');
  img.hidden=false;
  img.removeAttribute('src');
  if(frame){{ frame.hidden=true; frame.removeAttribute('src'); }}
  _pdfViewerUrl='';
  if(newtab) newtab.onclick=null;
}}
document.addEventListener('click', ev=>{{
  const btn=ev.target.closest('.shot-open');
  if(btn){{
    ev.preventDefault();
    openLightbox(btn.getAttribute('data-lb-src'), btn.getAttribute('data-lb-cap')||'');
    return;
  }}
  const lb=document.getElementById('img-lb');
  if(!lb.classList.contains('open')) return;
  if(ev.target===lb || ev.target.id==='img-lb-close') closeLightbox();
}});
document.addEventListener('keydown', ev=>{{
  const preview=document.getElementById('stress-preview');
  if(preview && !preview.hidden){{
    if(ev.key==='Escape'){{
      const full=preview.querySelector('.pv-full');
      if(full && full.classList.contains('open')){{ ev.preventDefault(); full.classList.remove('open'); return; }}
      ev.preventDefault(); closeStressPreview(); return;
    }}
    if(ev.key==='ArrowRight'){{ ev.preventDefault(); _stressShow(_stressPage+1); return; }}
    if(ev.key==='ArrowLeft'){{ ev.preventDefault(); _stressShow(_stressPage-1); return; }}
  }}
  if(ev.key!=='Escape') return;
  if(document.getElementById('img-lb').classList.contains('open')){{
    ev.preventDefault();
    closeLightbox();
    return;
  }}
  const dbg=document.getElementById('dev-modal-bg');
  if(dbg && dbg.classList.contains('open')){{
    ev.preventDefault();
    closeDevice();
  }}
}});
const _devBg=document.getElementById('dev-modal-bg');
if(_devBg) _devBg.addEventListener('click', ev=>{{ if(ev.target.id==='dev-modal-bg') closeDevice(); }});
bindSplitter();
bindNav();
window.addEventListener('hashchange', route);
route();
filterList();
</script>
</body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    dest = out_dir / "expert_review.html"
    dest.write_text(build_html(out_dir), encoding="utf-8")
    print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
