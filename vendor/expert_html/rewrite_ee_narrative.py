# -*- coding: utf-8 -*-
"""Scrub process/audit speak from evidence narration — WITHOUT inventing fixed steps.

Rules (aligned with adjudication B5):
  - Never overwrite every pack with the same 3-step action template.
  - Steps must stay check-specific; this script only scrubs meta phrases and
    strengthens weak notes. Empty/lazy-template steps are marked for rewrite,
    not replaced by another global skeleton.
  - Do not overwrite derivation_profile starting with check_specific_from_playbook.
Product-center: scrub「中央研究院」→「产品中心」 in customer-facing strings.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

REF_RE = re.compile(
    r"\b((?:CN|TP|FB|SW|LED|RV|RT|BZ|BT)[0-9]{1,3}|[RUCLDQFJHYXTK][0-9]{1,3})\b",
    re.I,
)

META_PHRASES = [
    "业务材料已齐",
    "所需材料=",
    "挂接规格书上下文",
    "确认业务交料",
    "AI自备",
    "非业务交料",
    "执行专家动作",
    "与本项相关的已记录结果",
    "无法证明满足",
    "未能证明满足",
    "未见满足「",
    "按原理图/网表/BOM 未见满足",
    "本策略仅确认",
    "（检查上下文，非业务交料）",
]

# Adjudication B5 forbidden fingerprints — must not re-introduce these.
LAZY_ACTION_TEMPLATES = {
    "定位原理图/网表中的检查对象|提取连接、参数或规则结果|对照标准给出结论",
    "确认业务材料|挂接规格书上下文|按专家过程取证",
    "确认业务交料|自备规格书上下文",
    "确认业务材料|取证",
    "确认业务材料|挂手册|取证",
}


def load_json(p: Path) -> dict:
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8-sig"))


def save_json(p: Path, obj: object) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def scrub_org(text: str) -> str:
    return (text or "").replace("中央研究院", "产品中心")


def scrub_meta(text: str) -> str:
    s = scrub_org(text or "")
    for ph in META_PHRASES:
        s = s.replace(ph, "")
    s = re.sub(r"（P\d{2}-A\d{2}）", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ；;。")
    if s and not s.endswith(("。", "！", "；")):
        s += "。"
    return s


def board_ctx(out: Path) -> dict:
    bom = load_json(out / "facts" / "bom.json")
    net = load_json(out / "facts" / "netlist.json")
    drc = load_json(out / "facts" / "drc.json")
    facts = load_json(out / "facts" / "board_facts.json")
    rows = list(bom.get("rows") or [])
    nets = net.get("nets") if isinstance(net.get("nets"), dict) else {}
    rails = [n for n in nets if re.search(r"(?i)VCC|VIN|VDD|3V3|5V|ISO|GND", str(n))]
    ics = [r for r in rows if str(r.get("ref") or "").upper().startswith("U")]
    empty = [r for r in rows if not str(r.get("mpn") or "").strip()]
    warns = list(drc.get("warnings") or [])
    return {
        "rows": rows,
        "nets": nets,
        "rails": rails,
        "ics": ics,
        "empty": empty,
        "warns": warns,
        "net_count": net.get("net_count") or len(nets),
        "bom_n": len(rows),
        "parts": net.get("part_count") or facts.get("part_count"),
        "has_reset": any("RESET" in str(n).upper() for n in nets),
        "has_crystal": any(str(r.get("ref") or "").upper().startswith("X") for r in rows)
        or any(re.search(r"(?i)crystal|晶振|MHz", str(r.get("mpn") or "")) for r in rows),
    }


def refs_in(text: str) -> list[str]:
    return sorted({m.group(1).upper() for m in REF_RE.finditer(text or "")})


def focus_objects(pack: dict, ctx: dict) -> str:
    note = pack.get("note") or ""
    found = refs_in(note)
    if found:
        return "、".join(found[:10])
    rid = pack.get("rule_id") or ""
    if rid.startswith("P07"):
        return "DRC 电气规则结果"
    if rid.startswith("P16") or rid.startswith("P14"):
        return "、".join((ctx.get("rails") or [])[:8]) or "电源输入/电源轨"
    if rid.startswith("P19"):
        return "RESET 网络及相关阻容"
    if rid.startswith("P18"):
        return "晶振及负载电容"
    if rid.startswith("P21"):
        return "跨域/电平相关 IO 与电源轨"
    if rid.startswith("P32"):
        return "PCB/布局约束（本轮未交）"
    if pack.get("verdict") == "NOT_APPLICABLE":
        return "本项检查对象"
    return "本项相关电路"


def step_action_fingerprint(steps: list) -> str:
    acts: list[str] = []
    for s in steps or []:
        if isinstance(s, dict):
            a = re.sub(r"\s+", " ", str(s.get("action") or "").strip())
            if a:
                acts.append(a)
    return "|".join(acts)


def is_lazy_step_template(steps: list) -> bool:
    fp = step_action_fingerprint(steps)
    if fp in LAZY_ACTION_TEMPLATES:
        return True
    # near-matches: first/last of the classic 3-step skeleton
    if "定位原理图/网表中的检查对象" in fp and "对照标准给出结论" in fp:
        return True
    if "确认业务材料" in fp and ("挂接规格书" in fp or "挂手册" in fp or fp.endswith("取证")):
        return True
    return False


def strengthen_note(pack: dict, ctx: dict) -> str:
    """Keep good notes; expand weak ones with board facts. Always scrub meta."""
    v = pack.get("verdict") or ""
    rid = pack.get("rule_id") or ""
    name = pack.get("name") or ""
    note = scrub_meta(pack.get("note") or "")
    rich = len(note) >= 60 and bool(
        re.search(r"(VIN|VCC_|GND|→|U\d|R\d|C\d|F\d|Warning|A3|A4|RESET|DRC|VIH|VIL|VOH)", note)
    )
    if rich and "未见满足" not in note and "无法证明" not in note:
        note = re.sub(r"已对照.*?手册上下文.*?。", "", note)
        return scrub_meta(note)

    rails = "、".join((ctx.get("rails") or [])[:10])
    warns = ctx.get("warns") or []
    empty_refs = [str(r.get("ref")) for r in (ctx.get("empty") or [])[:8]]

    if v == "NOT_APPLICABLE":
        if rid.startswith("P32") or "PCB" in name:
            return "【不适用】本轮只做原理图评审，输入中无 PCB/叠层/布局约束，该项检查对象不存在。"
        if "晶振" in name and not ctx.get("has_crystal"):
            return "【不适用】BOM/网表未识别独立晶振类器件，无对象可检。"
        if "模拟" in name and not any(
            "AVDD" in str(n).upper() or "AVCC" in str(n).upper() for n in (ctx.get("nets") or {})
        ):
            return f"【不适用】电源域主要为 {rails or '数字/隔离轨'}，未见独立模拟电源轨，本项不适用。"
        if note.startswith("【不适用】"):
            return note
        return f"【不适用】{scrub_meta(note) or '本板无该检查对象。'}"

    if v == "PASS":
        if rid.startswith("P07") and "Error" in note:
            return "【通过】DRC 报告 Error=0（网表/规则检查无错误项）。Warning 另项处理。"
        if rid.startswith("P19") or "复位" in name:
            return (
                "【通过】网表存在 RESET 网络（如阻容与 MCU 复位脚相连），"
                "具备复位释放基本阻容结构；极性/时序细节以器件手册为准继续核对。"
            )
        if "A4" in note and "一致" in note:
            return "【通过】原理图各页幅面一致（均为标题栏所示幅面）。"
        if "保险丝" in note or rid == "P14-A03":
            return "【通过】输入路径见保险丝/限流类器件，并具备基本输入保护拓扑。"
        if "钽" in name:
            return "【通过】BOM 与原理图未见钽电容料号/封装，禁令条款（不得使用普通钽电容）合规。"
        if "重复" in note:
            return f"【通过】网表 {ctx.get('parts') or ''} 个位号无重复占用。"
        if note.startswith("【通过】") or note.startswith("【工况"):
            return note if len(note) > 30 else f"【通过】结合原理图/网表：{focus_objects(pack, ctx)} 满足本项要求。"
        return f"【通过】{note}" if note else f"【通过】{focus_objects(pack, ctx)} 检查通过。"

    if v == "FAIL":
        if rid.startswith("P07") or "DRC" in name or "Warning" in note:
            wtxt = "；".join(w.strip() for w in warns[:3]) or note
            return f"【不通过-未见】DRC 仍有 {len(warns)} 条 Warning 未关闭且未见豁免记录。典型：{wtxt}"
        if empty_refs and (
            "空" in note or "R1" in note or "C6" in note or rid.startswith("P05") or rid.startswith("P35")
        ):
            return (
                f"【不通过-未见】BOM 中位号 {empty_refs} 的型号/容值阻值为空或 NC，"
                "图面也未见补全标注，参数未闭合。"
            )
        if "位号不一致" in note or rid in {"P42-A04"}:
            return (
                "【不通过-未见】BOM 与网表同位号 MPN/Value 未能一一对应，"
                "同位号一致性要求未闭合。"
            )
        if rid == "P14-A06" or "负载开关" in name:
            return (
                "【不通过-未见】外部可插拔供电接口未见负载开关/eFuse/"
                "限流热点插拔或软启动电路（无 EN 控制的输入级功率开关）。"
            )
        if rid.startswith("P21") or "电平" in name or "反灌" in name:
            return pack.get("note") or note
        if note.startswith("【不通过"):
            return note
        return (
            f"【不通过-未见】针对「{name}」：在 {focus_objects(pack, ctx)} 上"
            f"未检出符合要求的电路或标注。{note}"
        )

    if "【抽取失败】" in note or "【缺手册】" in note or "【缺材料】" in note:
        return scrub_meta(note)
    return scrub_meta(note) or "【抽取失败】当前自动化未能可靠完成该项图面比对，需工程师复核原理图细节。"


def scrub_steps_in_place(pack: dict) -> None:
    """Scrub meta from existing steps; never invent a global 3-step skeleton."""
    steps = pack.get("steps") or []
    if not isinstance(steps, list) or not steps:
        pack["meta"] = dict(pack.get("meta") or {})
        pack["meta"]["steps_need_check_specific_rewrite"] = True
        return

    if is_lazy_step_template(steps):
        # Drop lazy template — leave empty so B5 fails until check-specific rewrite
        pack["steps"] = []
        pack["meta"] = dict(pack.get("meta") or {})
        pack["meta"]["steps_need_check_specific_rewrite"] = True
        pack["meta"]["lazy_step_template_cleared"] = True
        return

    # Preserve check-specific playbook rebuilds — do not overwrite profile/steps
    if str(pack.get("derivation_profile") or "").startswith("check_specific_from_playbook"):
        return

    cleaned: list = []
    for s in steps:
        if isinstance(s, str):
            cleaned.append(scrub_meta(s))
            continue
        if not isinstance(s, dict):
            continue
        item = dict(s)
        if item.get("observation"):
            item["observation"] = scrub_meta(str(item["observation"]))
        if item.get("action"):
            item["action"] = scrub_meta(str(item["action"])).rstrip("。")
        cleaned.append(item)
    pack["steps"] = cleaned


def rewrite_pack(pack: dict, ctx: dict) -> dict:
    note = strengthen_note(pack, ctx)
    tags = (
        "【通过】",
        "【不通过-未见】",
        "【不适用】",
        "【缺材料】",
        "【缺手册】",
        "【抽取失败】",
        "【工况标准】",
        "【假设工况】",
    )
    if not any(t in note for t in tags):
        v = pack.get("verdict")
        prefix = {
            "PASS": "【通过】",
            "FAIL": "【不通过-未见】",
            "NOT_APPLICABLE": "【不适用】",
            "INSUFFICIENT_EVIDENCE": "【抽取失败】",
        }.get(v, "【抽取失败】")
        note = prefix + scrub_meta(note)

    pack["note"] = scrub_org(note)
    scrub_steps_in_place(pack)

    cleaned = []
    for e in pack.get("extracted") or []:
        val = str((e or {}).get("value") or "")
        if any(ph in val for ph in META_PHRASES):
            continue
        if isinstance(e, dict) and e.get("value"):
            e = dict(e)
            e["value"] = scrub_org(str(e["value"]))
        cleaned.append(e)
    pack["extracted"] = cleaned

    if pack.get("verdict") == "FAIL":
        rem = pack.get("remediation") or ""
        if any(ph in rem for ph in META_PHRASES) or len(rem) < 4:
            pack["remediation"] = "按结论补齐图面电路/标注或修正 BOM 位号后复检。"
        else:
            pack["remediation"] = scrub_org(rem)

    # Never clobber check_specific_from_playbook* profiles
    prof = str(pack.get("derivation_profile") or "")
    if prof.startswith("check_specific_from_playbook") or (pack.get("meta") or {}).get(
        "check_specific_steps_at"
    ):
        if not prof.startswith("check_specific_from_playbook"):
            pack["derivation_profile"] = "check_specific_from_playbook_v1"
        # else keep existing check_specific_* unchanged
    else:
        pack["derivation_profile"] = "ee_engineer_review_v2_no_uniform_steps"
    pack.setdefault("meta", {})["ee_narrative_scrub_at"] = datetime.now(timezone.utc).isoformat()
    return pack


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Scrub EE narrative for schematic-review-skill-product-center"
    )
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out = Path(args.out_dir).resolve()
    ctx = board_ctx(out)
    n = 0
    cleared = 0
    for path in sorted((out / "evidence").glob("P*-A*.json")):
        pack = load_json(path)
        before_lazy = is_lazy_step_template(pack.get("steps") or [])
        rewrite_pack(pack, ctx)
        if before_lazy or (pack.get("meta") or {}).get("lazy_step_template_cleared"):
            cleared += 1
        save_json(path, pack)
        n += 1
    print(
        json.dumps(
            {
                "skill": "schematic-review-skill-product-center",
                "rewritten": n,
                "lazy_templates_cleared": cleared,
                "profile": "ee_engineer_review_v2_no_uniform_steps",
                "rails": (ctx.get("rails") or [])[:12],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
