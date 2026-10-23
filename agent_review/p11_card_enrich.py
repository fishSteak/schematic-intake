# -*- coding: utf-8 -*-
"""把密封工况/应力/KG 富化进 P11 对象卡，对齐 expert SPA 所需字段。

对人可见文案契约：态结论用对照句；禁止本态残句、case_id=、dry-run、裸英文工况 id。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent_review.batching import safe_object_name
from kernel.util import read_json

_CASE_ID_EN = frozenset(
    {"supply_off", "clamp_work", "switch_pull", "port_gnd", "dc_work", "other", "deferred"}
)
# 对人展示禁止单独使用的分类词（须写成通路/端态怎么连通）
_TAXONOMY_LABELS = frozenset(
    {"有源拉低", "直流工作", "正常钳位", "外接接地", "其它工况", "未评", "钳位", "拉低", "接地"}
)
_GENERIC_REASON = frozenset({"电阻与电压源", "直流稳态", "电阻与电压源。", ""})
_FORBIDDEN_COPY = (
    "本态",
    "· P=",
    "case_id=",
    "dry-run",
    "dry_run",
)


def _mw(p: Any) -> str:
    if p is None:
        return "?"
    try:
        return f"{float(p) * 1000:.2f} mW"
    except (TypeError, ValueError):
        return "?"


def _fill_headline(out: dict[str, Any], rid: str, obj: str) -> None:
    """对象弹层只要一句：场景 → 数字来源 → 数字 → 相对限额。已有 headline 不动。"""
    hl = str(out.get("headline_zh") or "").strip()
    if hl:
        return
    elec = out.get("electrical") if isinstance(out.get("electrical"), dict) else {}
    after = out.get("after") if isinstance(out.get("after"), dict) else {}
    sol = out.get("solution") if isinstance(out.get("solution"), dict) else {}
    blobs = [
        st
        for st in list(sol.get("states") or []) + list(out.get("states") or [])
        if isinstance(st, dict)
    ]
    rows: list[dict[str, Any]] = []
    for st in blobs:
        for row in st.get("formula_lines") or st.get("lines") or []:
            if isinstance(row, dict):
                rows.append(row)
    scene = ""
    for row in rows:
        m = re.search(r"(.+?) 与 (.+?) 之差", str(row.get("expr") or ""))
        if m:
            scene = f"跨在 {m.group(1).strip()} 与 {m.group(2).strip()}，"
            break
    if not scene:
        a = str(after.get("net_a") or "").strip()
        b = str(after.get("net_b") or "").strip()
        if a and b and b not in {"GND", "DGND", "AGND", "PGND"}:
            scene = f"跨在 {a} 与 {b}，"
    source = ""
    for row in rows:
        src = str(row.get("src") or "")
        expr = str(row.get("expr") or "")
        if "手册" not in src:
            continue
        m = re.search(r"(\d+(?:\.\d+)?)\s*V", expr)
        if m:
            source = f"端压取手册上限 {m.group(1)}V 的分压，"
            break
    text = ""
    if rid == "P11-A01" and elec.get("P_max") is not None and elec.get("P_limit") is not None:
        try:
            op = "低于" if float(elec["P_max"]) <= float(elec["P_limit"]) else "高于"
        except (TypeError, ValueError):
            op = "对照"
        text = f"{obj}：{scene}{source}耗散 {_mw(elec.get('P_max'))}，{op}限额 {_mw(elec.get('P_limit'))}。"
    elif rid == "P11-A03" and elec.get("V_max") is not None and elec.get("V_limit") is not None:
        try:
            op = "低于" if float(elec["V_max"]) <= float(elec["V_limit"]) else "高于"
        except (TypeError, ValueError):
            op = "对照"
        text = f"{obj}：{scene}{source}端压 {elec.get('V_max')} V，{op}限额 {elec.get('V_limit')} V。"
    if text and len(text) > 90:
        text = text.replace(scene, "", 1)
    if not text or len(text) > 90:
        return
    out["headline_zh"] = text


def _state_verdict(p: Any, limit: Any, card_vz: str, *, status: str = "") -> str:
    if status in {"unmapped", "conditional"}:
        return "待确认"
    if p is None:
        return "待确认"
    if limit is None:
        return "待确认"
    try:
        return "是" if float(p) <= float(limit) else "否"
    except (TypeError, ValueError):
        return "待确认"


def _compare_eq(rid: str, p: Any, limit: Any, st_vz: str, *, status: str = "", stuck: str = "") -> str:
    if status == "unmapped":
        miss = stuck or "缺模型"
        return f"本工况未建模，{miss} → 待确认"
    if status == "conditional" and p is not None and limit is not None:
        label = "模型超限" if float(p) > float(limit) else "模型内满足限额"
        metric = f"{_mw(p)}，限额 {_mw(limit)}" if rid == "P11-A01" else f"{p} V，限额 {limit} V"
        return f"条件计算：{metric} → {label}；成立条件待确认"
    if rid == "P11-A01":
        if p is None or limit is None:
            return "P 或 P_limit 缺失 → 待确认"
        try:
            op = "≤" if float(p) <= float(limit) else ">"
        except (TypeError, ValueError):
            return "P 或 P_limit 无效 → 待确认"
        return f"P={_mw(p)} {op} P_limit={_mw(limit)} → {st_vz}"
    if p is None or limit is None:
        return "V 或 V_limit 缺失 → 待确认"
    try:
        op = "≤" if float(p) <= float(limit) else ">"
    except (TypeError, ValueError):
        return "V 或 V_limit 无效 → 待确认"
    return f"V={p} V {op} V_limit={limit} V → {st_vz}"


def _pending_aggregate_line(rid: str, elec: dict[str, Any]) -> str:
    """待确认短对照：区分「工况/应力未闭合」与「缺额定」。"""
    elec = elec if isinstance(elec, dict) else {}
    if rid == "P11-A03":
        v_max = elec.get("V_max")
        has_vmax = v_max is not None
        has_rated = elec.get("V_rated") is not None or elec.get("V_limit") is not None
        if has_vmax and not has_rated:
            try:
                return f"本卡：V_max≈{float(v_max):g} V 已闭合，缺额定耐压 → 待确认"
            except (TypeError, ValueError):
                return "本卡：端压已闭合，缺额定耐压 → 待确认"
        if not has_vmax and has_rated:
            return "本卡：工况/端压未闭合 → 待确认"
        if not has_vmax and not has_rated:
            return "本卡：端压与额定均未闭合 → 待确认"
        return "本卡：比较条件未齐 → 待确认"
    if rid == "P11-A01":
        p_max = elec.get("P_max")
        has_p = p_max is not None
        has_rated = elec.get("P_rated") is not None or elec.get("P_limit") is not None
        if has_p and not has_rated:
            return f"本卡：P_max={_mw(p_max)} 已闭合，缺额定功耗 → 待确认"
        if not has_p and has_rated:
            return "本卡：工况/功耗未闭合 → 待确认"
        if not has_p and not has_rated:
            return "本卡：功耗与额定均未闭合 → 待确认"
        return "本卡：比较条件未齐 → 待确认"
    return "本卡：证据未齐 → 待确认"


def _zh_case_name(name: str, cid: str) -> str:
    n = str(name or "").strip()
    if n and n not in _CASE_ID_EN and not re.fullmatch(r"[a-z_]+", n):
        if "未上电" in n:
            return ""
        return n
    return {
        "clamp_work": "正常钳位",
        "switch_pull": "有源拉低",
        "port_gnd": "外接接地",
        "dc_work": "直流工作",
        "other": "其它工况",
    }.get(str(cid), n or str(cid))


def _is_thin_state_label(name: str) -> bool:
    """分类词/内部 id：不能单独作为 SPA 工况标题。"""
    n = str(name or "").strip()
    if not n:
        return True
    if n in _TAXONOMY_LABELS or n in _CASE_ID_EN:
        return True
    if re.fullmatch(r"[a-z_]+", n):
        return True
    # 「有源拉低（未建模）」仍算薄
    core = re.sub(r"（未建模）|\(未建模\)", "", n).strip()
    if core in _TAXONOMY_LABELS:
        return True
    return False


def _path_hint(path_text: str, *, max_len: int = 56) -> str:
    pt = str(path_text or "").strip()
    if not pt:
        return ""
    if "旁路" in pt:
        bit = pt.split("旁路", 1)[-1].split("；")[0].strip(" ；|")
        if bit:
            return bit if len(bit) <= max_len else bit[: max_len - 1] + "…"
    bit = pt.split("；")[0].strip()
    if len(bit) > max_len:
        bit = bit[: max_len - 1] + "…"
    return bit


def _titles_from_reasoning(steps: Any) -> list[str]:
    out: list[str] = []
    for s in steps or []:
        for m in re.findall(r"工况[「『]([^」』]+)[」』]", str(s)):
            t = m.strip()
            if t and not _is_thin_state_label(t):
                out.append(t)
    return out


def _connectivity_title(
    *,
    prior_name: str = "",
    maj: dict[str, Any] | None = None,
    st: dict[str, Any] | None = None,
    path_text: str = "",
    cid: str = "",
    reasoning_title: str = "",
) -> str:
    """SPA 工况标题：优先写清通路/端态如何连通，不用纯分类词。"""
    maj = maj or {}
    st = st or {}
    candidates = [
        prior_name,
        reasoning_title,
        st.get("source_variant"),
        maj.get("what_changes"),
        maj.get("qualitative"),
        maj.get("name_zh"),
        st.get("name_zh"),
    ]
    for c in candidates:
        s = str(c or "").strip()
        if not s or "未上电" in s:
            continue
        if s in _CASE_ID_EN or s in _GENERIC_REASON:
            continue
        if _is_thin_state_label(s):
            continue
        return s
    hint = _path_hint(path_text)
    tax = _zh_case_name("", cid) or ""
    if hint and tax and _is_thin_state_label(tax):
        return f"{hint}（{tax}）"
    if hint:
        return hint
    return tax or "工况"


def _agent_states_usable(states: Any) -> bool:
    if not isinstance(states, list) or not states:
        return False
    scored = 0
    n = 0
    for s in states:
        if not isinstance(s, dict):
            continue
        n += 1
        conc = str(s.get("结论") or s.get("conclusion") or "")
        name = str(s.get("name_zh") or s.get("name") or "")
        if not name or _is_thin_state_label(name):
            continue
        # 对照句：箭头 / 是|否|待确认 / 超标|达标 / 与限额比较
        has_formula = any(
            isinstance(x, dict) and str(x.get("expr") or "").strip()
            for x in (s.get("formula_lines") or s.get("lines") or [])
        )
        usable_conc = (
            "→" in conc
            or "->" in conc
            or any(
                x in conc
                for x in (
                    "是",
                    "否",
                    "待确认",
                    "超标",
                    "达标",
                    "满足",
                    "不满足",
                    "低于",
                    "高于",
                    "不超过",
                    "未超过",
                )
            )
            or (
                ("P_limit" in conc or "V_limit" in conc or "限额" in conc)
                and any(x in conc for x in ("≤", "＞", ">", "<", "≧", "≦", "低于", "高于"))
            )
            or has_formula
        )
        if usable_conc:
            scored += 1
    return n > 0 and scored >= max(1, (n + 1) // 2)


def _match_variant(
    stress: dict[str, Any],
    *,
    cid: str,
    name: str,
    st: dict[str, Any],
) -> dict[str, Any] | None:
    """对齐应力包 variants，取带出处的 formula_lines。"""
    variants = [v for v in (stress.get("variants") or []) if isinstance(v, dict)]
    if not variants:
        return None
    for v in variants:
        if str(v.get("case_id") or "") == cid:
            return v
    cls = cid.split("::")[-1] if "::" in cid else ""
    src_var = str(st.get("source_variant") or "").strip()
    scored: list[tuple[int, dict[str, Any]]] = []
    for v in variants:
        score = 0
        vn = str(v.get("name_zh") or "")
        if cls and str(v.get("class_id") or "") == cls:
            score += 2
        if name and vn == name:
            score += 3
        if src_var and vn == src_var:
            score += 3
        if name and src_var and (name in vn or vn in name):
            score += 1
        # 功耗接近时加分（同一态）
        try:
            if st.get("P") is not None and v.get("P") is not None:
                if abs(float(st["P"]) - float(v["P"])) < 1e-9:
                    score += 4
                elif abs(float(st["P"]) - float(v["P"])) / max(abs(float(st["P"])), 1e-12) < 0.02:
                    score += 2
        except (TypeError, ValueError):
            pass
        if score:
            scored.append((score, v))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def _prior_power(prior: dict[str, Any] | None) -> Any:
    """Agent 有时写 P_w / p_w，统一成 P。"""
    if not isinstance(prior, dict):
        return None
    for k in ("P", "P_w", "p_w", "p"):
        if prior.get(k) is not None:
            return prior.get(k)
    return None


def _split_calc_by_case(calc: str) -> list[str]:
    """从 Agent calc 正文拆出「工况 A/B/一/二…」段落。"""
    text = str(calc or "").strip()
    if not text:
        return []
    parts = re.split(r"(?=工况\s*[A-Z一二三四五六七八九十\d]+)", text)
    out = [p.strip() for p in parts if p.strip() and re.match(r"工况\s*[A-Z一二三四五六七八九十\d]+", p.strip())]
    return out


def _lines_from_text_blob(blob: str, *, src: str) -> list[dict[str, str]]:
    """把一段代入叙述拆成若干 expr 行。"""
    t = scrub_forbidden_copy(str(blob or "").strip())
    if not t:
        return []
    # 去掉「工况 X：」/「工况三（…）：」前缀
    t = re.sub(
        r"^工况\s*[A-Z一二三四五六七八九十\d]+(?:（[^）]*）|\([^)]*\))?\s*[：:]\s*",
        "",
        t,
    )
    chunks = re.split(r"[；;\n]\s*", t)
    lines: list[dict[str, str]] = []
    for ch in chunks:
        ch = ch.strip().strip("。")
        if not ch or "未上电" in ch:
            continue
        if re.match(r"^(P_rated|P_limit|P_max)\b", ch):
            continue
        lines.append({"expr": ch, "src": src})
    return lines


def _lines_from_reasoning(steps: Any, index: int) -> list[dict[str, str]]:
    case_steps = [
        str(s).strip()
        for s in (steps or [])
        if str(s).strip().startswith("工况")
    ]
    if index < len(case_steps):
        return _lines_from_text_blob(case_steps[index], src="审图推理")
    return []


def _traceable_lines(
    *,
    stress: dict[str, Any],
    cid: str,
    name: str,
    st: dict[str, Any],
    prior: dict[str, Any] | None,
    formula: str,
    cite: str,
    calc_blob: str = "",
    state_index: int = 0,
    reasoning_steps: Any = None,
    prefer_agent: bool = False,
) -> tuple[list[dict[str, str]], str]:
    """审图 formula_lines 优先；否则拆 calc/推理；最后才用程序 variants。"""
    lines: list[dict[str, str]] = []
    note = ""

    def _ingest_prior_rows() -> None:
        nonlocal lines
        if not isinstance(prior, dict):
            return
        for row in prior.get("formula_lines") or prior.get("lines") or []:
            if not isinstance(row, dict):
                continue
            expr = scrub_forbidden_copy(str(row.get("expr") or "").strip())
            src = _business_src(str(row.get("src") or row.get("cite") or "").strip())
            if expr:
                lines.append({"expr": expr, "src": src or _business_src(cite) or "审图代入"})

    if prefer_agent:
        _ingest_prior_rows()
        # Agent 有时把链写在 states[].formula（分号串），拆成行
        if not lines and isinstance(prior, dict) and prior.get("formula"):
            for part in re.split(r"[；;]\s*", str(prior.get("formula") or "")):
                part = part.strip()
                if not part or "未上电" in part:
                    continue
                lines.append({"expr": scrub_forbidden_copy(part), "src": cite or "审图代入"})
        if not lines:
            cases = _split_calc_by_case(calc_blob)
            if state_index < len(cases):
                lines = _lines_from_text_blob(cases[state_index], src="审图 calc")
        if not lines:
            lines = _lines_from_reasoning(reasoning_steps, state_index)

    var = _match_variant(stress, cid=cid, name=name, st=st)
    if not lines and var:
        note = str(var.get("note") or "")
        for row in var.get("formula_lines") or []:
            if not isinstance(row, dict):
                continue
            expr = scrub_forbidden_copy(str(row.get("expr") or "").strip())
            src = _business_src(str(row.get("src") or "").strip())
            if not expr or "未上电" in expr:
                continue
            if not src or src in _CASE_ID_EN or _is_thin_state_label(src):
                src = _business_src(cite) or "规格书/应力代入"
            lines.append({"expr": expr, "src": src})
    if not lines:
        _ingest_prior_rows()
    if not lines and formula:
        for part in formula.split(";"):
            part = part.strip()
            if not part or "未上电" in part or "本态" in part:
                continue
            lines.append(
                {
                    "expr": scrub_forbidden_copy(part),
                    "src": _src_label(st.get("source_variant") or cite, name_zh=name),
                }
            )
    if not lines and not prefer_agent:
        cases = _split_calc_by_case(calc_blob)
        if state_index < len(cases):
            lines = _lines_from_text_blob(cases[state_index], src="审图 calc")
        if not lines:
            lines = _lines_from_reasoning(reasoning_steps, state_index)
    return lines, note


def _src_label(raw: Any, *, name_zh: str = "") -> str:
    s = str(raw or "").strip()
    if not s or s.startswith("case_id=") or s in _CASE_ID_EN:
        return name_zh or "应力包"
    if "未上电" in s and "Vf" in s:
        return "应力包"
    if s in {"board_paths"}:
        return "通路模型"
    return _business_src(s)


def _business_src(raw: Any) -> str:
    """把程序路径/内部字段改成业务可懂、可追溯的说法。"""
    s = scrub_forbidden_copy(str(raw or "").strip())
    if not s:
        return "原理图与手册"
    # 一律去掉「审图核对」话术与 neighbors 机器词
    s = re.sub(r"审图核对[：:]\s*", "", s)
    s = re.sub(r"（审图核对）", "", s)
    s = re.sub(r"工作台\s*neighbors", "原理图邻接器件", s, flags=re.I)
    s = re.sub(r"\bneighbors\b", "邻接器件", s, flags=re.I)
    s = re.sub(r"\s{2,}", " ", s).strip(" ；;|")
    if not s:
        return "原理图与手册"

    machine = bool(
        re.search(
            r"(context\.json|board_kg|context_store|review_out/|\.json\b|rail\.|pintable|vf_model|assumptions)",
            s,
            re.I,
        )
    )
    # 无机器痕迹且已像业务表述 → 原样（BOM 仍统一成「物料清单」）
    if not machine and not re.search(r"\bBOM\b", s):
        if any(
            k in s
            for k in ("原理图", "手册", "物料清单", "规格书", "审图假设", "审图前提", "由上", "推导", "网名")
        ):
            return s

    out = s

    # 手册齐纳 / 二极管（优先，避免被笼统替换冲掉）
    m = re.search(
        r"(D\d+)\s*(?:\(([^)]+)\)|([A-Za-z0-9_-]+))?.{0,40}?vz_(?:min|max)_v\s*=\s*(\d+(?:\.\d+)?)",
        out,
        re.I,
    )
    if m:
        mpn = (m.group(2) or m.group(3) or "").strip()
        kind = "下限" if "min" in out.lower() else "上限" if "max" in out.lower() else "齐纳"
        head = f"{m.group(1)}" + (f"（{mpn}）" if mpn else "")
        return f"{head} 手册齐纳{kind} {m.group(4)} V"

    m = re.search(r"(D\d+).{0,40}?vf(?:_model_v)?\s*=\s*(\d+(?:\.\d+)?)", out, re.I)
    if m:
        return f"{m.group(1)} 正向压降模型 {m.group(2)} V（手册未抽出时审图取值）"

    # 网络轨压：只认网名，勿用 *.context.json
    m = re.search(
        r"\bnets?\s+([+A-Za-z][A-Za-z0-9_+./-]*)\s+rail\s+closed\s+(\d+(?:\.\d+)?)",
        out,
        re.I,
    )
    if m:
        return f"原理图网络 {m.group(1)}（闭合轨压 {m.group(2)} V）"
    m = re.search(
        r"\b([+A-Za-z][A-Za-z0-9_+.-]{1,32})\b.{0,24}rail\s+closed\s+(\d+(?:\.\d+)?)",
        out,
        re.I,
    )
    if m and not re.search(r"context|\.json|board_kg|review_out", m.group(1), re.I):
        return f"原理图网络 {m.group(1)}（闭合轨压 {m.group(2)} V）"

    m = re.search(r"context_store/nets/([^/\s\"']+)", out)
    if m:
        net = re.sub(r"\.json$", "", m.group(1), flags=re.I)
        vm = re.search(r"(?:rail\.value\s*=\s*|闭合轨压\s*|=\s*)(\d+(?:\.\d+)?)", out, re.I)
        if not vm:
            vm = re.search(r"(\d+(?:\.\d+)?)\s*V", out)
        if vm:
            return f"原理图网络 {net}（闭合轨压 {vm.group(1)} V）"
        return f"原理图网络 {net}"

    # IC 开漏 / 邻居
    m = re.search(r"(U\d+).{0,20}(LM\d+\w*).{0,40}?引脚\s*([0-9/,与和]+).{0,10}开漏", out, re.I)
    if m:
        return f"原理图 {m.group(1)}（{m.group(2)}）开漏脚 {m.group(3)}"

    m = re.search(r"邻居\s+(R\d+|LED\d+|U\d+)", out)
    if m:
        return f"原理图邻接器件 {m.group(1)}"

    # BOM
    if re.search(r"\bBOM\b|物料清单", out, re.I):
        out2 = re.sub(r"\bBOM\b", "物料清单", out)
        out2 = re.sub(r"四只\s*0603\w+", "四只 120 Ω（同料号）", out2)
        out2 = re.sub(r"四段\s*120\s*Ω", "四段各 120 Ω", out2)
        out2 = re.sub(r"\s{2,}", " ", out2).strip()
        return out2

    # 笼统降级（与 report_text.humanize 对齐）
    try:
        from kernel.report_text import humanize_report_text

        out = humanize_report_text(out)
    except Exception:
        out = re.sub(r"[A-Za-z0-9_+.-]+\.context\.json\s*", "", out)
        out = re.sub(r"board_kg(?:\.json)?", "原理图解析", out, flags=re.I)
        out = re.sub(r"\bpintable\b", "料号参数表", out, flags=re.I)
        out = re.sub(r"review_out/context_store/nets/[^\s，,;；]+", "原理图网络", out)
        out = re.sub(r"review_out/[^\s，,;；]+", "", out)
    out = re.sub(r"\brail\.(?:status=)?closed\b", "闭合轨压", out, flags=re.I)
    out = re.sub(r"\brail\.value\s*=\s*", "轨压 ", out, flags=re.I)
    out = re.sub(r"\bvz_min_v\s*=\s*", "齐纳下限 ", out, flags=re.I)
    out = re.sub(r"\bvz_max_v\s*=\s*", "齐纳上限 ", out, flags=re.I)
    out = re.sub(r"\bvf_model_v\s*=\s*", "正向压降模型 ", out, flags=re.I)
    out = re.sub(r"\bpintable\b", "手册抽出", out, flags=re.I)
    out = re.sub(r"\bassumptions\b", "审图计算前提", out, flags=re.I)
    out = re.sub(r"工作台\s*neighbors", "原理图邻接器件", out, flags=re.I)
    out = re.sub(r"\bneighbors\b", "邻接器件", out, flags=re.I)
    # 去掉历史兜底话术
    out = re.sub(r"^审图核对[：:]\s*", "", out)
    out = re.sub(r"（审图核对）", "", out)
    out = re.sub(r"\s{2,}", " ", out).strip(" ；;|")
    if not out:
        return "原理图与手册"

    # 推导类短句：直接标「由上式推导」，不再加「审图核对」
    if re.search(r"(欧姆定律|串链均流|节点压降|I\s*²\s*R|I\^2\s*R|P\s*=\s*I|两端压差|远端节点)", out):
        return "由上式推导"
    if re.search(r"^[0-9.−\-×xX/+\sVAmWΩ()]+$", out) or re.search(
        r"^\d+(?:\.\d+)?\s*[−\-]\s*\d+", out
    ):
        return "由上式推导"

    if any(k in out for k in ("原理图", "手册", "物料", "规格书", "审图", "网络", "推导", "BOM")):
        return out
    if re.search(r"(D\d+|U\d+|R\d+|LED\d+|\+?\d+V|VCC)", out):
        return f"原理图 {out}" if not out.startswith("原理图") else out
    return out


def scrub_forbidden_copy(text: str) -> str:
    """去掉过程话术与内部 id（尽量保留语义）；系统路径改业务说法。"""
    t = str(text or "")
    for bad in _FORBIDDEN_COPY:
        t = t.replace(bad, "")
    for en in _CASE_ID_EN:
        t = re.sub(rf"\b{en}\b", "", t)
    try:
        from kernel.report_text import humanize_report_text

        t = humanize_report_text(t)
    except Exception:
        pass
    t = re.sub(r"\s{2,}", " ", t).strip(" ；;|")
    return t


def enrich_p11_card(o: Path, card: dict[str, Any], kg: dict[str, Any] | None = None) -> dict[str, Any]:
    """为 SPA 补齐 datasheet / identity / solution.states（数字只来自密封包）。"""
    out = dict(card)
    rid = str(out.get("rule_id") or "")
    obj = str(out.get("object") or "")
    if rid not in {"P11-A01", "P11-A03"} or not obj:
        return out

    sn = safe_object_name(obj)
    cases: dict[str, Any] = {}
    stress: dict[str, Any] = {}
    cp = o / "operating_cases" / rid / f"{sn}.json"
    sp = o / "program_stress" / rid / f"{sn}.json"
    if cp.is_file():
        cases = read_json(cp)
    if sp.is_file():
        stress = read_json(sp)

    comps = (kg or {}).get("components") or {}
    c = comps.get(obj) or {}
    params = ((c.get("pintable") or {}).get("params") or {}) if isinstance(c.get("pintable"), dict) else {}
    pt = c.get("pintable") if isinstance(c.get("pintable"), dict) else {}

    locs = c.get("location") or []
    loc0 = locs[0] if isinstance(locs, list) and locs and isinstance(locs[0], dict) else {}
    out.setdefault(
        "identity",
        {
            "mpn": c.get("mpn") or "",
            "value_bom": c.get("value") or "",
            "value_pdf": c.get("value") or "",
            "footprint": c.get("footprint") or "",
        },
    )
    out.setdefault(
        "location",
        {
            "pdf_page": loc0.get("pdf_page"),
            "page_name": loc0.get("pageName") or loc0.get("page_name") or "",
            "module_title": loc0.get("module_title") or "",
        },
    )
    path = cases.get("path") or {}
    nodes = list(path.get("nodes") or [])
    nets_a = path.get("supply_net") or (nodes[0] if nodes else "")
    nets_b = path.get("exit_net") or (nodes[1] if len(nodes) > 1 else "")
    if not nets_a or not nets_b:
        pins = c.get("pins") or []
        pin_nets = [str(p.get("net")) for p in pins if isinstance(p, dict) and p.get("net")]
        if len(pin_nets) >= 2:
            nets_a, nets_b = pin_nets[0], pin_nets[1]
    out.setdefault("nets", {"a": nets_a, "b": nets_b})

    ds_stress = stress.get("datasheet") if isinstance(stress.get("datasheet"), dict) else {}
    pr = stress.get("P_rated") if rid == "P11-A01" else stress.get("V_rated")
    if pr is None:
        if rid == "P11-A01":
            pr = params.get("p_rated_w") or params.get("ptot_w")
        else:
            pr = (
                params.get("v_rated_v")
                or params.get("rated_voltage_v")
                or params.get("voltage_rating_v")
            )
    cite = stress.get("cite") or params.get("extract_note") or ""
    candidates = [
        str(pt.get("pdf") or ""),
        str(ds_stress.get("local_path") or ""),
    ]
    if pt.get("path"):
        candidates.append(str(Path(str(pt["path"])).with_name("datasheet.pdf")))
    src_pdf = ""
    for cand in candidates:
        if cand and Path(cand).is_file():
            src_pdf = cand
            break
    mpn = str(c.get("mpn") or obj)
    local = ""
    if src_pdf and mpn:
        import shutil

        dest_dir = o / "cache" / "mpn" / safe_object_name(mpn)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "datasheet.pdf"
        try:
            if (not dest.is_file()) or dest.stat().st_size != Path(src_pdf).stat().st_size:
                shutil.copy2(src_pdf, dest)
            local = f"cache/mpn/{safe_object_name(mpn)}/datasheet.pdf"
        except Exception:
            local = src_pdf
    elif src_pdf:
        local = src_pdf
    extracted = dict(ds_stress.get("extracted") or {})
    if rid == "P11-A01" and pr is not None:
        extracted.setdefault("p_rated_w", float(pr) if pr is not None else pr)
    if rid == "P11-A03" and pr is not None:
        extracted.setdefault("v_rated_v", float(pr) if pr is not None else pr)
    if "p_rated_temp_c" not in extracted and "70" in str(cite):
        extracted["p_rated_temp_c"] = 70
    note = str(ds_stress.get("extract_note") or cite or params.get("extract_note") or "")
    # 电容项勿展示「P_rated」；Agent 已写出 V_rated/cite 时优先用人话说明
    if rid == "P11-A03":
        note = note.replace("P_rated 未抽出", "额定耐压 V_rated 未抽出")
        note = re.sub(r"\bP_rated\b", "V_rated", note)
        agent_cite = str((out.get("electrical") or {}).get("cite") or (card.get("electrical") or {}).get("cite") or "")
        agent_vr = (out.get("electrical") or {}).get("V_rated")
        if agent_vr is None:
            agent_vr = (card.get("electrical") or {}).get("V_rated")
        if agent_vr is not None and (not note or "未抽出" in note):
            note = agent_cite or f"V_rated={agent_vr} V"
            extracted.setdefault("v_rated_v", float(agent_vr))
        # 分层抽出：正式 Vr / 料号线索 / Agent 线索；冲突标存疑，线索不升绿
        try:
            from kernel.rated_hunt import (
                SEALABLE_VR_KINDS,
                collect_capacitor_vr_clues,
            )

            src_kind = str((params.get("rated_voltage_source") or {}).get("kind") or "")
            # 非密封出处误写入的 rated_voltage_v 不进正式行
            if (
                extracted.get("v_rated_v") is not None
                and src_kind
                and src_kind not in SEALABLE_VR_KINDS
            ):
                extracted.pop("v_rated_v", None)
            sealed_for_clue = extracted.get("v_rated_v")
            if sealed_for_clue is None and params.get("rated_voltage_v") is not None:
                if not src_kind or src_kind in SEALABLE_VR_KINDS:
                    try:
                        sealed_for_clue = float(params.get("rated_voltage_v"))
                    except (TypeError, ValueError):
                        sealed_for_clue = None
            board_path = o.parent if o.name == "review_out" else o
            pack = collect_capacitor_vr_clues(
                str(c.get("mpn") or mpn or ""),
                board_path,
                sealed_v=float(sealed_for_clue) if sealed_for_clue is not None else None,
            )
            clues = pack.get("clues") or {}
            mpn_c = clues.get("mpn_voltage_code") if isinstance(clues, dict) else None
            if isinstance(mpn_c, dict) and mpn_c.get("value_v") is not None:
                extracted["v_rated_mpn_code_v"] = float(mpn_c["value_v"])
            elif params.get("v_rated_mpn_code_v") is not None:
                extracted["v_rated_mpn_code_v"] = float(params["v_rated_mpn_code_v"])
            ag_c = clues.get("agent_lookup") if isinstance(clues, dict) else None
            if isinstance(ag_c, dict) and ag_c.get("value_v") is not None:
                extracted["v_rated_agent_v"] = float(ag_c["value_v"])
            elif params.get("v_rated_agent_v") is not None:
                extracted["v_rated_agent_v"] = float(params["v_rated_agent_v"])
            # 无硬出处的「密封」与料号码打架 → 不当正式 V_rated 展示
            try:
                ev = extracted.get("v_rated_v")
                mc = extracted.get("v_rated_mpn_code_v")
                if (
                    ev is not None
                    and mc is not None
                    and abs(float(ev) - float(mc)) > 0.05
                    and src_kind not in {"datasheet_page", "lcsc_product", "web_page", "agent_lookup"}
                ):
                    extracted.pop("v_rated_v", None)
            except (TypeError, ValueError):
                pass
            conflict = str(pack.get("conflict_note") or params.get("v_rated_conflict") or "").strip()
            clue_bits: list[str] = []
            if extracted.get("v_rated_mpn_code_v") is not None:
                clue_bits.append(
                    str(mpn_c.get("cite") if isinstance(mpn_c, dict) else "")
                    or f"料号电压码={extracted['v_rated_mpn_code_v']}V"
                )
            if extracted.get("v_rated_agent_v") is not None:
                ag_cite = ""
                if isinstance(ag_c, dict):
                    ag_cite = str(ag_c.get("cite") or "")
                    conf = str(ag_c.get("confidence") or "")
                    if conf:
                        ag_cite = f"{ag_cite}（confidence={conf}）" if ag_cite else f"confidence={conf}"
                clue_bits.append(ag_cite or f"Agent 查参={extracted['v_rated_agent_v']}V")
            if conflict:
                if not note or "未抽出" in note:
                    note = conflict
                elif conflict not in note:
                    note = note.rstrip("。;； ") + "；" + conflict
            elif extracted.get("v_rated_v") is None and clue_bits:
                joined = "；".join(x for x in clue_bits if x)
                if not note or "未抽出" in note:
                    note = joined + "；正本手册未闭合，仅作抽出线索，不作升绿依据"
                elif "料号电压码" not in note and "Agent 查参" not in note and joined:
                    note = note.rstrip("。;； ") + "；" + joined
        except Exception:
            pass
        # 已有正式 Vr 时清掉过时「未抽出」说明
        if extracted.get("v_rated_v") is not None and "未抽出" in note:
            src = params.get("rated_voltage_source") if isinstance(params, dict) else {}
            if not isinstance(src, dict):
                src = {}
            bit = f"{src.get('kind') or 'rated'}: {src.get('quote') or ''} @ {src.get('url') or src.get('path') or ''}"
            bit = bit.strip(" :@")
            note = bit or f"V_rated={extracted['v_rated_v']} V"
        # 丢掉误入的功率字段
        extracted.pop("p_rated_w", None)
        extracted.pop("p_rated_temp_c", None)
    out["datasheet"] = {
        "url": "",
        "local_path": local,
        "extracted": extracted,
        "extract_note": note,
        "mpn": c.get("mpn") or "",
    }

    elec = dict(out.get("electrical") or {})
    agent_ok = _agent_states_usable(out.get("states"))
    if rid == "P11-A01":
        # 应力包为密封数字源：有值则覆盖卡片旧 electrical（避免 Agent 旧卡钉死 P_max=0）
        # 深审已写可用工况时，保留卡上的 P_max，不用程序应力包盖掉。
        if stress.get("P_rated") is not None:
            elec["P_rated"] = stress.get("P_rated")
        elif pr is not None:
            elec.setdefault("P_rated", pr)
        if stress.get("P_limit") is not None:
            elec["P_limit"] = stress.get("P_limit")
        # Explicit null means the Judge could not close the stress. Zero is a
        # legitimate result too. Neither may be replaced by a background trial.
        program_peak = "P_max" not in elec
        if stress.get("P_max") is not None and program_peak:
            elec["P_max"] = stress.get("P_max")
        if stress.get("P_max_case") is not None and program_peak:
            elec["P_max_case"] = stress.get("P_max_case")
        if stress.get("ohm") is not None:
            elec["ohm"] = stress.get("ohm")
    else:
        if stress.get("V_rated") is not None:
            elec["V_rated"] = stress.get("V_rated")
        elif pr is not None and rid == "P11-A03":
            # 仅正式抽出行可回填 electrical；料号/Agent 线索不进 V_rated
            # Agent 卡可能显式写 V_rated=null，setdefault 不会覆盖
            if extracted.get("v_rated_v") is not None and elec.get("V_rated") is None:
                elec["V_rated"] = float(extracted["v_rated_v"])
        elif pr is not None and elec.get("V_rated") is None:
            elec["V_rated"] = pr
        if stress.get("V_limit") is not None:
            elec["V_limit"] = stress.get("V_limit")
        if stress.get("V_max") is not None and "V_max" not in elec:
            elec["V_max"] = stress.get("V_max")
    elec["cite"] = note or elec.get("cite") or ""
    out["electrical"] = elec

    limit = elec.get("P_limit") if rid == "P11-A01" else elec.get("V_limit")
    p_max = elec.get("P_max") if rid == "P11-A01" else elec.get("V_max")
    out["limit"] = limit
    out["computed"] = {"P_max_w": p_max} if rid == "P11-A01" else {"V_max": p_max}
    out["after"] = {
        "R": stress.get("ohm") or elec.get("ohm"),
        "v": None,
        "stress_kind": cases.get("kind") or "series_clamp",
        "net_a": nets_a,
        "net_b": nets_b,
    }
    out["stress_kind"] = cases.get("kind") or out.get("after", {}).get("stress_kind")
    out["recipe_id"] = "board_paths+operating_cases+program_stress"
    meta = dict(out.get("meta") or {})
    meta["unmapped_n"] = int((cases.get("meta") or {}).get("unmapped_n") or 0) or sum(
        1 for c0 in (cases.get("cases") or []) if isinstance(c0, dict) and c0.get("status") == "unmapped"
    )
    meta["closed_n"] = int((cases.get("meta") or {}).get("closed_n") or 0)
    out["meta"] = meta
    if meta["unmapped_n"] and out.get("verdict_zh") == "是":
        out["verdict_zh"] = "待确认"
        out["conclusion"] = "待确认（未建模）"
        out["summary"] = f"{obj} 含未建模工况，不得升绿"
        out["stuck"] = cases.get("stuck") or "含未建模工况"

    path_text = path.get("text") or obj
    # 剥掉旧 enrich 拼的「｜工况：…」再重写
    if "｜工况：" in str(path_text):
        path_text = str(path_text).split("｜工况：", 1)[0].strip()
    prior_states = [s for s in (out.get("states") or []) if isinstance(s, dict)]
    prior_by_cid = {str(s.get("case_id") or ""): s for s in prior_states if s.get("case_id")}
    reason_titles = _titles_from_reasoning(out.get("reasoning_steps"))
    stress_by = {str(s.get("case_id")): s for s in (stress.get("states") or []) if isinstance(s, dict)}
    vz_card = str(out.get("verdict_zh") or "")
    use_agent = _agent_states_usable(prior_states)

    card_states: list[dict[str, Any]] = []
    sol_states: list[dict[str, Any]] = []

    agent_calc_blob = str(out.get("calc") or "")
    agent_reasoning = list(out.get("reasoning_steps") or [])

    def _append_state(
        *,
        cid: str,
        maj: dict[str, Any],
        st: dict[str, Any],
        prior: dict[str, Any] | None,
        rtitle: str = "",
        state_index: int = 0,
    ) -> None:
        status = str(maj.get("status") or st.get("status") or (prior or {}).get("status") or "")
        prior_p = _prior_power(prior)
        if use_agent and prior is not None and prior_p is not None and rid == "P11-A01":
            p = prior_p
        elif use_agent and prior is not None and prior.get("V_across") is not None and rid != "P11-A01":
            p = prior.get("V_across")
        else:
            p = st.get("P") if rid == "P11-A01" else st.get("V_across")
            if p is None and rid == "P11-A01":
                p = prior_p
        if status == "unmapped":
            p = None
        prior_name = str((prior or {}).get("name_zh") or (prior or {}).get("name") or "")
        name = _connectivity_title(
            prior_name=prior_name,
            maj=maj,
            st=st,
            path_text=path_text,
            cid=cid,
            reasoning_title=rtitle,
        )
        if not name:
            return
        if status == "unmapped" and "未建模" not in name:
            name = f"{name}（未建模）"
        # Agent 已写对照结论则保留；否则按数字重算
        prior_eq = str((prior or {}).get("结论") or (prior or {}).get("conclusion") or "").strip()
        st_vz = str((prior or {}).get("verdict_zh") or "") or _state_verdict(
            p, limit, vz_card, status=status
        )
        prior_eq_usable = bool(prior_eq) and (
            "→" in prior_eq
            or "->" in prior_eq
            or any(x in prior_eq for x in ("是", "否", "待确认", "超标", "达标", "满足", "不满足"))
            or (
                ("P_limit" in prior_eq or "V_limit" in prior_eq or "限额" in prior_eq)
                and any(x in prior_eq for x in ("≤", "＞", ">", "<", "≧", "≦"))
            )
        )
        if prior_eq_usable:
            eq = prior_eq
            if (
                "→ 否" in prior_eq
                or "→否" in prior_eq
                or "超标" in prior_eq
                or "不满足" in prior_eq
                or (">P_limit" in prior_eq or "＞P_limit" in prior_eq or "> P_limit" in prior_eq)
                or (">V_limit" in prior_eq or "＞V_limit" in prior_eq or "> V_limit" in prior_eq)
            ):
                st_vz = "否"
            elif "待确认" in prior_eq or "未闭合" in prior_eq:
                # 「满足；未闭合…」不得因含「满足」升绿成「是」
                st_vz = "待确认"
            elif "→ 是" in prior_eq or "→是" in prior_eq or "达标" in prior_eq:
                st_vz = "是"
            elif "满足" in prior_eq and "不满足" not in prior_eq:
                st_vz = "是"
        else:
            st_vz = _state_verdict(p, limit, vz_card, status=status)
            eq = _compare_eq(rid, p, limit, st_vz, status=status, stuck=str(maj.get("stuck") or st.get("stuck") or ""))
        comparison_zh = ""
        if status == "conditional":
            st_vz = "待确认"
            eq = _compare_eq(rid, p, limit, st_vz, status=status)
            if p is not None and limit is not None:
                comparison_zh = "模型超限" if float(p) > float(limit) else "模型内满足限额"
        formula = str(st.get("formula") or (prior or {}).get("formula") or "")
        stuck_s = str(maj.get("stuck") or st.get("stuck") or (prior or {}).get("stuck") or "")
        if status == "unmapped":
            miss = maj.get("missing") or st.get("missing") or []
            lines = [{"expr": f"未建模：{stuck_s or '缺可闭合应力模型'}", "src": "通路模型"}]
            if miss:
                lines.append({"expr": "缺项：" + "/".join(str(x) for x in miss), "src": "通路模型"})
            var_note = ""
        else:
            lines, var_note = _traceable_lines(
                stress=stress,
                cid=cid,
                name=name,
                st=st,
                prior=prior,
                formula=formula,
                cite=note or cite,
                calc_blob=agent_calc_blob,
                state_index=state_index,
                reasoning_steps=agent_reasoning,
                prefer_agent=use_agent,
            )
        formula_clean = "; ".join(x.get("expr") or "" for x in lines if x.get("expr"))
        row = {
            "case_id": cid or str((prior or {}).get("case_id") or f"case_{len(card_states)+1}"),
            "name_zh": name,
            "name": name,
            "role": st.get("role") or (prior or {}).get("role") or ("unmapped" if status == "unmapped" else None),
            "stress_kind": (prior or {}).get("stress_kind") or st.get("stress_kind"),
            "status": status or None,
            "P": None if status == "unmapped" else (p if rid == "P11-A01" else st.get("P", (prior or {}).get("P"))),
            "V_across": None
            if status == "unmapped"
            else (p if rid != "P11-A01" else st.get("V_across", (prior or {}).get("V_across"))),
            "I": None if status == "unmapped" else st.get("I", (prior or {}).get("I")),
            "V_drop": None if status == "unmapped" else st.get("V_drop", (prior or {}).get("V_drop")),
            "formula": formula_clean,
            "stuck": stuck_s,
            "missing": list(maj.get("missing") or st.get("missing") or []),
            "verdict_zh": st_vz,
            "comparison_zh": comparison_zh,
            "结论": eq,
        }
        if rid == "P11-A01" and prior_p is not None and use_agent:
            row["P"] = prior_p
        card_states.append(row)
        reason = str(
            var_note
            or maj.get("what_changes")
            or maj.get("qualitative")
            or st.get("source_variant")
            or ""
        )
        if (
            "未上电" in reason
            or reason in _CASE_ID_EN
            or reason in _GENERIC_REASON
            or _is_thin_state_label(reason)
        ):
            reason = name
        fig_row: dict[str, Any] = {}
        if prior is not None:
            try:
                from agent_review.stress_figures import lookup as lookup_stress_figure

                fig_i = prior_states.index(prior)
                fig_row = lookup_stress_figure(o, rid, obj, fig_i) or {}
            except Exception:
                fig_row = {}
        why_worst = str((prior or {}).get("why_worst") or st.get("why_worst") or "").strip()
        role = st.get("role") or (prior or {}).get("role") or ("unmapped" if status == "unmapped" else None)
        sol_states.append(
            {
                "name": name,
                "verdict_zh": st_vz,
                "reason": reason,
                "role": role,
                "stress_kind": (prior or {}).get("stress_kind") or st.get("stress_kind"),
                "status": status,
                "comparison_zh": comparison_zh,
                "metric_unit": "W" if rid == "P11-A01" else "V",
                "conditions": (prior or {}).get("conditions") or st.get("conditions"),
                "why_worst": why_worst,
                "case_id": cid or str((prior or {}).get("case_id") or ""),
                "metric": None if status == "unmapped" else p,
                "lines": lines,
                "steps": [{"eq": eq}],
                "figure": fig_row.get("flow") or "",
                "figure_pages": fig_row.get("pages") or [],
                "figure_status": fig_row.get("status") or "",
                "figure_reason": fig_row.get("reason") or "",
            }
        )

    if use_agent:
        ai = 0
        for i, prior in enumerate(prior_states):
            cid = str(prior.get("case_id") or "")
            if cid == "supply_off" or "未上电" in str(prior.get("name_zh") or ""):
                continue
            maj = next(
                (c for c in (cases.get("cases") or []) if str(c.get("case_id") or "") == cid),
                {},
            )
            st = stress_by.get(cid) or {}
            rtitle = reason_titles[i] if i < len(reason_titles) else ""
            _append_state(
                cid=cid,
                maj=maj if isinstance(maj, dict) else {},
                st=st,
                prior=prior,
                rtitle=rtitle,
                state_index=ai,
            )
            ai += 1
    else:
        for i, maj in enumerate(cases.get("cases") or []):
            if not isinstance(maj, dict):
                continue
            cid = str(maj.get("case_id") or "")
            if cid == "supply_off" or "未上电" in str(maj.get("name_zh") or ""):
                continue
            st = stress_by.get(cid) or {}
            prior = prior_by_cid.get(cid)
            rtitle = reason_titles[i] if i < len(reason_titles) else ""
            _append_state(cid=cid, maj=maj, st=st, prior=prior, rtitle=rtitle, state_index=i)

    case_names = [str(s.get("name_zh") or "") for s in card_states if s.get("name_zh")]
    if case_names:
        out["phenomenon"] = f"{path_text}｜工况：{' / '.join(case_names)}"
    else:
        out["phenomenon"] = path_text

    if card_states:
        out["states"] = card_states
    out["cases"] = [
        {
            "name_zh": s.get("name_zh"),
            "v_across": s.get("V_drop") if rid == "P11-A01" else s.get("V_across"),
            "p_w": s.get("P"),
        }
        for s in card_states
    ]

    p_max_case = elec.get("P_max_case") or elec.get("P_max_case_id") or ""
    p_max_case_zh = _connectivity_title(
        prior_name="",
        maj={},
        st={},
        path_text=path_text,
        cid=str(p_max_case),
        reasoning_title="",
    )
    if _is_thin_state_label(p_max_case_zh) or not p_max_case_zh:
        p_max_case_zh = _zh_case_name(str(p_max_case), str(p_max_case)) or "最坏工作态"

    given = []
    if rid == "P11-A01":
        if elec.get("ohm") is not None:
            given.append({"sym": "R", "val": f"{elec.get('ohm')} Ω", "src": "BOM/图纸", "note": ""})
        if elec.get("P_rated") is not None:
            given.append(
                {
                    "sym": "P_rated",
                    "val": f"{elec.get('P_rated')} W",
                    "src": "规格书抽出",
                    "note": note,
                    "cite": note,
                }
            )
        if limit is not None:
            given.append({"sym": "P_limit", "val": _mw(limit), "src": "0.5×P_rated", "note": ""})
        if p_max is not None:
            given.append(
                {
                    "sym": "P_max",
                    "val": _mw(p_max),
                    "src": f"最坏态 {p_max_case_zh}".strip(),
                    "note": "",
                }
            )
    # 前提用 assumptions 原文在弹窗成段展示，不再拆成「计算前提」符号行。
    # 额定/限额对照留在下面的 aggregate_line，不占前提表。
    if p_max is not None and sol_states and not any(str(s.get("role") or "") == "worst" for s in sol_states):
        marked = False
        if p_max_case:
            for s in sol_states:
                if str(s.get("case_id") or "") == str(p_max_case):
                    s["role"] = "worst"
                    marked = True
                    break
        if not marked:
            best_i = None
            best_v = None
            for i, s in enumerate(sol_states):
                raw = s.get("metric")
                if raw is None:
                    continue
                try:
                    fv = float(raw)
                except (TypeError, ValueError):
                    continue
                if best_v is None or fv > best_v:
                    best_v = fv
                    best_i = i
            if best_i is not None:
                sol_states[best_i]["role"] = "worst"
    agg = ""
    if rid == "P11-A01" and elec.get("P_max") is not None and limit is not None:
        try:
            op = "≤" if float(elec["P_max"]) <= float(limit) else ">"
            agg = f"本卡：P_max={_mw(elec['P_max'])} {op} P_limit={_mw(limit)} → {vz_card}"
        except (TypeError, ValueError):
            agg = ""
    elif rid == "P11-A03" and elec.get("V_max") is not None and limit is not None:
        try:
            op = "≤" if float(elec["V_max"]) <= float(limit) else ">"
            agg = f"本卡：V_max={elec['V_max']} V {op} V_limit={limit} V → {vz_card}"
        except (TypeError, ValueError):
            agg = ""
    if not agg:
        if vz_card == "是":
            agg = "本卡：各态均未超限额 → 是"
        elif vz_card == "否":
            agg = "本卡：存在不达标 → 否"
        elif vz_card == "待确认":
            agg = _pending_aggregate_line(rid, elec)
        else:
            agg = f"本卡：{vz_card}"
    out["solution"] = {
        "given": given,
        "states": sol_states,
        "rule": "P_max ≤ 0.5 × P_rated" if rid == "P11-A01" else "V_max ≤ 0.5 × V_rated",
        "verdict_zh": vz_card,
        "aggregate_line": scrub_forbidden_copy(agg),
    }
    _fill_headline(out, rid, obj)

    bits = []
    for s in card_states[:6]:
        if s.get("status") == "unmapped":
            miss = s.get("missing") or []
            bits.append(
                f"{s.get('name_zh')}: 未建模"
                + (f"（缺：{'/'.join(str(x) for x in miss)}）" if miss else "")
            )
        elif rid == "P11-A01":
            bits.append(
                _compare_eq(rid, s.get("P"), limit, s.get("verdict_zh") or "", status=str(s.get("status") or ""))
            )
        else:
            bits.append(
                _compare_eq(
                    rid,
                    s.get("V_across"),
                    limit,
                    s.get("verdict_zh") or "",
                    status=str(s.get("status") or ""),
                )
            )
    if bits:
        rated_s = (
            f"P_rated={elec.get('P_rated')} W（{note or '规格书'}），限额 {_mw(limit)}"
            if rid == "P11-A01"
            else f"V_rated={elec.get('V_rated')} V，限额 {limit}"
        )
        new_calc = rated_s + "；" + "；".join(bits)
        # Agent 已写完整工况代入时保留原文，避免冲成只剩对照短句
        if (
            len(agent_calc_blob) > len(new_calc)
            and re.search(r"(工况|I\s*=|P_R|代入|V_出口|KCL)", agent_calc_blob)
        ):
            out["calc"] = agent_calc_blob
        else:
            out["calc"] = new_calc

    for key in ("summary", "conclusion", "calc", "phenomenon", "stuck", "action"):
        if key in out and out[key] is not None:
            out[key] = scrub_forbidden_copy(str(out[key]))
    # solution / states 结论一并清洗
    for st in out.get("states") or []:
        if not isinstance(st, dict):
            continue
        if st.get("结论") is not None:
            st["结论"] = scrub_forbidden_copy(str(st["结论"]))
        if st.get("formula") is not None:
            st["formula"] = scrub_forbidden_copy(str(st["formula"]))
            if "本态" in str(st["formula"]):
                st["formula"] = st["formula"].replace("本态", "")
        if st.get("结论") is not None and (
            "本态" in str(st.get("结论") or "") or not str(st.get("结论") or "").strip()
        ):
            st["结论"] = st.get("verdict_zh") or st["结论"]
    sol = out.get("solution")
    if isinstance(sol, dict):
        for st in sol.get("states") or []:
            if not isinstance(st, dict):
                continue
            st["reason"] = scrub_forbidden_copy(str(st.get("reason") or ""))
            for row in st.get("lines") or []:
                if isinstance(row, dict):
                    row["expr"] = scrub_forbidden_copy(str(row.get("expr") or ""))
                    row["src"] = _business_src(str(row.get("src") or "")) or "原理图与手册"
            for step in st.get("steps") or []:
                if isinstance(step, dict) and step.get("eq") is not None:
                    step["eq"] = scrub_forbidden_copy(str(step["eq"]))

    actors = cases.get("actors") or []
    if actors and not out.get("meta", {}).get("deferred"):
        if not str(out.get("stuck") or "").strip() or out.get("stuck") == "无":
            out["stuck"] = "无"
            out.setdefault("meta", {})
            out["meta"]["actors"] = actors

    return out
