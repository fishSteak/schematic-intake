"""Program checkers for product-center 15 items. Pure functions over BoardKG.

Customer verdict defaults to program results (--skip-sdk or Agent 未填).
Agent may only propose facts_patch on 待确认; never invents voltages into verdicts.
Each check emits object_cards with 四格: phenomenon / calc / stuck / conclusion.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from kernel.checkers import CheckResult
from kernel.checkers.extra4 import check_p14_a05, check_p16_a05, check_p19_a03, check_p21_a01
from kernel.checkers.cards import (
    BLOCK_ZH,
    base_card,
    datasheet_card,
    ensure_cards_nonempty,
    fill_four,
    identity_card,
    loc_card,
    loc_str,
    na_explain_card,
)
from kernel.rated_fields import pin_supply_role, range_for_role
from kernel.source_cite import cite_param
from kernel.recipes import p11_card_recipe
from kernel.resistor_network import (
    resistor_series_to_unknown_load,
    two_terminal_dc_open,
)
from kernel.util import ROOT, board_schematic_pdf, is_file_safe
from kernel.solution import (
    apply_solution,
    fmt_ohm as sol_fmt_ohm,
    fmt_power as sol_fmt_power,
    fmt_volt as sol_fmt_volt,
    gate,
    pack_solution,
    states_power,
    states_voltage,
)

_OK_NET = re.compile(r"^[A-Za-z0-9_]+$")
_INTERNAL_NET = re.compile(r"^N\$|^$|^UNCONNECTED", re.I)
_POWER_NET = re.compile(r"^(VCC|VDD|VIN|AVDD|DVDD|VCCINT|VBAT|VBUS|3V3|5V|12V|24V)", re.I)
_GROUND = {"GND", "AGND", "DGND", "PGND", "VSS", "AVSS"}
_TOL = re.compile(r"\d+(?:\.\d+)?\s*%")
_DIEL = re.compile(r"X7R|X5R|X6S|C0G|NP0|NPO|COG|MLCC|电解|tant|TANT", re.I)
_CAP_V = re.compile(r"(\d+(?:\.\d+)?)\s*V", re.I)
_NEEDLE_POWER = re.compile(r"VCC|VDD|VIN|AVDD|DVDD|VCCINT|VBAT|VEE|VPP", re.I)

TITLES = {
    "P42-A01": "位号唯一性",
    "P42-A04": "BOM↔网表 Value/MPN 一致",
    "P05-A01": "电阻标注完整可解析",
    "P05-A06": "电容标注完整可解析",
    "P04-A01": "网络命名合法且图面可核对",
    "P04-A03": "短接/重名标签",
    "P11-A01": "电阻功耗降额",
    "P11-A03": "电容耐压降额",
    "P11-A07": "电感 Isat/温升",
    "P12-A01": "IC 供电脚与轨压",
    "P12-A02": "发送/接收 VOH/VOL↔VIH/VIL",
    "P12-A03": "有源器件温度等级覆盖环境",
    "P12-A04": "MOSFET Vds≤0.7×额定",
    "P12-A05": "MOSFET Ids≤0.7×额定",
    "P12-A06": "TVS VRWM/钳位与后级耐压",
    "P12-A07": "连接器电流/寿命/防护覆盖需求",
    "P14-A02": "入口防反路径",
    "P14-A06": "供电口限流/负载开关",
    "P16-A01": "IC 电源脚去耦",
    "P16-A05": "模拟电源 RC/磁珠隔离",
    "P07-A05": "电源/信号域隔离",
    "P18-A02": "晶振负载电容",
    "P14-A05": "端口浪涌防护匹配",
    "P19-A03": "复位上拉/下拉",
    "P21-A01": "发送/接收电平兼容",
}


def _comps(kg: dict) -> dict[str, Any]:
    return kg.get("components") or {}


def _nets(kg: dict) -> dict[str, Any]:
    return kg.get("nets") or {}


def _oc(kg: dict) -> dict[str, Any]:
    return kg.get("operating_conditions") or {}


def _task(kg: dict) -> dict[str, Any]:
    return kg.get("design_task") or {}


def _dnp(c: dict[str, Any]) -> bool:
    blob = " ".join(
        str(x or "")
        for x in (c.get("status"), c.get("value"), c.get("value_bom"), c.get("mpn"))
    ).upper()
    # NF = not fitted. Do not match the capacitance unit inside 100nF.
    return bool(re.search(r"(?<![A-Z0-9])(DNP|DNI|NF)(?![A-Z0-9])", blob))


def _type(c: dict[str, Any], ref: str) -> str:
    t = (c.get("type") or "").lower()
    if t and t != "unknown":
        return t
    m = re.match(r"^([A-Za-z]+)", ref or "")
    p = (m.group(1) if m else "").upper()
    return {
        "R": "resistor",
        "RN": "resistor",
        "C": "capacitor",
        "L": "inductor",
        "U": "ic",
        "Y": "crystal",
        "X": "crystal",
        "CN": "connector",
        "J": "connector",
        "P": "connector",
        "TB": "connector",
        "H": "header",
    }.get(p, "unknown")


def _assembled(kg: dict, kind: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for ref, c in _comps(kg).items():
        if _dnp(c):
            continue
        if _type(c, ref) == kind or (kind == "connector" and _type(c, ref) in {"connector", "header"}):
            out.append((ref, c))
    return out


def _parse_cap_f(val: str) -> float | None:
    s = (val or "").strip().replace("µ", "u").replace("μ", "u")
    if not s:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(pF|nF|uF|mF|F)$", s, re.I)
    if m:
        n = float(m.group(1))
        u = m.group(2).lower()
        return n * {"pf": 1e-12, "nf": 1e-9, "uf": 1e-6, "mf": 1e-3, "f": 1.0}[u]
    m3 = re.match(r"^(\d)(\d)(\d)$", s)
    if m3:
        return (int(m3.group(1)) * 10 + int(m3.group(2))) * (10 ** int(m3.group(3))) * 1e-12
    return None


def _parse_res_ohm(val: str) -> float | None:
    s = (val or "").strip().replace("Ω", "").replace("ohm", "").replace("Ohm", "")
    if not s or s.upper() == "TBD":
        return None
    m = re.match(r"^(\d+)[Rr](\d+)$", s)
    if m:
        return float(m.group(1)) + float(m.group(2)) / (10 ** len(m.group(2)))
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*([kKmMrR])?$", s)
    if not m:
        return None
    n = float(m.group(1))
    u = (m.group(2) or "").lower()
    if u in {"k"}:
        return n * 1e3
    if u in {"m"}:
        return n * 1e6
    return n


def _parse_res_ok(val: str) -> bool:
    return _parse_res_ohm(val) is not None


def _pintable(c: dict[str, Any]) -> dict[str, Any]:
    return c.get("pintable") or {}


def _params(c: dict[str, Any]) -> dict[str, Any]:
    pt = _pintable(c)
    return pt.get("params") or {}


def _ladder_cite(params: dict[str, Any], key: str, value: Any, note: str) -> str:
    """额定栏来源：规格书第N页 / 立创参数 / 联网 / 未抽出。比对不用这句。"""
    if value is None:
        return "未抽出：程序已走规格书、立创、联网"
    text = cite_param(params, key)
    if text.startswith("未抽出") and note:
        return f"规格书 {note}".strip()[:200]
    return text


def _vcc_cite(c: dict[str, Any]) -> str:
    from kernel.source_cite import format_source

    params = _params(c)
    text = cite_param(params, "vcc_min")
    if not text.startswith("未抽出"):
        return text
    rec = (_pintable(c).get("recommended") or {}) if isinstance(_pintable(c).get("recommended"), dict) else {}
    if rec.get("vcc_min") is not None or rec.get("quote") or rec.get("page"):
        return format_source(
            {"kind": "datasheet_page", "page": rec.get("page"), "quote": rec.get("quote") or rec.get("vi_quote") or ""}
        )
    return "未抽出：程序已走规格书、立创、联网"


def _closed_rail(nd: dict[str, Any]) -> float | None:
    rail = (nd or {}).get("rail") or {}
    if rail.get("status") == "closed" and rail.get("value") is not None:
        src = rail.get("source")
        # 拒绝历史残留的裸 netname（无 value 语义）；允许 netname_voltage 业务闭合
        if src == "netname" or (isinstance(src, dict) and src.get("kind") == "netname"):
            return None
        try:
            return float(rail["value"])
        except (TypeError, ValueError):
            return None
    return None


def _used_candidate(nd: dict[str, Any]) -> bool:
    rail = (nd or {}).get("rail") or {}
    return rail.get("status") == "candidate"


def _schematic_label_n(nd: dict[str, Any]) -> int:
    """图上可见网标次数。所有用户网名共用这一条，不按位号或网名特例。

    一个网络标识符号（componentId）算 1 处，同一符号多只脚不重复算。
    该页已有网络标识时，导线 NET 与标识是同一处字，不再加一次。
    该页没有网络标识时，该页有名字的导线算 1 处。不算 BOM、规格书、网表。
    """
    members = nd.get("jlc_members") if isinstance(nd.get("jlc_members"), dict) else {}
    locations = members.get("locations") if isinstance(members.get("locations"), list) else []
    flag_ids: set[str] = set()
    flag_pages: set[str] = set()
    wire_pages: set[str] = set()

    def _page(loc: dict[str, Any]) -> str:
        return str(loc.get("pageName") or loc.get("pageId") or loc.get("page") or "")

    for loc in locations:
        if not isinstance(loc, dict):
            continue
        page = _page(loc)
        cid = loc.get("componentId")
        wid = loc.get("wireId")
        if cid:
            cid_s = str(cid)
            flag_ids.add(cid_s)
            flag_pages.add(page if page else f"flag:{cid_s}")
        elif wid:
            wire_pages.add(page if page else f"wire:{wid}")
    return len(flag_ids) + sum(1 for page in wire_pages if page not in flag_pages)


def _schematic_user_nets(kg: dict) -> list[tuple[str, dict[str, Any], int]]:
    """检查对象：图上有网标的用户网。无标注的网表网不进名单。"""
    out: list[tuple[str, dict[str, Any], int]] = []
    for n, nd in _nets(kg).items():
        if _INTERNAL_NET.match(n):
            continue
        hit = _schematic_label_n(nd or {})
        if hit <= 0:
            continue
        out.append((n, nd or {}, hit))
    return out


def _collect_user_nets(kg: dict) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for n, nd, _hit in _schematic_user_nets(kg):
        members = nd.get("jlc_members") if isinstance(nd.get("jlc_members"), dict) else {}
        cands = list(members.get("names") or nd.get("jlc_alias_names") or []) or [n]
        for cand in cands:
            s = str(cand or "")
            if not s or s in seen or _INTERNAL_NET.match(s):
                continue
            seen.add(s)
            names.append(s)
    return names


def _charset_ok(name: str, task: dict[str, Any]) -> bool:
    if _OK_NET.match(name):
        return True
    extras = ""
    apply_power_only = False
    for ex in task.get("charset_exceptions") or []:
        if isinstance(ex, dict):
            extras += str(ex.get("chars") or ex.get("allow") or "")
            if (ex.get("class") or "") == "power":
                apply_power_only = True
        elif isinstance(ex, str):
            extras += ex
    if not extras:
        return False
    extra_set = "".join(ch for ch in extras if ch not in "A-Za-z0-9_")
    pat = re.compile(r"^[A-Za-z0-9_" + re.escape(extra_set) + r"]+$")
    if not pat.match(name):
        return False
    if apply_power_only and not (_POWER_NET.match(name) or name.startswith("+")):
        return False
    return True


def _pin_nets(c: dict[str, Any]) -> list[tuple[str, str]]:
    out = []
    for p in c.get("pins") or []:
        if isinstance(p, dict):
            out.append((str(p.get("pin") or p.get("name") or ""), str(p.get("net") or "")))
        elif isinstance(p, str):
            out.append(("", p))
    return out


def _is_power_net(name: str, nd: dict[str, Any]) -> bool:
    if not name or name in _GROUND:
        return False
    if _POWER_NET.match(name) or name.startswith("+"):
        return True
    if _closed_rail(nd) is not None:
        return True
    return False


def _fmt_pins(c: dict[str, Any]) -> str:
    bits = []
    for pin, net in _pin_nets(c):
        if not net:
            continue
        bits.append(f"pin{pin}={net}" if pin else net)
    return "、".join(bits) if bits else "没有读到引脚网络"


def _illegal_chars(name: str) -> str:
    bad: list[str] = []
    for ch in name:
        if re.match(r"[A-Za-z0-9_]", ch):
            continue
        if ch not in bad:
            bad.append(ch)
    return "、".join(bad)


def _charset_pass_text(name: str) -> str:
    """已通过字符集的用户网名，写出通过原因。"""
    if _OK_NET.match(name):
        return f"网名「{name}」只含字母、数字、下划线"
    extra = _illegal_chars(name)
    if extra:
        return f"网名「{name}」含任务书允许的符号（{extra}）"
    return f"网名「{name}」符合任务书写明的字符集例外"


def _entry_power_pins(c: dict[str, Any], kg: dict) -> list[tuple[str, str]]:
    nets = _nets(kg)
    out: list[tuple[str, str]] = []
    for pin, net in _pin_nets(c):
        if not net or net in _GROUND:
            continue
        if _is_power_net(net, nets.get(net) or {}):
            out.append((pin, net))
    return out


_KIND_ZH = {
    "fuse": "熔丝",
    "diode": "二极管",
    "ic": "芯片",
    "connector": "连接器",
    "header": "排针",
    "resistor": "电阻",
    "capacitor": "电容",
    "inductor": "电感",
    "transistor": "三极管",
    "mosfet": "MOS",
    "crystal": "晶振",
}


def _others_on_net(kg: dict, net: str, skip: str) -> list[str]:
    bits = []
    for ref, comp in _comps(kg).items():
        if ref == skip:
            continue
        for pin, n in _pin_nets(comp):
            if n != net:
                continue
            kind = _type(comp, ref)
            label = _KIND_ZH.get(kind, "器件")
            mpn = str(comp.get("mpn") or comp.get("value") or "")
            extra = f"（{mpn}）" if mpn and kind == "fuse" else ""
            bits.append(f"{ref} pin{pin} {label}{extra}")
            break
    return bits


def _fuse_downstream_note(kg: dict, entry_nets: set[str]) -> str:
    notes = []
    for ref, comp in _comps(kg).items():
        if _type(comp, ref) != "fuse":
            continue
        pins = [(p, n) for p, n in _pin_nets(comp) if n]
        if not any(n in entry_nets for _, n in pins):
            continue
        for _, on in pins:
            if on in entry_nets:
                continue
            diodes = []
            for r2, c2 in _comps(kg).items():
                if _type(c2, r2) != "diode":
                    continue
                dn = [n for _, n in _pin_nets(c2) if n]
                if on in dn:
                    diodes.append(f"{r2}（{'、'.join(dn)}）")
            if diodes:
                notes.append(f"{ref} 是熔丝，另一头接到 {on}，二极管 {'、'.join(diodes)} 在熔丝后面")
            else:
                notes.append(f"{ref} 是熔丝，另一头接到 {on}")
    return "；".join(notes)


def check_p04_a01(kg: dict) -> CheckResult:
    raw = _collect_user_nets(kg)
    cr = CheckResult("P04-A01", "是", title=TITLES["P04-A01"], card_class="net_label")
    cr.add_step("收集网名", f"raw={len(raw)}", n=1)
    user = [n for n in raw if not _INTERNAL_NET.match(n)]
    internal = [n for n in raw if _INTERNAL_NET.match(n)]
    cr.add_step("拆内部网", f"user={len(user)} internal={len(internal)}", n=2)
    task = _task(kg)
    bad = [n for n in user if not _charset_ok(n, task)]
    bad_set = set(bad)
    cr.coverage_n = len(user)
    cr.objects = list(user)
    cr.add_step(
        "字符集",
        f"illegal={len(bad)} pass={len(user) - len(bad)}",
        "FAIL" if bad else ("INSUFFICIENT" if not user else "PASS"),
        n=3,
    )
    cards: list[dict[str, Any]] = []
    if not user:
        cr.verdict_zh = "INSUFFICIENT"
        card = base_card("(nets)", verdict_zh="待确认", block_class="miss_taskbook")
        fill_four(
            card,
            phenomenon="原理图上没有抽出网标（导线 NET 或网络标识）",
            calc="这项只核对付在图上的网标。BOM、规格书、网表里的网名不进对象名单",
            stuck="图上没有网标可核对",
            conclusion="待确认 · 缺图面网标",
        )
        cards.append(card)
    else:
        cr.verdict_zh = "否" if bad else "是"
        cr.violations = list(bad)
        for n in user:
            if n in bad_set:
                card = base_card(n, verdict_zh="否", block_class=None)
                card["net_name"] = n
                card["charset_or_alias_issue"] = "illegal_charset"
                bad_ch = _illegal_chars(n)
                fill_four(
                    card,
                    phenomenon=f"用户网名「{n}」",
                    calc="允许的字符是字母、数字、下划线。任务书没有另写电源网可以带的符号时，带其他符号的网名不通过",
                    stuck="无",
                    conclusion=f"否 · 网名「{n}」含不允许的字符" + (f"（{bad_ch}）" if bad_ch else ""),
                )
            else:
                note = _charset_pass_text(n)
                card = base_card(n, verdict_zh="是")
                card["net_name"] = n
                fill_four(
                    card,
                    phenomenon=f"用户网名「{n}」",
                    calc=note,
                    stuck="无",
                    conclusion=f"是 · {note}",
                )
            cards.append(card)
    cr.object_cards = cards
    return cr


def check_p04_a03(kg: dict) -> CheckResult:
    cr = CheckResult("P04-A03", "是", title=TITLES["P04-A03"], card_class="net_label")
    user = _schematic_user_nets(kg)
    cr.coverage_n = len(user)
    cr.add_step("收集图面网标", f"user={len(user)}", n=1)
    cards: list[dict[str, Any]] = []
    if not user:
        cr.verdict_zh = "INSUFFICIENT"
        cr.add_step("判定", "图上没有网标", "INSUFFICIENT", n=2)
        card = base_card("(nets)", verdict_zh="待确认", block_class="miss_taskbook")
        fill_four(
            card,
            phenomenon="原理图上没有抽出网标（导线 NET 或网络标识）",
            calc="这项只核对付在图上的网标。BOM、规格书、网表里的网名不进对象名单",
            stuck="图上没有网标可核对",
            conclusion="待确认 · 缺图面网标",
        )
        cr.object_cards = [card]
        return cr
    lone: list[str] = []
    multi = 0
    for n, nd, n_hit in user:
        card = base_card(n, verdict_zh="是")
        card["net_name"] = n
        card["recipe_id"] = "net_label_count"
        card["label_hit_n"] = n_hit
        phen = f"用户网名「{n}」图上同名网标 {n_hit} 处"
        if n_hit <= 1:
            lone.append(n)
            card["verdict_zh"] = "否"
            card["charset_or_alias_issue"] = "single_net_label"
            fill_four(
                card,
                phenomenon=phen,
                calc="同名网标只有 1 处。电路连通仍可能成立，按漏标标出。",
                stuck="无",
                conclusion=f"否 · 网名「{n}」图上同名网标只有 {n_hit} 处，按漏标标出",
            )
        else:
            multi += 1
            fill_four(
                card,
                phenomenon=phen,
                calc=f"同名网标 {n_hit} 处（≥2）。",
                stuck="无",
                conclusion=f"是 · 网名「{n}」图上同名网标 {n_hit} 处",
            )
        cards.append(card)
    cr.add_step("点网标次数", f"lone={len(lone)} multi={multi}", "FAIL" if lone else "PASS", n=2)
    cr.verdict_zh = "否" if lone else "是"
    cr.violations = list(lone)
    cr.objects = [n for n, _, _ in user]
    cr.object_cards = cards
    cr.notes = ["对象只来自原理图网标。1 处按漏标标出；≥2 处通过。BOM、规格书、网表不进名单。"]
    return cr


def check_p05_a01(kg: dict) -> CheckResult:
    rs = _assembled(kg, "resistor")
    cr = CheckResult(
        "P05-A01", "是", title=TITLES["P05-A01"], objects=[r for r, _ in rs], coverage_n=len(rs), card_class="annotation"
    )
    cr.add_step("列出装配电阻", f"N={len(rs)}", n=1)
    cards: list[dict[str, Any]] = []
    bad = []
    parsed = 0
    for ref, c in rs:
        val = c.get("value") or ""
        ohm = _parse_res_ohm(val)
        fp = (c.get("footprint") or "").strip()
        tol = bool(_TOL.search(val) or c.get("tolerance") or c.get("accuracy") or c.get("precision"))
        miss: list[str] = []
        if ohm is None:
            miss.append("阻值不可解析")
            bad.append(f"{ref}:阻值不可解析={val!r}")
        if not fp:
            miss.append("缺封装")
            if ohm is not None:
                bad.append(f"{ref}:缺封装")
        if not tol:
            miss.append("标注未写百分号")
            if ohm is not None and fp:
                bad.append(f"{ref}:标注未写百分号")
        gates = [
            gate(
                "阻值可解析",
                ohm is not None,
                f"{val!r} → {sol_fmt_ohm(ohm)}" if ohm is not None else f"{val!r} 不可解析",
            ),
            gate(
                "精度",
                tol,
                "value/公差栏含 %" if tol else "value/公差栏均无 %。本项只认图纸或 BOM 上的百分号，不把料号后缀当成标注",
            ),
            gate(
                "封装",
                bool(fp),
                fp or "缺封装",
            ),
        ]
        sol = pack_solution(
            given=[
                {"sym": "value", "val": str(val), "cite": "图纸/BOM"},
                {"sym": "footprint", "val": fp or "缺", "cite": "封装栏"},
            ],
            gates=gates,
            rule="阻值、精度、封装任一缺 → 整卡 否",
        )
        if not miss:
            parsed += 1
            card = base_card(ref, c, verdict_zh="是")
            _sol(card, sol, f"{ref} 阻值={val}，封装={fp or '缺'}")
        else:
            card = base_card(ref, c, verdict_zh="否", block_class="device_irregular")
            card["missing_fields"] = miss
            _sol(card, sol, f"{ref} 阻值={val or '缺'}，封装={fp or '缺'}，{'、'.join(miss)}")
        cards.append(card)
    cr.add_step("解析三项", f"ok={parsed} missing={len(bad)}", n=2)
    if not rs:
        cr.verdict_zh = "不适用"
        cr.add_step("判定齐套", "无装配电阻", "N/A", n=3)
        cr.object_cards = [na_explain_card("P05-A01", "无装配电阻")]
    elif bad:
        cr.verdict_zh = "否"
        cr.violations = bad[:40]
        cr.add_step("判定齐套", f"缺项{len(bad)}", "FAIL", n=3)
        cr.object_cards = cards
    else:
        cr.add_step("判定齐套", "三项齐且可解析", "PASS", n=3)
        cr.object_cards = cards
    return cr


_DIEL_CODE = re.compile(r"X7R|X5R|X6S|C0G|NP0|NPO|COG|电解|钽|tant", re.I)
_FP_ANN = re.compile(r"\b(C\d{4}|R\d{4}|CAP-[A-Za-z0-9._-]+)\b", re.I)
_VR_TOKEN = re.compile(r"^(\d+(?:\.\d+)?)V$", re.I)


def _on_schematic(c: dict[str, Any]) -> bool:
    if c.get("in_jlc"):
        return True
    loc = c.get("location")
    return bool(loc)


def _schematic_caps(kg: dict) -> list[tuple[str, dict[str, Any]]]:
    return [(ref, c) for ref, c in _assembled(kg, "capacitor") if _on_schematic(c)]


def _kg_schematic_pdf(kg: dict, cache: dict[str, Any]) -> Path | None:
    """sources.pdf 是生成时的绝对路径；换机失效时退回 boards/<板名> 的板配置。每轮只查一次。"""
    if "_schematic_pdf" not in cache:
        raw = (kg.get("sources") or {}).get("pdf")
        board = ROOT / "boards" / str(kg.get("board") or "")
        if raw and is_file_safe(raw):
            cache["_schematic_pdf"] = Path(str(raw))
        elif kg.get("board") and board.is_dir():
            cache["_schematic_pdf"] = board_schematic_pdf(board)
        else:
            cache["_schematic_pdf"] = None
    return cache["_schematic_pdf"]


def _pdf_ann_tokens(kg: dict, ref: str, c: dict[str, Any], cache: dict[str, Any]) -> list[str]:
    """位号旁边的 PDF 文字。网名、其他位号不计入。"""
    pdf = _kg_schematic_pdf(kg, cache)
    loc = loc_card(c)
    page_n = loc.get("pdf_page")
    if pdf is None or not isinstance(page_n, int) or page_n < 1:
        return []
    key = (str(pdf), page_n)
    words = cache.get(key)
    if words is None:
        try:
            import fitz  # type: ignore

            doc = fitz.open(str(pdf))
            page = doc[page_n - 1]
            words = list(page.get_text("words") or [])
            doc.close()
        except Exception:
            words = []
        cache[key] = words
    skip = set(_nets(kg)) | set(_comps(kg)) | {ref}
    out: list[str] = []
    seen: set[str] = set()
    for a in words:
        if str(a[4]) != ref:
            continue
        ax, ay = float(a[0]), float(a[1])
        for w in words:
            tok = str(w[4] or "")
            if not tok or tok in skip or tok in seen:
                continue
            if abs(float(w[0]) - ax) > 100 or abs(float(w[1]) - ay) > 50:
                continue
            seen.add(tok)
            out.append(tok)
    return out


def _sch_ann_text(kg: dict, ref: str, c: dict[str, Any], cache: dict[str, Any]) -> str:
    src = c.get("sources") if isinstance(c.get("sources"), dict) else {}
    bits = [
        str((src.get("value_jlc") or {}).get("value") or ""),
        str(c.get("voltage") or c.get("rated_voltage") or ""),
        str(c.get("dielectric") or c.get("material") or ""),
    ]
    bits.extend(_pdf_ann_tokens(kg, ref, c, cache))
    return " ".join(x for x in bits if str(x).strip())


def _p05_value_jlc(c: dict[str, Any]) -> str:
    src = c.get("sources") if isinstance(c.get("sources"), dict) else {}
    return str((src.get("value_jlc") or {}).get("value") or "").strip()


def _p05_vr(ann: str) -> tuple[str | None, str]:
    for tok in re.findall(r"[^\s,;/]+", ann or ""):
        if tok.startswith("+"):
            continue
        m = _VR_TOKEN.match(tok)
        if m:
            return m.group(1), "图面标注"
    return None, "图面未标额定耐压"


def _p05_diel(ann: str) -> tuple[str | None, str]:
    m = _DIEL_CODE.search(ann or "")
    if m:
        return m.group(0), "图面标注"
    if re.search(r"MLCC", ann or "", re.I):
        return None, "图面仅有 MLCC，不单独当介质"
    return None, "图面未标介质"


def _p05_fp(ann: str) -> tuple[str | None, str]:
    m = _FP_ANN.search(ann or "")
    if m:
        return m.group(1), "图面标注"
    return None, "图面未标封装"


def check_p05_a06(kg: dict) -> CheckResult:
    cs = _schematic_caps(kg)
    cr = CheckResult(
        "P05-A06", "是", title=TITLES["P05-A06"], objects=[r for r, _ in cs], coverage_n=len(cs), card_class="annotation"
    )
    cr.add_step("列出图面电容", f"N={len(cs)}", n=1)
    cards: list[dict[str, Any]] = []
    bad = []
    pdf_cache: dict[str, Any] = {}
    for ref, c in cs:
        val = _p05_value_jlc(c)
        ann = _sch_ann_text(kg, ref, c, pdf_cache)
        miss: list[str] = []
        cap_token = val
        cf = _parse_cap_f(val)
        if cf is None:
            for tok in re.findall(r"[^\s,;/]+", val):
                cf = _parse_cap_f(tok)
                if cf is not None:
                    cap_token = tok
                    break
        if val.upper() == "TBD" or cf is None:
            miss.append("容值不可解析")
            bad.append(f"{ref}:容值不可解析={val!r}")
        vr, vr_cite = _p05_vr(ann)
        diel, diel_cite = _p05_diel(ann)
        fp, fp_cite = _p05_fp(ann)
        if "容值不可解析" not in miss:
            if vr is None:
                miss.append("缺额定耐压")
                bad.append(f"{ref}:缺额定耐压")
            if not diel:
                miss.append("缺介质")
                bad.append(f"{ref}:缺介质")
            if not fp:
                miss.append("缺封装")
                bad.append(f"{ref}:缺封装")
        gates = [
            gate(
                "容值可解析",
                cf is not None and val.upper() != "TBD",
                f"{val!r}" if cf is None or val.upper() == "TBD" else f"{cap_token!r} → {cf} F · 图面标注",
            ),
            gate("额定耐压 Vr", vr is not None, f"{vr} · {vr_cite}" if vr is not None else vr_cite),
            gate("介质", bool(diel), f"{diel} · {diel_cite}" if diel else diel_cite),
            gate("封装", bool(fp), f"{fp} · {fp_cite}" if fp else fp_cite),
        ]
        sol = pack_solution(
            given=[
                {"sym": "value", "val": val or "缺", "cite": "图面标注"},
                {"sym": "Vr", "val": str(vr) if vr is not None else "缺", "cite": vr_cite},
                {"sym": "介质", "val": str(diel) if diel else "缺", "cite": diel_cite},
                {"sym": "footprint", "val": fp or "缺", "cite": fp_cite},
            ],
            gates=gates,
            rule="容值、Vr、介质、封装只认图面标注；BOM、规格书不补",
        )
        if miss:
            card = base_card(ref, c, verdict_zh="否", block_class="device_irregular")
            card["missing_fields"] = miss
            _sol(card, sol, f"{ref} 容值={val or '缺'}，耐压={vr if vr is not None else '缺'}，封装={fp or '缺'}，{'、'.join(miss)}")
        else:
            card = base_card(ref, c, verdict_zh="是")
            _sol(card, sol, f"{ref} 容值={val}，耐压={vr}，封装={fp}")
        cards.append(card)
    cr.add_step("解析图面标注", f"bad={len(bad)}", n=2)
    if not cs:
        cr.verdict_zh = "INSUFFICIENT"
        cr.add_step("核耐压/介质/封装", "图上没有电容", "INSUFFICIENT", n=3)
        card = base_card("(caps)", verdict_zh="待确认", block_class="miss_taskbook")
        fill_four(
            card,
            phenomenon="原理图上没有抽出电容位号",
            calc="这项只核对图画上的容值、耐压、介质、封装。BOM、规格书不进对象名单",
            stuck="图上没有电容可核对",
            conclusion="待确认 · 缺图面电容",
        )
        cr.object_cards = [card]
    elif bad:
        cr.verdict_zh = "否"
        cr.violations = bad[:40]
        cr.add_step("核耐压/介质/封装", f"缺项{len(bad)}", "FAIL", n=3)
        cr.object_cards = cards
    else:
        cr.add_step("核耐压/介质/封装", "四项齐", "PASS", n=3)
        cr.object_cards = cards
    cr.notes = ["对象和四字段只来自原理图/PDF 标注。BOM、规格书不补。"]
    return cr


def check_p07_a05(kg: dict) -> CheckResult:
    cr = CheckResult("P07-A05", "INSUFFICIENT", title=TITLES["P07-A05"], card_class="path_topo")
    pairs = _task(kg).get("forbidden_pairs") or _oc(kg).get("forbidden_pairs") or []
    issues = kg.get("jlc_connection_issues") or []
    shorts = []
    if isinstance(pairs, list):
        netmap = _nets(kg)
        for row in pairs:
            if not isinstance(row, dict):
                continue
            a, b = str(row.get("a") or ""), str(row.get("b") or "")
            if a and b and a == b:
                shorts.append(f"同网直连 {a}")
            elif a and b and a in netmap and b in netmap:
                if a == b:
                    shorts.append(f"{a}=={b}")
    cr.evidence = [f"jlc_connection_issues={len(issues)}", f"pairs={len(pairs)}"]
    cr.add_step("追踪真实通路", f"shorts={len(shorts)} issues={len(issues)}", n=2)
    cards: list[dict[str, Any]] = []
    if not pairs:
        card = base_card("(forbidden_pairs)", verdict_zh="待确认", block_class="miss_taskbook")
        fill_four(
            card,
            phenomenon=f"任务书和工况里没有「哪些网不准连在一起」的清单。连接问题记录 {len(issues)} 条",
            calc="没有这份清单时，不能因为连接问题为 0 就判通过",
            stuck="任务书未写禁止短接的网对",
            conclusion="待确认 · 缺禁止短接的网对",
        )
        cards.append(card)
        cr.object_cards = cards
        cr.notes = ["无任务书/工况禁止对，不因 issues=0 判是"]
        return cr
    if shorts:
        cr.verdict_zh = "否"
        cr.violations = shorts
        for s in shorts:
            card = base_card(s, verdict_zh="否")
            fill_four(
                card,
                phenomenon=f"禁止短接的网在网表里连在一起：{s}",
                calc="任务书禁止的两根网被连成了同一根",
                stuck="无",
                conclusion=f"否 · {s}",
            )
            cards.append(card)
    else:
        cr.verdict_zh = "INSUFFICIENT"
        card = base_card("(pairs)", verdict_zh="待确认", block_class="rule_gap")
        fill_four(
            card,
            phenomenon=f"任务书给出 {len(pairs)} 对禁止短接的网，网表里没有同名直连",
            calc="0Ω 或 NetTie 这类允许的跨接还没有逐条核对，不能判通过",
            stuck="允许跨接的清单没有核对完",
            conclusion="待确认 · 允许跨接的清单未核对完",
        )
        cards.append(card)
        cr.notes = ["禁止对已给，受控白名单（0Ω/NetTie）未闭合"]
    cr.object_cards = cards
    return cr


def _rail_before(nd: dict[str, Any]) -> dict[str, Any]:
    rail = (nd or {}).get("rail") or {}
    return {
        "status": rail.get("status"),
        "value": rail.get("value"),
        "candidate_v": rail.get("candidate_v"),
        "source": rail.get("source"),
    }


_BLOCKED_NEIGHBOR_TYPES = {"transistor", "mosfet", "bjt", "fet", "jfet"}


def _blocked_neighbors(ref: str, c: dict[str, Any], kg: dict) -> list[tuple[str, str]]:
    """Neighbors whose device type is not admitted into pure-R DC solve."""
    comps = _comps(kg)
    my_nets = {n for _, n in _pin_nets(c) if n and n not in _GROUND}
    hit: list[tuple[str, str]] = []
    for r2, c2 in comps.items():
        if r2 == ref:
            continue
        t = _type(c2, r2)
        if t not in _BLOCKED_NEIGHBOR_TYPES:
            continue
        for _, n in _pin_nets(c2):
            if n in my_nets:
                hit.append((r2, t))
                break
    return hit


def _resistor_dc_path_open(ref: str, c: dict[str, Any], kg: dict[str, Any]) -> bool:
    """True when one end is only unknown_load after removing this resistor."""
    return bool(resistor_series_to_unknown_load(ref, c, _comps(kg), _nets(kg)))


def check_p11_a01(kg: dict) -> CheckResult:
    rs = _assembled(kg, "resistor")
    cr = CheckResult(
        "P11-A01",
        "INSUFFICIENT",
        title=TITLES["P11-A01"],
        objects=[r for r, _ in rs],
        coverage_n=len(rs),
        card_class="stress_calc",
    )
    cr.add_step("列出装配电阻", f"N={len(rs)}", n=1)
    if not rs:
        cr.verdict_zh = "不适用"
        cr.add_step("算P_max", "无电阻", "N/A", n=4)
        return cr
    fail: list[str] = []
    closed: list[str] = []
    missing: list[str] = []
    cards: list[dict[str, Any]] = []
    from kernel.dc_modes import as_stress, dc_modes, project_resistor, rule_needs_dc_modes

    if not rule_needs_dc_modes("P11-A01"):
        cr.verdict_zh = "INSUFFICIENT"
        cr.add_step("取工况", "P11-A01 未登记 needs_dc_modes，不调用直流工况分析器", n=3)
        cr.object_cards = [na_explain_card("P11-A01", "未登记直流工况")]
        return cr
    cr.add_step(
        "取工况",
        "直流工况分析器列出连通块；本项只取该电阻电流，P=I²R，再比 0.5×P_rated",
        n=3,
    )

    block_zh = BLOCK_ZH

    for ref, c in rs:
        ohm = _parse_res_ohm(c.get("value") or "")
        pr = _params(c).get("ptot_w") or _params(c).get("p_rated_w") or c.get("p_rated")
        if pr is None and _params(c).get("ptot_text"):
            m = re.search(r"(\d+(?:\.\d+)?)\s*(mW|W)", str(_params(c).get("ptot_text")), re.I)
            if m:
                pr = float(m.group(1)) / (1000.0 if m.group(2).lower() == "mw" else 1.0)

        block = dc_modes(kg, ref)
        ends = (block.get("ends") or {}).get(ref) or ("", "")
        n_a = ends[0] if ends else ""
        n_b = ends[1] if len(ends) > 1 else ""
        if not n_a or not n_b:
            pins = _pin_nets(c)
            n_a = n_a or (pins[0][1] if pins else "")
            n_b = n_b or (pins[1][1] if len(pins) > 1 else "")
        stress = {
            "nets": {"a": n_a, "b": n_b},
            "v": None,
            "block_class": None if block.get("in_index") else "rule_gap",
            "false_close": None if block.get("in_index") else "通路种类未登记，不能判通过或不通过",
            "cases": [],
            "states": [],
            "before": {},
            "fanout": {},
            "stress_kind": block.get("kind"),
            "drive_kind": block.get("drive"),
        }
        before = stress.get("before") or {}
        loc = loc_card(c)
        loc_s = loc_str(loc)

        card: dict[str, Any] = {
            "object": ref,
            "location": loc,
            "identity": identity_card(c),
            "datasheet": datasheet_card(c),
            "nets": {"a": n_a, "b": n_b},
            "before": before,
            "after": {},
            "formula": "",
            "limit": None,
            "computed": {},
            "verdict_zh": "待确认",
            "block_class": None,
            "stress_kind": stress.get("stress_kind"),
            "states": list(stress.get("states") or []),
            "cases": list(stress.get("cases") or []),
            "drive_kind": stress.get("drive_kind"),
            "recipe_id": None,
            "assumptions": [],
            "fanout": stress.get("fanout") or {},
            "summary": "",
            "phenomenon": "",
            "calc": "",
            "stuck": "",
            "conclusion": "",
        }

        phen_base = f"{loc_s + ' · ' if loc_s else ''}{ref} 阻值 {c.get('value') or '缺'}，两端网 {n_a}、{n_b}"
        fan = card["fanout"] if isinstance(card["fanout"], dict) else {}
        fan_paths = fan.get("paths") or []
        if fan_paths:
            phen_base += f"；fanout={fan_paths[0]}"
        elif fan.get("devices"):
            phen_base += f"；邻接器件 {','.join(str(x) for x in fan.get('devices')[:6])}"

        ident = card.get("identity") or {}
        ds_note = str((card.get("datasheet") or {}).get("extract_note") or "")

        def _wait_sol(reason: str, stuck: str, extra: list | None = None) -> None:
            sol = pack_solution(
                given=[
                    {"sym": f"R({ref})", "val": sol_fmt_ohm(ohm) if ohm is not None else "缺", "cite": f"value={c.get('value')!r}"},
                    {"sym": "P_rated", "val": "缺" if pr is None else f"{pr} W", "cite": _ladder_cite(_params(c), "p_rated_w", pr, ds_note)},
                ] + list(extra or []),
                gates=[gate("应力闭合", False, reason, wait=True)],
                rule="任一态 P > 限额 → 整卡 否；缺证据不判否",
                verdict_zh="待确认",
            )
            _sol(card, sol, phen_base, stuck)

        if ohm is None:
            card["block_class"] = "device_irregular"
            _wait_sol("阻值不可解析，未列 P=V²/R", f"{ref} 的阻值「{c.get('value') or '空'}」读不出来，没有算功耗")
            missing.append(ref)
            cards.append(card)
            continue
        if pr is None:
            card["block_class"] = "miss_datasheet"
            _wait_sol("缺 P_rated，未与 0.5×P_rated 比较", f"{ref} 的额定功率没有从规格书抽出，没有和一半额定功率比较")
            missing.append(ref)
            cards.append(card)
            continue

        limit = 0.5 * float(pr)
        card["limit"] = limit
        card["datasheet"]["extracted"]["p_rated_w"] = float(pr)

        if block.get("in_index"):
            stress = as_stress(
                kg,
                block,
                ref,
                n_a,
                n_b,
                float(ohm),
                project_resistor(block, ref, float(ohm), limit),
            )
            card["before"] = stress.get("before") or {}
            card["stress_kind"] = stress.get("stress_kind")
            card["drive_kind"] = stress.get("drive_kind")
        nets = _nets(kg)
        v = stress.get("v")
        i = None
        for n in (n_a, n_b):
            iw = (nets.get(n) or {}).get("i_work") or {}
            if iw.get("status") == "closed" and iw.get("value") is not None:
                i = float(iw["value"])
                break

        path_missing = list(stress.get("path_missing_fields") or fan.get("missing_fields") or [])
        assumed = list(stress.get("assumed_zero_drops") or [])

        if stress.get("block_class") == "topology_open" or (
            v is None
            and i is None
            and not (stress.get("cases") or [])
            and _resistor_dc_path_open(ref, c, kg)
        ):
            note = str(stress.get("note") or f"网 {n_a}/{n_b}")
            card["block_class"] = "topology_open"
            _wait_sol(note if stress.get("note") else "未列方程（拓扑开路）", f"{ref} 两端电压算不出来：{note}")
            card["after"] = {"v": None, "i": None, "note": note}
            missing.append(ref)
            cards.append(card)
            continue

        if stress.get("false_close") or stress.get("block_class") == "rule_gap":
            card["block_class"] = "rule_gap"
            _wait_sol(
                str(stress.get("false_close") or stress.get("note") or "路径器件未列入方程"),
                f"{ref} 路径上还有器件没写进功耗式子：" + str(stress.get("false_close") or stress.get("note") or "式子不完整"),
            )
            card["after"] = {
                "v": None,
                "i": None,
                "note": stress.get("false_close") or stress.get("note"),
                "cases": list(stress.get("cases") or []),
            }
            missing.append(ref)
            cards.append(card)
            continue

        if v is None and i is None:
            stuck_bits = [f"{ref} 两端对地电压还没有同时定下来，没有算功耗"]
            if stress.get("note"):
                stuck_bits.append(str(stress.get("note")))
            if path_missing:
                stuck_bits.append("路径上缺：" + "、".join(path_missing[:6]))
            card["block_class"] = "miss_stress"
            _wait_sol("两端对地电位未同时闭合，未列 P=V²/R", "。".join(stuck_bits))
            card["after"] = {"v": None, "i": None, "note": "未闭合"}
            missing.append(ref)
            cards.append(card)
            continue

        kind = stress.get("stress_kind") or "operating_point"
        v_source = stress.get("v_source")
        cases = list(stress.get("cases") or [])
        states = list(stress.get("states") or [])
        if not cases and states:
            cases = [
                {
                    "name_zh": s.get("name") or f"态{i+1}",
                    "v_across": s.get("v_across"),
                    "va": s.get("va"),
                    "vb": s.get("vb"),
                    "p_w": s.get("p_w"),
                    "va_trace": s.get("va_trace"),
                    "vb_trace": s.get("vb_trace"),
                    "note": s.get("note"),
                    "driver_mask": s.get("driver_mask"),
                    "i_dc": s.get("i_dc"),
                    "dc_open_cite": s.get("dc_open_cite"),
                }
                for i, s in enumerate(states)
            ]
        if not states and cases:
            states = [
                {
                    "name": c.get("name_zh"),
                    "v_across": c.get("v_across"),
                    "va": c.get("va"),
                    "vb": c.get("vb"),
                    "p_w": c.get("p_w"),
                    "va_trace": c.get("va_trace"),
                    "vb_trace": c.get("vb_trace"),
                    "note": c.get("note"),
                    "driver_mask": c.get("driver_mask"),
                    "i_dc": c.get("i_dc"),
                    "dc_open_cite": c.get("dc_open_cite"),
                }
                for c in cases
            ]
        drive_kind = str(stress.get("drive_kind") or ("常通" if not cases or len(cases) <= 1 else "多态"))
        n_cases = len(cases) if cases else len(states)
        if n_cases <= 0:
            drive_phen = f"{drive_kind} 0态"
        elif n_cases == 1:
            drive_phen = f"{drive_kind} 1态"
        else:
            names = [str(c.get("name_zh") or "") for c in cases] if cases else [
                str(s.get("name") or "") for s in states
            ]
            short = "/".join(x.split("；")[0] for x in names if x)[:48]
            drive_phen = f"{drive_kind} {n_cases}态" + (f"：{short}" if short else "")

        extra_given = [
            {
                "sym": f"R({ref})",
                "val": sol_fmt_ohm(ohm),
                "cite": f"图纸/BOM value={ident.get('value_bom') or ident.get('value_pdf') or ident.get('value_tel') or c.get('value')}",
            },
            {
                "sym": "P_rated",
                "val": f"{pr} W",
                "cite": _ladder_cite(_params(c), "p_rated_w", pr, ds_note),
            },
            {
                "sym": "限额",
                "val": f"0.5 × P_rated = {float(limit):.6g} W",
                "cite": "P11-A01 降额",
            },
        ]

        if v is not None:
            powers = [
                float(item["p_w"])
                for item in cases
                if item.get("p_w") is not None and item.get("closes_verdict") is not False
            ]
            p = max(powers) if powers else (float(v) * float(v)) / ohm
            if not cases:
                cases = [
                    {
                        "name_zh": "常通",
                        "v_across": float(v),
                        "p_w": p,
                        "va": None,
                        "vb": None,
                    }
                ]
            card["after"] = {
                "v": float(v),
                "source": v_source,
                "R": ohm,
                "stress_kind": kind,
                "net_a": n_a,
                "net_b": n_b,
                "states": states,
                "cases": cases,
                "assumed_zero_drops": assumed,
                "drive_kind": drive_kind,
            }
            sol = states_power(
                ref, n_a, n_b, ohm, cases, limit, assumed=assumed, extra_given=extra_given
            )
        else:
            p = (i * i) * ohm
            kind = "operating_point"
            drive_phen = "电流工作点 1态"
            card["after"] = {"i": i, "R": ohm, "stress_kind": "operating_point"}
            steps = [
                f"I = {i} A    网工作电流闭合",
                f"P = I²R = {i}² × {ohm} = {sol_fmt_power(p)}",
            ]
            if p > limit:
                steps.append(f"{sol_fmt_power(p)} > {sol_fmt_power(limit)} → 本态 否")
                st_zh = "否"
            else:
                steps.append(f"{sol_fmt_power(p)} ≤ {sol_fmt_power(limit)} → 本态 是")
                st_zh = "是"
            sol = pack_solution(
                given=extra_given + [{"sym": "I", "val": f"{i} A", "cite": "网 i_work 闭合"}],
                states=[{"name": "电流工作点", "steps": steps, "verdict_zh": st_zh}],
                rule="任一态 P > 限额 → 整卡 否",
            )
        card["stress_kind"] = kind
        card["states"] = states
        card["cases"] = cases
        card["drive_kind"] = drive_kind
        card["computed"] = {"P_max_w": p, "half_P_rated_w": limit, "margin_w": limit - p}

        phen = phen_base + f"；{drive_phen}"
        rid, assumptions, match_ok = p11_card_recipe(stress, kg)
        card["recipe_id"] = rid
        card["assumptions"] = assumptions
        zh = sol.get("verdict_zh")
        if zh in {"是", "否"} and (not rid or not match_ok):
            zh = "待确认"
            sol = dict(sol)
            sol["verdict_zh"] = "待确认"
            card["block_class"] = "rule_gap"
            stuck = (
                f"{ref} 的工作状态没有对上已命名的算法，不能判通过或不通过"
                if not match_ok
                else f"{ref} 还没有对应的命名算法，不能判通过或不通过"
            )
            missing.append(ref)
            _sol(card, sol, phen, stuck)
            card["verdict_zh"] = "待确认"
            card["conclusion"] = f"待确认 · {stuck}"
            card["summary"] = card["conclusion"]
            cards.append(card)
            continue
        if zh == "待确认" and assumed:
            card["block_class"] = "miss_datasheet"
            stuck = f"{ref} 路径上缺规格书参数：" + "、".join(assumed[:6])
            missing.append(ref)
        elif zh == "待确认":
            card["block_class"] = card.get("block_class") or "miss_stress"
            stuck = card.get("stuck") or "待确认"
            missing.append(ref)
        elif zh == "否":
            card["block_class"] = None
            stuck = "无"
            fail.append(sol.get("aggregate_line") or f"{ref} 否")
        else:
            card["block_class"] = None
            stuck = "无"
            closed.append(ref)
        _sol(card, sol, phen, stuck)
        cards.append(card)

    cr.object_cards = cards
    cr.measured = [f"{x}:ok" for x in closed[:20]] + fail[:20]
    by_bc: dict[str, list[str]] = {}
    for card in cards:
        if card.get("verdict_zh") in {"待确认", "INSUFFICIENT"} and card.get("block_class"):
            by_bc.setdefault(card["block_class"], []).append(card["object"])
    note_parts = [f"closed={len(closed)} fail={len(fail)} missing={len(missing)}"]
    for bc, refs_bc in sorted(by_bc.items()):
        note_parts.append(
            f"{bc}:{len(refs_bc)}({','.join(refs_bc[:8])}{'…' if len(refs_bc) > 8 else ''})"
        )
    cr.add_step("算P_max", "; ".join(note_parts), n=4)
    cr.notes = note_parts
    if fail:
        cr.verdict_zh = "否"
        cr.violations = fail
    elif missing:
        cr.verdict_zh = "INSUFFICIENT"
    elif rs:
        cr.verdict_zh = "是"
    return cr


def check_p11_a03(kg: dict) -> CheckResult:
    """电容耐压：同一直流工况的 |Va−Vb| ≤ 0.5×Vr。"""
    cs = _assembled(kg, "capacitor")
    cr = CheckResult(
        "P11-A03",
        "INSUFFICIENT",
        title=TITLES["P11-A03"],
        objects=[r for r, _ in cs],
        coverage_n=len(cs),
        card_class="stress_calc",
    )
    cards: list[dict[str, Any]] = []
    fail: list[str] = []
    missing: list[str] = []
    closed_ok: list[str] = []
    from kernel.dc_modes import capacitor_view, rule_needs_dc_modes

    if not rule_needs_dc_modes("P11-A03"):
        cr.verdict_zh = "INSUFFICIENT"
        cr.add_step("取工况", "P11-A03 未登记 needs_dc_modes，不调用直流工况分析器", n=2)
        cr.object_cards = [na_explain_card("P11-A03", "未登记直流工况")]
        return cr
    cr.add_step("列出装配电容", f"N={len(cs)}", n=1)
    cr.add_step("取同一档节点电压", "电容直流开路，跨压=|Va−Vb|，不把电源网名当成端电压", n=2)
    if not cs:
        cr.verdict_zh = "不适用"
        cr.object_cards = [na_explain_card("P11-A03", "无装配电容")]
        return cr

    for ref, c in cs:
        pins = _pin_nets(c)
        n1 = pins[0][1] if pins else ""
        n2 = pins[1][1] if len(pins) > 1 else ""
        vr = _params(c).get("rated_voltage_v") or c.get("voltage") or c.get("rated_voltage")
        vr_src = _params(c).get("rated_voltage_source")
        if isinstance(vr_src, dict) and vr_src.get("kind"):
            from kernel.source_cite import format_source

            vr_cite = format_source(vr_src)
        elif vr is None:
            vr_cite = "未抽出：程序已走规格书、立创、联网"
        else:
            vr_cite = str(_params(c).get("extract_note") or "标注")
        if vr is None:
            blob = " ".join(str(x or "") for x in (c.get("value"), c.get("mpn"), c.get("desc")))
            m = _CAP_V.search(blob)
            vr = float(m.group(1)) if m else None
            if vr is not None and not (isinstance(vr_src, dict) and vr_src.get("kind")):
                vr_cite = str(_params(c).get("extract_note") or "标注")
        else:
            try:
                vr = float(vr)
            except (TypeError, ValueError):
                vr = None
        pol = c.get("polarity") or ("polar" if re.search(r"电解|tant", str(c.get("mpn") or ""), re.I) else "")
        env = capacitor_view(kg, n1, n2)
        cases = list(env.get("cases") or [])
        drive_kind = str(env.get("drive_kind") or "直流工况")
        v_max = env.get("v_max")
        card = base_card(ref, c, verdict_zh="待确认")
        card["nets"] = {"a": n1, "b": n2}
        card["before"] = {
            n1: {"net": n1},
            n2: {"net": n2},
        }
        card["cases"] = cases
        card["drive_kind"] = drive_kind
        n_cases = len(cases)
        if n_cases <= 0:
            drive_phen = f"{drive_kind} 0态"
        elif n_cases == 1:
            drive_phen = f"{drive_kind} 1态"
        else:
            names = "/".join(str(x.get("name_zh") or "") for x in cases)[:48]
            drive_phen = f"{drive_kind} {n_cases}态：{names}"
        pol_txt = "有极性" if pol else "未标极性"
        phen = f"{ref} 两端网 {n1}、{n2}，{pol_txt}，{drive_phen}"

        if vr is None:
            card["block_class"] = "miss_datasheet"
            sol = pack_solution(
                given=[{"sym": "Vr", "val": "缺", "cite": vr_cite if vr is None else "未抽出：程序已走规格书、立创、联网"}],
                gates=[gate("Vr 抽出", False, "缺额定耐压，未比 0.5×Vr", wait=True)],
                rule="任一态 |Va−Vb| > 限额 → 整卡 否；缺证据不判否",
                verdict_zh="待确认",
            )
            _sol(card, sol, phen, f"{ref} 的额定耐压没有从规格书抽出，没有和一半耐压比较")
            missing.append(ref)
            cards.append(card)
            continue
        limit = 0.5 * float(vr)
        card["limit"] = limit
        card["datasheet"]["extracted"]["rated_voltage_v"] = float(vr)

        if env.get("status") != "closed" or v_max is None:
            card["block_class"] = "miss_stress"
            sol = pack_solution(
                given=[{"sym": "Vr", "val": f"{vr} V", "cite": vr_cite}],
                gates=[gate("两端电位闭合", False, "两端没有落在同一档已解工况", wait=True)],
                rule="任一态 |Va−Vb| > 限额 → 整卡 否；缺证据不判否",
                verdict_zh="待确认",
            )
            _sol(
                card,
                sol,
                phen,
                str(env.get("note") or "两端没有落在同一档已解工况"),
            )
            missing.append(ref)
            cards.append(card)
            continue

        v_across = float(v_max)

        extra_given = [
            {"sym": "Vr", "val": f"{vr} V", "cite": vr_cite},
            {"sym": "限额", "val": f"0.5 × Vr = {sol_fmt_volt(limit)}", "cite": "P11-A03 降额"},
        ]
        sol = states_voltage(ref, n1, n2, cases, limit, extra_given=extra_given)
        card["after"] = {
            "v": v_across,
            "cases": cases,
            "drive_kind": drive_kind,
            "va": (cases[0].get("va") if cases else None),
            "vb": (cases[0].get("vb") if cases else None),
        }
        card["computed"] = {"v_work_v": v_across, "half_vr_v": limit}
        card["stress_kind"] = "p_max_envelope" if len(cases) > 1 else "operating_point"
        card["states"] = [
            {
                "name": c.get("name_zh"),
                "v_across": c.get("v_across"),
                "va": c.get("va"),
                "vb": c.get("vb"),
                "va_trace": c.get("va_trace"),
                "vb_trace": c.get("vb_trace"),
                "note": c.get("note"),
            }
            for c in cases
        ]
        zh = sol.get("verdict_zh")
        rid, assumptions, match_ok = p11_card_recipe(
            {
                "stress_kind": "dc_modes",
                "drive_kind": drive_kind,
                "drivers": [],
                "v_source": {"kind": "dc_modes", "block_id": env.get("block_id")},
                "cases": cases,
                "nets": {"a": n1, "b": n2},
            },
            kg,
        )
        card["recipe_id"] = rid
        card["assumptions"] = assumptions
        if zh in {"是", "否"} and (not rid or not match_ok):
            zh = "待确认"
            sol = dict(sol)
            sol["verdict_zh"] = "待确认"
            card["block_class"] = "rule_gap"
            stuck = (
                f"{ref} 的工作状态没有对上已命名的算法，不能判通过或不通过"
                if not match_ok
                else f"{ref} 还没有对应的命名算法，不能判通过或不通过"
            )
            missing.append(ref)
            _sol(card, sol, phen, stuck)
            card["verdict_zh"] = "待确认"
            card["conclusion"] = f"待确认 · {stuck}"
            card["summary"] = card["conclusion"]
            cards.append(card)
            continue
        if zh == "否":
            fail.append(sol.get("aggregate_line") or ref)
            stuck = "无"
        elif zh == "待确认":
            missing.append(ref)
            stuck = "待确认"
        else:
            closed_ok.append(ref)
            stuck = "无"
        _sol(card, sol, phen, stuck)
        cards.append(card)

    cr.object_cards = cards
    cr.measured = [f"{r}:ok" for r in closed_ok[:20]] + fail[:20]
    cr.add_step("比0.5×Vr", f"ok={len(closed_ok)} fail={len(fail)} miss={len(missing)}", n=3)
    if fail:
        cr.verdict_zh = "否"
        cr.violations = fail
    elif missing:
        cr.verdict_zh = "INSUFFICIENT"
    else:
        cr.verdict_zh = "是"
    return cr


def check_p11_a07(kg: dict) -> CheckResult:
    ls = _assembled(kg, "inductor")
    cr = CheckResult(
        "P11-A07",
        "INSUFFICIENT",
        title=TITLES["P11-A07"],
        objects=[r for r, _ in ls],
        coverage_n=len(ls),
        card_class="stress_calc",
    )
    cr.sub_questions = [{"q": "Isat", "status": "pending"}, {"q": "温升", "status": "pending"}]
    cr.add_step("识别全部电感", f"N={len(ls)}", n=1)
    cards: list[dict[str, Any]] = []
    if not ls:
        cr.verdict_zh = "不适用"
        cr.add_step("比Isat≥1.5×I_sat_cmp", "无电感", "N/A", n=3)
        cr.add_step("读曲线算M>10℃", "无电感", "N/A", n=4)
        cr.object_cards = [na_explain_card("P11-A07", "无装配电感")]
        return cr
    tamb = _oc(kg).get("t_amb_c")
    isat_fail, isat_ok, isat_miss = [], [], []
    th_fail, th_ok, th_miss = [], [], []
    for ref, c in ls:
        p = _params(c)
        isat = p.get("isat")
        i_cmp = None
        for _, net in _pin_nets(c):
            iw = (_nets(kg).get(net) or {}).get("i_work") or {}
            if iw.get("status") == "closed" and iw.get("value") is not None:
                i_cmp = float(iw["value"])
                break
        dc_open = None
        if i_cmp is None:
            dc_open = two_terminal_dc_open(ref, c, kg)
            if dc_open:
                i_cmp = 0.0
        card = base_card(ref, c, verdict_zh="待确认")
        phen = f"{ref} 饱和电流={isat if isat is not None else '缺'}，工作电流={i_cmp if i_cmp is not None else '缺'}，环境温度={tamb if tamb is not None else '缺'}"
        isat_fail_one = False
        if isat is None or i_cmp is None:
            isat_miss.append(ref)
            stuck_isat = "饱和电流或工作电流还没有数"
        elif float(isat) < 1.5 * float(i_cmp):
            isat_fail.append(f"{ref}:Isat={isat}<1.5*{i_cmp}")
            isat_fail_one = True
            stuck_isat = "无"
        else:
            isat_ok.append(ref)
            stuck_isat = "无"
        tmax = p.get("t_max_c")
        dt = p.get("delta_t") or p.get("dt_c")
        th_fail_one = False
        m_val = None
        if tmax is None or tamb is None or dt is None:
            th_miss.append(ref)
            stuck_th = "最高温度、环境温度或温升曲线读数还没有，温升不能口头估"
        else:
            m_val = float(tmax) - float(tamb) - float(dt)
            if m_val > 10:
                th_ok.append(ref)
                stuck_th = "无"
            else:
                th_fail.append(f"{ref}:M={m_val}")
                th_fail_one = True
                stuck_th = "无"
        g_isat: dict[str, Any]
        if isat is None or i_cmp is None:
            g_isat = gate("Isat 门", None, "饱和电流或工作电流还没有数", wait=True)
        elif float(isat) < 1.5 * float(i_cmp):
            g_isat = gate("Isat 门", False, f"Isat={isat} < 1.5×{i_cmp}")
        else:
            g_isat = gate("Isat 门", True, f"Isat={isat} ≥ 1.5×{i_cmp}")
        g_th: dict[str, Any]
        if tmax is None or tamb is None or dt is None:
            g_th = gate("温升门", None, "最高温度、环境温度或温升曲线读数还没有，温升不能口头估", wait=True)
        elif m_val is not None and m_val > 10:
            g_th = gate("温升门", True, f"M=Tmax−Tamb−ΔT={m_val} > 10℃")
        else:
            g_th = gate("温升门", False, f"M={m_val} 不大于 10℃")
        sol = pack_solution(
            given=[
                {"sym": "Isat", "val": "缺" if isat is None else str(isat), "cite": _ladder_cite(_params(c), "isat", isat, "")},
                {"sym": "I_work", "val": "缺" if i_cmp is None else str(i_cmp), "cite": ("无直流通路 I=0" if dc_open else "网 i_work")},
                {"sym": "Tmax", "val": "缺" if tmax is None else str(tmax), "cite": _ladder_cite(_params(c), "t_max_c", tmax, "")},
                {"sym": "Tamb", "val": "缺" if tamb is None else str(tamb), "cite": "工况"},
                {"sym": "ΔT", "val": "缺" if dt is None else str(dt), "cite": "规格书曲线"},
            ],
            gates=[g_isat, g_th],
            rule="Isat 门或温升门任一否 → 整卡 否",
        )
        if sol.get("verdict_zh") == "否":
            stuck = "无"
        elif sol.get("verdict_zh") == "待确认":
            card["block_class"] = "miss_datasheet" if isat is None or tmax is None or dt is None else "miss_taskbook"
            stuck = f"{stuck_isat}；{stuck_th}"
        else:
            stuck = "无"
        _sol(card, sol, phen, stuck)
        cards.append(card)
    cr.add_step("比Isat≥1.5×I_sat_cmp", f"ok={len(isat_ok)} fail={len(isat_fail)} miss={len(isat_miss)}", n=3)
    cr.add_step("读曲线算M>10℃", f"ok={len(th_ok)} fail={len(th_fail)} miss={len(th_miss)}（缺ΔT不得口头）", n=4)
    cr.violations = isat_fail + th_fail
    cr.sub_questions = [
        {"q": "Isat", "status": "fail" if isat_fail else ("ok" if not isat_miss else "pending")},
        {"q": "温升", "status": "fail" if th_fail else ("ok" if not th_miss else "pending")},
    ]
    cr.object_cards = cards
    if isat_fail or th_fail:
        cr.verdict_zh = "否"
    elif isat_miss or th_miss:
        cr.verdict_zh = "INSUFFICIENT"
        cr.notes = ["缺 Isat/I_sat_cmp 或缺 Tmax/Tamb/ΔT，禁止口头ΔT"]
    else:
        cr.verdict_zh = "是"
    return cr


def check_p12_a01(kg: dict) -> CheckResult:
    from kernel.resistor_network import (
        _is_oc_pull_down_net,
        _net_attachments,
        _switch_ce_nets,
        SWITCH_TYPES,
    )

    ics = _assembled(kg, "ic")
    cr = CheckResult(
        "P12-A01", "INSUFFICIENT", title=TITLES["P12-A01"], objects=[r for r, _ in ics], card_class="stress_calc"
    )
    rails_list = []
    measured, fail, missing = [], [], []
    cards: list[dict[str, Any]] = []
    nets = _nets(kg)
    comps = kg.get("components") or {}

    def _net_switched(net: str) -> tuple[str, str] | None:
        """If net is OC or switch CE, return (kind, label)."""
        if _is_oc_pull_down_net(net, comps):
            return ("oc", f"{net} OC")
        for ref, _pin, typ in _net_attachments(net, comps):
            if typ not in SWITCH_TYPES:
                continue
            ce = _switch_ce_nets(ref, comps.get(ref) or {})
            if ce and net in ce:
                return ("switch", ref)
        return None

    for ref, c in ics:
        pt = _pintable(c)
        power_pins: list[tuple[str, str, dict, str]] = []
        for pin, net in _pin_nets(c):
            if not net or net in _GROUND:
                continue
            nd = nets.get(net) or {}
            role = pin_supply_role(c, pin)
            sw = _net_switched(net)
            if role in {"vi", "vo"}:
                power_pins.append((pin, net, nd, role))
                rails_list.append(f"{ref}.{pin}:{net}:{role}")
                continue
            if _NEEDLE_POWER.search(pin or ""):
                power_pins.append((pin, net, nd, "vcc"))
                rails_list.append(f"{ref}.{pin}:{net}:vcc")
                continue
            if (_POWER_NET.match(net) or str(net).startswith("+")) and not sw:
                power_pins.append((pin, net, nd, "vcc"))
                rails_list.append(f"{ref}.{pin}:{net}:vcc")
        card = base_card(ref, c, verdict_zh="待确认")
        pin_cases_preview: list[dict[str, Any]] = []
        for pin, net, nd, role in power_pins:
            rv = _closed_rail(nd)
            sw = _net_switched(net)
            lo, hi, lab = range_for_role(pt, role)
            base = {"pin": pin, "net": net, "role": role, "lo": lo, "hi": hi, "lab": lab}
            if sw:
                kind, label = sw
                pin_cases_preview.append(
                    {
                        **base,
                        "drive": kind,
                        "cases": [
                            {"name_zh": f"{label}导通", "v": 0.0},
                            {"name_zh": f"{label}截止/常通", "v": float(rv) if rv is not None else None},
                        ],
                    }
                )
            elif rv is not None:
                pin_cases_preview.append({**base, "drive": "常通", "cases": [{"name_zh": "常通", "v": float(rv)}]})
            else:
                pin_cases_preview.append({**base, "drive": "未闭合", "cases": []})
        card["cases"] = pin_cases_preview
        mode_phen = "；".join(
            f"pin{pc.get('pin')} 网 {pc.get('net')}（{pc.get('lab')}）={'导通和截止两态' if pc.get('drive') in ('oc', 'switch') else ('常通' if pc.get('drive') == '常通' else '电压未定')}"
            for pc in pin_cases_preview
        ) or "没有识别出供电脚"
        miss_range = [pc for pc in pin_cases_preview if pc.get("lo") is None or pc.get("hi") is None]
        if not power_pins or (miss_range and all(pc.get("lo") is None or pc.get("hi") is None for pc in pin_cases_preview)):
            missing.append(ref)
            sol = pack_solution(
                given=[{"sym": "Vcc 推荐", "val": "缺", "cite": _vcc_cite(c)}],
                gates=[gate("推荐 Vcc", None, "推荐工作电压没有从规格书抽出", wait=True)],
                rule="任一供电脚有电态越出推荐范围 → 整卡 否；掉电不比 Vcc_min",
                verdict_zh="待确认",
            )
            _sol(card, sol, f"{ref} 供电脚：{mode_phen}", f"{ref} 的推荐工作电压没有从规格书抽出")
            card["block_class"] = "miss_datasheet"
            cards.append(card)
            continue
        pin_cases = [pc for pc in pin_cases_preview if pc.get("lo") is not None and pc.get("hi") is not None]
        closed = []
        for pc in pin_cases:
            net = str(pc.get("net") or "")
            nd = nets.get(net) or {}
            if _used_candidate(nd):
                continue
            rv = _closed_rail(nd)
            if rv is None:
                continue
            mode = "开关2态" if pc.get("drive") in ("oc", "switch") else "常通1态"
            closed.append((net, float(rv), (nd.get("rail") or {}).get("source"), mode))
        if not closed:
            missing.append(ref)
            lo0, hi0 = pin_cases[0].get("lo"), pin_cases[0].get("hi")
            sol = pack_solution(
                given=[
                    {"sym": "Vcc_min", "val": f"{lo0} V", "cite": _vcc_cite(c)},
                    {"sym": "Vcc_max", "val": f"{hi0} V", "cite": _vcc_cite(c)},
                ],
                gates=[gate("闭合轨压", None, "这根网的电压只是网名上的数，还没被印证，不能判通过", wait=True)],
                rule="任一供电脚有电态越出推荐范围 → 整卡 否；掉电不比 Vcc_min",
                verdict_zh="待确认",
            )
            _sol(card, sol, f"{ref} 推荐工作电压已抽出；{mode_phen}", f"{ref} 供电网上的电压还没有定下来")
            card["block_class"] = "miss_stress"
            cards.append(card)
            continue
        states_sol: list[dict[str, Any]] = []
        given = []
        seen_lab: set[str] = set()
        for pc in pin_cases:
            lab = str(pc.get("lab") or "Vcc")
            if lab not in seen_lab:
                seen_lab.add(lab)
                given.append({"sym": f"{lab}_min", "val": f"{pc.get('lo')} V", "cite": _vcc_cite(c)})
                given.append({"sym": f"{lab}_max", "val": f"{pc.get('hi')} V", "cite": _vcc_cite(c)})
            net = str(pc.get("net") or "")
            nd = nets.get(net) or {}
            src = (nd.get("rail") or {}).get("source")
            vmin, vmax = float(pc["lo"]), float(pc["hi"])
            for cs in pc.get("cases") or []:
                vv = cs.get("v")
                name = f"{pc.get('pin')}/{net} {cs.get('name_zh')}"
                if vv is None:
                    continue
                fv = float(vv)
                is_off = pc.get("drive") in ("oc", "switch") and abs(fv) < 1e-9
                steps = [f"V({net}) = {sol_fmt_volt(fv)}"]
                if is_off:
                    steps.append(f"掉电≈0 V，不与 {lab}_min 比较 → 本态 不参评")
                    states_sol.append({"name": name, "steps": steps, "verdict_zh": "—"})
                    continue
                measured.append(f"{ref} {net}={fv}V source={src} {lab}[{vmin},{vmax}]")
                steps.append(f"{lab} 推荐 [{vmin}, {vmax}] V")
                if fv < vmin or fv > vmax:
                    steps.append(f"{sol_fmt_volt(fv)} 超出 [{vmin}, {vmax}] → 本态 否")
                    states_sol.append({"name": name, "steps": steps, "verdict_zh": "否"})
                    fail.append(f"{ref} {net}={fv} 超出{lab} [{vmin},{vmax}]")
                else:
                    steps.append(f"{sol_fmt_volt(fv)} 在推荐范围内 → 本态 是")
                    states_sol.append({"name": name, "steps": steps, "verdict_zh": "是"})
        if miss_range:
            missing.append(ref)
            sol = pack_solution(
                given=given or [{"sym": "Vcc 推荐", "val": "部分缺", "cite": _vcc_cite(c)}],
                states=states_sol,
                rule="任一供电脚有电态越出推荐范围 → 整卡 否；掉电不比 Vcc_min",
                verdict_zh="待确认",
            )
            _sol(card, sol, f"{ref} 供电脚：{mode_phen}", f"{ref} 有的供电脚推荐工作电压没有抽出")
            card["block_class"] = "miss_datasheet"
            cards.append(card)
            continue
        sol = pack_solution(
            given=given,
            states=states_sol,
            rule="任一供电脚有电态越出推荐范围 → 整卡 否；掉电不比 Vcc_min",
        )
        phen = f"{ref} 供电脚：{mode_phen}"
        stuck = "无" if sol.get("verdict_zh") in {"是", "否"} else f"{ref} 供电脚上的电压还没有全部定下来"
        _sol(card, sol, phen, stuck)
        cards.append(card)
    cr.steps = []
    cr.add_step("列供电脚与来源", f"ics={len(ics)} rails={len(rails_list)}", n=1)
    cr.measured = measured[:30]
    cr.object_cards = cards
    cr.notes = ["需推荐栏+闭合轨压；开关网列通/断；常通脚标常通1态；网名 candidate 不得 PASS"]
    if not ics:
        cr.verdict_zh = "不适用"
        cr.object_cards = [na_explain_card("P12-A01", "无装配 IC")]
    elif fail:
        cr.verdict_zh = "否"
        cr.violations = fail
    elif missing:
        cr.verdict_zh = "INSUFFICIENT"
    elif ics and measured:
        cr.verdict_zh = "是"
    else:
        cr.verdict_zh = "INSUFFICIENT"
    return cr


def _p12_wait_batch(
    rule_id: str,
    title: str,
    objs: list[tuple[str, dict[str, Any]] | str],
    *,
    na_reason: str,
    stuck: str,
    card_class: str = "stress_calc",
) -> CheckResult:
    """一期占位：能枚举对象则整项待确认；无对象则不适用。不猜数字升绿。"""
    refs: list[str] = []
    pairs: list[tuple[str, dict[str, Any]]] = []
    for o in objs:
        if isinstance(o, tuple):
            refs.append(o[0])
            pairs.append(o)
        else:
            refs.append(str(o))
            pairs.append((str(o), {}))
    cr = CheckResult(
        rule_id,
        "INSUFFICIENT",
        title=title,
        objects=refs[:40],
        coverage_n=len(refs),
        card_class=card_class,
    )
    cr.add_step("枚举对象", f"N={len(refs)}", n=1)
    if not refs:
        cr.verdict_zh = "不适用"
        cr.object_cards = [na_explain_card(rule_id, na_reason)]
        return cr
    cards: list[dict[str, Any]] = []
    for ref, c in pairs[:40]:
        card = base_card(ref, c or None, verdict_zh="待确认", block_class="miss_evidence")
        fill_four(
            card,
            phenomenon=f"{ref} 已列入 {rule_id} 检查对象",
            calc=f"判据见规则详情 {rule_id}；程序本轮只枚举，不替代闭合证据",
            stuck=stuck,
            conclusion=f"待确认 · {ref} 关键额定/工况未闭合",
        )
        cards.append(card)
    cr.object_cards = cards
    cr.notes = ["一期程序占位：有对象→待确认；闭合证据齐备后再实现完整算法"]
    return cr


def _mosfet_objs(kg: dict) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for ref, c in _comps(kg).items():
        if _dnp(c):
            continue
        typ = _type(c, ref)
        blob = " ".join(str(x or "") for x in (c.get("mpn"), c.get("value"), c.get("type"), ref)).upper()
        if typ in {"mosfet", "fet", "jfet"} or "MOSFET" in blob:
            out.append((ref, c))
            continue
        if typ in {"transistor", "bjt"} and re.match(r"^Q\d", ref or "", re.I):
            if any(k in blob for k in ("MOS", "FET", "BSS", "AO34", "SI23", "2N700")):
                out.append((ref, c))
    return sorted(out, key=lambda x: x[0])


def _tvs_objs(kg: dict) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for ref, c in _comps(kg).items():
        if _dnp(c):
            continue
        typ = _type(c, ref)
        blob = " ".join(
            str(x or "") for x in (c.get("mpn"), c.get("value"), c.get("type"), c.get("description"))
        ).upper()
        if "TVS" in blob or "ESD" in blob or "TRANSIENT" in blob:
            out.append((ref, c))
            continue
        if typ == "diode" and any(k in blob for k in ("SMAJ", "SMBJ", "SMCJ", "PESD", "USBLC", "SRV05")):
            out.append((ref, c))
    return sorted(out, key=lambda x: x[0])


def _active_objs(kg: dict) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for ref, c in _comps(kg).items():
        if _dnp(c):
            continue
        if _type(c, ref) in {"ic", "transistor", "mosfet", "bjt", "fet", "jfet", "led"}:
            out.append((ref, c))
    return sorted(out, key=lambda x: x[0])


def _multi_ic_signal_nets(kg: dict) -> list[str]:
    nets = _nets(kg)
    by_net: dict[str, set[str]] = {}
    for ref, c in _assembled(kg, "ic"):
        for _pin, net in _pin_nets(c):
            if not net or net in _GROUND:
                continue
            if _is_power_net(net, nets.get(net) or {}):
                continue
            if _INTERNAL_NET.match(net):
                continue
            by_net.setdefault(str(net), set()).add(ref)
    return sorted(n for n, refs in by_net.items() if len(refs) >= 2)


def check_p12_a02(kg: dict) -> CheckResult:
    """与 P21-A01 同算法（电平兼容）；规则编号落 P12-A02，供产品中心选型类覆盖。"""
    from kernel.checkers.extra4 import check_p21_a01

    cr = check_p21_a01(kg)
    cr.rule_id = "P12-A02"
    cr.title = TITLES["P12-A02"]
    # 对象卡内若写了 P21 文案，保持判定逻辑；notes 标明同源
    notes = list(cr.notes or [])
    notes.append("程序与 P21-A01 共用 IO 阈值算法；本项归属器件选型 P12")
    cr.notes = notes
    return cr


def check_p12_a03(kg: dict) -> CheckResult:
    return _p12_wait_batch(
        "P12-A03",
        TITLES["P12-A03"],
        _active_objs(kg),
        na_reason="无装配有源器件",
        stuck="缺项目环境温度边界或手册工作温度等级（不得用存储温度代替）",
    )


def check_p12_a04(kg: dict) -> CheckResult:
    return _p12_wait_batch(
        "P12-A04",
        TITLES["P12-A04"],
        _mosfet_objs(kg),
        na_reason="无装配 MOSFET",
        stuck="缺额定 VDS 或启动/关断等瞬态漏源峰值",
    )


def check_p12_a05(kg: dict) -> CheckResult:
    return _p12_wait_batch(
        "P12-A05",
        TITLES["P12-A05"],
        _mosfet_objs(kg),
        na_reason="无装配 MOSFET",
        stuck="缺匹配条件下的 ID 额定/持续电流，或脉冲工况缺 SOA",
    )


def check_p12_a06(kg: dict) -> CheckResult:
    return _p12_wait_batch(
        "P12-A06",
        TITLES["P12-A06"],
        _tvs_objs(kg),
        na_reason="无装配 TVS/ESD 保护器件",
        stuck="缺 VRWM/钳位数据，或浪涌与 ESD 威胁/后级适用限制未闭合",
    )


def check_p12_a07(kg: dict) -> CheckResult:
    return _p12_wait_batch(
        "P12-A07",
        TITLES["P12-A07"],
        _assembled(kg, "connector"),
        na_reason="无装配连接器",
        stuck="缺项目电流/插拔寿命/防护需求或厂家额定（不自补统一比例门槛）",
    )


def check_p14_a02(kg: dict) -> CheckResult:
    conns = _assembled(kg, "connector")
    ifaces = _task(kg).get("interfaces") or []
    cr = CheckResult(
        "P14-A02", "INSUFFICIENT", title=TITLES["P14-A02"], objects=[r for r, _ in conns], card_class="path_topo"
    )
    entries = [r for r, _ in conns]
    if isinstance(ifaces, list):
        for it in ifaces:
            if isinstance(it, dict) and it.get("name"):
                entries.append(str(it["name"]))
            elif isinstance(it, str) and it.strip():
                entries.append(it.strip())
    cr.add_step("找可能接反的入口", f"entries={entries[:20]}", n=1)
    cards: list[dict[str, Any]] = []
    if not conns and not ifaces:
        cr.verdict_zh = "不适用"
        cr.object_cards = [na_explain_card("P14-A02", "无连接器且任务书无接口")]
        return cr
    caliber = "正接要能供电，反接后级不能吃到反向电压。熔丝不算防反。任务书要写明能否反接，或写出豁免条款号"
    for ref, c in conns:
        card = base_card(ref, c, verdict_zh="待确认", block_class="miss_taskbook" if not ifaces else "rule_gap")
        powers = _entry_power_pins(c, kg)
        entry_nets = {net for _, net in powers}
        others = []
        diode_on_entry = False
        for net in entry_nets:
            hit = _others_on_net(kg, net, ref)
            if any("二极管" in x for x in hit):
                diode_on_entry = True
            others.append(f"网 {net} 上还有 {'、'.join(hit) if hit else '没有其他器件'}")
        down = _fuse_downstream_note(kg, entry_nets)
        if powers:
            phen = f"{ref}：{_fmt_pins(c)}。" + (" ".join(others) + "。" if others else "")
            if not diode_on_entry:
                phen += "进线网上没有二极管。"
            if down:
                phen += down + "。"
            stuck = f"任务书未写 {ref} 能否反接，也没有豁免条款号"
        else:
            phen = f"{ref}：{_fmt_pins(c)}。没有供电针"
            stuck = f"{ref} 没有供电针。任务书未写它能否反接，也没有豁免条款号"
        fill_four(card, phenomenon=phen, calc=caliber, stuck=stuck, conclusion=f"待确认 · {ref} 能否反接任务书未写")
        card["path_refs"] = [ref]
        cards.append(card)
    if not conns and ifaces:
        card = base_card("(interfaces)", verdict_zh="待确认", block_class="rule_gap")
        fill_four(
            card,
            phenomenon=f"任务书写了接口 {'、'.join(entries[:20])}，板上没有连接器",
            calc=caliber,
            stuck="板上没有连接器，防反路径对不上任务书接口",
            conclusion="待确认 · 任务书有接口，板上没有连接器",
        )
        cards.append(card)
    cr.object_cards = cards
    cr.notes = ["按连接器列出针脚和进线网上的器件；能否反接以任务书为准"]
    cr.verdict_zh = "INSUFFICIENT"
    return cr


def check_p14_a06(kg: dict) -> CheckResult:
    conns = _assembled(kg, "connector")
    ifaces = _task(kg).get("interfaces") or []
    cr = CheckResult(
        "P14-A06", "INSUFFICIENT", title=TITLES["P14-A06"], objects=[r for r, _ in conns], card_class="path_topo"
    )
    classified = 0
    hot_names: list[str] = []
    if isinstance(ifaces, list):
        for it in ifaces:
            if isinstance(it, dict) and any(k in it for k in ("hotplug", "power", "供电", "插拔", "插入带电")):
                classified += 1
                hot_names.append(str(it.get("name") or it))
            elif isinstance(it, str) and any(k in it for k in ("供电", "插拔", "热插")):
                classified += 1
                hot_names.append(it)
    cr.add_step("全量接口分类", f"connectors={len(conns)} classified={classified}", n=1)
    cards: list[dict[str, Any]] = []
    caliber = "这项要分别看负载开关、限流、软启动。三样都要有。熔丝不算负载开关，也不能用别的芯片代替"
    if classified == 0:
        if not conns:
            card = base_card("(interfaces)", verdict_zh="待确认", block_class="miss_taskbook")
            fill_four(
                card,
                phenomenon="板上没有连接器，任务书也没有写哪些口是可插拔供电口",
                calc=caliber,
                stuck="任务书未写是否供电口、能否插拔、插入时是否带电",
                conclusion="待确认 · 是否供电口任务书未写",
            )
            cards.append(card)
        for ref, c in conns:
            card = base_card(ref, c, verdict_zh="待确认", block_class="miss_taskbook")
            powers = _entry_power_pins(c, kg)
            if powers:
                pins = "、".join(f"pin{p}={n}" for p, n in powers)
                phen = f"{ref}：{_fmt_pins(c)}。供电针 {pins}"
                stuck = f"任务书未写 {ref} 能否插拔、插入时是否带电"
            else:
                phen = f"{ref}：{_fmt_pins(c)}。没有供电针"
                stuck = f"{ref} 没有供电针。任务书未写它是不是供电口"
            fill_four(
                card,
                phenomenon=phen,
                calc=caliber,
                stuck=stuck,
                conclusion=(
                    f"待确认 · {ref} 的可插拔和插入带电任务书未写"
                    if powers
                    else f"待确认 · {ref} 是不是供电口任务书未写"
                ),
            )
            card["path_refs"] = [ref]
            cards.append(card)
        cr.notes = ["按连接器列出供电针；可插拔和插入带电任务书未写，不判不适用"]
        cr.verdict_zh = "INSUFFICIENT"
    else:
        for name in hot_names or ["(hotplug)"]:
            card = base_card(name, verdict_zh="待确认", block_class="rule_gap")
            fill_four(
                card,
                phenomenon=f"任务书已把 {name} 标成供电或可插拔接口",
                calc=caliber,
                stuck=f"{name} 的负载开关、限流、软启动还没有在进线路径上对上",
                conclusion=f"待确认 · {name} 三项功能还没有对上",
            )
            cards.append(card)
        cr.notes = ["任务书已有分类；三项功能没有对上之前保持待确认"]
        cr.verdict_zh = "INSUFFICIENT"
    cr.object_cards = cards
    return cr


def _fmt_cap_f(farads: float) -> str:
    a = abs(float(farads))
    if a >= 0.1e-6 - 1e-15:
        return f"{float(farads) * 1e6:g} µF"
    if a >= 1e-9:
        return f"{float(farads) * 1e9:g} nF"
    return f"{float(farads) * 1e12:g} pF"


def _cap_hits_target(farads: float, target: float) -> bool:
    return abs(float(farads) - float(target)) <= max(1e-15, abs(float(target)) * 1e-6)


def _cap_value_txt(kg: dict, ref: str, farads: float) -> str:
    raw = str((_comps(kg).get(ref) or {}).get("value") or "").strip()
    return raw or _fmt_cap_f(farads)


def _l78l_pin_role(mpn: str, pin: str) -> str:
    if not re.match(r"^L78L", mpn or "", re.I):
        return ""
    return {"1": "VOUT", "3": "VIN"}.get(str(pin), "")


def _diode_other_nets(kg: dict, net: str) -> list[tuple[str, str]]:
    """(diode ref, other net) for a 2-pin diode with one pin on net."""
    out: list[tuple[str, str]] = []
    for ref, comp in _comps(kg).items():
        if _dnp(comp):
            continue
        if _type(comp, ref) != "diode" and not re.match(r"^D\d", ref or "", re.I):
            continue
        ends = []
        for _, n in _pin_nets(comp):
            if n and n not in _GROUND and n not in ends:
                ends.append(n)
        if len(ends) != 2 or net not in ends:
            continue
        other = ends[0] if ends[1] == net else ends[1]
        out.append((ref, other))
    return out


def check_p16_a01(kg: dict) -> CheckResult:
    ics = _assembled(kg, "ic")
    caps = _assembled(kg, "capacitor")
    cr = CheckResult(
        "P16-A01", "INSUFFICIENT", title=TITLES["P16-A01"], objects=[r for r, _ in ics], card_class="path_topo"
    )
    nets = _nets(kg)
    cap_by_net: dict[str, list[tuple[str, float]]] = {}
    for ref, c in caps:
        f = _parse_cap_f(c.get("value") or "")
        if f is None:
            continue
        for _, net in _pin_nets(c):
            if net and net not in _GROUND:
                cap_by_net.setdefault(net, []).append((ref, f))
    fail = []
    share_block = []
    pin_n = 0
    cards: list[dict[str, Any]] = []
    target = 0.1e-6
    target_txt = _fmt_cap_f(target)
    rule = "任一电源脚未命中目标容量 → 整卡 否"
    for ref, c in ics:
        mpn = str(c.get("mpn") or c.get("value") or "")
        hint = str(_params(c).get("decoupling_hint") or "")
        allow_share = "共享" in hint or "share" in hint.lower()
        pins_by_net: dict[str, list[str]] = {}
        for pin, net in _pin_nets(c):
            nd = nets.get(net) or {}
            if not _is_power_net(net, nd) and not _NEEDLE_POWER.search(pin or ""):
                continue
            if net in _GROUND:
                continue
            pins_by_net.setdefault(net, []).append(pin or "?")
        ordered: list[tuple[str, str]] = []
        seen_pin: set[tuple[str, str]] = set()
        for pin, net in _pin_nets(c):
            pin_s = pin or "?"
            if net not in pins_by_net or (pin_s, net) in seen_pin:
                continue
            seen_pin.add((pin_s, net))
            ordered.append((pin_s, net))
        given: list[dict[str, str]] = []
        if re.match(r"^L78L", mpn, re.I):
            given.extend(
                [
                    {
                        "sym": "手册外接要求",
                        "val": "不要求外接元件",
                        "cite": "封面：No external components are required",
                    },
                    {
                        "sym": "CI_test",
                        "val": "0.33 µF",
                        "cite": "电气特性表测试条件（第7页、第12页）；测试电路，非装机要求",
                    },
                    {
                        "sym": "CO_test",
                        "val": "0.1 µF",
                        "cite": "同上；测试电路，非装机要求",
                    },
                    {
                        "sym": "C_target",
                        "val": target_txt,
                        "cite": "手册未指定必须的高频去耦容量 → 本项缺省目标（不是 0.33 µF）",
                    },
                ]
            )
        else:
            given.append(
                {
                    "sym": "C_target",
                    "val": target_txt,
                    "cite": "手册未指定必须的高频去耦容量 → 本项缺省目标",
                }
            )
        seen_given: set[str] = {str(g["sym"]) for g in given}
        states: list[dict[str, Any]] = []
        pin_fail_here: list[str] = []
        phen_bits: list[str] = []
        for pin, net in ordered:
            role = _l78l_pin_role(mpn, pin)
            st_name = f"脚{pin} {role}".strip()
            clist = cap_by_net.get(net) or []
            hit = [(r, f) for r, f in clist if _cap_hits_target(f, target)]
            miss_caps = [(r, f) for r, f in clist if not _cap_hits_target(f, target)]
            for cref, farads in clist:
                sym = f"C({cref})"
                if sym in seen_given:
                    continue
                seen_given.add(sym)
                raw = _cap_value_txt(kg, cref, farads)
                given.append(
                    {
                        "sym": sym,
                        "val": raw,
                        "cite": f"图纸/BOM value={raw}，网 {net}",
                    }
                )
            excluded: list[tuple[str, str, float, str]] = []
            ex_seen: set[str] = set()
            for diode, other in _diode_other_nets(kg, net):
                for cref, farads in cap_by_net.get(other) or []:
                    if cref in ex_seen:
                        continue
                    ex_seen.add(cref)
                    excluded.append((cref, other, farads, diode))
                    sym = f"C({cref})"
                    if sym in seen_given:
                        continue
                    seen_given.add(sym)
                    raw = _cap_value_txt(kg, cref, farads)
                    given.append(
                        {
                            "sym": sym,
                            "val": raw,
                            "cite": f"图纸/BOM value={raw}，网 {other}，隔 {diode}，不参与本 IC 电源脚",
                        }
                    )
            steps = [f"网 = {net}    网表"]
            if clist:
                bits = "；".join(f"{r} = {_cap_value_txt(kg, r, f)}" for r, f in clist)
                steps.append(f"同网电容 = {bits}    图纸/BOM")
            else:
                steps.append("同网电容 = 无    该电源脚网上没有装配电容")
            steps.append(f"目标 = {target_txt}    本项缺省 C_target")
            for cref, other, farads, diode in excluded:
                raw = _cap_value_txt(kg, cref, farads)
                steps.append(f"{cref} = {raw} 不计入    网 {other}，隔 {diode}，与本脚不同网")
            pin_n += 1
            if hit:
                shown = "、".join(r for r, _ in hit)
                steps.append(f"{shown} = {target_txt} = C_target → 本态 是")
                st_zh = "是"
                phen_bits.append(f"{st_name} 网 {net} 命中 {target_txt}")
            else:
                if miss_caps:
                    shown = "、".join(f"{r}={_cap_value_txt(kg, r, f)}" for r, f in miss_caps)
                    steps.append(f"{shown} ≠ C_target {target_txt} → 本态 否")
                else:
                    steps.append(f"同网无 {target_txt} → 本态 否")
                st_zh = "否"
                msg = f"{ref} 的 pin{pin} 在网 {net} 上，没有 {target_txt} 或手册指定的去耦电容"
                fail.append(msg)
                pin_fail_here.append(msg)
                phen_bits.append(f"{st_name} 网 {net} 未命中 {target_txt}")
            states.append({"name": st_name, "steps": steps, "verdict_zh": st_zh})
            if len(pins_by_net.get(net) or []) > 1 and len(clist) <= 1 and not allow_share:
                share_block.append(
                    f"{ref} 网 {net} 上有脚 {'、'.join(pins_by_net[net])}，同网电容 {len(clist)} 只，手册未写允许共用"
                )
        gates: list[dict[str, Any]] = []
        share_here = [s for s in share_block if s.startswith(ref + " ")]
        stuck = "无"
        block = None
        if not ordered:
            gates.append(gate("电源脚", None, "认不出电源脚就不能判通过", wait=True))
            stuck = f"{ref} 封装上的电源脚没有列出来"
            block = "rule_gap"
            phen = f"{ref}（{mpn or '未写料号'}）没有识别出电源脚"
        elif share_here and not pin_fail_here:
            gates.append(gate("同网共用", None, "同网一只电容不能默认盖住所有电源脚，除非手册写明允许共用", wait=True))
            stuck = f"{ref} 同网多脚是否允许共用一只电容，手册未写"
            block = "rule_gap"
            phen = "；".join(share_here)
        elif pin_fail_here:
            phen = f"{ref} 电源脚：" + "；".join(phen_bits[:4])
        else:
            phen = f"{ref} " + "；".join(phen_bits[:4])
        sol = pack_solution(given=given, states=states, gates=gates, rule=rule)
        card = base_card(ref, c)
        _sol(card, sol, phen, stuck=stuck)
        if block:
            card["block_class"] = block
        if pin_fail_here:
            card["missing_fields"] = ["缺 0.1µF 高频去耦"]
        card["protection_or_decoupling"] = "decoupling"
        cards.append(card)
    cr.measured = [f"power_pins={pin_n}", f"pcb=转PCB"]
    cr.target = ["手册优先否则缺省0.1µF；同网一只电容不能覆盖所有脚；转PCB"]
    cr.notes = ["禁止0.047–0.22µF窗口当通过"]
    cr.add_step("逐脚匹配", f"pins={pin_n} fail={len(fail)} share_block={len(share_block)}", n=3)
    cr.object_cards = cards
    if not ics:
        cr.verdict_zh = "不适用"
        cr.object_cards = [na_explain_card("P16-A01", "无装配 IC")]
    elif fail:
        cr.verdict_zh = "否"
        cr.violations = fail[:40]
    elif share_block:
        cr.verdict_zh = "INSUFFICIENT"
        cr.notes.append(";".join(share_block[:8]))
    elif pin_n == 0:
        cr.verdict_zh = "INSUFFICIENT"
        cr.notes.append("封装电源脚枚举不出")
    else:
        cr.verdict_zh = "是"
    return cr


def check_p18_a02(kg: dict) -> CheckResult:
    xtals = _assembled(kg, "crystal")
    cr = CheckResult(
        "P18-A02", "INSUFFICIENT", title=TITLES["P18-A02"], objects=[r for r, _ in xtals], card_class="formula"
    )
    if not xtals:
        cr.verdict_zh = "不适用"
        cr.add_step("代入公式", "无无源晶体", "N/A", n=4)
        cr.object_cards = [na_explain_card("P18-A02", "无无源晶体")]
        return cr
    c_par = _oc(kg).get("c_par") or _task(kg).get("c_par") or _oc(kg).get("C_par")
    fail: list[str] = []
    missing: list[str] = []
    ok: list[str] = []
    measured: list[str] = []
    cards: list[dict[str, Any]] = []
    caps = _assembled(kg, "capacitor")
    for ref, c in xtals:
        cl = _params(c).get("cl_pf")
        cin = float(_params(c).get("cin_pf") or 0.0)
        ynets = [n for _, n in _pin_nets(c) if n and n not in _GROUND]
        neigh: list[float] = []
        for _cref, cc in caps:
            for _, n in _pin_nets(cc):
                if n in ynets:
                    f = _parse_cap_f(cc.get("value") or "")
                    if f is not None:
                        neigh.append(f * 1e12)
                    break
        card = base_card(ref, c, verdict_zh="待确认")
        if cl is None or len(neigh) < 2 or c_par is None:
            missing.append(ref)
            stuck_bits = []
            if cl is None:
                stuck_bits.append("规格书没有给出负载电容 CL")
            if len(neigh) < 2:
                stuck_bits.append("晶振两脚旁边没有凑齐两只负载电容")
            if c_par is None:
                stuck_bits.append("任务书或工况没有写寄生电容 C_par，不能用 3pF 代替")
            card["block_class"] = "miss_datasheet" if cl is None else "miss_taskbook"
            sol = pack_solution(
                given=[
                    {"sym": "CL", "val": "缺" if cl is None else str(cl), "cite": _ladder_cite(_params(c), "cl_pf", cl, "")},
                    {"sym": "邻容", "val": str(neigh), "cite": "晶振脚邻接电容"},
                    {"sym": "C_par", "val": "缺" if c_par is None else str(c_par), "cite": "工况/任务书"},
                ],
                gates=[gate("CL_eff 可算", None, "；".join(stuck_bits), wait=True)],
                rule="任一颗晶振 CL_eff 相对目标不达标 → 否",
                verdict_zh="待确认",
            )
            _sol(
                card,
                sol,
                f"{ref} 手册负载电容 CL={cl if cl is not None else '缺'} pF，旁边电容 {neigh or '无'} pF，寄生电容 C_par={c_par if c_par is not None else '缺'}",
                "；".join(stuck_bits),
            )
            cards.append(card)
            continue
        c1, c2 = neigh[0], neigh[1]
        cl_eff = (c1 * c2) / (c1 + c2) + float(c_par) + cin
        measured.append(f"{ref}:CL_eff={cl_eff:.3g} CL={cl}")
        card["c1"], card["c2"], card["cl"] = c1, c2, cl
        card["formula_text"] = f"CL_eff=({c1}∥{c2})+{c_par}+{cin}={cl_eff:.3g}pF"
        try:
            target = float(cl)
        except (TypeError, ValueError):
            missing.append(ref)
            sol = pack_solution(
                given=[{"sym": "CL", "val": repr(cl), "cite": _ladder_cite(_params(c), "cl_pf", cl, "")}],
                gates=[gate("CL 可解析", None, "不可解析为浮点", wait=True)],
                rule="任一颗晶振 CL_eff 相对目标不达标 → 否",
                verdict_zh="待确认",
            )
            _sol(card, sol, f"{ref} 手册负载电容 CL={cl!r}", "规格书里的负载电容 CL 不是一个数")
            cards.append(card)
            continue
        ok_cl = abs(cl_eff - target) <= 1.0
        steps = [
            f"CL_eff = (C1∥C2)+C_par+Cin = ({c1}∥{c2})+{c_par}+{cin} = {cl_eff:.3g} pF",
            f"目标 CL = {target} pF（容差 ±1 pF）",
            f"|{cl_eff:.3g} − {target}| {'≤' if ok_cl else '>'} 1 → 本态 {'是' if ok_cl else '否'}",
        ]
        sol = pack_solution(
            given=[
                {"sym": "C1", "val": f"{c1} pF", "cite": "邻接负载电容"},
                {"sym": "C2", "val": f"{c2} pF", "cite": "邻接负载电容"},
                {"sym": "C_par", "val": f"{c_par} pF", "cite": "工况/任务书"},
                {"sym": "Cin", "val": f"{cin} pF", "cite": "手册"},
                {"sym": "CL", "val": f"{target} pF", "cite": _ladder_cite(_params(c), "cl_pf", target, "")},
            ],
            states=[{"name": "CL_eff", "steps": steps, "verdict_zh": "是" if ok_cl else "否"}],
            rule="任一颗晶振 CL_eff 相对目标不达标 → 否",
        )
        if ok_cl:
            ok.append(ref)
        else:
            fail.append(f"{ref}:CL_eff={cl_eff:.3g} vs CL={target}")
        _sol(card, sol, f"{ref} 旁边两只负载电容 {c1} pF、{c2} pF")
        cards.append(card)
    cr.measured = measured
    cr.add_step("代入公式", f"ok={len(ok)} fail={len(fail)} miss={len(missing)}", n=4)
    cr.notes = ["缺 C_par 约定不拍 3pF"]
    cr.object_cards = cards
    if fail:
        cr.verdict_zh = "否"
        cr.violations = fail
    elif missing:
        cr.verdict_zh = "INSUFFICIENT"
    elif ok:
        cr.verdict_zh = "是"
    return cr


def check_p42_a01(kg: dict) -> CheckResult:
    refs = list(_comps(kg).keys())
    cr = CheckResult(
        "P42-A01", "INSUFFICIENT", title=TITLES["P42-A01"], coverage_n=len(refs), objects=refs[:40], card_class="identity"
    )
    cr.add_step("提全部位号与层次", f"components={len(refs)}", n=1)
    symbols = kg.get("jlc_symbols") or []
    if not symbols and isinstance(kg.get("jlc_symbol_refs"), list):
        symbols = [{"ref": r} for r in kg.get("jlc_symbol_refs") or []]
    entities = []
    for s in symbols:
        if not isinstance(s, dict):
            continue
        ref = s.get("ref") or ""
        unit = str(s.get("unit") or s.get("unitId") or "1")
        dev = str(s.get("deviceId") or s.get("device") or "")
        hier = str(s.get("hierarchy") or s.get("path") or "")
        entities.append((hier, ref, unit, dev))
    cr.add_step("区分单元vs实体", f"symbols={len(entities)}", n=2)
    dup = []
    seen: dict[tuple[str, str, str], str] = {}
    for hier, ref, unit, dev in entities:
        ukey = (hier, ref, unit)
        ident = f"{hier}|{ref}|{dev}|{unit}"
        if ukey in seen:
            dup.append(ref)
        else:
            seen[ukey] = ident
    dict_dup = [r for r, n in {x: refs.count(x) for x in refs}.items() if n > 1]
    cr.add_step("查重复", f"entity_dup={dup} dict_dup={dict_dup}", n=3)
    cards: list[dict[str, Any]] = []
    if dict_dup:
        cr.verdict_zh = "否"
        cr.violations = [f"重复位号: {','.join(dict_dup)}"]
        for r in dict_dup:
            card = base_card(r, verdict_zh="否")
            fill_four(
                card,
                phenomenon=f"位号 {r} 在器件表里出现了多次",
                calc="同一位号不能对应多颗器件",
                stuck="无",
                conclusion=f"否 · 位号 {r} 重复",
            )
            cards.append(card)
        cr.object_cards = cards
        return cr
    if dup:
        cr.verdict_zh = "否"
        cr.violations = [f"独立实体冲突: {','.join(sorted(set(dup)))}"]
        for r in sorted(set(dup)):
            card = base_card(r, verdict_zh="否")
            fill_four(
                card,
                phenomenon=f"位号 {r} 在同一层次、同一单元下对应了多个实体",
                calc="同一层次、同一位号、同一单元只能有一个实体",
                stuck="无",
                conclusion=f"否 · 位号 {r} 对应多个实体",
            )
            cards.append(card)
        cr.object_cards = cards
        return cr
    if not entities:
        cr.verdict_zh = "INSUFFICIENT"
        card = base_card("(symbols)", verdict_zh="待确认", block_class="rule_gap")
        fill_four(
            card,
            phenomenon=f"器件表有 {len(refs)} 个位号，图符的单元身份没有读到",
            calc="器件表里位号不重复，还不能证明图上的多单元没有撞号",
            stuck="缺图符的单元身份，无法完成位号唯一性核对",
            conclusion="待确认 · 缺图符单元身份",
        )
        cr.object_cards = [card]
        cr.notes = ["无 symbols 单元/设备身份，dict 键唯一不是唯一性证明"]
        return cr
    cr.verdict_zh = "是"
    card = base_card("(refs)", verdict_zh="是")
    fill_four(
        card,
        phenomenon=f"图符 {len(entities)} 个，器件表位号 {len(refs)} 个",
        calc="按层次、位号、单元核对，没有重复",
        stuck="无",
        conclusion="是 · 位号没有重复",
    )
    cr.object_cards = [card]
    cr.notes = ["按层次+位号+unit 区分合法多单元"]
    return cr


def check_p42_a04(kg: dict) -> CheckResult:
    cr = CheckResult("P42-A04", "INSUFFICIENT", title=TITLES["P42-A04"], card_class="annotation")
    tel, bom, jlc, common = [], [], [], []
    for ref, c in _comps(kg).items():
        it, ib, ij = bool(c.get("in_tel")), bool(c.get("in_bom")), bool(c.get("in_jlc"))
        if it and not ib:
            tel.append(ref)
        if ib and not it:
            bom.append(ref)
        if ij and not it and not ib:
            jlc.append(ref)
        if it and ib:
            common.append(ref)
    cr.add_step("三源位号集合", f"tel_only={len(tel)} bom_only={len(bom)} jlc_only={len(jlc)} common={len(common)}", n=1)
    exclusions = _task(kg).get("bom_exclusions") or _task(kg).get("exclusions") or []
    field_bad = []
    cards: list[dict[str, Any]] = []
    for ref in common:
        c = _comps(kg)[ref]
        vt = (c.get("sources") or {}).get("value_tel", {}).get("value") or c.get("value") or ""
        vb = (c.get("sources") or {}).get("value_bom", {}).get("value") or ""
        mpn = c.get("mpn") or ""
        mj = (c.get("sources") or {}).get("mpn_jlc", {}).get("value") or ""
        issues = []
        if vb.upper() == "TBD" or vt.upper() == "TBD":
            issues.append("Value=TBD")
            field_bad.append(f"{ref}:Value=TBD")
        if vb and vt and vb != vt and vb.upper() != vt.upper():
            issues.append(f"Value tel={vt} bom={vb}")
            field_bad.append(f"{ref}:Value tel={vt} bom={vb}")
        if mj and mpn and mj != mpn:
            issues.append(f"MPN bom={mpn} jlc={mj}")
            field_bad.append(f"{ref}:MPN bom={mpn} jlc={mj}")
        if issues:
            card = base_card(ref, c, verdict_zh="否")
            card["field_bom"], card["field_tel"] = vb, vt
            issue_zh = []
            for it in issues:
                if it == "Value=TBD":
                    issue_zh.append("数值是 TBD")
                elif it.startswith("Value "):
                    issue_zh.append(f"原理图数值「{vt}」，BOM 数值「{vb}」")
                elif it.startswith("MPN "):
                    issue_zh.append(f"BOM 料号「{mpn}」，工程文件料号「{mj}」")
                else:
                    issue_zh.append(it)
            fill_four(
                card,
                phenomenon=f"{ref} 原理图数值「{vt or '空'}」，BOM 数值「{vb or '空'}」，BOM 料号「{mpn or '空'}」，工程文件料号「{mj or '空'}」",
                calc="同一位号在原理图、BOM、工程文件里的数值和料号要一致，且不能是 TBD",
                stuck="无",
                conclusion=f"否 · {ref} " + "；".join(issue_zh),
            )
            cards.append(card)
    cr.add_step("共有比MPN/Value", f"conflicts={len(field_bad)}", n=3)
    unique = tel + bom + jlc
    if field_bad:
        cr.verdict_zh = "否"
        cr.violations = field_bad[:40]
        cr.object_cards = cards
    elif unique and not exclusions:
        cr.verdict_zh = "INSUFFICIENT"
        where = {r: "只在网表里，BOM 没有" for r in tel}
        where.update({r: "只在 BOM 里，网表没有" for r in bom})
        where.update({r: "只在工程文件里，网表和 BOM 都没有" for r in jlc})
        for r in unique[:40]:
            card = base_card(r, _comps(kg).get(r) or {}, verdict_zh="待确认", block_class="miss_taskbook")
            place = where.get(r) or "只出现在一处"
            fill_four(
                card,
                phenomenon=f"{r} {place}",
                calc="三份资料都有的位号才能直接比数值。只出现在一处的，要有任务书排除说明",
                stuck=f"任务书没有写 {r} 可以排除",
                conclusion=f"待确认 · {r} 只在一处出现，任务书没有排除说明",
            )
            cards.append(card)
        if not cards:
            cards.append(
                fill_four(
                    base_card("(unique)", verdict_zh="待确认", block_class="miss_taskbook"),
                    phenomenon=f"有 {len(unique)} 个位号只出现在一处",
                    calc="三份资料都有的位号才能直接比数值。只出现在一处的，要有任务书排除说明",
                    stuck="任务书没有写这些位号可以排除",
                    conclusion="待确认 · 有位号只在一处出现",
                )
            )
        cr.object_cards = cards
        cr.notes = [f"无排除规则的独有 {len(unique)}，不以共有匹配掩盖缺颗"]
        cr.objects = unique[:40]
    elif unique and exclusions:
        cr.verdict_zh = "INSUFFICIENT"
        card = base_card("(exclusions)", verdict_zh="待确认", block_class="rule_gap")
        fill_four(
            card,
            phenomenon=f"有 {len(unique)} 个位号只出现在一处，任务书写了排除规则",
            calc="排除规则还没有逐条对完这些只出现在一处的位号",
            stuck="排除规则还没有逐条核对完",
            conclusion="待确认 · 排除规则还没有核对完",
        )
        cr.object_cards = [card]
        cr.notes = ["有排除规则但程序未逐条核完差集"]
    else:
        cr.verdict_zh = "是"
        card = base_card("(common)", verdict_zh="是")
        fill_four(
            card,
            phenomenon=f"三份资料共有 {len(common)} 个位号，没有只出现在一处的位号",
            calc="共有位号的数值和料号一致",
            stuck="无",
            conclusion=f"是 · {len(common)} 个共有位号一致，且没有只在一处出现的位号",
        )
        cr.object_cards = [card]
        cr.notes = [f"common={len(common)} 字段一致且无独有"]
    return cr


def _sol(card: dict[str, Any], sol: dict[str, Any], phen: str, stuck: str = "无") -> dict[str, Any]:
    from kernel.cause import attach_cause

    apply_solution(card, sol, phenomenon=phen, stuck=stuck, fill_four=fill_four)
    attach_cause(card)
    return card


def _wrap(fn: Callable[[dict], CheckResult]) -> Callable[[dict], CheckResult]:
    def _inner(kg: dict) -> CheckResult:
        cr = fn(kg)
        ensure_cards_nonempty(cr, cr.rule_id, f"{cr.rule_id} 无对象卡")
        return cr

    _inner.__name__ = getattr(fn, "__name__", "check")
    return _inner


REGISTRY: dict[str, Callable[[dict], CheckResult]] = {
    "P42-A01": _wrap(check_p42_a01),
    "P42-A04": _wrap(check_p42_a04),
    "P05-A01": _wrap(check_p05_a01),
    "P05-A06": _wrap(check_p05_a06),
    "P04-A01": _wrap(check_p04_a01),
    "P04-A03": _wrap(check_p04_a03),
    "P11-A01": _wrap(check_p11_a01),
    "P11-A03": _wrap(check_p11_a03),
    "P11-A07": _wrap(check_p11_a07),
    "P12-A01": _wrap(check_p12_a01),
    "P12-A02": _wrap(check_p12_a02),
    "P12-A03": _wrap(check_p12_a03),
    "P12-A04": _wrap(check_p12_a04),
    "P12-A05": _wrap(check_p12_a05),
    "P12-A06": _wrap(check_p12_a06),
    "P12-A07": _wrap(check_p12_a07),
    "P14-A02": _wrap(check_p14_a02),
    "P14-A06": _wrap(check_p14_a06),
    "P16-A01": _wrap(check_p16_a01),
    "P16-A05": _wrap(check_p16_a05),
    "P07-A05": _wrap(check_p07_a05),
    "P18-A02": _wrap(check_p18_a02),
    "P14-A05": _wrap(check_p14_a05),
    "P19-A03": _wrap(check_p19_a03),
    "P21-A01": _wrap(check_p21_a01),
}
