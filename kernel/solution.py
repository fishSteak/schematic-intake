# -*- coding: utf-8 -*-
"""Object-card 解题过程：已知 → 按态/门代入 → 本态是/否 → 整卡。

任一态或任一门不达标 → 整卡 否。缺证据 → 待确认。禁止填典型值升绿。
"""

from __future__ import annotations

import re
from typing import Any

GROUND_NETS = {"GND", "AGND", "DGND", "PGND", "VSS", "AVSS"}


def num(x: Any) -> str:
    if x is None:
        return "缺"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "缺"
    if abs(v) == 0:
        return "0"
    if abs(v) >= 10 and abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    s = f"{v:.6g}"
    if abs(v - round(v)) < 1e-9 and "." not in s:
        return f"{v:.1f}"
    return s


def fmt_ohm(x: float | None) -> str:
    if x is None:
        return "缺"
    if x >= 1e6:
        return f"{x / 1e6:g} MΩ"
    if x >= 1e3:
        return f"{x / 1e3:g} kΩ"
    return f"{x:g} Ω"


def fmt_power(w: float | None) -> str:
    if w is None:
        return "缺"
    if abs(w) == 0:
        return "0 W"
    if abs(w) < 1:
        return f"{w * 1e3:.6g} mW"
    return f"{w:.6g} W"


def fmt_volt(v: float | None) -> str:
    if v is None:
        return "缺"
    return f"{num(v)} V"


def short_state_name(name: str) -> str:
    s = str(name or "")
    if "点亮" in s:
        return "点亮"
    if "熄灭" in s:
        return "熄灭"
    if "拉低" in s:
        return "对端拉低"
    if "未拉低" in s:
        return "常通"
    if "驱动高" in s or s.endswith("高"):
        return "驱动高"
    if "驱动低" in s or s.endswith("低"):
        return "驱动低"
    if "常通" in s:
        return "常通"
    if "导通" in s:
        return "导通"
    if "截止" in s:
        return "截止"
    return s.split("；")[0][:20] or "态"


def _origin_cite(trace: dict[str, Any]) -> str:
    return str(trace.get("origin_cite") or trace.get("cite") or "")


def trace_row(net: str, trace: dict[str, Any] | None) -> dict[str, str]:
    """一个节点电压：左边是怎么算到这个数的，右边是输入从哪来。"""
    if not isinstance(trace, dict) or trace.get("v") is None:
        return {"expr": f"V({net}) = 缺", "src": "这一端电压没有闭合"}
    expr = str(trace.get("expr") or f"V({net})")
    subs = str(trace.get("subs") or num(trace.get("v")))
    result = fmt_volt(trace.get("v"))
    src = _origin_cite(trace) or str(trace.get("cite") or "") or str(trace.get("origin_kind") or "板内求解")
    if expr == f"V({net})" and not trace.get("drops"):
        shown = f"V({net}) = {result}"
    else:
        shown = f"V({net}) = {expr} = {subs} = {result}"
    return {"expr": shown, "src": src}


def power_lines(
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float | None,
    vv: float,
    pp: float,
    ta: dict[str, Any] | None,
    tb: dict[str, Any] | None,
) -> list[dict[str, str]]:
    rows = [trace_row(n_a, ta), trace_row(n_b, tb)]
    va = ta.get("v") if isinstance(ta, dict) else None
    vb = tb.get("v") if isinstance(tb, dict) else None
    if va is None or vb is None:
        rows.append({"expr": f"V({ref}) = |V({n_a}) − V({n_b})| = 缺", "src": "两端电压没有同时闭合"})
        return rows
    rows.append(
        {
            "expr": f"V({ref}) = |{num(va)} − {num(vb)}| = {fmt_volt(vv)}",
            "src": f"V({n_a}) 与 V({n_b}) 相减，取绝对值",
        }
    )
    if ohm:
        rows.append(
            {
                "expr": f"P = ({num(vv)})² / {num(ohm)} = {num(pp)} W",
                "src": "跨压的平方除以本只电阻",
            }
        )
    return rows


