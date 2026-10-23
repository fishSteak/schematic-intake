# -*- coding: utf-8 -*-
"""把 solve_prog 的交付写成 HTML。数字只来自 run() 的结果。"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from solve_prog.connection import connection_card
from solve_prog.deliver import OUT_DIR, run
from solve_prog.stimulus import find_series_groups

KG_DEFAULT = Path(__file__).resolve().parents[1] / "boards" / "0918全量" / "review_out" / "board_kg.json"


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _mw(watts: float) -> str:
    mw = float(watts) * 1000.0
    if abs(mw) >= 1000:
        return f"{mw / 1000.0:.4g} W"
    return f"{mw:.2f} mW"


def _badge(text: str, kind: str) -> str:
    return f'<span class="badge {kind}">{_esc(text)}</span>'


def _object_block(ref: str, payload: dict[str, Any], card: dict[str, Any]) -> str:
    info = (payload.get("objects") or {}).get(ref) or {}
    word = info.get("conclusion") or payload.get("conclusion") or "未过门禁"
    kind = {"否": "bad", "是": "ok"}.get(word, "wait")
    limit = info.get("limit_w")
    rows = []
    max_p = 0.0
    deciding = ""
    for state in payload["states"]:
        power = float(state["powers_w"].get(ref, 0.0))
        mark = "参评" if state["participates"] else "不参评"
        if state["participates"] and power >= max_p:
            max_p = power
            deciding = state["name"]
        steps = "".join(f"<li>{_esc(step)}</li>" for step in state["steps"])
        dropped = "，已脱离钳位后重算" if state["dropped"] else ""
        rows.append(
            "<section class='state'>"
            f"<h3>{_esc(state['name'])} {_badge(mark, 'ok' if state['participates'] else 'wait')}"
            f"{_badge('脱离钳位', 'wait') if state['dropped'] else ''}</h3>"
            f"<p>本态功耗 {_mw(power)}{dropped}。来源：求解步骤。</p>"
            f"<ol>{steps}</ol>"
            "</section>"
        )
    nets = []
    for net in card["nets"]:
        pins = "、".join(f"{row['ref']}.{row['pin']}" for row in net["pins"])
        voltage = "工程文件无电压" if net["voltage_v"] is None else f"{net['voltage_v']} V"
        nets.append(
            f"<li>{_esc(net['net'])} · {_esc(net['kind'])} · { _esc(voltage) } · {_esc(pins)}</li>"
        )
    limit_text = f"{_mw(limit)}（0.5×额定）" if limit is not None else "无"
    return f"""
<article class="card" id="{_esc(ref)}">
  <header>
    <h2>{_esc(ref)} {_badge(word, kind)}</h2>
    <p>计入的最大功耗 {_mw(max_p)} · 决定结论的情况：{_esc(deciding or '无')} · 限额 {limit_text}</p>
  </header>
  <h3>连接卡</h3>
  <ul>{''.join(nets)}</ul>
  {''.join(rows)}
