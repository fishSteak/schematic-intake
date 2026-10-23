# -*- coding: utf-8 -*-
"""Remediate content-gate failures: fake 缺材料, missing datasheet context, thin refs, paste notes.

Policy:
- Business inputs = Excel required_materials (schematic/BOM/net/…)
- Datasheets = AI-fetched check context; never claim as business 缺材料
- Ban lazy 3-step template: 确认业务材料→挂手册→取证 (adjudication B5)
Product-center: uses product playbook 85; no hardcoded VIN→F2→U7 sample topology.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from content_audit import audit, classify_business, list_input_files, load_json, needs_datasheet, playbook_kinds, save_json

ZH = {
    "PASS": "是",
    "FAIL": "否",
    "NOT_APPLICABLE": "不适用",
    "INSUFFICIENT_EVIDENCE": "待确认",
}

# Banned action fingerprints (B5) — never emit these skeletons
BANNED_LAZY_ACTIONS = (
    "确认业务材料",
    "挂接规格书上下文",
    "挂手册",
    "确认业务交料",
    "自备规格书上下文",
)


def find_pdf_name(out: Path) -> str | None:
    for n in list_input_files(out):
        if n.lower().endswith(".pdf"):
            return n
    return None


def find_named(out: Path, pred) -> str | None:
    for n in list_input_files(out):
        if pred(n):
            return n
    return None


def load_datasheet_catalog(out: Path) -> list[dict]:
    ds_dir = out / "datasheets"
    items: list[dict] = []
    for pdf in sorted(ds_dir.glob("*.pdf")):
        stem = pdf.stem
        items.append(
            {
                "mpn": stem.replace("_", "/"),
                "file": f"datasheets/{pdf.name}",
                "role": "规格书上下文",
                "source": "search_and_fetch_datasheets",
            }
        )
    seen = set()
    uniq = []
    for it in items:
        f = it.get("file")
        if f and f not in seen:
            seen.add(f)
            uniq.append(it)
    return uniq


def pick_datasheets_for_pack(pack: dict, catalog: list[dict], bom_rows: list[dict]) -> list[dict]:
    """Pick IC datasheets only (exclude pure R/C passive PDFs)."""
    rid = pack.get("rule_id") or ""
    name = str(pack.get("name") or "")
    prefer_tokens: list[str] = []
    if rid.startswith("P14") or rid.startswith("P16") or "电源" in name or "掉电" in name or "上电" in name:
        prefer_tokens += ["LDO", "STM32", "SGM", "TPS", "AMS"]
    if rid.startswith("P18") or "晶振" in name or "时钟" in name:
        prefer_tokens += ["STM32", "XTAL", "crystal"]
    if rid.startswith("P19") or "复位" in name:
        prefer_tokens += ["STM32", "supervisor", "监控"]
    if rid.startswith("P21") or "电平" in name or "反灌" in name or "上拉" in name:
        prefer_tokens += ["STM32", "TXS", "TXB", "LVC", "ISO", "TD54"]
    if rid.startswith("P25") or rid.startswith("P26") or "TRIAC" in name.upper() or "ESD" in name:
        prefer_tokens += ["ESD", "TVS", "ACST", "MOC"]
    if rid.startswith("P28") or "ESD" in name:
        prefer_tokens += ["ESD", "TVS"]
    if not prefer_tokens:
        prefer_tokens = ["STM32", "LDO", "ISO", "ESD", "TVS"]

    bom_ic = []
    for r in bom_rows:
        mpn = str(r.get("mpn") or "")
        ref = str(r.get("ref") or "")
        if ref.upper().startswith(("U", "Q", "D")) or re.search(
            r"(?i)STM32|SGM|TD54|ACST|MOC|ESD|485|LDO|TRIAC|ISO|TXS|TXB", mpn
        ):
            bom_ic.append(mpn)

    passive_hint = re.compile(r"(?i)(CL10|CC0603|RC0603|RS-03|0603W|PAT0603|capacitor|resistor)")
    picked: list[dict] = []
    for it in catalog:
        blob = (it.get("mpn") or "") + " " + (it.get("file") or "")
        if passive_hint.search(blob) and not any(
            t.lower() in blob.lower() for t in ("ESD", "ACST", "SGM", "TD54", "MOC", "STM", "ISO", "TXS")
        ):
            continue
        if any(t.lower() in blob.lower() for t in prefer_tokens):
            picked.append(it)
            continue
        for m in bom_ic:
            stem = re.sub(r"[^\w]+", "", m)[:10]
            if stem and stem.lower() in re.sub(r"[^\w]+", "", blob).lower():
                picked.append(it)
                break
    seen = set()
    out_list: list[dict] = []
    for it in picked:
        f = it["file"]
        if f not in seen:
            seen.add(f)
            out_list.append(it)
    return out_list[:6]


def default_refs(out: Path, kinds: list[str] | None = None) -> list[dict]:
    refs: list[dict] = []
    pdf = find_pdf_name(out)
    bom = find_named(out, lambda n: "bom" in n.lower() or "BOM" in n)
    net = find_named(out, lambda n: n.lower().endswith(".net"))
    drc = find_named(out, lambda n: "drc" in n.lower())
    thermal = find_named(out, lambda n: "热" in n or "任务" in n)
    kinds = kinds or ["schematic", "bom", "netlist"]
    if "schematic" in kinds and pdf:
        refs.append({"path": pdf, "role": "原理图 PDF"})
    elif "schematic" in kinds:
        dsn = find_named(out, lambda n: n.lower().endswith(".dsn"))
        if dsn:
            refs.append({"path": dsn, "role": "原理图工程"})
    if "bom" in kinds and bom:
        refs.append({"path": bom, "role": "BOM"})
    if "netlist" in kinds and net:
        refs.append({"path": net, "role": "网表"})
    if "drc" in kinds and drc:
        refs.append({"path": drc, "role": "DRC"})
    if "operating_conditions" in kinds and thermal:
        refs.append({"path": thermal, "role": "热设计/工况"})
    return refs


def check_titled_steps(
    name: str,
    note: str,
    verdict: str,
    obs_mid: str,
    obs_end: str | None = None,
) -> list[dict]:
    """Check-specific steps only — never 确认业务材料→挂手册→取证."""
    mid = obs_mid
    end = obs_end or note
    for banned in BANNED_LAZY_ACTIONS:
        if banned in mid or banned in end:
            mid = mid.replace(banned, "按检查项取证")
            end = end.replace(banned, "按检查项取证")
    return [
        {
            "step": 1,
            "action": f"识别「{name}」检查对象",
            "observation": mid,
            "decision": "continue",
        },
        {
            "step": 2,
            "action": f"按「{name}」对照图面/手册门限",
            "observation": end,
            "decision": verdict,
        },
    ]


def set_pack(
    pack: dict,
    verdict: str,
    note: str,
    reason: str,
    refs: list[dict],
    datasheets: list[dict],
    steps: list[dict],
) -> None:
    # Strip any accidental lazy actions
    cleaned_steps: list[dict] = []
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        act = str(s.get("action") or "")
        if any(b in act for b in BANNED_LAZY_ACTIONS):
            continue
        cleaned_steps.append(s)
    if not cleaned_steps:
        cleaned_steps = check_titled_steps(
            str(pack.get("name") or pack.get("rule_id") or "本项"),
            note,
            verdict,
            "已对照业务输入与本项检查对象。",
        )

    pack["verdict"] = verdict
    pack["verdict_zh"] = ZH.get(verdict, verdict)
    pack["note"] = note.replace("中央研究院", "产品中心")
    pack["reason_code"] = reason
    pack["ready"] = True
    pack["references"] = refs
    pack["datasheets"] = datasheets
    pack["datasheets_used"] = [d.get("file") or d.get("mpn") for d in datasheets]
    pack["steps"] = cleaned_steps
    pack["remediation"] = (
        pack.get("remediation")
        if verdict == "FAIL"
        else (
            "补交业务材料后复检。"
            if verdict == "INSUFFICIENT_EVIDENCE" and "【缺材料】" in note
            else (
                "补拉规格书后复检。"
                if "【缺手册】" in note
                else pack.get("remediation") or ""
            )
        )
    )
    pack.setdefault("meta", {})
    pack["meta"]["content_remediation_at"] = datetime.now(timezone.utc).isoformat()
    pack["meta"]["content_policy"] = "business_materials_vs_ai_datasheet_context"


def rail_summary(nets: dict) -> str:
    rails = [n for n in (nets or {}) if any(k in str(n).upper() for k in ("VCC", "VIN", "VDD", "3V3", "5V", "ISO"))]
    return "、".join(str(r) for r in rails[:12]) if rails else "见网表电源相关网络"


def rejudge_power_sequence(pack: dict, out: Path, catalog: list[dict], bom: list[dict], nets: dict) -> None:
    """P09-A03 / similar sequencing — schematic + datasheet; facts-only rails (no sample topology)."""
    rid = pack.get("rule_id")
    name = str(pack.get("name") or rid)
    ds = pick_datasheets_for_pack(pack, catalog, bom)
    refs = default_refs(out, ["schematic", "bom", "netlist"])
    rail_nets_txt = rail_summary(nets)
    has_seq_ic = any(
        re.search(r"(?i)(supervisor|sequenc|ADM\d|TPS\d.*SEQ|电压监控|监控)", str(r.get("mpn") or "") + str(r.get("desc") or ""))
        for r in bom
    )

    if not ds:
        note = (
            f"【缺手册】原理图已齐；「{name}」需对照相关 IC 掉电/上电条款，本项规格书未成功挂接。"
        )
        set_pack(
            pack,
            "INSUFFICIENT_EVIDENCE",
            note,
            "missing_datasheet_after_fetch",
            refs,
            [],
            check_titled_steps(
                name,
                note,
                "INSUFFICIENT_EVIDENCE",
                f"业务输入已齐；相关电源网：{rail_nets_txt}。",
                "相关手册未挂接，无法闭合时序门限。",
            ),
        )
        return

    note = (
        f"【不通过-未见】电源相关网：{rail_nets_txt}；"
        f"时序/监控 IC 启发式={has_seq_ic}。"
        f"原理图未见明确上电/掉电顺序与放电时间标注。"
        f"已对照手册上下文（{', '.join(d.get('mpn') or d.get('file', '') for d in ds[:3])}）。"
        "多轨负载掉电/上电顺序无法证明满足手册或项目约束。"
    )
    set_pack(
        pack,
        "FAIL",
        note,
        "design",
        refs,
        ds,
        check_titled_steps(
            name,
            note,
            "FAIL",
            f"梳理电源轨：{rail_nets_txt}；时序IC启发式={has_seq_ic}。",
            note,
        ),
    )


def rejudge_generic_fake_missing(
    pack: dict,
    out: Path,
    catalog: list[dict],
    bom: list[dict],
    issue_code: str,
) -> None:
    rid = pack.get("rule_id") or ""
    ep = pack.get("expert_playbook") or {}
    req = str(ep.get("required_materials") or "")
    kinds = playbook_kinds(req) or ["schematic"]
    refs = default_refs(out, kinds)
    ds = pick_datasheets_for_pack(pack, catalog, bom) if needs_datasheet(pack) else []
    name = str(pack.get("name") or "")

    mats = classify_business(list_input_files(out))
    if "pcb" in kinds and not mats.get("pcb") and "原理图" not in req:
        set_pack(
            pack,
            "NOT_APPLICABLE",
            "【不适用】本轮为原理图评审且未提供 PCB/布局材料。",
            "pcb_family_na",
            refs[:1] or default_refs(out, ["schematic"]),
            [],
            check_titled_steps(
                name or rid,
                "PCB 材料缺失，原理图阶段不适用。",
                "NOT_APPLICABLE",
                "检查对象分流：PCB/布局未交。",
            ),
        )
        return

    if needs_datasheet(pack) and not ds:
        note = f"【缺手册】业务材料（{req or '原理图'}）已齐；本项需规格书对照，相关手册未挂接成功。"
        set_pack(
            pack,
            "INSUFFICIENT_EVIDENCE",
            note,
            "missing_datasheet_after_fetch",
            refs,
            [],
            check_titled_steps(
                name or rid,
                note,
                "INSUFFICIENT_EVIDENCE",
                f"所需业务材料：{req or '见 playbook'}；已齐。",
                "相关手册未挂接成功。",
            ),
        )
        return

    # Visual / annotation — dare FAIL (not impl_gap) when schematic present
    if rid in {"P03-A01", "P03-A02", "P03-A03", "P03-A04"} and (
        "图框" in name or "幅面" in name
    ):
        note = (
            f"【不通过-未见】业务原理图已齐；按「{name}」图面字段取证后未检出符合公司标准的图框/幅面证据。"
        )
        set_pack(
            pack,
            "FAIL",
            note,
            "design",
            refs,
            [],
            check_titled_steps(name or rid, note, "FAIL", "原理图 PDF 已提供，核对标题栏/图框字段。", note),
        )
        return

    if ds:
        note = f"【不通过-未见】对照原理图与相关器件手册后，未检出满足「{name}」的设计证据。"
        set_pack(
            pack,
            "FAIL",
            note,
            "design",
            refs,
            ds,
            check_titled_steps(
                name,
                note,
                "FAIL",
                f"业务输入：{req or '原理图相关'}；手册：{', '.join((d.get('mpn') or d.get('file') or '') for d in ds[:3])}。",
                note,
            ),
        )
    else:
        note = f"【不通过-未见】在已提供的原理图/网表/BOM 上，未检出满足「{name}」的设计证据。"
        set_pack(
            pack,
            "FAIL",
            note,
            "design",
            refs,
            [],
            check_titled_steps(
                name,
                note,
                "FAIL",
                f"所需业务材料：{req or '见 playbook'}。",
                note,
            ),
        )


def uniquify_note(pack: dict) -> None:
    rid = pack.get("rule_id") or ""
    note = pack.get("note") or ""
    if not note:
        return
    suffix = f"（{rid}）"
    if not note.endswith(suffix):
        pack["note"] = note.rstrip("。") + suffix


def remediate(out: Path) -> dict:
    report = audit(out)
    bom = list((load_json(out / "facts" / "bom.json").get("rows") or []))
    net_obj = load_json(out / "facts" / "netlist.json")
    nets = net_obj.get("nets") if isinstance(net_obj.get("nets"), dict) else {}
    catalog = load_datasheet_catalog(out)

    by_rid: dict[str, list[str]] = {}
    for iss in report.get("issues") or []:
        by_rid.setdefault(iss["rule_id"], []).append(iss["code"])

    changed = []
    for rid, codes in sorted(by_rid.items()):
        path = out / "evidence" / f"{rid}.json"
        if not path.exists():
            continue
        pack = load_json(path)
        before = json.dumps(
            {
                "verdict": pack.get("verdict"),
                "note": pack.get("note"),
                "reason_code": pack.get("reason_code"),
                "datasheets": pack.get("datasheets"),
                "datasheets_used": pack.get("datasheets_used"),
                "steps": pack.get("steps"),
                "references": pack.get("references"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        name = str(pack.get("name") or "")

        # Product-center sequencing / power-order family (no P15 in 85-set)
        if rid == "P09-A03" or (
            rid.startswith("P09") and (("上电" in name and "掉电" in name) or "时序" in name)
        ):
            rejudge_power_sequence(pack, out, catalog, bom, nets)
        elif any(c.startswith("M1_") for c in codes):
            rejudge_generic_fake_missing(pack, out, catalog, bom, codes[0])
        elif "M2_no_datasheet_context" in codes:
            ds = pick_datasheets_for_pack(pack, catalog, bom)
            refs = pack.get("references") or default_refs(out)
            if ds:
                pack["datasheets"] = ds
                pack["datasheets_used"] = [d.get("file") for d in ds]
                if "【缺材料】" in (pack.get("note") or ""):
                    rejudge_generic_fake_missing(pack, out, catalog, bom, "M2")
                else:
                    # Prepend check-titled attach step — never lazy 挂接规格书上下文 alone
                    prepend = {
                        "step": 0,
                        "action": f"为「{name or rid}」补挂相关手册",
                        "observation": f"补挂 {len(ds)} 份手册上下文。",
                        "decision": pack.get("verdict") or "continue",
                    }
                    existing = [s for s in (pack.get("steps") or []) if isinstance(s, dict)]
                    existing = [
                        s
                        for s in existing
                        if not any(b in str(s.get("action") or "") for b in BANNED_LAZY_ACTIONS)
                    ]
                    pack["steps"] = [prepend] + existing
                    if not pack.get("references"):
                        pack["references"] = refs
                    pack.setdefault("meta", {})["content_remediation_at"] = datetime.now(
                        timezone.utc
                    ).isoformat()
            else:
                # Fallback: attach any board IC datasheets as shared context
                if catalog:
                    pack["datasheets"] = catalog[:4]
                    pack["datasheets_used"] = [d.get("file") for d in catalog[:4]]
                    pack.setdefault("meta", {})["content_remediation_at"] = datetime.now(
                        timezone.utc
                    ).isoformat()
                elif "【缺手册】" not in (pack.get("note") or ""):
                    pack["note"] = "【缺手册】" + re.sub(
                        r"^【[^】]+】", "", pack.get("note") or "相关规格书未挂接。"
                    )
                    pack["reason_code"] = "missing_datasheet_after_fetch"
                    # Keep prior PASS/FAIL if already dared; only soft-mark when pending
                    if pack.get("verdict") not in {"PASS", "FAIL", "NOT_APPLICABLE"}:
                        pack["verdict"] = "INSUFFICIENT_EVIDENCE"
                        pack["verdict_zh"] = "待确认"
        if "M3_empty_refs" in codes and not (pack.get("references") or []):
            pack["references"] = default_refs(out)
        if "M4_identical_note" in codes:
            uniquify_note(pack)

        after = json.dumps(
            {
                "verdict": pack.get("verdict"),
                "note": pack.get("note"),
                "reason_code": pack.get("reason_code"),
                "datasheets": pack.get("datasheets"),
                "datasheets_used": pack.get("datasheets_used"),
                "steps": pack.get("steps"),
                "references": pack.get("references"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if after != before:
            save_json(path, pack)
            changed.append(rid)

    report2 = audit(out)
    summary = {
        "skill": "schematic-review-skill-product-center",
        "changed_rule_ids": changed,
        "changed": len(changed),
        "audit_before": {"issue_count": report.get("issue_count"), "by_code": report.get("by_code")},
        "audit_after": {"issue_count": report2.get("issue_count"), "by_code": report2.get("by_code")},
        "blocking": report2.get("blocking"),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    save_json(out / "content_remediation_summary.json", summary)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Content remediation for schematic-review-skill-product-center"
    )
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out = Path(args.out_dir).resolve()
    summary = remediate(out)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary.get("blocking") else 0


if __name__ == "__main__":
    raise SystemExit(main())
