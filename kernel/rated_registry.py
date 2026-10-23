# -*- coding: utf-8 -*-
"""检查项 → 额定字段 → 抽取策略注册表。

允许不同 check 用不同抽取逻辑；材料侧按器件类型补参，检查项按表消费。
策略标签：
  hard     — PDF / 立创 / 网页（可密封）
  mpn_code — 料号编码线索（仅电容 Vr 等）
  agent    — 硬出处空时 Agent 查参（须 cite）
  stress   — 工况应力（非规格书额定，本表不抽）
"""

from __future__ import annotations

from typing import Any

# 器件类型默认要争取的额定字段（s5 apply_rated_fields）
TYPE_FIELDS: dict[str, list[str]] = {
    "capacitor": ["rated_voltage_v", "t_op_min_c", "t_op_max_c"],
    "resistor": ["p_rated_w", "rated_voltage_v", "t_op_min_c", "t_op_max_c"],
    "inductor": ["isat", "t_max_c", "t_op_min_c", "t_op_max_c"],
    "crystal": ["cl_pf", "t_op_min_c", "t_op_max_c"],
    "ic": [
        "recommended.vcc_min",
        "recommended.vcc_max",
        "abs_max.vcc_max",
        "voh_min_v",
        "vol_max_v",
        "vih_min_v",
        "vil_max_v",
        "t_op_min_c",
        "t_op_max_c",
    ],
    "transistor": ["vceo_v", "ic_max_a", "p_rated_w", "t_op_min_c", "t_op_max_c", "t_max_c"],
    "mosfet": ["vds_rated_v", "id_rated_a", "p_rated_w", "t_op_min_c", "t_op_max_c"],
    "diode": ["vz_max_v", "vf_v", "p_rated_w", "t_op_min_c", "t_op_max_c"],
    "led": ["t_op_min_c", "t_op_max_c"],
    "fuse": ["i_rated_a", "t_op_min_c", "t_op_max_c"],
    "connector": ["i_rated_a", "mating_cycles", "ip_rating", "t_op_min_c", "t_op_max_c"],
    "header": ["i_rated_a", "mating_cycles", "ip_rating", "t_op_min_c", "t_op_max_c"],
}

# 检查项消费哪些字段、用什么策略（文档 + 驱动 Agent 白名单）
RULE_RATED: dict[str, dict[str, Any]] = {
    "P11-A01": {
        "title": "电阻稳态耗散 vs 额定功率",
        "comp_types": ["resistor"],
        "fields": [
            {
                "field": "p_rated_w",
                "strategies": ["hard", "agent"],
                "note": "立创/手册功率；无 MLCC 式料号码",
            }
        ],
        "stress_fields": ["P_max"],
    },
    "P11-A02": {
        "title": "高压回路电阻工作电压 vs 额定耐压",
        "comp_types": ["resistor"],
        "fields": [
            {"field": "rated_voltage_v", "strategies": ["hard", "agent"]},
        ],
        "stress_fields": ["V_max"],
    },
    "P11-A03": {
        "title": "电容工作电压 vs 额定耐压",
        "comp_types": ["capacitor"],
        "fields": [
            {
                "field": "rated_voltage_v",
                "strategies": ["hard", "mpn_code", "agent"],
                "special": "capacitor_vr_waterfall",
                "note": "专用瀑布 apply_capacitor_vr",
            }
        ],
        "stress_fields": ["V_max"],
    },
    "P11-A07": {
        "title": "电感饱和/热",
        "comp_types": ["inductor"],
        "fields": [
            {"field": "isat", "strategies": ["hard", "agent"], "note": "禁止用 Irms 冒充"},
            {"field": "t_max_c", "strategies": ["hard", "agent"]},
        ],
        "stress_fields": ["I_peak", "I_rms", "delta_t"],
    },
    "P11-A06": {
        "title": "电感饱和电流 vs 最大工作电流",
        "comp_types": ["inductor"],
        "fields": [
            {"field": "isat", "strategies": ["hard", "agent"], "note": "禁止用 Irms 冒充"},
        ],
        "stress_fields": ["I_peak"],
    },
    "P12-A01": {
        "title": "电源/IC 轨电压适用",
        "comp_types": ["ic"],
        "fields": [
            {"field": "recommended.vcc_min", "strategies": ["hard", "agent"]},
            {"field": "recommended.vcc_max", "strategies": ["hard", "agent"]},
            {"field": "abs_max.vcc_max", "strategies": ["hard", "agent"]},
        ],
        "stress_fields": ["V_rail"],
    },
    "P12-A02": {
        "title": "逻辑电平匹配（程序）",
        "comp_types": ["ic"],
        "fields": [
            {"field": "voh_min_v", "strategies": ["hard", "agent"]},
            {"field": "vol_max_v", "strategies": ["hard", "agent"]},
            {"field": "vih_min_v", "strategies": ["hard", "agent"]},
            {"field": "vil_max_v", "strategies": ["hard", "agent"]},
        ],
        "stress_fields": [],
    },
    "P12-A03": {
        "title": "工作温度",
        "comp_types": ["*"],
        "fields": [
            {"field": "t_op_min_c", "strategies": ["hard", "agent"]},
            {"field": "t_op_max_c", "strategies": ["hard", "agent"]},
        ],
        "stress_fields": ["T_amb"],
        "note": "多器件类；任务书环境温度属 stress",
    },
    "P12-A04": {
        "title": "MOS Vds vs 额定",
        "comp_types": ["mosfet", "transistor"],
        "fields": [
            {"field": "vds_rated_v", "strategies": ["hard", "agent"]},
        ],
        "stress_fields": ["VDS_peak"],
    },
    "P12-A05": {
        "title": "MOS Ids vs 额定",
        "comp_types": ["mosfet", "transistor"],
        "fields": [
            {"field": "id_rated_a", "strategies": ["hard", "agent"]},
        ],
        "stress_fields": ["ID_max"],
    },
    "P12-A06": {
        "title": "TVS VRWM/钳位",
        "comp_types": ["diode", "tvs"],
        "fields": [
            {"field": "vrwm_v", "strategies": ["hard", "agent"]},
            {"field": "vc_v", "strategies": ["hard", "agent"]},
            {"field": "ipp_a", "strategies": ["hard", "agent"]},
        ],
        "stress_fields": ["V_normal", "V_clamp_peak"],
    },
    "P12-A07": {
        "title": "连接器额定电流/寿命/防护",
        "comp_types": ["connector", "header"],
        "fields": [
            {"field": "i_rated_a", "strategies": ["hard", "agent"]},
            {"field": "mating_cycles", "strategies": ["hard", "agent"]},
            {"field": "ip_rating", "strategies": ["hard", "agent"]},
        ],
        "stress_fields": ["I_load"],
        "note": "寿命/IP 常为字符串，Agent 更适合",
    },
    "P21-A01": {
        "title": "电平匹配（与 P12-A02 同类字段）",
        "comp_types": ["ic"],
        "fields": [
            {"field": "voh_min_v", "strategies": ["hard", "agent"]},
            {"field": "vol_max_v", "strategies": ["hard", "agent"]},
            {"field": "vih_min_v", "strategies": ["hard", "agent"]},
            {"field": "vil_max_v", "strategies": ["hard", "agent"]},
        ],
        "stress_fields": [],
    },
}

