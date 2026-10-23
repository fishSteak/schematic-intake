# -*- coding: utf-8 -*-
"""一期新增四项。配方为空时只枚举对象并待确认，不启发式升绿。"""

from __future__ import annotations

import re
from typing import Any

from kernel.cause import attach_cause
from kernel.checkers import CheckResult
from kernel.checkers.cards import base_card, fill_four, na_explain_card
from kernel.solution import apply_solution, gate, pack_solution
from kernel.source_cite import cite_param

_MISSED = "未抽出：程序已走规格书、立创、联网"

_ANA_NET = re.compile(r"AVDD|AGND|AREF|VREF|ANALOG|VCCA|VDDA|AVCC", re.I)
_ANA_PIN = re.compile(r"AVDD|AVCC|VCCA|VDDA|AGND|AREF|VREF", re.I)
_BEAD = re.compile(r"磁珠|ferrite|bead|\bBLM\b|\bMPZ\b", re.I)
_RESET = re.compile(r"(^|_)(N?RST|RESET|NRST|POR|NRESET)(_|$)|复位", re.I)
_SURGE = re.compile(r"TVS|ESD|SMBJ|SMAJ|varistor|压敏|气体放电|GDT", re.I)

_RULE_WAIT = "缺命名配方或关键额定未抽出 → 整卡待确认；不得无配方升绿"


def _sol(card: dict[str, Any], sol: dict[str, Any], phen: str, stuck: str) -> dict[str, Any]:
    apply_solution(card, sol, phenomenon=phen, stuck=stuck, fill_four=fill_four)
    attach_cause(card)
    return card


def _wait(
    obj: str,
    comp: dict[str, Any] | None,
    *,
    phen: str,
    stuck: str,
    given: list[dict[str, str]],
    detail: str,
    block: str,
) -> dict[str, Any]:
    sol = pack_solution(
        given=given,
        gates=[gate("配方", None, detail, wait=True)],
        rule=_RULE_WAIT,
        verdict_zh="待确认",
    )
    card = base_card(obj, comp, verdict_zh="待确认", block_class=block)
    return _sol(card, sol, phen, stuck)


def _helpers():
    from kernel.checkers import all15 as a

    return a