def voltage_lines(
    ref: str,
    n_a: str,
    n_b: str,
    vv: float,
    ta: dict[str, Any] | None,
    tb: dict[str, Any] | None,
) -> list[dict[str, str]]:
    rows = [trace_row(n_a, ta), trace_row(n_b, tb)]
    va = ta.get("v") if isinstance(ta, dict) else None
    vb = tb.get("v") if isinstance(tb, dict) else None
    if va is None or vb is None:
        rows.append({"expr": f"|V({n_a}) − V({n_b})| = 缺", "src": "两端电压没有同时闭合"})
        return rows
    rows.append(
        {
            "expr": f"|V| = |{num(va)} − {num(vb)}| = {fmt_volt(vv)}",
            "src": f"V({n_a}) 与 V({n_b}) 相减，取绝对值",
        }
    )
    return rows


def node_eq(net: str, trace: dict[str, Any] | None) -> str:
    if not isinstance(trace, dict) or trace.get("v") is None:
        return f"V({net}) = 缺"
    expr = str(trace.get("expr") or f"V({net})")
    subs = str(trace.get("subs") or num(trace.get("v")))
    result = fmt_volt(trace.get("v"))
    cite = _origin_cite(trace) if not trace.get("drops") else str(trace.get("cite") or "")
    extra = f"    {cite}" if cite and not trace.get("drops") else ""
    if expr == f"V({net})" and not trace.get("drops"):
        return f"V({net}) = {result}{extra}".rstrip()
    return f"V({net}) = {expr} = {subs} = {result}"


def _is_gnd_trace(t: dict[str, Any]) -> bool:
    origin = str(t.get("origin") or "")
    kind = str(t.get("origin_kind") or "")
    if origin in GROUND_NETS or kind == "ground":
        return True
    if kind in {"oc", "cmos_low"} and abs(float(t.get("origin_v") or 0.0)) < 1e-12:
        return True
    return False


def _is_rail_trace(t: dict[str, Any]) -> bool:
    kind = str(t.get("origin_kind") or "")
    if kind in {"closed_rail", "cmos_high"}:
        return True
    try:
        ov = float(t.get("origin_v") or 0.0)
    except (TypeError, ValueError):
        return False
    return ov > 1e-3 and kind not in {"divider_solve", "ground"}


def compact_across(
    ref: str,
    n_a: str,
    ta: dict[str, Any] | None,
    n_b: str,
    tb: dict[str, Any] | None,
) -> str | None:
    """V(R) = V(轨) − Σ压降 when one end rails and the other grounds."""
    if not isinstance(ta, dict) or not isinstance(tb, dict):
        return None
    if ta.get("origin_kind") == "divider_solve" or tb.get("origin_kind") == "divider_solve":
        return None
    rail, gnd = None, None
    if _is_rail_trace(ta) and _is_gnd_trace(tb):
        rail, gnd = ta, tb
    elif _is_rail_trace(tb) and _is_gnd_trace(ta):
        rail, gnd = tb, ta
    else:
        return None
    if not (rail.get("drops") or gnd.get("drops")):
        return None
    expr = f"V({rail.get('origin')})"
    subs = num(rail.get("origin_v"))
    for d in rail.get("drops") or []:
        if not isinstance(d, dict):
            continue
        if int(d.get("sign") or 0) < 0:
            expr += f" − {d.get('sym')}"
            subs += f" − {num(d.get('val'))}"
        else:
            expr += f" + {d.get('sym')}"
            subs += f" + {num(d.get('val'))}"
    for d in gnd.get("drops") or []:
        if not isinstance(d, dict):
            continue
        # Va − Vb：翻转地端 drops 符号
        if int(d.get("sign") or 0) > 0:
            expr += f" − {d.get('sym')}"
            subs += f" − {num(d.get('val'))}"
        else:
            expr += f" + {d.get('sym')}"
            subs += f" + {num(d.get('val'))}"
    v = abs(float(ta.get("v") or 0.0) - float(tb.get("v") or 0.0))
    if v < 1e-6:
        v = 0.0
    return f"V({ref}) = {expr} = {subs} = {fmt_volt(v)}"


