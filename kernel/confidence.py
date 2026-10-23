# -*- coding: utf-8 -*-
"""判定置信度：证据完备度计分，禁止模型自报。"""

from __future__ import annotations

from typing import Any

DS_FAMILY = {
    "P11-A01",
    "P11-A03",
    "P11-A07",
    "P12-A01",
    "P14-A02",
    "P14-A06",
    "P16-A01",
    "P18-A02",
}

MEANING = "对本项合并后判定词的证据完备程度，不是模型自我感觉"


def _insuf(z: str) -> bool:
    return str(z).startswith("INSUFFICIENT") or z == "待确认"


def _round5(n: int) -> int:
    return int(5 * round(n / 5.0))


def _band(score: int) -> str:
    if score >= 80:
        return "高"
    if score >= 50:
        return "中"
    return "低"


def _ds_pages(pack: dict[str, Any]) -> bool:
    used = pack.get("datasheets_used") or []
    if not isinstance(used, list):
        return False
    for it in used:
        if not isinstance(it, dict):
            continue
        pages = it.get("pages") or it.get("page")
        path = it.get("local_path") or it.get("path")
        if path and pages:
            return True
    return False


def _used_candidate(result: dict[str, Any], pack: dict[str, Any]) -> bool:
    blob = " ".join(
        [
            " ".join(str(x) for x in (result.get("measured") or [])),
            " ".join(str(x) for x in (result.get("notes") or [])),
            " ".join(str(x) for x in (pack.get("measured") or [])),
        ]
    ).lower()
    return "candidate" in blob


def score_confidence(
    *,
    rule_id: str,
    merged_zh: str,
    prog_zh: str,
    agent_zh: str | None,
    agent_ready: bool,
    skip_sdk: bool,
    blocked_green: bool,
    coverage_n: Any,
    pack: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    score = 0
    objects_ok = bool(coverage_n) or merged_zh == "不适用" or bool(result.get("objects"))
    if objects_ok:
        score += 15
        reasons.append("已枚举对象或本项不适用")
    if prog_zh in {"是", "否", "不适用"}:
        score += 25
        reasons.append("程序词已闭合")
    elif _insuf(prog_zh):
        score += 5
        reasons.append("程序待确认")
    if agent_ready and not skip_sdk:
        score += 15
        reasons.append("Agent已填证据包")
    if agent_ready and agent_zh and prog_zh and agent_zh == prog_zh:
        score += 15
        reasons.append("程序与Agent词一致")
    need_ds = rule_id in DS_FAMILY
    if need_ds and _ds_pages(pack):
        score += 15
        reasons.append("规格书路径+页码")
    if not _used_candidate(result, pack):
        score += 10
        reasons.append("未把网名candidate当闭合电压")
    else:
        reasons.append("证据含candidate电压")

    caps: list[str] = []
    if merged_zh == "是":
        if prog_zh != "是":
            score = min(score, 40)
            caps.append("是但程序未闭合→上限40")
        if need_ds and not _ds_pages(pack):
            score = min(score, 45)
            caps.append("是但缺手册页→上限45")
    if _insuf(merged_zh):
        score = min(score, 50)
        caps.append("待确认→上限50")
    if merged_zh == "否" and prog_zh == "否":
        if score < 70:
            score = 70
            caps.append("程序否→下限70")
    if merged_zh == "否" and prog_zh != "否":
        score = min(score, 75)
        caps.append("仅Agent否→上限75")
    if merged_zh == "不适用" and not coverage_n:
        if score < 75:
            score = 75
        score = min(score, 90)
        caps.append("不适用且对象空→75-90")
    if blocked_green:
        score = min(score, 40)
        caps.append("合并拦升绿→上限40")
    if _used_candidate(result, pack):
        score = min(score, 45)
        caps.append("candidate电压→上限45")

    score = max(0, min(100, _round5(score)))
    reasons.extend(caps)
    return {
        "score": score,
        "band": _band(score),
        "reasons": reasons,
        "meaning": MEANING,
    }


def summarize_confidence(results: list[dict[str, Any]]) -> dict[str, Any]:
    scores = []
    bands = {"高": 0, "中": 0, "低": 0}
    low_ids = []
    for r in results:
        conf = r.get("confidence") or {}
        s = conf.get("score")
        if isinstance(s, int):
            scores.append(s)
        b = conf.get("band")
        if b in bands:
            bands[b] += 1
        if b == "低":
            low_ids.append(r.get("rule_id"))
    avg = int(round(sum(scores) / len(scores))) if scores else 0
    return {
        "avg": avg,
        "bands": bands,
        "low_rule_ids": low_ids,
    }