def check_p16_a05(kg: dict) -> CheckResult:
    """模拟电源隔离：只列出模拟轨/磁珠。无噪声预算、无配方 → 待确认。无对象 → 不适用。"""
    a = _helpers()
    cr = CheckResult("P16-A05", "不适用", title="模拟电源 RC/磁珠隔离", card_class="path_topo")
    cards: list[dict[str, Any]] = []
    beads = []
    for ref, c in a._comps(kg).items():
        if a._dnp(c):
            continue
        blob = " ".join(str(x or "") for x in (ref, c.get("mpn"), c.get("value"), c.get("type")))
        if _BEAD.search(blob):
            beads.append((ref, c))
    ana_nets = []
    for name, nd in a._nets(kg).items():
        if name and _ANA_NET.search(name) and not a._INTERNAL_NET.match(name):
            ana_nets.append(name)
    ana_pins = []
    for ref, c in a._assembled(kg, "ic"):
        for pin, net in a._pin_nets(c):
            if pin and _ANA_PIN.search(pin):
                ana_pins.append((ref, c, pin, net))
    cr.add_step("列模拟电源与隔离件", f"bead={len(beads)} ana_net={len(ana_nets)} ana_pin={len(ana_pins)}", n=1)
    if not beads and not ana_nets and not ana_pins:
        cr.verdict_zh = "不适用"
        cr.notes = ["网名、引脚名、料号都没有模拟电源或磁珠，本项无对象"]
        cr.object_cards = [na_explain_card("P16-A05", "无模拟电源轨、无模拟电源脚、无磁珠")]
        return cr
    budget = (a._task(kg).get("noise_budget") or a._oc(kg).get("noise_budget") or "")
    for ref, c in beads:
        pins = [(p, n) for p, n in a._pin_nets(c) if n]
        nets = " / ".join(n for _, n in pins) or "未读到两端网"
        given = [
            {"sym": "隔离件", "val": ref, "cite": "料号/位号含磁珠"},
            {"sym": "两端网", "val": nets, "cite": "网表"},
            {"sym": "噪声预算", "val": str(budget) if budget else "缺", "cite": "任务书/工况"},
            {"sym": "配方", "val": "空", "cite": "topology_recipes 本项子集为空"},
        ]
        cards.append(
            _wait(
                ref,
                c,
                phen=f"{ref} 接在 {nets}",
                stuck="任务书未写噪声预算，且没有命名配方把 RC/磁珠判成隔离成立",
                given=given,
                detail="磁珠在网上，不等于按噪声预算完成隔离",
                block="miss_taskbook" if not budget else "rule_gap",
            )
        )
        cards[-1]["path_refs"] = [ref]
    for net in ana_nets:
        given = [
            {"sym": "模拟网", "val": net, "cite": "网名"},
            {"sym": "噪声预算", "val": str(budget) if budget else "缺", "cite": "任务书/工况"},
            {"sym": "配方", "val": "空", "cite": "topology_recipes 本项子集为空"},
        ]
        card = _wait(
            net,
            None,
            phen=f"网 {net} 被识别为模拟电源网",
            stuck="噪声预算未写，不能判断这根网要不要 RC 或磁珠",
            given=given,
            detail="网名像模拟电源，不据此判通过或不通过",
            block="miss_taskbook",
        )
        card["net_name"] = net
        cards.append(card)
    for ref, c, pin, net in ana_pins:
        given = [
            {"sym": "脚", "val": f"{ref}.{pin}", "cite": "引脚名"},
            {"sym": "网", "val": net or "缺", "cite": "网表"},
            {"sym": "噪声预算", "val": str(budget) if budget else "缺", "cite": "任务书/工况"},
            {"sym": "配方", "val": "空", "cite": "topology_recipes 本项子集为空"},
        ]
        cards.append(
            _wait(
                f"{ref}.{pin}",
                c,
                phen=f"{ref} 脚 {pin} 网 {net or '未连接'}",
                stuck="模拟电源脚未对上噪声预算和隔离件",
                given=given,
                detail="引脚名像模拟电源，不据此判通过或不通过",
                block="miss_taskbook" if not budget else "rule_gap",
            )
        )
    cr.object_cards = cards
    cr.objects = [str(c.get("object")) for c in cards]
    cr.verdict_zh = "INSUFFICIENT"
    cr.notes = ["禁止因单轨或看见磁珠就判通过；禁止无噪声预算判不通过"]
    return cr