def across_steps(
    ref: str,
    n_a: str,
    ta: dict[str, Any] | None,
    n_b: str,
    tb: dict[str, Any] | None,
) -> list[str]:
    lines: list[str] = []
    if ta:
        lines.append(node_eq(n_a, ta))
    else:
        lines.append(f"V({n_a}) = 缺")
    if tb:
        lines.append(node_eq(n_b, tb))
    else:
        lines.append(f"V({n_b}) = 缺")
    va = ta.get("v") if isinstance(ta, dict) else None
    vb = tb.get("v") if isinstance(tb, dict) else None
    if va is None or vb is None:
        lines.append(f"V({ref}) = |V({n_a}) − V({n_b})| = 缺")
        return lines
    if abs(float(va)) < 1e-6:
        va = 0.0
    if abs(float(vb)) < 1e-6:
        vb = 0.0
    v = abs(float(va) - float(vb))
    if v < 1e-6:
        v = 0.0
    lines.append(
        f"V({ref}) = |V({n_a}) − V({n_b})| = |{num(va)} − {num(vb)}| = {fmt_volt(v)}"
    )
    compact = compact_across(ref, n_a, ta, n_b, tb)
    if compact:
        # drop leading "V(ref) = " duplication: append as continuation
        rest = compact.split(" = ", 1)[-1] if compact.startswith(f"V({ref})") else compact
        lines.append(f"       = {rest}" if compact.startswith(f"V({ref})") else compact)
    return lines


