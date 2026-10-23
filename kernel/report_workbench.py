# -*- coding: utf-8 -*-
"""Workbench payloads for expert_review.html detail pages.

关联图幅按检查项 sheet_scope 取页，不是每项都跟两端网络。
原理图整页 JPEG 写到 review_out/sch/；原 PDF 拷贝为 sch/schematic.pdf 供 PDF.js 查看。
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from kernel.report_text import humanize_report_text, pretty_evidence_label
from kernel.util import board_schematic_pdf, is_file_safe

GND_NETS = {"GND", "DGND", "AGND", "PGND", "VSS"}
CAP_TYPES = {"capacitor", "cap", "mlcc"}

# Relative to review_out/ — used by expert_review PDF.js viewer
SCHEMATIC_PDF_REL = "sch/schematic.pdf"

# device：仅对象所在页（标注/额定/位号）
# net：仅该网络出现过的图幅（对象是网名）
# nets：器件所在页 + 两端网络图幅（功耗/禁止对，GND 不跟）
# peer_caps：器件所在页 + 同网电容所在页（去耦/晶体负载，不跟电源网全板）
# path：器件所在页 + path_refs 所在页（防反/热插拔，不跟电源网全板）
SHEET_SCOPE: dict[str, str] = {
    "P42-A01": "device",
    "P42-A04": "device",
    "P05-A01": "device",
    "P05-A06": "device",
    "P04-A01": "net",
    "P04-A03": "net",
    "P11-A01": "nets",
    "P11-A03": "device",
    "P11-A07": "device",
    "P12-A01": "device",
    "P14-A02": "path",
    "P14-A05": "path",
    "P14-A06": "path",
    "P16-A01": "peer_caps",
    "P16-A05": "nets",
    "P07-A05": "nets",
    "P18-A02": "peer_caps",
    "P19-A03": "nets",
    "P21-A01": "nets",
}

SHEET_HINT: dict[str, str] = {
    "device": "仅器件所在页。本项不跟两端网络跨页。",
    "net": "仅该网络出现过的图幅。",
    "nets": "器件所在页 + 两端网络出现过的图幅（GND 全板铺开不跟）。",
    "peer_caps": "器件所在页 + 同网电容所在页（不跟电源网全板其它器件）。",
    "path": "器件所在页 + 路径相关位号所在页（不跟电源网全板）。",
}
BLOCK_ZH = {
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


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def parse_ohm(raw: str | None) -> float | None:
    s = (raw or "").strip().replace(" ", "").replace("Ω", "").replace("ohm", "")
    if not s:
        return None
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*([kKmMrR])?", s)
    if not m:
        return None
    v = float(m.group(1))
    suf = (m.group(2) or "").lower()
    if suf == "k":
        v *= 1e3
    elif suf == "m":
        v *= 1e6
    return v


def fmt_num(x: float | None, unit: str = "") -> str:
    if x is None:
        return "？"
    ax = abs(x)
    s = f"{x:.6g}" if ax < 1 or ax >= 100 else f"{x:.4g}"
    return f"{s} {unit}".strip()


def fmt_ohm(x: float | None) -> str:
    if x is None:
        return "？"
    if x >= 1e6:
        return f"{x / 1e6:g} MΩ"
    if x >= 1e3:
        return f"{x / 1e3:g} kΩ"
    return f"{x:g} Ω"


def fmt_power(w: float | None) -> str:
    if w is None:
        return "—"
    if abs(w) < 1:
        return f"{w * 1e3:.3g} mW"
    return f"{w:.4g} W"


def zh_bucket(zh: str) -> str:
    z = str(zh or "")
    if z in {"是", "PASS"}:
        return "通过"
    if z in {"否", "FAIL"}:
        return "不通过"
    if z in {"不适用", "NOT_APPLICABLE", "N/A"}:
        return "不适用"
    return "待确认"


def build_net_pages(comps: dict) -> dict[str, list[dict]]:
    tmp: dict[str, dict[int, dict]] = {}
    for ref, c in comps.items():
        if not isinstance(c, dict):
            continue
        nets = [
            p.get("net")
            for p in (c.get("pins") or [])
            if isinstance(p, dict) and p.get("net")
        ]
        for loc in c.get("location") or []:
            if not isinstance(loc, dict):
                continue
            page = loc.get("pdf_page")
            if not isinstance(page, int):
                continue
            for net in nets:
                bucket = tmp.setdefault(str(net), {})
                rec = bucket.setdefault(
                    page,
                    {
                        "page": page,
                        "page_name": loc.get("pageName") or loc.get("page_name") or "",
                        "module_title": loc.get("module_title") or "",
                        "refs": [],
                    },
                )
                if ref not in rec["refs"]:
                    rec["refs"].append(ref)
    return {n: [bucket[k] for k in sorted(bucket)] for n, bucket in tmp.items()}


def page_meta_from_sch(sch: dict) -> dict[int, dict]:
    meta: dict[int, dict] = {}
    for row in sch.get("page_detail") or []:
        n = row.get("page")
        if not isinstance(n, int):
            continue
        prev = str(row.get("preview") or "")
        m = re.search(r"图页\s*\n\s*(P\d+[A-Z0-9_]*)", prev)
        name = m.group(1) if m else ""
        title_m = re.search(name + r"\s*\n([^\n]+)", prev) if name else None
        title = (title_m.group(1).strip() if title_m else "")
        if title in {"嘉立创EDA", "板子", "原理图"}:
            title = ""
        meta[n] = {"page_name": name, "module_title": title}
    return meta


def sheet_scope_of(rule_id: str) -> str:
    return SHEET_SCOPE.get(str(rule_id or "").strip(), "device")


def sheet_hint_of(rule_id: str, scope: str | None = None) -> str:
    sc = scope or sheet_scope_of(rule_id)
    return SHEET_HINT.get(sc, SHEET_HINT["device"])


def _placeholder_obj(obj: str) -> bool:
    s = str(obj or "").strip()
    return (not s) or s.startswith("(")


def _looks_capacitor(ref: str, comps: dict) -> bool:
    c = comps.get(ref) if isinstance(comps, dict) else None
    if isinstance(c, dict):
        t = str(c.get("type") or c.get("class") or "").lower()
        if t in CAP_TYPES:
            return True
    return bool(re.match(r"^C\d+", str(ref or ""), re.I))


def _comp_of(obj: str, comps: dict) -> dict:
    if _placeholder_obj(obj) or not isinstance(comps, dict):
        return {}
    ref = str(obj or "").split(".")[0]
    c = comps.get(obj) or comps.get(ref)
    return c if isinstance(c, dict) else {}


def _collect_pin_nets(obj: str, card: dict, comps: dict, net_pages: dict[str, list[dict]]) -> list[str]:
    pin_nets: list[str] = []
    comp = _comp_of(obj, comps)
    for p in comp.get("pins") or []:
        if isinstance(p, dict) and p.get("net"):
            n = str(p["net"])
            if n not in pin_nets:
                pin_nets.append(n)
    card_nets = card.get("nets") or {}
    if isinstance(card_nets, dict):
        for n in (card_nets.get("a"), card_nets.get("b")):
            if n and str(n) not in pin_nets:
                pin_nets.append(str(n))
    if card.get("net_name") and str(card["net_name"]) not in pin_nets:
        pin_nets.append(str(card["net_name"]))
    if (
        obj
        and not _placeholder_obj(obj)
        and obj not in comps
        and obj not in pin_nets
        and obj in net_pages
    ):
        pin_nets.append(str(obj))
    return pin_nets


def _add_comp_locs(add, ref: str, comps: dict, role: str) -> None:
    comp = _comp_of(ref, comps)
    for loc in comp.get("location") or []:
        if not isinstance(loc, dict):
            continue
        add(
            loc.get("pdf_page"),
            loc.get("pageName") or loc.get("page_name") or "",
            loc.get("module_title") or "",
            role,
        )


def loc_pages(
    obj: str,
    card: dict,
    comps: dict,
    page_meta: dict[int, dict],
    net_pages: dict[str, list[dict]],
    scope: str = "device",
) -> list[dict]:
    by: dict[int, dict] = {}
    sc = scope if scope in SHEET_HINT else "device"

    def add(page: int | None, name: str, title: str, role: str) -> None:
        if not isinstance(page, int):
            return
        meta = page_meta.get(page) or {}
        rec = by.setdefault(
            page,
            {
                "page": page,
                "page_name": name or meta.get("page_name") or "",
                "module_title": title or meta.get("module_title") or "",
                "img": f"sch/p{page:02d}.jpg",
                "pdf": SCHEMATIC_PDF_REL,
                "roles": [],
            },
        )
        if role and role not in rec["roles"]:
            rec["roles"].append(role)

    if sc in {"device", "nets", "peer_caps", "path"}:
        _add_comp_locs(add, obj, comps, "器件所在")
        cloc = card.get("location") or {}
        if isinstance(cloc, dict):
            add(
                cloc.get("pdf_page"),
                cloc.get("page_name") or "",
                cloc.get("module_title") or "",
                "器件所在",
            )

    pin_nets = _collect_pin_nets(obj, card, comps, net_pages)

    if sc == "net":
        for net in pin_nets:
            if net in GND_NETS:
                continue
            for rec in net_pages.get(net) or []:
                add(rec.get("page"), rec.get("page_name") or "", rec.get("module_title") or "", f"网络 {net}")
    elif sc == "nets":
        for net in pin_nets:
            if net in GND_NETS:
                continue
            for rec in net_pages.get(net) or []:
                add(rec.get("page"), rec.get("page_name") or "", rec.get("module_title") or "", f"连线 {net}")
    elif sc == "peer_caps":
        seen_c: set[str] = set()
        obj_ref = str(obj or "").split(".")[0]
        for net in pin_nets:
            if net in GND_NETS:
                continue
            for rec in net_pages.get(net) or []:
                for r in rec.get("refs") or []:
                    if r == obj_ref or r in seen_c or not _looks_capacitor(str(r), comps):
                        continue
                    seen_c.add(str(r))
                    _add_comp_locs(add, str(r), comps, f"同网电容 {r}")
    elif sc == "path":
        extra: list[str] = []
        for key in ("path_refs", "related_refs", "protection_refs"):
            raw = card.get(key)
            if isinstance(raw, list):
                extra.extend(str(x) for x in raw if x)
            elif isinstance(raw, str) and raw.strip():
                extra.append(raw.strip())
        obj_ref = str(obj or "").split(".")[0]
        for r in extra:
            if r == obj or r == obj_ref:
                continue
            _add_comp_locs(add, r, comps, f"路径 {r}")

    out = list(by.values())
    out.sort(key=lambda x: (0 if "器件所在" in x["roles"] else 1, x["page"]))
    return out


def node_line(net: str, node: dict | None) -> str:
    node = node or {}
    st = node.get("status") or "unknown"
    val = node.get("value")
    src = node.get("source")
    src_s = ""
    if isinstance(src, dict):
        src_s = src.get("kind") or src.get("note") or ""
        if src.get("ref"):
            src_s = f"{src_s} {src.get('ref')}".strip()
    elif isinstance(src, str) and src not in {"none", ""}:
        src_s = src
    if val is not None:
        extra = f"（{src_s}）" if src_s else ""
        return f"V({net}) = {fmt_num(float(val), 'V')}{extra}"
    extra = f"，来源 {src_s}" if src_s else ""
    return f"V({net}) = ？（{st}{extra}）"


def stuck_short(card: dict) -> str:
    """兼容入口：表列短句来自 reason_of.one。通过卡不再返回「无」。"""
    return str(reason_of(card).get("one") or "")


_PY_STR_LIST_RE = re.compile(
    r"\[((?:\s*(?:'[^']*'|\"[^\"]*\")\s*,?)*)\]"
)
_EMPTY_REASON = {"", "无", "—", "-", "None"}
_CANDIDATE_ONLY = "candidate 不判是"
# 旧批评 stuck/conclusion：求解假设 … ≠ 网表 … → 展示只留网表侧
_CRITIC_MISMATCH_RE = re.compile(
    r"(?:^|·\s*)批评：\s*求解假设\s+.+\s*≠\s*网表\s+(.+)$",
    re.S,
)



def _assumption_rows(raw: Any) -> list[str]:
    """前提是一整段中文时按一段展示，不要把字符串拆成逐字列表。"""
    if isinstance(raw, str):
        text = pretty_stuck_text(raw).strip()
        return [text] if text else []
    if not isinstance(raw, list):
        return []
    if raw and all(isinstance(x, str) and len(x.strip()) <= 1 for x in raw) and len(raw) > 8:
        text = pretty_stuck_text("".join(raw)).strip()
        return [text] if text else []
    rows: list[str] = []
    for a in raw:
        if isinstance(a, dict):
            text = pretty_stuck_text(str(a.get("val") or a.get("note") or "")).strip()
        else:
            text = pretty_stuck_text(str(a or "")).strip()
        if text:
            rows.append(text)
    return rows


def pretty_stuck_text(s: str) -> str:
    """展示层：把 stuck 里的 Python 列表改成顿号连接；压缩批评 mismatch 长句。不改卡字段。"""

    def _repl(m: re.Match[str]) -> str:
        names = [a or b for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", m.group(1))]
        return "、".join(names) if names else m.group(0)

    text = _PY_STR_LIST_RE.sub(_repl, str(s or ""))
    text = humanize_report_text(text)
    m = _CRITIC_MISMATCH_RE.search(text.strip())
    if m:
        shows = m.group(1).strip()
        # 保留「待确认 · 」前缀（若有）
        prefix = ""
        head = text.strip()
        if "·" in head and head.split("·", 1)[0].strip() in {"待确认", "是", "否"}:
            prefix = head.split("·", 1)[0].strip() + " · "
        return f"{prefix}批评：{shows}"
    return text


def _reason_kind(zh: str) -> tuple[str, str]:
    z = str(zh or "")
    if z in {"是", "PASS"}:
        return "pass", "通过理由"
    if z in {"否", "FAIL"}:
        return "fail", "不通过处"
    if z in {"不适用", "NOT_APPLICABLE", "N/A"}:
        return "na", "不适用"
    return "wait", "未闭合原因"


def _reason_add(lines: list[str], seen: set[str], text: str) -> None:
    s = pretty_stuck_text(str(text or "")).strip()
    s = re.sub(r"^(rule_gap|miss_stress|topology_open|extract_fail|miss_datasheet)\s*·\s*", "", s)
    s = s.strip(" ·")
    if s in _EMPTY_REASON or s in seen:
        return
    seen.add(s)
    lines.append(s)


def _gate_reason_line(g: dict, *, skip_details: frozenset[str] | set[str] | None = None) -> str:
    name = str(g.get("name") or "门").strip() or "门"
    zh = str(g.get("verdict_zh") or "").strip()
    detail = str(g.get("detail") or "").strip()
    if skip_details and detail in skip_details:
        detail = ""
    if detail in _EMPTY_REASON:
        detail = ""
    line = f"{name}：{zh}" if zh else name
    if detail and detail not in line:
        line = f"{line} · {detail}"
    return line


def _state_reason_line(st: dict) -> str:
    name = str(st.get("name") or "态").strip() or "态"
    zh = str(st.get("verdict_zh") or "").strip()
    detail = str(st.get("detail") or st.get("note") or "").strip()
    if not detail:
        steps = st.get("steps") or []
        last = steps[-1] if steps else ""
        if isinstance(last, str) and (
            "→ 本态" in last or "→ 是" in last or "→ 否" in last or "→ 待确认" in last
        ):
            detail = last.strip()
    line = f"{name}：{zh}" if zh else name
    if detail and detail not in line:
        line = f"{line} · {detail}"
    return line


def _before_unclosed_nets(card: dict) -> list[str]:
    before = card.get("before") if isinstance(card.get("before"), dict) else {}
    out: list[str] = []
    closed_status = {
        "closed",
        "closed_rail",
        "divider_solve",
        "fixed",
        "netname_voltage",
        "from_facts",
    }
    for net, rec in before.items():
        if not net:
            continue
        if not isinstance(rec, dict):
            out.append(str(net))
            continue
        st = str(rec.get("status") or "")
        if rec.get("value") is not None:
            continue
        if st in closed_status:
            continue
        out.append(str(net))
    return out


def reason_of(card: dict) -> dict[str, Any]:
    """15 项共用判定说明：通过理由 / 不通过处 / 未闭合原因。"""
    kind, title = _reason_kind(str(card.get("verdict_zh") or ""))
    sol = card.get("solution") if isinstance(card.get("solution"), dict) else {}
    gates = [g for g in (sol.get("gates") or []) if isinstance(g, dict)]
    states = [s for s in (sol.get("states") or []) if isinstance(s, dict)]
    lines: list[str] = []
    seen: set[str] = set()
    stuck_pretty = pretty_stuck_text(str(card.get("stuck") or "")).strip()
    skip_gate_details: set[str] = set()
    if stuck_pretty not in _EMPTY_REASON:
        skip_gate_details.add(_CANDIDATE_ONLY)

    def add(text: str) -> None:
        _reason_add(lines, seen, text)

    if kind == "wait":
        # stuck 全文已在「对象评审摘要」展示，勿再塞进未闭合原因造成重复
        for g in gates:
            gz = str(g.get("verdict_zh") or "")
            if gz not in {"待确认", "INSUFFICIENT"} and g.get("ok") is not None:
                continue
            # stuck 已是批评事实句时，不再叠「批评：待确认 · 同一 detail」
            if str(g.get("name") or "") == "批评" and stuck_pretty.startswith("批评"):
                continue
            g_detail = pretty_stuck_text(str(g.get("detail") or ""))
            g2 = dict(g)
            if g_detail != str(g.get("detail") or ""):
                g2["detail"] = g_detail
            if g_detail and g_detail in stuck_pretty:
                g2["detail"] = ""
            add(_gate_reason_line(g2, skip_details=skip_gate_details))
        unknown = _before_unclosed_nets(card)
        if unknown:
            add("未闭合网：" + "、".join(unknown))
        add(str(sol.get("aggregate_line") or ""))
    elif kind == "fail":
        for g in gates:
            if str(g.get("verdict_zh") or "") == "否" or (
                g.get("ok") is False and str(g.get("verdict_zh") or "") != "待确认"
            ):
                add(_gate_reason_line(g))
        for st in states:
            if str(st.get("verdict_zh") or "") == "否":
                add(_state_reason_line(st))
        # 长 summary 只出现在「不通过处」；短 aggregate_line 给公式区，二者勿重复粘贴
        summary = str(card.get("summary") or "").strip()
        agg = str(sol.get("aggregate_line") or "").strip()
        if summary:
            add(summary)
        elif not lines:
            add(str(card.get("conclusion") or ""))
        if agg and agg != summary:
            add(agg)
    elif kind == "pass":
        for g in gates:
            if str(g.get("verdict_zh") or "") == "是" or g.get("ok") is True:
                add(_gate_reason_line(g))
        for st in states:
            if str(st.get("verdict_zh") or "") == "是":
                add(_state_reason_line(st))
        summary = str(card.get("summary") or "").strip()
        agg = str(sol.get("aggregate_line") or "").strip()
        if summary:
            add(summary)
        if agg and agg != summary:
            add(agg)
        if not lines:
            add(str(card.get("conclusion") or card.get("summary") or ""))
    else:
        # 不适用：一句话即可，勿重复堆「不适用」
        obj = str(card.get("object") or "")
        if obj in {"", "(none)", "(无)"}:
            add("本板无适用对象")
        else:
            add(str(card.get("conclusion") or card.get("summary") or "不适用"))

    if not lines:
        add(str(card.get("conclusion") or card.get("summary") or stuck_pretty or card.get("verdict_zh") or "—"))
    cause = card.get("cause") if isinstance(card.get("cause"), dict) else None
    if cause is None:
        from kernel.cause import attach_cause

        attach_cause(card)
        cause = card.get("cause") if isinstance(card.get("cause"), dict) else {}
    if cause.get("engineering_gap"):
        apx = str(cause.get("appendix") or "").strip()
        if apx and stuck_pretty and (stuck_pretty in apx or apx in stuck_pretty):
            apx = ""
        add(apx)
    one = lines[0] if lines else title
    if len(one) > 180:
        one = one[:180].rstrip() + "…"
    return {"kind": kind, "title": title, "one": one, "lines": lines}


EXTRACT_FIELD_ZH = {
    "p_rated_w": ("额定功率 P_rated", "W"),
    "p_rated_temp_c": ("额定温度", "℃"),
    "v_rated_v": ("额定耐压 V_rated", "V"),
    "v_rated_mpn_code_v": ("额定耐压（料号电压码·线索）", "V"),
    "v_rated_agent_v": ("额定耐压（Agent 查参·线索）", "V"),
    "rated_voltage_v": ("额定耐压 Vr", "V"),
    "vz_max_v": ("齐纳 Vz max", "V"),
    "vf_v": ("正向压降 Vf", "V"),
    "vce_sat_v": ("饱和压降 Vce_sat", "V"),
    "vbe_v": ("基极-发射极 Vbe", "V"),
    "rds_on_ohm": ("导通电阻 Rds_on", "Ω"),
    "isat": ("饱和电流 Isat", "A"),
    "cl_pf": ("负载电容 CL", "pF"),
    "t_max_c": ("最高温度", "℃"),
    "delta_t": ("温升 ΔT", "℃"),
    "dt_c": ("温升 ΔT", "℃"),
}

# 按检查项过滤规格书抽出字段（电容项不展示功率额定）
_EXTRACT_DENY_BY_RULE: dict[str, frozenset[str]] = {
    "P11-A03": frozenset({"p_rated_w", "p_rated_temp_c"}),
    "P05-A06": frozenset({"p_rated_w", "p_rated_temp_c"}),
    "P12-A01": frozenset({"p_rated_w", "p_rated_temp_c"}),
}
_EXTRACT_ALLOW_BY_RULE: dict[str, frozenset[str]] = {
    "P11-A01": frozenset({"p_rated_w", "p_rated_temp_c"}),
    "P11-A03": frozenset(
        {"v_rated_v", "v_rated_mpn_code_v", "v_rated_agent_v", "rated_voltage_v"}
    ),
}


def extract_rows(
    extracted: dict | None, note: str = "", *, rule_id: str = ""
) -> list[dict[str, str]]:
    """Normalize datasheet.extracted into display rows."""
    rid = str(rule_id or "")
    deny = _EXTRACT_DENY_BY_RULE.get(rid) or frozenset()
    allow = _EXTRACT_ALLOW_BY_RULE.get(rid)
    rows: list[dict[str, str]] = []
    for k, v in (extracted or {}).items():
        if v is None or v == "":
            continue
        if k in deny:
            continue
        if allow is not None and k not in allow and k not in {"extract_note"}:
            # 有白名单时只展示本项相关字段
            if rid.startswith("P11-A0"):
                continue
        label, unit = EXTRACT_FIELD_ZH.get(k, (k, ""))
        rows.append({"key": k, "label": label, "value": str(v), "unit": unit})
    note_s = pretty_stuck_text(str(note or ""))
    if rid == "P11-A03":
        note_s = note_s.replace("P_rated 未抽出", "额定耐压 V_rated 未抽出")
        note_s = re.sub(r"\bP_rated\b", "V_rated", note_s)
    # 已有额定抽出行时，不再挂「未抽出」空说明
    if note_s and not (rows and "未抽出" in note_s):
        rows.append({"key": "extract_note", "label": "抽出说明", "value": note_s, "unit": ""})
    return rows


def fanout_text(fanout: dict | None) -> str:
    if not isinstance(fanout, dict) or not fanout:
        return ""
    lines = []
    hops = fanout.get("hops")
    if hops is not None:
        lines.append(f"N 跳连通：{hops} 跳 · 器件 {fanout.get('device_n') or 0} 个")
    for path in fanout.get("paths") or []:
        if isinstance(path, str) and path.strip():
            lines.append(f"路径：{path}")
        elif isinstance(path, dict):
            s = path.get("text") or " → ".join(str(x) for x in (path.get("nodes") or []) if x)
            if s:
                lines.append(f"路径：{s}")
    rails = fanout.get("closed_rails") or []
    if rails:
        lines.append("闭合轨：" + "、".join(str(x) for x in rails))
    open_ends = fanout.get("open_ends") or []
    if open_ends:
        lines.append("未闭合：" + "、".join(str(x) for x in open_ends))
    missing = fanout.get("missing_fields") or []
    if missing:
        lines.append("路径缺字段：" + "、".join(str(x) for x in missing))
    return "\n".join(lines)


def explain_cite(cite: str) -> tuple[str, str]:
    """Split a raw cite into (来源标签, 说明). Empty cite → empty pair."""
    s = (cite or "").strip()
    if not s:
        return "", ""
    if s in {"电源网名闭合", "电源网名"}:
        return "电源网名", "网名含电压即闭合"
    if "电源网名" in s:
        return "电源网名", s
    if s in {"地", "地网"} or s in {"GND=0", "GND = 0"}:
        return "地网", "GND=0"
    if s.startswith("地") and len(s) <= 8:
        return "地网", "GND=0"
    if "图纸/BOM" in s or (s.startswith("value=") and "图纸" not in s):
        val = s
        if "value=" in s:
            val = s.split("value=", 1)[-1].strip().strip("'\"")
        return "图纸/BOM", val or s
    if "UNI-ROYAL" in s or "thick film" in s.lower() or "系列表" in s:
        return "电阻系列表", s
    if "P11-A01" in s and "降额" in s:
        return "P11-A01", "限额=0.5×P_rated"
    if "节点方程" in s or "divider_solve" in s or s.startswith("DC 电阻"):
        return "板内直流求解", s
    if "规格书" in s or "抽出" in s:
        return "规格书", s
    if "switch" in s.lower() or "导通" in s or "截止" in s:
        return "驱动态", s
    if "OC" in s or "开集" in s:
        return "开集电极", s
    if "CMOS" in s or "驱动高" in s or "驱动低" in s:
        return "驱动态", s
    if s.startswith("跟随器"):
        rest = s[len("跟随器") :].lstrip("：: ")
        return "跟随器", rest or s
    if "Vz_max" in s or "阳极" in s or "shunt clamp" in s:
        return "稳压管钳位", s
    if "对地" in s or "开路" in s:
        return "连接器包络", s
    if "网 i_work" in s:
        return "网工作电流", s
    return "", s


def split_eq_cite(step: str) -> tuple[str, str]:
    """Split a step line into equation vs trailing cite (2+ spaces)."""
    s = str(step or "").rstrip()
    if not s:
        return "", ""
    if " → 本态 " in s or s.endswith(" → 是") or s.endswith(" → 否") or s.endswith(" → 待确认"):
        return s, ""
    m = re.search(r"^(.*\S)\s{2,}(\S.*)$", s)
    if not m:
        return s, ""
    cite = m.group(2)
    if cite.startswith("="):
        return s, ""
    if re.match(r"^[A-Za-z][A-Za-z0-9()]*\s*=", cite) and ("缺" in cite or re.search(r"[0-9]", cite)):
        return s, ""
    return m.group(1), cite


def _calc_kind_note(kind: str) -> str:
    if kind == "divider":
        return "串联分压"
    if kind == "short":
        return "直流短接"
    return "节点方程"


def _calc_ui(calc: dict | None) -> dict[str, Any] | None:
    if not isinstance(calc, dict):
        return None
    if not (calc.get("steps") or calc.get("eq") or calc.get("knowns")):
        return None
    knowns: list[dict[str, str]] = []
    for k in calc.get("knowns") or []:
        if not isinstance(k, dict):
            continue
        cite = str(k.get("cite") or "")
        src, note = explain_cite(cite)
        item: dict[str, Any] = {
            "sym": str(k.get("sym") or ""),
            "val": str(k.get("val") or ""),
            "cite": cite,
            "src": src,
            "note": note,
        }
        nested = _calc_ui(k.get("calc") if isinstance(k.get("calc"), dict) else None)
        if nested:
            item["calc"] = nested
        knowns.append(item)
    return {
        "kind": str(calc.get("kind") or "node_eq"),
        "eq": str(calc.get("eq") or ""),
        "subs": str(calc.get("subs") or ""),
        "result": str(calc.get("result") or ""),
        "knowns": knowns,
        "steps": [str(s) for s in (calc.get("steps") or []) if s],
    }


def _apply_calc_note(src: str, note: str, calc: dict | None) -> str:
    if not calc:
        return note
    short = _calc_kind_note(str(calc.get("kind") or ""))
    if src == "板内直流求解" or "节点方程" in (note or "") or "divider_solve" in (note or ""):
        return short
    return note


def _given_ui(rows: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for g in rows or []:
        if not isinstance(g, dict):
            continue
        # 卡上常只写 src/note、cite 为空；勿用空 cite 冲掉来源列
        raw_cite = str(g.get("cite") or "").strip()
        raw_src = str(g.get("src") or "").strip()
        raw_note = str(g.get("note") or "").strip()
        cite = raw_cite or raw_src
        src, note = explain_cite(cite)
        if not src:
            src = raw_src
        if not note:
            note = raw_note or (raw_cite if raw_cite != src else "")
        elif raw_note and raw_note not in note:
            note = raw_note
        calc = _calc_ui(g.get("calc") if isinstance(g.get("calc"), dict) else None)
        note = _apply_calc_note(src, note, calc)
        row: dict[str, Any] = {
            "sym": str(g.get("sym") or ""),
            "val": str(g.get("val") or ""),
            "cite": raw_cite or cite,
            "src": src,
            "note": note,
        }
        if calc:
            row["calc"] = calc
        out.append(row)
    return out


def _states_ui(states: list, given: list | None = None) -> list[dict[str, Any]]:
    by_sym: dict[str, dict[str, Any]] = {}
    for g in given or []:
        if isinstance(g, dict) and g.get("sym") and g.get("calc"):
            by_sym[str(g["sym"])] = g
    out: list[dict[str, Any]] = []
    for st in states or []:
        if not isinstance(st, dict):
            continue
        steps_ui: list[dict[str, Any]] = []
        for step in st.get("steps") or []:
            if isinstance(step, dict):
                eq = str(step.get("eq") or step.get("expr") or "").strip()
                cite = str(step.get("cite") or step.get("src") or "").strip()
                if not cite:
                    eq, cite = split_eq_cite(eq)
            else:
                eq, cite = split_eq_cite(str(step))
            if not eq:
                continue
            src, note = explain_cite(cite)
            calc = None
            m = re.match(r"^(V\([^)]+\))\s*=", eq)
            if m and m.group(1) in by_sym:
                calc = by_sym[m.group(1)].get("calc")
            note = _apply_calc_note(src, note, calc if isinstance(calc, dict) else None)
            row: dict[str, Any] = {"eq": eq, "cite": cite, "src": src, "note": note}
            if calc:
                row["calc"] = calc
            steps_ui.append(row)
        # 标题优先用通路描述；薄分类词时用 reason 顶替
        raw_name = str(st.get("name") or "").strip()
        reason = str(st.get("reason") or "").strip()
        thin = raw_name in {
            "有源拉低",
            "直流工作",
            "正常钳位",
            "外接接地",
            "其它工况",
            "未评",
            "态",
            "",
        }
        if thin and reason and reason != raw_name:
            display_name = reason
        else:
            display_name = raw_name or reason or "工况"
        item: dict[str, Any] = {
            "name": display_name,
            "verdict_zh": st.get("verdict_zh") or "",
            "steps": steps_ui,
        }
        if st.get("role"):
            item["role"] = st.get("role")
        if st.get("status"):
            item["status"] = st.get("status")
        if st.get("conditions"):
            item["conditions"] = st.get("conditions")
        for key in ("comparison_zh", "metric_unit"):
            if st.get(key):
                item[key] = st[key]
        if str(st.get("why_worst") or "").strip():
            item["why_worst"] = str(st.get("why_worst") or "").strip()
        if st.get("metric") is not None:
            item["metric"] = st.get("metric")
        if st.get("figure"):
            item["figure"] = st.get("figure")
        pages = st.get("figure_pages")
        if isinstance(pages, list) and pages:
            item["figure_pages"] = [
                {"src": str(p.get("src") or ""), "label": str(p.get("label") or "")}
                for p in pages
                if isinstance(p, dict) and p.get("src")
            ]
        if st.get("figure_status"):
            item["figure_status"] = st.get("figure_status")
        if st.get("figure_reason"):
            item["figure_reason"] = st.get("figure_reason")
        if reason and reason != display_name:
            item["reason"] = reason
        raw_lines = [x for x in (st.get("lines") or []) if isinstance(x, dict)]
        if not raw_lines:
            raw_lines = [
                {"expr": s.get("eq") or "", "src": s.get("src") or s.get("cite") or s.get("note") or ""}
                for s in steps_ui
                if s.get("eq") and "本态" not in str(s.get("eq"))
            ]
        if raw_lines:
            item["lines"] = [
                {"expr": str(x.get("expr") or ""), "src": str(x.get("src") or "")} for x in raw_lines
            ]
        out.append(item)
    return out


def _scrub_ben_tai(text: str) -> str:
    """对人可见文案去掉「本态」残句前缀。"""
    t = str(text or "")
    t = t.replace("本态不参评", "不参评")
    t = t.replace("→ 本态 ", "→ ")
    t = t.replace("→本态 ", "→ ")
    t = t.replace("本态各角点取齐", "各角点取齐")
    t = t.replace("本态 ", "")
    t = t.replace("本态", "")
    return t


def _formula_pack(
    *,
    kind: str,
    body: str,
    given: list | None = None,
    states: list | None = None,
    gates: list | None = None,
    rule: str = "",
    aggregate_line: str = "",
    verdict_zh: str = "",
    layout: str = "",
) -> dict[str, Any]:
    clean_states: list = []
    for st in states or []:
        if not isinstance(st, dict):
            clean_states.append(st)
            continue
        st2 = dict(st)
        if st2.get("reason") is not None:
            st2["reason"] = _scrub_ben_tai(str(st2.get("reason") or ""))
        steps = []
        for step in st2.get("steps") or []:
            if isinstance(step, dict):
                s2 = dict(step)
                if s2.get("eq") is not None:
                    s2["eq"] = _scrub_ben_tai(str(s2.get("eq") or ""))
                steps.append(s2)
            else:
                steps.append(step)
        if steps:
            st2["steps"] = steps
        lines = []
        for row in st2.get("lines") or []:
            if isinstance(row, dict):
                r2 = dict(row)
                if r2.get("expr") is not None:
                    r2["expr"] = _scrub_ben_tai(str(r2.get("expr") or ""))
                if r2.get("src") is not None:
                    r2["src"] = _scrub_ben_tai(str(r2.get("src") or ""))
                lines.append(r2)
            elif isinstance(row, str):
                lines.append(_scrub_ben_tai(row))
            else:
                lines.append(row)
        if lines:
            st2["lines"] = lines
        clean_states.append(st2)
    return {
        "kind": kind,
        "body": pretty_stuck_text(_scrub_ben_tai(body)),
        "given": list(given or []),
        "states": clean_states,
        "gates": list(gates or []),
        "rule": rule or "",
        "aggregate_line": pretty_stuck_text(_scrub_ben_tai(aggregate_line or "")),
        "verdict_zh": verdict_zh or "",
        "layout": layout or "",
    }


def formula_of(card: dict) -> dict[str, Any]:
    zh = card.get("verdict_zh") or ""
    sol = card.get("solution") if isinstance(card.get("solution"), dict) else None
    if sol and (sol.get("given") or sol.get("states") or sol.get("gates") or sol.get("rule")):
        from kernel.solution import render_solution

        sol_ui = dict(sol)
        gates_raw = [g for g in (sol.get("gates") or []) if isinstance(g, dict)]
        gates_clean: list[dict] = []
        for g in gates_raw:
            g2 = dict(g)
            cite = pretty_stuck_text(str(g.get("detail") or ""))
            if cite.startswith("批评："):
                cite = cite[3:].lstrip("：: ").strip()
            g2["detail"] = cite
            gates_clean.append(g2)
        sol_ui["gates"] = gates_clean
        body = render_solution(sol_ui)
        z = zh if zh in ("是", "否", "待确认", "不适用") else (sol.get("verdict_zh") or zh)
        kind = "pass" if z == "是" else ("fail" if z == "否" else "wait")
        gates_ui = []
        for g in gates_clean:
            cite = str(g.get("detail") or "")
            src, note = explain_cite(cite)
            gates_ui.append(
                {
                    "name": g.get("name") or "",
                    "verdict_zh": g.get("verdict_zh") or "",
                    "detail": cite,
                    "src": src,
                    "note": note,
                }
            )
        given_ui = _given_ui(list(sol.get("given") or []))
        return _formula_pack(
            kind=kind,
            body=body,
            given=given_ui,
            states=_states_ui(list(sol.get("states") or []), given_ui),
            gates=gates_ui,
            rule=sol.get("rule") or "",
            aggregate_line=sol.get("aggregate_line") or "",
            verdict_zh=z,
        )

    after = card.get("after") if isinstance(card.get("after"), dict) else {}
    computed = card.get("computed") if isinstance(card.get("computed"), dict) else {}
    stress = bool(after or computed or card.get("limit") is not None)
    fan_txt = fanout_text(card.get("fanout") if isinstance(card.get("fanout"), dict) else None)
    if stress:
        ident = card.get("identity") or {}
        ds = card.get("datasheet") or {}
        extracted = ds.get("extracted") if isinstance(ds.get("extracted"), dict) else {}
        nets = card.get("nets") if isinstance(card.get("nets"), dict) else {}
        a = nets.get("a") or "a"
        b = nets.get("b") or "b"
        before = card.get("before") if isinstance(card.get("before"), dict) else {}
        R = after.get("R")
        if R is None:
            R = parse_ohm(ident.get("value_bom") or ident.get("value_pdf") or ident.get("value_tel"))
        v = after.get("v")
        p_rated = extracted.get("p_rated_w")
        limit = card.get("limit")
        if limit is None and p_rated is not None:
            limit = 0.5 * float(p_rated)
        lines = ["公式：P = V² / R"]
        if fan_txt:
            lines.append(fan_txt)
        na = before.get(a) if isinstance(before.get(a), dict) else None
        nb = before.get(b) if isinstance(before.get(b), dict) else None
        lines.append(f"代入：{node_line(str(a), na)}")
        lines.append(f"      {node_line(str(b), nb)}")
        lines.append(f"      R = {fmt_ohm(float(R))}" if R is not None else "      R = ？")
        cases = card.get("cases") or after.get("cases") or []
        states = card.get("states") or after.get("states") or []
        drive_kind = card.get("drive_kind") or after.get("drive_kind")
        if drive_kind:
            n_show = len(cases) if cases else len(states)
            lines.append(f"驱动：{drive_kind} · {n_show or 0}态")
        rid = card.get("recipe_id")
        if rid:
            lines.append(f"配方：{rid}")
        assumptions = [p["text"] for p in card.get("key_assumptions",[])] or card.get("assumptions") or after.get("assumed_zero_drops") or []
        if isinstance(assumptions, str):
            assumptions = [assumptions] if assumptions.strip() else []
        if assumptions:
            bits = []
            for a in assumptions[:6]:
                if isinstance(a, dict):
                    bits.append(f"{a.get('sym') or '?'}={a.get('val')}")
                else:
                    bits.append(str(a))
            if bits:
                lines.append("假设：" + "、".join(bits))
        if cases:
            lines.append("态表：")
            for c in cases[:12]:
                if not isinstance(c, dict):
                    continue
                name = c.get("name_zh") or "态"
                vv = c.get("v_across")
                pp = c.get("p_w")
                if vv is None:
                    continue
                if pp is None and R:
                    pp = (float(vv) ** 2) / float(R)
                lines.append(f"  · {name}：V={fmt_num(float(vv), 'V')} P={fmt_power(float(pp or 0))}")
        elif states:
            for st in states[:12]:
                if not isinstance(st, dict):
                    continue
                if st.get("name") is not None and st.get("v_across") is not None:
                    vv = float(st["v_across"])
                    pp = st.get("p_w")
                    if pp is None and R:
                        pp = (vv ** 2) / float(R)
                    lines.append(
                        f"态：{st.get('name')}：V={fmt_num(vv, 'V')} P={fmt_power(float(pp or 0))}"
                    )
                elif st.get("note"):
                    note = str(st.get("note") or "")
                    # Skip raw global mask dumps
                    if note.startswith("mask=") or "OC_ON:" in note or "SW_ON:" in note:
                        continue
                    lines.append(f"态：{note}")
        p = computed.get("P_max_w")
        if v is not None and R:
            if p is None:
                p = (float(v) ** 2) / float(R)
            lines.append(f"      V_env = max|Va−Vb| = {fmt_num(float(v), 'V')}")
            lines.append(f"计算：P_max = {fmt_num(float(v))}² / {fmt_ohm(float(R))} = {fmt_power(float(p))}")
        else:
            lines.append("计算：缺电压或电阻，不能写出 P")
        if p_rated is not None:
            lines.append(f"限额：0.5 × P_rated = 0.5 × {fmt_power(float(p_rated))} = {fmt_power(limit)}")
        elif limit is not None:
            lines.append(f"限额：{fmt_power(float(limit))}")
        else:
            lines.append("限额：缺 P_rated")
        if zh == "是" and p is not None and limit is not None:
            lines.append(f"裕量：{fmt_power(float(limit) - float(p))}")
            lines.append("结论：通过")
            kind = "pass"
        elif zh == "否":
            lines.append("结论：不通过")
            kind = "fail"
        else:
            why = reason_of(card)
            lines.append(f"结论：待确认 · {why.get('one') or '证据未齐'}")
            kind = "wait"
        return _formula_pack(kind=kind, body="\n".join(lines), verdict_zh=zh)

    # 无对象 / 不适用：不要四行「不适用」堆砌
    obj = str(card.get("object") or "")
    if zh in {"不适用", "NOT_APPLICABLE"} or obj in {"", "(none)", "(无)"}:
        return _formula_pack(
            kind="wait",
            body="本板无适用对象，本项不适用。",
            verdict_zh=zh or "不适用",
        )

    lines = []
    if fan_txt:
        lines.append(fan_txt)
    if card.get("calc"):
        lines.append(f"口径：{pretty_stuck_text(str(card.get('calc')))}")
    if card.get("phenomenon"):
        lines.append(f"现象：{pretty_stuck_text(str(card.get('phenomenon')))}")
    why = reason_of(card)
    title = str(why.get("title") or "判定说明")
    for bit in why.get("lines") or []:
        lines.append(f"{title}：{pretty_stuck_text(str(bit))}")
    lines.append(
        f"结论：{pretty_stuck_text(str(card.get('conclusion') or card.get('summary') or zh_bucket(zh)))}"
    )
    kind = "pass" if zh == "是" else ("fail" if zh == "否" else "wait")
    # 四格叙述：前端用分节卡片，不再用等宽 pre
    return _formula_pack(kind=kind, body="\n".join(lines), verdict_zh=zh, layout="quad")


def _safe_mpn(mpn: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", mpn or "ds")[:80]


def resolve_datasheet(mpn: str, local_path: str, out_dir: Path, root: Path) -> tuple[str, int]:
    candidates: list[Path] = []
    raw = (local_path or "").replace("\\", "/")
    if raw:
        p = Path(raw)
        if p.is_file():
            candidates.append(p)
        else:
            for base in (out_dir, root, out_dir.parent):
                cand = base / raw
                if cand.is_file():
                    candidates.append(cand)
                    break
    if mpn:
        for folder in (out_dir / "datasheets", root / "cache" / "mpn" / mpn):
            if not folder.exists():
                continue
            names=(f"{mpn}.pdf", f"{_safe_mpn(mpn)}.pdf")
            if folder != out_dir / "datasheets":names=("datasheet.pdf",)+names
            for name in names:
                cand = folder / name
                if cand.is_file():
                    candidates.append(cand)
    # The shared datasheets directory contains many unrelated devices. Never
    # substitute its first PDF (or an arbitrary alternate in an MPN cache) for
    # a missing part. Audited family handbooks must be passed explicitly.
    src = next((c for c in candidates if c.is_file() and _pdf_pages(c)>0), None)
    if src is None:
        return "", 0
    try:
        rel = src.resolve().relative_to(out_dir.resolve()).as_posix()
        pages = _pdf_pages(src)
        return rel, pages
    except ValueError:
        dest_dir = out_dir / "datasheets"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{_safe_mpn(mpn) or src.stem}.pdf"
        if not dest.exists() or dest.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dest)
        return f"datasheets/{dest.name}", _pdf_pages(dest)


def _pdf_pages(path: Path) -> int:
    try:
        import fitz

        doc = fitz.open(path)
        n = doc.page_count
        doc.close()
        return n
    except Exception:
        return 0


def _reuse_preview_jpgs(out_dir: Path, root: Path) -> int:
    src = root / "_preview" / "sch"
    if not src.is_dir():
        return 0
    dest = out_dir / "sch"
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for jpg in sorted(src.glob("p*.jpg")):
        target = dest / jpg.name
        if not target.exists() or target.stat().st_size < 2000:
            shutil.copy2(jpg, target)
        if target.is_file() and target.stat().st_size > 2000:
            n += 1
    return n


def ensure_schematic_pdf(out_dir: Path, pdf: Path) -> bool:
    """Copy schematic PDF into review_out/sch/schematic.pdf for the HTML viewer."""
    if not is_file_safe(pdf):
        return False
    dest = out_dir / "sch" / "schematic.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        src_stat = pdf.stat()
        if (
            dest.is_file()
            and dest.stat().st_size == src_stat.st_size
            and dest.stat().st_mtime >= src_stat.st_mtime
        ):
            return True
        shutil.copy2(pdf, dest)
        return dest.is_file() and dest.stat().st_size > 0
    except Exception:
        return False


def render_schematic_jpgs(out_dir: Path, pdf: Path) -> int:
    if not is_file_safe(pdf):
        return 0
    try:
        import fitz
    except Exception:
        return 0
    dest_dir = out_dir / "sch"
    dest_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf)
    n = 0
    pdf_mtime = pdf.stat().st_mtime
    for i in range(doc.page_count):
        outp = dest_dir / f"p{i + 1:02d}.jpg"
        if outp.is_file() and outp.stat().st_mtime >= pdf_mtime and outp.stat().st_size > 2000:
            n += 1
            continue
        pix = doc[i].get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False)
        pix.save(outp, jpg_quality=78)
        n += 1
    doc.close()
    return n


def _ref_key(ref: str) -> tuple:
    m = re.match(r"([A-Za-z]+)(\d+)", ref or "")
    if m:
        return (m.group(1), int(m.group(2)))
    return (ref or "", 0)


def devices_from_cards(
    cards: list,
    comps: dict,
    page_meta: dict[int, dict],
    net_pages: dict[str, list[dict]],
    out_dir: Path,
    root: Path,
    rule_id: str = "",
) -> list[dict]:
    devices = []
    scope = sheet_scope_of(rule_id)
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        obj = str(card.get("object") or "")
        ident = card.get("identity") if isinstance(card.get("identity"), dict) else {}
        ds = card.get("datasheet") if isinstance(card.get("datasheet"), dict) else {}
        nets = card.get("nets") if isinstance(card.get("nets"), dict) else {}
        after = card.get("after") if isinstance(card.get("after"), dict) else {}
        computed = card.get("computed") if isinstance(card.get("computed"), dict) else {}
        ref_comp = comps.get(obj) or comps.get(obj.split(".")[0]) or {}
        mpn = ident.get("mpn") or (ref_comp.get("mpn") if isinstance(ref_comp, dict) else "") or ""
        pages = loc_pages(obj, card, comps, page_meta, net_pages, scope=scope)
        ds_rel, ds_pages = resolve_datasheet(str(mpn), str(ds.get("local_path") or ""), out_dir, root)
        zh = card.get("verdict_zh") or ""
        bucket = zh_bucket(zh)
        net_list = [nets.get("a") or "", nets.get("b") or ""]
        if not any(net_list) and card.get("net_name"):
            net_list = [str(card.get("net_name")), ""]
        extracted = ds.get("extracted") if isinstance(ds.get("extracted"), dict) else {}
        extract_note = str(ds.get("extract_note") or "")
        why = reason_of(card)
        if card.get('review_detail'):
            why = {**why, 'one':card['review_detail']['headline']}
        # 对象评审摘要只要一句。长 summary 不是摘要。
        blurb = ""
        for key, cand in (
            ("headline_zh", card.get("headline_zh")),
            ("summary", card.get("summary")),
            ("conclusion", card.get("conclusion")),
            ("one", why.get("one")),
            ("stuck", card.get("stuck")),
        ):
            text = pretty_stuck_text(str(cand or "")).strip()
            if not text or text in {"无", "—", "-"}:
                continue
            if key == "summary" and len(text) > 90:
                continue
            blurb = text
            break
        if blurb in {"无", "—", ""}:
            blurb = str(why.get("one") or "")
        evidence_human: list[str] = []
        for er in card.get("evidence_refs") or []:
            if not isinstance(er, dict):
                continue
            lab = str(er.get("label") or "").strip()
            if not lab:
                lab = pretty_evidence_label(str(er.get("path") or ""), str(er.get("note") or ""))
            if lab and lab not in evidence_human:
                evidence_human.append(lab)
        devices.append(
            {
                "ref": obj,
                "bucket": bucket,
                "block": card.get("block_class"),
                "mpn": mpn,
                "value": ident.get("value_bom") or ident.get("value_pdf") or ident.get("value_tel") or "",
                "footprint": ident.get("footprint") or "",
                "nets": net_list,
                "p_w": computed.get("P_max_w"),
                "p_txt": ((card['review_detail'].get('primary_value_text','')+'（'+card['review_detail'].get('primary_label','')+'）') if (card.get('review_detail') or {}).get('primary_value_text') else (fmt_power(computed.get("P_max_w")) if computed.get("P_max_w") is not None else "")),
                "limit": card.get("limit"),
                "limit_txt": fmt_power(card.get("limit")) if card.get("limit") is not None else "",
                "v": after.get("v"),
                "stuck": why.get("one") or "",
                "headline_zh": str(card.get("headline_zh") or "").strip(),
                "summary": pretty_stuck_text(str(card.get("summary") or "")),
                "action": str(card.get("action") or "").strip(),
                "blurb": blurb,
                "assumptions": _assumption_rows([p["text"] for p in card.get("key_assumptions",[])] or card.get("assumptions")),
                "evidence_human": evidence_human[:12],
                "reason": why,
                "formula": formula_of(card),
                "review_detail": card.get("review_detail"),
                "decision_basis": card.get("decision_basis") or {},
                "fanout": card.get("fanout") if isinstance(card.get("fanout"), dict) else {},
                "pages": pages,
                "page_n": len(pages),
                "datasheet": ds_rel,
                "datasheet_pages": ds_pages,
                "datasheet_local": str(ds.get("local_path") or ""),
                "extracted": dict(extracted),
                "extract_note": extract_note,
                "extract_rows": extract_rows(extracted, extract_note, rule_id=rule_id),
                "phenomenon": card.get("phenomenon") or "",
                "conclusion": pretty_stuck_text(
                    str(card.get("conclusion") or card.get("summary") or "")
                ),
                "sheet_scope": scope,
            }
        )
    devices.sort(
        key=lambda d: (
            0 if d["bucket"] == "待确认" else 1 if d["bucket"] == "不通过" else 2,
            _ref_key(d["ref"]),
        )
    )
    return devices


def _resolve_schematic_pdf(out_dir: Path) -> Path | None:
    """sch_index → review_out/materials → 板配置相对路径 → sch/schematic.pdf。"""
    sch = _read_json(out_dir / "sch_index.json")
    mats = _read_json(out_dir / "materials.json")
    for raw in (sch.get("pdf"), mats.get("schematic_pdf")):
        if raw and is_file_safe(raw):
            return Path(str(raw))
    return board_schematic_pdf(out_dir.parent)


def load_workbench(out_dir: Path, root: Path) -> dict[str, Any]:
    kg = _read_json(out_dir / "board_kg.json")
    sch = _read_json(out_dir / "sch_index.json")
    comps = kg.get("components") if isinstance(kg.get("components"), dict) else {}
    net_pages = build_net_pages(comps)
    page_meta = page_meta_from_sch(sch)
    pdf = _resolve_schematic_pdf(out_dir)
    has_pdf = ensure_schematic_pdf(out_dir, pdf) if pdf else False
    if not has_pdf:
        has_pdf = (out_dir / "sch" / "schematic.pdf").is_file()
    n_img = render_schematic_jpgs(out_dir, pdf) if pdf else 0
    if n_img == 0:
        n_img = _reuse_preview_jpgs(out_dir, root)
    return {
        "comps": comps,
        "net_pages": net_pages,
        "page_meta": page_meta,
        "sheet_n": n_img,
        "schematic_pdf": SCHEMATIC_PDF_REL if has_pdf else "",
        "root": root,
        "out_dir": out_dir,
    }


def pack_extract_groups(devices: list[dict]) -> list[dict]:
    """Aggregate datasheet extracts by MPN for the detail-page module."""
    groups: dict[str, dict[str, Any]] = {}
    for d in devices:
        mpn = str(d.get("mpn") or "").strip() or "(无MPN)"
        g = groups.get(mpn)
        if g is None:
            g = {
                "mpn": mpn,
                "refs": [],
                "extracted": dict(d.get("extracted") or {}),
                "extract_note": d.get("extract_note") or "",
                "extract_rows": list(d.get("extract_rows") or []),
                "datasheet": d.get("datasheet") or "",
                "datasheet_pages": d.get("datasheet_pages") or 0,
                "has_extract": bool(d.get("extracted") or d.get("extract_note")),
            }
            groups[mpn] = g
        ref = d.get("ref") or ""
        if ref and ref not in g["refs"]:
            g["refs"].append(ref)
        # Prefer non-empty extract if later card has more fields
        if d.get("extracted") and len(d["extracted"]) >= len(g["extracted"]):
            g["extracted"] = dict(d["extracted"])
            g["extract_note"] = d.get("extract_note") or g["extract_note"]
            g["extract_rows"] = list(d.get("extract_rows") or extract_rows(g["extracted"], g["extract_note"]))
            g["has_extract"] = bool(g["extracted"] or g["extract_note"])
        if d.get("datasheet") and not g["datasheet"]:
            g["datasheet"] = d["datasheet"]
            g["datasheet_pages"] = d.get("datasheet_pages") or 0
    out = list(groups.values())
    out.sort(key=lambda x: (-len(x["refs"]), x["mpn"]))
    return out


def pack_devices(pack: dict, wb: dict[str, Any]) -> dict[str, Any]:
    cards = pack.get("object_cards") or []
    rid = str(pack.get("rule_id") or "")
    devices = devices_from_cards(
        cards,
        wb.get("comps") or {},
        wb.get("page_meta") or {},
        wb.get("net_pages") or {},
        wb["out_dir"],
        wb["root"],
        rule_id=rid,
    )
    counts = {
        "通过": sum(1 for d in devices if d["bucket"] == "通过"),
        "待确认": sum(1 for d in devices if d["bucket"] == "待确认"),
        "不通过": sum(1 for d in devices if d["bucket"] == "不通过"),
        "不适用": sum(1 for d in devices if d["bucket"] == "不适用"),
    }
    scope = sheet_scope_of(rid)
    sch_pdf = str(wb.get("schematic_pdf") or "")
    return {
        "rule_id": rid,
        "sheet_scope": scope,
        "sheet_hint": sheet_hint_of(rid, scope),
        "schematic_pdf": sch_pdf,
        "n": len(devices),
        "counts": counts,
        "devices": devices,
    }
