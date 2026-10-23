# -*- coding: utf-8 -*-
"""报告展示用语：系统路径 → 业务可懂说法。"""

from __future__ import annotations

import re


def humanize_report_text(s: str) -> str:
    """把系统文件路径/内部字段改成业务可懂说法（仅展示层）。"""
    text = str(s or "")
    if not text:
        return text
    text = re.sub(
        r"工作台\s*[A-Za-z0-9_+.-]+\.context\.json\s*为空壳",
        "本对象审图材料未写出有效内容",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"[A-Za-z0-9_+.-]+\.context\.json",
        "本对象审图材料",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"review_out/agent_jobs/[^/\s，,;；]+/[^/\s，,;；]+\.analysis\.md",
        "本对象审图分析",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"review_out/agent_jobs/[^/\s，,;；]+/[^/\s，,;；]+\.context\.json",
        "本对象审图材料",
        text,
        flags=re.I,
    )
    def _strip_json(name: str) -> str:
        return re.sub(r"\.json$", "", name or "", flags=re.I)

    text = re.sub(
        r"review_out/context_store/nets/([^\s/\"'，,;；]+)",
        lambda m: f"原理图网络 {_strip_json(m.group(1))}",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"review_out/context_store/objects/([^\s/\"'，,;；]+)",
        lambda m: f"器件 {_strip_json(m.group(1))} 脚网关系",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"review_out/context_store/mpn/([^\s/\"'，,;；]+)",
        lambda m: f"料号 {_strip_json(m.group(1))} 密封参数",
        text,
        flags=re.I,
    )
    text = re.sub(r"review_out/design_task\.json", "设计任务书", text, flags=re.I)
    text = re.sub(r"review_out/program_candidate\.json", "程序检查结果", text, flags=re.I)
    text = re.sub(r"review_out/program_stress/[^\s，,;；]+", "程序应力试算", text, flags=re.I)
    # 内部备忘词 → 业务话（勿把「程序应力包」原样给人看）
    text = re.sub(r"未引用程序应力包[。.]?", "", text)
    text = re.sub(r"程序应力包", "内部试算（不作依据）", text)
    text = re.sub(r"\binterface_specs\b", "接口规格", text, flags=re.I)
    text = re.sub(r"rail\s*unknown", "轨压未闭合", text, flags=re.I)
    text = re.sub(r"密封\s*([A-Za-z0-9_./+-]+)\s*rail\s*unknown", r"\1 轨压未闭合", text, flags=re.I)
    text = re.sub(r"review_out/board_kg\.json", "原理图解析", text, flags=re.I)
    text = re.sub(
        r"(?:[A-Za-z]:)?[^\"'\s]*?(?:review_out/)?cache/mpn/([^/\s\"']+)/datasheet\.pdf",
        r"料号 \1 规格书",
        text,
        flags=re.I,
    )
    text = re.sub(r"board_kg(?:\.json)?\s*/?\s*pintable", "料号参数表", text, flags=re.I)
    text = re.sub(r"\bboard_kg(?:\.json)?\b", "原理图解析", text, flags=re.I)
    text = re.sub(r"\bpintable\b", "料号参数表", text, flags=re.I)
    text = re.sub(r"\bcontext_store\b", "板级密封数据", text, flags=re.I)
    text = re.sub(r"\breview_out/[^\s，,;；]+", "评审材料", text)
    text = re.sub(
        r"(?:[A-Za-z0-9_+.-]+\.rail\s*/\s*)*[A-Za-z0-9_+.-]+\.rail\s*=\s*unknown",
        "相关网络轨压未闭合",
        text,
        flags=re.I,
    )
    text = re.sub(r"\brail\s*=\s*unknown\b", "轨压未闭合", text, flags=re.I)
    text = re.sub(r"\brail\s+unknown\b", "轨压未闭合", text, flags=re.I)
    text = re.sub(r"密封轨\s+unknown", "轨压未闭合", text, flags=re.I)
    text = re.sub(r"\brail\.status\s*=\s*closed\b", "轨压已闭合", text, flags=re.I)
    text = re.sub(r"\brail\s+closed\s*=\s*", "轨压 ", text, flags=re.I)
    text = re.sub(r"\brail(?:\.value)?\s*=\s*", "轨压 ", text, flags=re.I)
    text = re.sub(r"[A-Za-z0-9_+.-]+\.rail\b", "该网轨压", text)
    text = re.sub(r"components\.([A-Za-z0-9_+.-]+)", r"器件 \1", text)
    text = re.sub(r"工作台(?!\s*材料)", "审图材料", text)
    text = re.sub(r"datasheet\.extracted\s*为空", "规格书参数尚未抽出", text, flags=re.I)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def pretty_evidence_label(path: str, note: str = "") -> str:
    note_s = humanize_report_text(str(note or "")).strip()
    path_s = humanize_report_text(str(path or "")).strip()
    if note_s and path_s and path_s not in note_s:
        if path_s in {
            "本对象审图材料",
            "本对象审图分析",
            "原理图解析",
            "设计任务书",
            "程序检查结果",
            "程序应力试算",
        } or path_s.startswith(("原理图网络", "器件 ", "料号 ")):
            return f"{path_s}：{note_s}"
        return note_s
    return note_s or path_s or "审图依据"