def collect_given(
    traces: list[dict[str, Any] | None],
    extra: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(sym: str, val: str, cite: str, calc: dict[str, Any] | None = None) -> None:
        key = (sym, val)
        if not sym or key in seen:
            return
        seen.add(key)
        row: dict[str, Any] = {"sym": sym, "val": val, "cite": cite or ""}
        if isinstance(calc, dict) and (calc.get("steps") or calc.get("eq") or calc.get("knowns")):
            row["calc"] = calc
        rows.append(row)

    for t in traces:
        if not isinstance(t, dict):
            continue
        origin = str(t.get("origin") or "")
        kind = str(t.get("origin_kind") or "")
        calc = t.get("calc") if isinstance(t.get("calc"), dict) else None
        if kind == "divider_solve":
            net = str(t.get("net") or origin)
            if isinstance(calc, dict):
                for k in calc.get("knowns") or []:
                    if not isinstance(k, dict):
                        continue
                    add(str(k.get("sym") or ""), str(k.get("val") or ""), str(k.get("cite") or ""))
            add(
                f"V({net})",
                fmt_volt(t.get("v")),
                _origin_cite(t) or "DC 电阻网节点方程",
                calc=calc,
            )
            continue
        if kind == "follower" and isinstance(calc, dict):
            for k in calc.get("knowns") or []:
                if not isinstance(k, dict):
                    continue
                nested = k.get("calc") if isinstance(k.get("calc"), dict) else None
                add(str(k.get("sym") or ""), str(k.get("val") or ""), str(k.get("cite") or ""), calc=nested)
            net = str(t.get("net") or origin)
            add(
                f"V({net})",
                fmt_volt(t.get("v")),
                _origin_cite(t),
                calc=calc,
            )
            continue
        if origin:
            cite = _origin_cite(t)
            if t.get("origin_kind") == "ground" or origin in GROUND_NETS:
                cite = cite or "地"
            elif t.get("origin_kind") == "closed_rail":
                cite = cite or "电源网名闭合"
            add(f"V({origin})", fmt_volt(t.get("origin_v")), cite)
        for d in t.get("drops") or []:
            if not isinstance(d, dict):
                continue
            add(str(d.get("sym") or ""), fmt_volt(d.get("val")), str(d.get("cite") or ""))
    for g in extra or []:
        add(str(g.get("sym") or ""), str(g.get("val") or ""), str(g.get("cite") or ""))
    return rows


def gate(name: str, ok: bool | None, detail: str, *, wait: bool = False) -> dict[str, Any]:
    if wait or ok is None:
        zh = "待确认"
    elif ok:
        zh = "是"
    else:
        zh = "否"
    return {"name": name, "ok": ok, "detail": detail, "verdict_zh": zh}


def aggregate_any_fail(verdicts: list[str]) -> str:
    """否 wins; else 待确认; else 是. '—' skipped."""
    zs = [str(v) for v in verdicts if v and v not in {"—", "不参评", "不适用"}]
    if any(z == "否" for z in zs):
        return "否"
    if any(z in {"待确认", "INSUFFICIENT"} for z in zs):
        return "待确认"
    if zs and all(z == "是" for z in zs):
        return "是"
    if not zs:
        return "待确认"
    return "待确认"


def _clip_pending_bit(text: str, *, max_len: int = 56) -> str:
    bit = re.split(r"[。；;\n]", str(text or "").strip(), maxsplit=1)[0].strip()
    bit = re.sub(r"\s+", " ", bit)
    if len(bit) > max_len:
        bit = bit[: max_len - 1].rstrip("，,、 ") + "…"
    return bit


def _looks_like_formula_blob(text: str) -> bool:
    s = str(text or "")
    return (
        "【已知】" in s
        or "【态" in s
        or "【门】" in s
        or "【整卡】" in s
        or s.count("\n") >= 3
        or len(s) > 160
    )


def short_pending_aggregate_line(
    *,
    gates: list[dict[str, Any]] | None = None,
    states: list[dict[str, Any]] | None = None,
    stuck: str | None = None,
) -> str:
    """待确认短对照：优先 stuck / 待确认门 detail，避免笼统「证据未齐」。"""
    stuck_s = str(stuck or "").strip()
    if stuck_s and stuck_s not in {"无", "—", "-", "待确认", "INSUFFICIENT"}:
        return f"本卡：{_clip_pending_bit(stuck_s)} → 待确认"
    for g in gates or []:
        if not isinstance(g, dict):
            continue
        zh = str(g.get("verdict_zh") or "")
        waitish = zh in {"待确认", "INSUFFICIENT"} or g.get("ok") is None
        if not waitish:
            continue
        detail = str(g.get("detail") or "").strip()
        name = str(g.get("name") or "").strip()
        if detail and not _looks_like_formula_blob(detail):
            return f"本卡：{_clip_pending_bit(detail)} → 待确认"
        if name and name not in {"判定", "门"}:
            return f"本卡：{name}未齐 → 待确认"
    for st in states or []:
        if not isinstance(st, dict):
            continue
        if str(st.get("verdict_zh") or "") not in {"待确认", "INSUFFICIENT"}:
            continue
        reason = str(st.get("reason") or st.get("stuck") or "").strip()
        name = str(st.get("name") or st.get("name_zh") or "").strip()
        if reason and not _looks_like_formula_blob(reason):
            return f"本卡：{_clip_pending_bit(reason)} → 待确认"
        if name:
            return f"本卡：{name}未闭合 → 待确认"
    return "本卡：证据未齐 → 待确认"


def pack_solution(
    *,
    given: list[dict[str, str]] | None = None,
    states: list[dict[str, Any]] | None = None,
    gates: list[dict[str, Any]] | None = None,
    rule: str,
    verdict_zh: str | None = None,
    stuck: str | None = None,
) -> dict[str, Any]:
    states = list(states or [])
    gates = list(gates or [])
    zs = [str(s.get("verdict_zh") or "") for s in states] + [str(g.get("verdict_zh") or "") for g in gates]
    zh = verdict_zh or aggregate_any_fail(zs)
    if zh == "否":
        agg_line = "本卡：存在不达标 → 否"
    elif zh == "是":
        agg_line = "本卡：各条均为 是 → 是"
    elif zh == "不适用":
        agg_line = "本卡：不适用"
    else:
        agg_line = short_pending_aggregate_line(gates=gates, states=states, stuck=stuck)
    return {
        "given": list(given or []),
        "states": states,
        "gates": gates,
        "aggregate": "any_fail",
        "rule": rule,
        "aggregate_line": agg_line,
        "verdict_zh": zh,
    }


def short_conclusion(sol: dict[str, Any]) -> str:
    bits: list[str] = []
    for st in sol.get("states") or []:
        if not isinstance(st, dict):
            continue
        zh = st.get("verdict_zh") or ""
        if zh in {"—", "不参评"}:
            continue
        bits.append(f"{short_state_name(str(st.get('name') or '态'))}：{zh}")
    for g in sol.get("gates") or []:
        if not isinstance(g, dict):
            continue
        bits.append(f"{g.get('name') or '门'}：{g.get('verdict_zh') or ''}")
    bits.append(f"整卡：{sol.get('verdict_zh') or ''}")
    return "；".join(bits)


def _step_text(step: Any) -> str:
    """steps 可为字符串或 {eq: ...}；禁止 str(dict) 漏到报告。"""
    if isinstance(step, dict):
        return str(step.get("eq") or step.get("expr") or "").strip()
    return str(step or "").strip()


def render_solution(sol: dict[str, Any]) -> str:
    lines: list[str] = ["【已知】"]
    given = sol.get("given") or []
    if given:
        width = max((len(str(g.get("sym") or "")) for g in given), default=8)
        for g in given:
            if not isinstance(g, dict):
                continue
            sym = str(g.get("sym") or "")
            val = str(g.get("val") or "")
            cite = str(g.get("cite") or "")
            pad = " " * max(1, width - len(sym) + 2)
            lines.append(f"{sym} = {val}{pad}{cite}".rstrip())
    else:
        lines.append("（无）")
    for i, st in enumerate(sol.get("states") or [], 1):
        if not isinstance(st, dict):
            continue
        name = st.get("name") or f"态{i}"
        lines.append("")
        lines.append(f"【态 {i} · {name}】")
        if st.get("reason"):
            lines.append(str(st["reason"]))
        traced = st.get("lines") or []
        if traced:
            for row in traced:
                if isinstance(row, dict):
                    lines.append(f"{row.get('expr') or ''}    {row.get('src') or ''}".rstrip())
            for step in st.get("steps") or []:
                text = _step_text(step)
                if not text:
                    continue
                if text.startswith("正向") or text.startswith("压降") or "本态" in text or "不参评" in text or "不单独" in text or "→" in text:
                    lines.append(text)
        else:
            for step in st.get("steps") or []:
                text = _step_text(step)
                if text:
                    lines.append(text)
    gates = sol.get("gates") or []
    if gates:
        lines.append("")
        lines.append("【门】")
        for g in gates:
            if not isinstance(g, dict):
                continue
            zh = g.get("verdict_zh") or ""
            detail = g.get("detail") or ""
            lines.append(f"{g.get('name')}：{zh}" + (f" · {detail}" if detail else ""))
    lines.append("")
    lines.append("【整卡】")
    lines.append(f"规则：{sol.get('rule') or '任一不达标 → 整卡 否'}")
    lines.append(str(sol.get("aggregate_line") or f"本卡：{sol.get('verdict_zh')}"))
    return "\n".join(lines)


def states_power(
    ref: str,
    n_a: str,
    n_b: str,
    ohm: float | None,
    cases: list[dict[str, Any]],
    limit: float | None,
    *,
    assumed: list[str] | None = None,
    extra_given: list[dict[str, str]] | None = None,
    rule: str = "任一态 P > 限额 → 整卡 否",
) -> dict[str, Any]:
    assumed = list(assumed or [])
    traces: list[dict[str, Any] | None] = []
    states: list[dict[str, Any]] = []
    for c in cases:
        if not isinstance(c, dict) or c.get("v_across") is None:
            continue
        i_dc_zero = c.get("i_dc") == 0 or c.get("i_dc") == 0.0
        ta = c.get("va_trace") if isinstance(c.get("va_trace"), dict) else None
        tb = c.get("vb_trace") if isinstance(c.get("vb_trace"), dict) else None
        if not i_dc_zero:
            traces.extend([ta, tb])
        vv = float(c["v_across"])
        if abs(vv) < 1e-6:
            vv = 0.0
        if c.get("closes_verdict") is False:
            cite = str(c.get("note") or "本态列出，不单独决定整卡")
            states.append(
                {
                    "name": c.get("name_zh") or "态",
                    "steps": [cite + " → 本态 不参评"],
                    "verdict_zh": "不参评",
                    "reason": c.get("reason") or cite,
                    "lines": list(c.get("lines") or [])
                    or [
                        {"expr": "I = 0", "src": cite},
                        {"expr": "P = 0", "src": "电流为 0，功耗为 0"},
                    ],
                }
            )
            continue
        pp = c.get("p_w")
        if pp is None and ohm:
            pp = (vv * vv) / float(ohm)
        pp = float(pp or 0.0)
        if abs(pp) < 1e-18:
            pp = 0.0
        if i_dc_zero:
            cite = str(c.get("dc_open_cite") or c.get("note") or "一端无直流通路")
            steps = [f"I({ref}) = 0          {cite}"]
            if ohm is None:
                steps.append("V = I × R    R = 缺")
                steps.append("P 不能写出")
                st_zh = "待确认"
            else:
                steps.append(f"V({ref}) = I × R = 0 × {fmt_ohm(ohm)} = 0 V")
                steps.append("P = 0 W")
                if limit is None:
                    steps.append("限额 = 缺 P_rated → 本态 待确认")
                    st_zh = "待确认"
                else:
                    steps.append(f"0 W ≤ {num(limit)} W → 本态 是")
                    st_zh = "是"
            states.append(
                {
                    "name": c.get("name_zh") or "无直流通路",
                    "steps": steps,
                    "verdict_zh": st_zh,
                    "reason": c.get("reason") or "",
                    "lines": list(c.get("lines") or []),
                }
            )
            extra_given = list(extra_given or []) + [
                {"sym": f"I({ref})", "val": "0", "cite": cite}
            ]
            continue
        steps = across_steps(ref, n_a, ta, n_b, tb)
        if ohm is None:
            steps.append("P = V² / R    R = 缺    不能写出 P")
            st_zh = "待确认"
        else:
            steps.append(f"P = V² / R = {num(vv)}² / {num(ohm)} = {num(pp)} W")
            if c.get("force_verdict"):
                steps.append(str(c.get("force_note") or c.get("force_verdict")))
                st_zh = str(c["force_verdict"])
            elif limit is None:
                steps.append("限额 = 缺 P_rated → 本态 待确认")
                st_zh = "待确认"
            elif pp > float(limit) + 1e-18:
                if c.get("bound_note"):
                    steps.append(f"{c.get('bound_note')} → 本态 否")
                elif assumed:
                    steps.append(
                        f"上界假设压降=0 仍 P={num(pp)} W > 限额 {num(limit)} W"
                        f"（{'、'.join(assumed[:6])}）→ 本态 否"
                    )
                else:
                    steps.append(f"{num(pp)} W > {num(limit)} W → 本态 否")
                st_zh = "否"
            else:
                steps.append(f"{num(pp)} W ≤ {num(limit)} W → 本态 是")
                st_zh = "是"
        states.append(
            {
                "name": c.get("name_zh") or "态",
                "steps": steps,
                "verdict_zh": st_zh,
                "reason": c.get("reason") or "",
                "lines": list(c.get("lines") or [])
                or power_lines(ref, n_a, n_b, ohm, vv, pp, ta, tb),
            }
        )
    given = collect_given(traces, extra_given)
    return pack_solution(given=given, states=states, rule=rule)


def states_voltage(
    ref: str,
    n_a: str,
    n_b: str,
    cases: list[dict[str, Any]],
    limit: float | None,
    *,
    extra_given: list[dict[str, str]] | None = None,
    rule: str = "任一态 |Va−Vb| > 限额 → 整卡 否",
) -> dict[str, Any]:
    traces: list[dict[str, Any] | None] = []
    states: list[dict[str, Any]] = []
    for c in cases:
        if not isinstance(c, dict) or c.get("v_across") is None:
            continue
        ta = c.get("va_trace") if isinstance(c.get("va_trace"), dict) else None
        tb = c.get("vb_trace") if isinstance(c.get("vb_trace"), dict) else None
        traces.extend([ta, tb])
        vv = float(c["v_across"])
        if abs(vv) < 1e-6:
            vv = 0.0
        steps = across_steps(ref, n_a, ta, n_b, tb)
        if limit is None:
            steps.append("限额 = 缺 Vr → 本态 待确认")
            st_zh = "待确认"
        elif vv > float(limit) + 1e-9:
            steps.append(f"{fmt_volt(vv)} > {fmt_volt(limit)} → 本态 否")
            st_zh = "否"
        else:
            steps.append(f"{fmt_volt(vv)} ≤ {fmt_volt(limit)} → 本态 是")
            st_zh = "是"
        states.append(
            {
                "name": c.get("name_zh") or "态",
                "steps": steps,
                "verdict_zh": st_zh,
                "lines": list(c.get("lines") or []) or voltage_lines(ref, n_a, n_b, vv, ta, tb),
            }
        )
    given = collect_given(traces, extra_given)
    return pack_solution(given=given, states=states, rule=rule)


def apply_solution(
    card: dict[str, Any],
    sol: dict[str, Any],
    *,
    phenomenon: str,
    stuck: str,
    fill_four,
) -> dict[str, Any]:
    sol = dict(sol or {})
    zh = str(sol.get("verdict_zh") or card.get("verdict_zh") or "")
    if zh in {"待确认", "INSUFFICIENT"} or str(zh).startswith("INSUFFICIENT"):
        sol["aggregate_line"] = short_pending_aggregate_line(
            gates=list(sol.get("gates") or []),
            states=list(sol.get("states") or []),
            stuck=stuck,
        )
        if zh.startswith("INSUFFICIENT"):
            sol["verdict_zh"] = "待确认"
    card["solution"] = sol
    card["verdict_zh"] = sol.get("verdict_zh") or card.get("verdict_zh")
    body = render_solution(sol)
    card["formula"] = body
    fill_four(
        card,
        phenomenon=phenomenon,
        calc=body,
        stuck=stuck,
        conclusion=short_conclusion(sol),
    )
    return card