def check_p14_a05(kg: dict) -> CheckResult:
    """端口浪涌：按连接器列入口器件。威胁等级和浪涌额定未抽出 → 待确认。不用齐纳 Vz 冒充钳位能量。"""
    a = _helpers()
    conns = a._assembled(kg, "connector")
    cr = CheckResult("P14-A05", "不适用", title="端口浪涌防护匹配", card_class="path_topo")
    cr.add_step("列连接器", f"N={len(conns)}", n=1)
    if not conns:
        cr.object_cards = [na_explain_card("P14-A05", "无连接器")]
        cr.notes = ["无外部端口"]
        return cr
    threat = a._task(kg).get("port_threat") or a._oc(kg).get("port_threat") or ""
    cards = []
    for ref, c in conns:
        powers = a._entry_power_pins(c, kg)
        entry_nets = [net for _, net in powers]
        others = []
        surge_parts: list[tuple[str, dict[str, Any], str]] = []
        for net in entry_nets:
            hit = a._others_on_net(kg, net, ref)
            if hit:
                others.append(f"网 {net}：{'、'.join(hit[:6])}")
            for r2, c2 in a._comps(kg).items():
                blob = " ".join(str(x or "") for x in (r2, c2.get("mpn"), c2.get("value"), c2.get("desc")))
                nets2 = {n for _, n in a._pin_nets(c2) if n}
                if net in nets2 and _SURGE.search(blob) and not re.search(r"\b1N52|zener|稳压管", blob, re.I):
                    surge_parts.append((r2, c2, net))
        given = [
            {"sym": "端口", "val": ref, "cite": "连接器"},
            {"sym": "入口电源网", "val": "、".join(entry_nets) if entry_nets else "无供电针", "cite": "网表"},
            {"sym": "同网器件", "val": "；".join(others) if others else "无", "cite": "网表"},
        ]
        given.extend(_surge_given(surge_parts))
        given.extend(
            [
                {"sym": "威胁等级", "val": str(threat) if threat else "缺", "cite": "任务书" if threat else "任务书缺；不联网编威胁等级"},
                {"sym": "配方", "val": "空", "cite": "topology_recipes 本项子集为空"},
            ]
        )
        if not surge_parts:
            stuck = "入口网未挂 TVS/压敏/GDT。威胁等级只认任务书，任务书没有这一栏时不判通过或不通过"
        else:
            stuck = "浪涌器件已挂上入口网；三项额定按规格书、立创、联网抽出。威胁等级仍只认任务书"
        card = _wait(
            ref,
            c,
            phen=f"{ref}：{a._fmt_pins(c)}。" + (" ".join(others) if others else "入口电源网上没有其他器件。"),
            stuck=stuck,
            given=given,
            detail="看见二极管或齐纳不等于端口浪涌防护已经匹配威胁等级",
            block="miss_taskbook" if not threat else "miss_datasheet",
        )
        card["path_refs"] = [ref]
        cards.append(card)
    cr.object_cards = cards
    cr.objects = [ref for ref, _ in conns]
    cr.verdict_zh = "INSUFFICIENT"
    cr.notes = ["禁止用齐纳 Vz 冒充浪涌钳位电压或能量；禁止无威胁等级判通过或不通过"]
    return cr


def check_p19_a03(kg: dict) -> CheckResult:
    """复位网上拉/下拉。没有复位网 → 不适用。有网则列电阻与另一端，阈值未抽出 → 待确认。"""
    a = _helpers()
    cr = CheckResult("P19-A03", "不适用", title="复位上拉/下拉", card_class="path_topo")
    hit_nets: dict[str, list[str]] = {}
    for ref, c in a._comps(kg).items():
        if a._dnp(c):
            continue
        for pin, net in a._pin_nets(c):
            blob = f"{pin} {net}"
            if net and _RESET.search(blob):
                hit_nets.setdefault(net, []).append(f"{ref}.{pin}")
        if _RESET.search(str(c.get("value") or "") + str(c.get("mpn") or "")):
            for _, net in a._pin_nets(c):
                if net and net not in a._GROUND:
                    hit_nets.setdefault(net, [])
    for name in a._nets(kg):
        if name and _RESET.search(name) and not a._INTERNAL_NET.match(name):
            hit_nets.setdefault(name, hit_nets.get(name, []))
    cr.add_step("列复位网", f"N={len(hit_nets)}", n=1)
    if not hit_nets:
        cr.object_cards = [na_explain_card("P19-A03", "网名和引脚名都没有复位信号")]
        cr.notes = ["无 RESET/RST/NRST/POR 对象"]
        return cr
    cards = []
    for net, ends in hit_nets.items():
        pulls = []
        for ref, c in a._assembled(kg, "resistor"):
            pins = [(p, n) for p, n in a._pin_nets(c) if n]
            if not any(n == net for _, n in pins):
                continue
            other = "、".join(n for _, n in pins if n != net) or "另一端未读到"
            pulls.append(f"{ref}={c.get('value') or '缺'} 另一端 {other}")
        given = [
            {"sym": "复位网", "val": net, "cite": "网名或引脚名"},
            {"sym": "端点", "val": "、".join(ends) if ends else "未挂到器件引脚", "cite": "网表"},
            {"sym": "上拉或下拉电阻", "val": "；".join(pulls) if pulls else "同网无电阻", "cite": "网表；不把电阻另一端猜成上拉轨"},
        ]
        given.extend(_reset_given(kg, ends))
        given.append({"sym": "配方", "val": "空", "cite": "topology_recipes 本项子集为空"})
        card = _wait(
            net,
            None,
            phen=f"网 {net}，端点 {'、'.join(ends) if ends else '无'}，电阻 {('；'.join(pulls) if pulls else '无')}",
            stuck="复位阈值和上电默认状态未从手册抽出，不能判断上拉或下拉是否满足器件",
            given=given,
            detail="看见上拉电阻不等于电压和默认状态已经满足器件要求",
            block="miss_datasheet",
        )
        card["net_name"] = net
        cards.append(card)
    cr.object_cards = cards
    cr.objects = list(hit_nets)
    cr.verdict_zh = "INSUFFICIENT"
    cr.notes = ["禁止用 0.69·Rp·C 或口头电压判通过"]
    return cr


