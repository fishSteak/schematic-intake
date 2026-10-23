# -*- coding: utf-8 -*-
"""Object-card helpers: 四格（现象/计算/卡点/结论）+ 公共壳。"""

from __future__ import annotations

from typing import Any

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


def loc_card(c: dict[str, Any]) -> dict[str, Any]:
    locs = c.get("location") or []
    if isinstance(locs, dict):
        locs = [locs]
    if not locs:
        return {"pdf_page": None, "page_name": "", "module_title": "", "note": "无嘉立创坐标"}
    loc = locs[0] if isinstance(locs[0], dict) else {}
    return {
        "pdf_page": loc.get("pdf_page"),
        "page_name": loc.get("pageName") or "",
        "module_title": loc.get("module_title") or "",
        "note": "",
    }


def identity_card(c: dict[str, Any]) -> dict[str, Any]:
    src = c.get("sources") or {}
    return {
        "mpn": c.get("mpn") or "",
        "value_pdf": (src.get("value_jlc") or {}).get("value") or c.get("value") or "",
        "value_bom": (src.get("value_bom") or {}).get("value") or "",
        "value_tel": (src.get("value_tel") or {}).get("value") or "",
        "footprint": c.get("footprint") or "",
    }


def datasheet_card(c: dict[str, Any]) -> dict[str, Any]:
    pt = c.get("pintable") or {}
    params = pt.get("params") or {}
    extracted: dict[str, Any] = {}
    for k in (
        "p_rated_w",
        "p_rated_temp_c",
        "vz_max_v",
        "rated_voltage_v",
        "vf_v",
        "vce_sat_v",
        "vbe_v",
        "rds_on_ohm",
        "isat",
        "cl_pf",
        "t_max_c",
        "delta_t",
        "dt_c",
    ):
        if params.get(k) is not None:
            extracted[k] = params[k]
    note = params.get("extract_note") or params.get("p_rated_source") or params.get("vz_source") or ""
    return {
        "url": "",
        "local_path": pt.get("pdf") or "",
        "extracted": extracted,
        "extract_note": note,
    }


def loc_str(loc: dict[str, Any]) -> str:
    return " / ".join(
        str(x)
        for x in (
            f"p{loc.get('pdf_page')}" if loc.get("pdf_page") is not None else "",
            loc.get("page_name") or "",
            loc.get("module_title") or "",
        )
        if x
    )


def fill_four(
    card: dict[str, Any],
    *,
    phenomenon: str,
    calc: str,
    stuck: str,
    conclusion: str,
) -> dict[str, Any]:
    """Write 四格；summary 用结论，禁止只回声原因码裸词。"""
    card["phenomenon"] = (phenomenon or "").strip() or "（未写现象）"
    card["calc"] = (calc or "").strip() or "未列方程"
    card["stuck"] = (stuck or "").strip() or "无"
    card["conclusion"] = (conclusion or "").strip() or str(card.get("verdict_zh") or "待确认")
    card["summary"] = card["conclusion"]
    if not card.get("solution"):
        from kernel.solution import gate, pack_solution, render_solution

        zh = str(card.get("verdict_zh") or "待确认")
        if zh == "INSUFFICIENT":
            zh = "待确认"
        wait = zh not in {"是", "否", "不适用"}
        ok: bool | None
        if zh == "是":
            ok = True
        elif zh == "否":
            ok = False
        else:
            ok = None
        sol = pack_solution(
            given=[],
            gates=[gate("判定", ok, card["calc"], wait=wait)],
            rule="任一不达标 → 整卡 否；缺证据不判否",
            verdict_zh=zh,
            stuck=card["stuck"] if wait else None,
        )
        card["solution"] = sol
        card["formula"] = render_solution(sol)
    from kernel.cause import attach_cause

    attach_cause(card)
    return card


def base_card(
    obj: str,
    c: dict[str, Any] | None = None,
    *,
    verdict_zh: str = "待确认",
    block_class: str | None = None,
) -> dict[str, Any]:
    c = c or {}
    return {
        "object": obj,
        "location": loc_card(c) if c else {"pdf_page": None, "page_name": "", "module_title": "", "note": ""},
        "identity": identity_card(c) if c else {"mpn": "", "value_pdf": "", "value_bom": "", "value_tel": "", "footprint": ""},
        "datasheet": datasheet_card(c) if c else {"url": "", "local_path": "", "extracted": {}, "extract_note": ""},
        "verdict_zh": verdict_zh,
        "block_class": block_class,
        "phenomenon": "",
        "calc": "",
        "stuck": "",
        "conclusion": "",
        "summary": "",
    }


def na_explain_card(rule_id: str, reason: str) -> dict[str, Any]:
    """无对象时的项级说明卡。"""
    card = base_card(f"({rule_id})", verdict_zh="不适用", block_class="not_in_scope")
    return fill_four(
        card,
        phenomenon=reason,
        calc="无装配对象，不进入比较",
        stuck="无",
        conclusion=f"不适用 · {reason}",
    )


def ensure_cards_nonempty(cr: Any, rule_id: str, reason_if_empty: str) -> None:
    """保证 CheckResult.object_cards 非空。"""
    if getattr(cr, "object_cards", None):
        for card in cr.object_cards:
            if not card.get("phenomenon") and not card.get("calc"):
                # backfill from summary if legacy
                fill_four(
                    card,
                    phenomenon=str(card.get("summary") or card.get("object") or ""),
                    calc=str(card.get("formula") or "见公式/步骤"),
                    stuck="无" if card.get("verdict_zh") in {"是", "否", "不适用"} else str(card.get("block_class") or "未写卡点"),
                    conclusion=str(card.get("summary") or card.get("verdict_zh") or ""),
                )
        return
    if cr.verdict_zh == "不适用":
        cr.object_cards = [na_explain_card(rule_id, reason_if_empty)]
    else:
        card = base_card(f"({rule_id})", verdict_zh=cr.verdict_zh or "待确认")
        fill_four(
            card,
            phenomenon=reason_if_empty,
            calc="项级无逐对象展开",
            stuck="无" if cr.verdict_zh in {"是", "否"} else "项级未展开对象卡",
            conclusion=f"{cr.verdict_zh} · {reason_if_empty}",
        )
        cr.object_cards = [card]