# 字段 → 人话 / 单位（报告与 Agent prompt）
FIELD_META: dict[str, dict[str, str]] = {
    "rated_voltage_v": {"zh": "额定耐压", "unit": "V"},
    "p_rated_w": {"zh": "额定功率", "unit": "W"},
    "isat": {"zh": "饱和电流 Isat", "unit": "A"},
    "t_max_c": {"zh": "最高温度", "unit": "℃"},
    "t_op_min_c": {"zh": "工作温度下限", "unit": "℃"},
    "t_op_max_c": {"zh": "工作温度上限", "unit": "℃"},
    "recommended.vcc_min": {"zh": "推荐 VCC 最小", "unit": "V"},
    "recommended.vcc_max": {"zh": "推荐 VCC 最大", "unit": "V"},
    "abs_max.vcc_max": {"zh": "绝对最大 VCC", "unit": "V"},
    "voh_min_v": {"zh": "VOH min", "unit": "V"},
    "vol_max_v": {"zh": "VOL max", "unit": "V"},
    "vih_min_v": {"zh": "VIH min", "unit": "V"},
    "vil_max_v": {"zh": "VIL max", "unit": "V"},
    "vds_rated_v": {"zh": "额定 VDS", "unit": "V"},
    "id_rated_a": {"zh": "额定 ID", "unit": "A"},
    "vceo_v": {"zh": "VCEO", "unit": "V"},
    "ic_max_a": {"zh": "IC max", "unit": "A"},
    "vrwm_v": {"zh": "VRWM", "unit": "V"},
    "vc_v": {"zh": "钳位电压 VC", "unit": "V"},
    "ipp_a": {"zh": "Ipp", "unit": "A"},
    "vz_max_v": {"zh": "齐纳 Vz max", "unit": "V"},
    "vf_v": {"zh": "正向 Vf", "unit": "V"},
    "i_rated_a": {"zh": "额定电流", "unit": "A"},
    "mating_cycles": {"zh": "插拔寿命", "unit": "次"},
    "ip_rating": {"zh": "防护等级", "unit": ""},
    "cl_pf": {"zh": "负载电容 CL", "unit": "pF"},
}


def fields_for_type(comp_type: str) -> list[str]:
    return list(TYPE_FIELDS.get(str(comp_type or ""), []) or [])


def fields_for_rule(rule_id: str) -> list[str]:
    spec = RULE_RATED.get(str(rule_id or "")) or {}
    return [str(x.get("field")) for x in (spec.get("fields") or []) if x.get("field")]


def agent_fields_for_type(comp_type: str) -> list[str]:
    """该器件类型上允许 Agent 查参兜底的字段。

    并集：TYPE_FIELDS（材料默认要抽的）+ RULE_RATED 中带 agent 策略的字段。
    避免「wanted 有、硬路径空、却进不了 Agent 队列」（如三极管 Vceo/Ic）。
    """
    typ = str(comp_type or "")
    out: list[str] = []
    for f in TYPE_FIELDS.get(typ) or []:
        if f not in out:
            out.append(f)
    for rid, spec in RULE_RATED.items():
        types = spec.get("comp_types") or []
        if "*" not in types and typ not in types:
            continue
        for item in spec.get("fields") or []:
            if "agent" in (item.get("strategies") or []) and item.get("field"):
                f = str(item["field"])
                if f not in out:
                    out.append(f)
    return out


def rule_cover_summary() -> list[dict[str, Any]]:
    rows = []
    for rid, spec in RULE_RATED.items():
        rows.append(
            {
                "rule_id": rid,
                "title": spec.get("title"),
                "comp_types": spec.get("comp_types"),
                "rated_fields": fields_for_rule(rid),
                "stress_fields": spec.get("stress_fields") or [],
                "note": spec.get("note") or "",
            }
        )
    return rows