</article>
"""


def render(payloads: list[dict[str, Any]], cards: dict[str, dict[str, Any]]) -> str:
    objects: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for payload in payloads:
        for ref in payload.get("stimulus", {}).get("objects") or [payload["object"]]:
            if ref not in seen:
                seen.add(ref)
                objects.append((ref, payload))
    counts = {"是": 0, "否": 0, "未过门禁": 0}
    for ref, payload in objects:
        word = (payload.get("objects") or {}).get(ref, {}).get("conclusion") or payload["conclusion"]
        counts[word] = counts.get(word, 0) + 1
    nav = []
    for ref, payload in objects:
        word = (payload.get("objects") or {}).get(ref, {}).get("conclusion") or payload["conclusion"]
        nav.append(f'<a href="#{_esc(ref)}">{_esc(ref)} {_badge(word, {"否":"bad","是":"ok"}.get(word,"wait"))}</a>')
    gates_html = []
    for payload in payloads:
        title = "、".join(payload.get("stimulus", {}).get("objects") or [payload["object"]])
        seen_gate: set[tuple[str, str, bool]] = set()
        gate_lines = []
        for gate in payload["gates"]:
            key = (gate["id"], gate["detail"], bool(gate["pass"]))
            if key in seen_gate:
                continue
            seen_gate.add(key)
            gate_lines.append(
                f"<li>{_esc(gate['id'])} {'通过' if gate['pass'] else '失败'} · {_esc(gate['detail'])}</li>"
            )
        items = "".join(gate_lines)
        # 同一编号在逐态重复，列表里保留每条，便于核对
        model = payload.get("stimulus", {}).get("model") or []
        model_html = "".join(
            f"<li>{_esc(row['ref'])} {_esc(row['param'])} = {_esc(row['volts'])} V · {_esc(row['origin'])}</li>"
            for row in model
        ) or "<li>无抽出角点</li>"
        spice = payload.get("spice") or {}
        gates_html.append(
            f"<section><h2>{_esc(title)}</h2>"
            f"<p>种类 {_esc((payload.get('stimulus') or {}).get('kind'))} · "
            f"来源 {_esc(payload.get('source'))} · {_esc(spice.get('detail'))}</p>"
            f"<ul>{model_html}</ul><ul>{items}</ul></section>"
        )
    blocks = "".join(_object_block(ref, payload, cards[ref]) for ref, payload in objects)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>solve_prog · P11-A01</title>
<style>
body {{ margin: 0; font: 15px/1.55 "Segoe UI", "Microsoft YaHei", sans-serif; color: #1c2430; background: #f4f6f8; }}
header.top {{ padding: 20px 28px; background: #123043; color: #fff; }}
header.top p {{ margin: 6px 0 0; color: #d5e2ea; }}
main {{ display: grid; grid-template-columns: 220px 1fr; gap: 16px; padding: 16px; }}
nav {{ position: sticky; top: 12px; align-self: start; display: flex; flex-direction: column; gap: 8px; }}
nav a {{ text-decoration: none; color: inherit; background: #fff; border: 1px solid #d7dee6; border-radius: 8px; padding: 8px 10px; }}
.card, section.group {{ background: #fff; border: 1px solid #d7dee6; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }}
.badge {{ display: inline-block; border-radius: 999px; padding: 0 8px; font-size: 12px; }}
.badge.bad {{ background: #fde2e1; color: #9d1c1c; }}
.badge.ok {{ background: #e3f6e8; color: #146c36; }}
.badge.wait {{ background: #fff4d6; color: #8a5a00; }}
.state {{ border-top: 1px solid #e6ebf0; margin-top: 12px; padding-top: 8px; }}
ol {{ margin: 6px 0 0; padding-left: 18px; }}
h2 {{ margin: 0 0 6px; font-size: 18px; }}
h3 {{ margin: 12px 0 4px; font-size: 15px; }}
</style>
</head>
<body>
<header class="top">
  <h1>solve_prog 交付 · P11-A01</h1>
  <p>板 0918全量 · 是 {counts.get('是', 0)} · 否 {counts.get('否', 0)} · 未过门禁 {counts.get('未过门禁', 0)} · 只含已点名的串联钳位通路</p>
</header>
<main>
  <nav>{''.join(nav)}</nav>
  <div>
    {''.join(f'<section class="group">{block}</section>' for block in gates_html)}
    {blocks}
  </div>
</main>
</body>
</html>
"""


def build(kg_path: Path = KG_DEFAULT) -> Path:
    kg = json.loads(kg_path.read_text(encoding="utf-8"))
    groups = find_series_groups(kg)
    payloads = []
    cards: dict[str, dict[str, Any]] = {}
    for group in groups:
        payload = run(kg, "P11-A01", group["refs"][0])
        payloads.append(payload)
        for ref in group["refs"]:
            cards[ref] = connection_card(kg, ref)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "P11-A01.html"
    path.write_text(render(payloads, cards), encoding="utf-8")
    (OUT_DIR / "delivery_groups.json").write_text(
        json.dumps(payloads, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def main() -> int:
    path = build()
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