def _io_role(func: str) -> str:
    s = (func or "").upper().replace(" ", "")
    if re.search(r"OUT|TXD|TX", s) and "INPUT" not in s:
        return "output"
    if re.search(r"IN|RXD|RX", s):
        return "input"
    return ""


def _pin_func_map(nd: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    bags = [nd]
    for key in ("jlc_members", "net_context_pins"):
        extra = nd.get(key)
        if isinstance(extra, dict):
            bags.append(extra)
    for bag in bags:
        for item in bag.get("ic_pin_functions") or []:
            if isinstance(item, dict):
                out.update({str(k): str(v) for k, v in item.items()})
        for row in bag.get("pins_detail") or []:
            if not isinstance(row, dict):
                continue
            key = str(row.get("ref_pin") or "")
            name = str(row.get("pin_name") or row.get("pin_role") or "")
            if key and name and key not in out:
                out[key] = name
    return out


def _surge_given(parts: list[tuple[str, dict[str, Any], str]]) -> list[dict[str, str]]:
    if not parts:
        none = "入口网未挂 TVS/压敏/GDT；齐纳 Vz 不写入浪涌额定"
        return [
            {"sym": "浪涌器件", "val": "无", "cite": none},
            {"sym": "Vrwm", "val": "无器件", "cite": none},
            {"sym": "Vc", "val": "无器件", "cite": none},
            {"sym": "Ipp", "val": "无器件", "cite": none},
        ]
    rows: list[dict[str, str]] = []
    for ref, c, net in parts:
        params = (c.get("pintable") or {}).get("params") or {}
        rows.append(
            {
                "sym": "浪涌器件",
                "val": f"{ref} {c.get('mpn') or ''} @ {net}".strip(),
                "cite": "料号；齐纳 Vz 不写入这三栏",
            }
        )
        for sym, key, unit in (("Vrwm", "vrwm_v", "V"), ("Vc", "vc_v", "V"), ("Ipp", "ipp_a", "A")):
            val = params.get(key)
            rows.append(
                {
                    "sym": sym,
                    "val": "缺" if val is None else f"{val:g} {unit}" if isinstance(val, (int, float)) else f"{val} {unit}",
                    "cite": cite_param(params, key, missing=_MISSED + "；不用齐纳 Vz"),
                }
            )
    return rows


def _reset_given(kg: dict, ends: list[str]) -> list[dict[str, str]]:
    a = _helpers()
    comps = a._comps(kg)
    seen: list[str] = []
    for end in ends:
        ref = str(end).split(".", 1)[0]
        c = comps.get(ref) or {}
        if str(c.get("type") or "") != "ic" or ref in seen:
            continue
        seen.append(ref)
    if not seen:
        return [
            {"sym": "复位阈值高", "val": "无 IC", "cite": "复位网上没有 IC，不抽复位阈值"},
            {"sym": "复位阈值低", "val": "无 IC", "cite": "复位网上没有 IC，不抽复位阈值"},
            {"sym": "上电默认", "val": "无 IC", "cite": "复位网上没有 IC"},
        ]
    rows: list[dict[str, str]] = []
    for ref in seen:
        params = (comps[ref].get("pintable") or {}).get("params") or {}
        for sym, key, unit in (
            ("复位阈值高", "reset_vih_v", "V"),
            ("复位阈值低", "reset_vil_v", "V"),
            ("上电默认", "reset_default", ""),
        ):
            val = params.get(key)
            shown = "缺" if val is None else (f"{val:g} {unit}".strip() if isinstance(val, (int, float)) else str(val))
            rows.append({"sym": f"{ref} {sym}", "val": shown, "cite": cite_param(params, key, missing=_MISSED + "；不用绝对最大额定")})
    return rows


def _io_param(c: dict[str, Any], key: str) -> tuple[float | None, str]:
    params = (c.get("pintable") or {}).get("params") or {}
    missing = _MISSED + "；不用绝对最大，不用 0.7×VCC"
    val = params.get(key)
    if val is None:
        return None, cite_param(params, key, missing=missing)
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None, missing
    return num, cite_param(params, key, missing=missing)


def check_p21_a01(kg: dict) -> CheckResult:
    """IC 与 IC 的信号网。先抽手册电气特性里的 VOH/VOL/VIH/VIL，再写比对。缺则待确认。"""
    a = _helpers()
    cr = CheckResult("P21-A01", "不适用", title="发送/接收电平兼容", card_class="path_topo")
    by_net: dict[str, list[tuple[str, str, dict]]] = {}
    nets = a._nets(kg)
    for ref, c in a._assembled(kg, "ic"):
        for pin, net in a._pin_nets(c):
            if not net or net in a._GROUND:
                continue
            if a._is_power_net(net, nets.get(net) or {}):
                continue
            if a._INTERNAL_NET.match(net):
                continue
            by_net.setdefault(net, []).append((ref, pin or "?", c))
    pairs = []
    for net, pins in by_net.items():
        refs = {r for r, _, _ in pins}
        if len(refs) >= 2:
            pairs.append((net, pins))
    pairs.sort(key=lambda x: x[0])
    cr.add_step("列芯片之间的信号网", f"N={len(pairs)}", n=1)
    if not pairs:
        cr.object_cards = [na_explain_card("P21-A01", "没有连接两颗及以上 IC 的信号网")]
        cr.notes = ["电源脚和地不参与"]
        return cr
    cards = []
    for net, pins in pairs:
        funcs = _pin_func_map(nets.get(net) or {})
        outs: list[tuple[str, str, dict, str]] = []
        ins: list[tuple[str, str, dict, str]] = []
        unknown: list[str] = []
        for ref, pin, c in pins:
            func = funcs.get(f"{ref}.{pin}") or ""
            role = _io_role(func)
            label = f"{ref}.{pin}" + (f" {func}" if func else "")
            if role == "output":
                outs.append((ref, pin, c, label))
            elif role == "input":
                ins.append((ref, pin, c, label))
            else:
                unknown.append(label)
        given: list[dict[str, str]] = [
            {"sym": "网", "val": net, "cite": "网表；电源网和地已排除"},
        ]
        if unknown:
            given.append({"sym": "未分端", "val": "、".join(unknown), "cite": "引脚功能不是 OUT/IN，不指定发送端或接收端"})
        voh = vol = vih = vil = None
        voh_cite = vol_cite = vih_cite = vil_cite = "无发送端或接收端"
        if len(outs) == 1:
            _, _, c, label = outs[0]
            given.append({"sym": "发送端", "val": label, "cite": "引脚功能含 OUT"})
            voh, voh_cite = _io_param(c, "voh_min_v")
            vol, vol_cite = _io_param(c, "vol_max_v")
        elif len(outs) > 1:
            given.append({"sym": "发送端", "val": "、".join(x[3] for x in outs), "cite": "多个输出端，不选一个来比"})
            voh_cite = vol_cite = "多个输出端，未比"
        else:
            given.append({"sym": "发送端", "val": "缺", "cite": "这根网上没有识别出输出脚"})
        if ins:
            given.append({"sym": "接收端", "val": "、".join(x[3] for x in ins), "cite": "引脚功能含 IN"})
            vih_vals = []
            vil_vals = []
            vih_bits = []
            vil_bits = []
            for _, _, c, label in ins:
                hi, hc = _io_param(c, "vih_min_v")
                lo, lc = _io_param(c, "vil_max_v")
                if hi is not None:
                    vih_vals.append(hi)
                    vih_bits.append(f"{label} {hc}")
                else:
                    vih_bits.append(f"{label} {hc}")
                if lo is not None:
                    vil_vals.append(lo)
                    vil_bits.append(f"{label} {lc}")
                else:
                    vil_bits.append(f"{label} {lc}")
            if vih_vals and len(vih_vals) == len(ins):
                vih = max(vih_vals)
                vih_cite = "取各接收端 VIH 最小允许值中最严的；" + "；".join(vih_bits)
            else:
                vih_cite = "；".join(vih_bits)
            if vil_vals and len(vil_vals) == len(ins):
                vil = min(vil_vals)
                vil_cite = "取各接收端 VIL 最大允许值中最严的；" + "；".join(vil_bits)
            else:
                vil_cite = "；".join(vil_bits)
        else:
            given.append({"sym": "接收端", "val": "缺", "cite": "这根网上没有识别出输入脚"})
        given.extend(
            [
                {"sym": "VOH_min", "val": "缺" if voh is None else f"{voh:g} V", "cite": voh_cite},
                {"sym": "VOL_max", "val": "缺" if vol is None else f"{vol:g} V", "cite": vol_cite},
                {"sym": "VIH_min", "val": "缺" if vih is None else f"{vih:g} V", "cite": vih_cite},
                {"sym": "VIL_max", "val": "缺" if vil is None else f"{vil:g} V", "cite": vil_cite},
            ]
        )
        missing = []
        if not outs or not ins or unknown or len(outs) != 1:
            missing.append("发送端或接收端未分清")
        if voh is None or vol is None or vih is None or vil is None:
            missing.append("VOH/VOL/VIH/VIL 未齐")
        states = []
        if not missing and voh is not None and vol is not None and vih is not None and vil is not None:
            high_ok = voh + 1e-9 >= vih
            low_ok = vol <= vil + 1e-9
            states = [
                {
                    "name": "高电平",
                    "steps": [f"VOH_min = {voh:g} V    {voh_cite}", f"VIH_min = {vih:g} V    {vih_cite}", f"{voh:g} {'≥' if high_ok else '<'} {vih:g} → 本态 {'是' if high_ok else '否'}"],
                    "verdict_zh": "是" if high_ok else "否",
                },
                {
                    "name": "低电平",
                    "steps": [f"VOL_max = {vol:g} V    {vol_cite}", f"VIL_max = {vil:g} V    {vil_cite}", f"{vol:g} {'≤' if low_ok else '>'} {vil:g} → 本态 {'是' if low_ok else '否'}"],
                    "verdict_zh": "是" if low_ok else "否",
                },
            ]
            sol = pack_solution(
                given=given,
                states=states,
                gates=[gate("配方", None, "io_threshold_pair 尚未写入配方表，比对不作为终稿", wait=True)],
                rule="VOH_min≥VIH_min 且 VOL_max≤VIL_max → 整卡 是；任一不满足 → 否；缺数或未分端 → 待确认",
                verdict_zh="待确认",
            )
            stuck = "电平已经写出，配方表还没有 io_threshold_pair，不落是/否"
            block = "rule_gap"
        else:
            sol = pack_solution(
                given=given,
                gates=[gate("电平比对", None, "；".join(missing) + "；不用 0.7×VCC", wait=True)],
                rule="VOH_min≥VIH_min 且 VOL_max≤VIL_max → 整卡 是；任一不满足 → 否；缺数或未分端 → 待确认",
                verdict_zh="待确认",
            )
            stuck = "；".join(missing)
            block = "miss_datasheet"
        ends = "、".join(f"{r}.{p}" for r, p, _ in pins)
        card = base_card(net, pins[0][2], verdict_zh="待确认", block_class=block)
        _sol(card, sol, f"网 {net} 连接 {ends}", stuck)
        card["net_name"] = net
        card["path_refs"] = sorted({r for r, _, _ in pins})
        cards.append(card)
    cr.object_cards = cards
    cr.objects = [net for net, _ in pairs][:40]
    cr.coverage_n = len(pairs)
    cr.verdict_zh = "INSUFFICIENT"
    cr.notes = ["禁止 0.7×VCC 启发式判通过；禁止用绝对最大额定代替 VIH/VIL；配方未登记时比对不升绿"]
    return cr
