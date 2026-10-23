# -*- coding: utf-8 -*-
"""对象卡原因层：本项卡点另写；仅工程输入缺失时追加附录。

layer=input 才算工程缺项（BOM/网表/标注/任务书/工况）。
extract / solve / rule 不是工程缺项，不改写、不前置。
"""
from __future__ import annotations

import re
from typing import Any

from kernel.rated_hunt import is_placeholder_mpn

SOURCE_ZH = {
    "bom": "BOM",
    "tel": "网表",
    "sch": "原理图标注",
    "taskbook": "任务书",
    "oc": "工况",
}

_PLACEHOLDER_VALUE = re.compile(r"^(tbd|nc|n/?a|cap|res)$", re.I)


def is_placeholder_value(raw: str) -> bool:
    s = (raw or "").strip()
    return (not s) or bool(_PLACEHOLDER_VALUE.match(s))


def _identity(card: dict[str, Any]) -> tuple[str, str]:
    ident = card.get("identity") if isinstance(card.get("identity"), dict) else {}
    mpn = str(ident.get("mpn") or "").strip()
    value = str(
        ident.get("value_bom") or ident.get("value_tel") or ident.get("value_pdf") or ""
    ).strip()
    return mpn, value


def _unclosed_nets(card: dict[str, Any]) -> list[str]:
    before = card.get("before") if isinstance(card.get("before"), dict) else {}
    out: list[str] = []
    for net, rec in before.items():
        if not net:
            continue
        if not isinstance(rec, dict):
            out.append(str(net))
            continue
        if rec.get("status") == "closed" or rec.get("value") is not None:
            continue
        out.append(str(net))
    return out


def _has_extracted_rating(card: dict[str, Any]) -> bool:
    ds = card.get("datasheet") if isinstance(card.get("datasheet"), dict) else {}
    ext = ds.get("extracted") if isinstance(ds.get("extracted"), dict) else {}
    return any(v is not None and v != "" for v in ext.values())


def _miss_blob(card: dict[str, Any]) -> str:
    bits = [str(card.get("stuck") or ""), str(card.get("conclusion") or "")]
    for x in card.get("missing_fields") or []:
        bits.append(str(x))
    sol = card.get("solution") if isinstance(card.get("solution"), dict) else {}
    for g in sol.get("gates") or []:
        if isinstance(g, dict):
            bits.append(str(g.get("name") or ""))
            bits.append(str(g.get("detail") or ""))
    return " ".join(bits)


def _record(
    *,
    layer: str,
    source: str = "",
    engineering_gap: bool,
    appendix: str = "",
) -> dict[str, Any]:
    return {
        "layer": layer,
        "source": source,
        "engineering_gap": bool(engineering_gap),
        "appendix": appendix if engineering_gap else "",
    }


def classify_cause(card: dict[str, Any]) -> dict[str, Any]:
    """不按 rule_id 分支。通过/不适用不标工程缺项。"""
    zh = str(card.get("verdict_zh") or "")
    if zh in {"是", "PASS", "不适用", "NOT_APPLICABLE", "N/A"}:
        return _record(layer="", engineering_gap=False)

    mpn, value = _identity(card)
    bc = str(card.get("block_class") or "")
    stuck = str(card.get("stuck") or "").strip()

    if (mpn and is_placeholder_mpn(mpn)) or (value and is_placeholder_value(value)):
        got_bits = []
        if mpn:
            got_bits.append(f"mpn={mpn}")
        if value:
            got_bits.append(f"value={value}")
        got = "，".join(got_bits) or "空"
        return _record(
            layer="input",
            source="bom",
            engineering_gap=True,
            appendix=f"工程输入：BOM/网表未写真实料号（{got}）",
        )

    if bc == "miss_taskbook":
        blob = stuck + _miss_blob(card)
        oc_hit = any(k in blob for k in ("Tamb", "工况", "ΔT", "C_par", "I_sat_cmp", "i_work"))
        src = "oc" if oc_hit else "taskbook"
        src_zh = SOURCE_ZH[src]
        field = (stuck or "所需字段").strip()
        prefix = f"{src_zh}未写"
        appendix = f"工程输入：{field}" if field.startswith(src_zh) else f"工程输入：{prefix}{field}"
        return _record(
            layer="input",
            source=src,
            engineering_gap=True,
            appendix=appendix,
        )

    blob = _miss_blob(card)
    if bc == "device_irregular" or "缺精度" in blob or "缺介质" in blob:
        if "缺精度" in blob:
            field = "精度"
        elif "缺介质" in blob:
            field = "介质"
        elif "缺封装" in blob:
            field = "封装"
        else:
            field = "标注"
        got = value or "空"
        return _record(
            layer="input",
            source="sch",
            engineering_gap=True,
            appendix=f"工程输入：原理图标注/BOM未写{field}（value={got}）",
        )

    if bc in {"miss_stress", "topology_open"} or _unclosed_nets(card):
        return _record(layer="solve", engineering_gap=False)

    if bc == "miss_datasheet" or (mpn and not _has_extracted_rating(card) and "未抽出" in (stuck + blob)):
        return _record(layer="extract", engineering_gap=False)

    if bc == "rule_gap":
        return _record(layer="rule", engineering_gap=False)

    if bc == "miss_datasheet":
        return _record(layer="extract", engineering_gap=False)

    return _record(layer="", engineering_gap=False)


def attach_cause(card: dict[str, Any]) -> dict[str, Any]:
    card["cause"] = classify_cause(card)
    return card
