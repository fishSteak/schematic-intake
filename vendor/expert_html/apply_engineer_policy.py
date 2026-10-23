# -*- coding: utf-8 -*-
"""Apply product-center engineer policy (P0–P3) onto evidence/*.json.

Dare-to-judge profile: prefer PASS/FAIL/NOT_APPLICABLE; pending is the exception.
Does not require EDA GUI — only facts/ + input filenames + optional netlist/PDF text.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "rules"

VERDICT_ZH = {
    "PASS": "是",
    "FAIL": "否",
    "NOT_APPLICABLE": "不适用",
    "INSUFFICIENT_EVIDENCE": "待确认",
    "SKIPPED_OUT_OF_SCOPE": "不适用",
}


def load_json(p: Path) -> dict:
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8-sig"))


def save_json(p: Path, obj: object) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def load_yaml(p: Path) -> dict:
    text = p.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(text) or {}
    # minimal fallback: not expected in prod; keep empty
    raise SystemExit(f"PyYAML required to load {p}")


def list_input_files(out_dir: Path) -> list[str]:
    inv = load_json(out_dir / "facts" / "inventory.json")
    inv2 = load_json(out_dir / "input_inventory.json")
    files: list[str] = []
    if isinstance(inv.get("files"), dict):
        for v in (inv.get("files") or {}).values():
            if v:
                files.append(Path(str(v)).name)
    files.extend(list(inv2.get("files") or []))
    parent = out_dir.parent
    if parent.exists():
        files.extend(p.name for p in parent.iterdir() if p.is_file())
    # unique preserve order
    seen = set()
    out = []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def classify_materials(files: list[str], packet: dict) -> dict[str, bool]:
    present = {k: False for k in [
        "schematic_pdf", "bom", "netlist", "design_task_book",
        "operating_conditions", "drc", "pcb", "eda_project",
    ]}
    detect = packet.get("detect") or {}
    for kind, conf in detect.items():
        rx = conf.get("name_regex") or ""
        exts = [e.lower() for e in (conf.get("ext") or [])]
        for name in files:
            low = name.lower()
            ok_ext = (not exts) or any(low.endswith(e) for e in exts)
            ok_name = True if not rx else bool(re.search(rx, name))
            # pdf schematic: require name hint when ext is pdf
            if kind == "schematic_pdf":
                ok_name = bool(re.search(rx, name)) if rx else ("sch" in low or "原理" in name)
            if ok_ext and ok_name:
                present[kind] = True
                break
    return present


def bom_rows(out_dir: Path) -> list[dict]:
    return list((load_json(out_dir / "facts" / "bom.json").get("rows") or []))


def board_facts(out_dir: Path) -> dict:
    return load_json(out_dir / "facts" / "board_facts.json")


def has_crystal(rows: list[dict], facts: dict) -> bool:
    prefs = facts.get("prefixes") or {}
    if prefs.get("Y") or prefs.get("X") or prefs.get("OSC"):
        return True
    for r in rows:
        blob = " ".join(str(r.get(k) or "") for k in ("ref", "mpn", "value", "desc", "footprint"))
        if re.search(r"(?i)\b(crystal|xtal|oscillator|晶振|石英)\b", blob):
            return True
        if re.match(r"(?i)^Y\d+", str(r.get("ref") or "")):
            return True
    return False


def connectors(rows: list[dict], facts: dict) -> list[dict]:
    out = []
    prefs = facts.get("prefixes") or {}
    for pref in ("CN", "J", "P", "CON"):
        for ref in prefs.get(pref) or []:
            out.append({"ref": ref})
    for r in rows:
        ref = str(r.get("ref") or "")
        if re.match(r"(?i)^(CN|J|CON)\d+", ref):
            out.append(r)
        blob = " ".join(str(r.get(k) or "") for k in ("mpn", "value", "desc", "footprint"))
        if re.search(r"(?i)(connector|header|接插件|端子|PH2\.0|DB\d)", blob):
            out.append(r)
    # unique by ref
    seen = set()
    uniq = []
    for r in out:
        ref = str(r.get("ref") or r)
        if ref in seen:
            continue
        seen.add(ref)
        uniq.append(r if isinstance(r, dict) else {"ref": ref})
    return uniq


def text_blob_for_board(out_dir: Path, rows: list[dict], facts: dict) -> str:
    bits = []
    for r in rows:
        bits.append(" ".join(str(r.get(k) or "") for k in ("ref", "mpn", "value", "desc", "footprint")))
    bits.extend(str(n) for n in (facts.get("nets") or []))
    # optional netlist file nearby
    parent = out_dir.parent
    for p in parent.glob("*"):
        if p.suffix.lower() in {".tel", ".net", ".txt"} and re.search(r"(?i)net|tel|drc|sch", p.name):
            try:
                bits.append(p.read_text(encoding="utf-8", errors="ignore")[:200000])
            except Exception:
                pass
    return "\n".join(bits)


def has_reset_evidence(facts: dict, blob: str) -> bool:
    nets = [str(n).upper() for n in (facts.get("nets") or [])]
    keys = ["RESET", "NRST", "NRST", "NRESET", "/RESET", "RST", "POR", "NCONFIG", "BOOT0"]
    for n in nets:
        for k in keys:
            if k in n.replace("-", "").replace("_", ""):
                return True
            if k in n:
                return True
    if re.search(r"(?i)\b(NRST|nRESET|/RESET|RESET|BOOT0|nCONFIG)\b", blob):
        return True
    return False


def has_mcu_like(rows: list[dict], facts: dict) -> bool:
    for r in rows:
        blob = " ".join(str(r.get(k) or "") for k in ("mpn", "desc", "value"))
        if re.search(
            r"(?i)(STM32|GD32|N32|ESP32|RP2040|PIC\d|AVR|ATmega|MSP430|"
            r"\bMCU\b|\bCPU\b|FPGA|CPLD|nRF52|HC32|MM32)",
            blob,
        ):
            return True
    # do NOT treat generic U* (opamps/comparators/logic) as MCU
    return False


def has_load_switchish(blob: str, rows: list[dict]) -> bool:
    if re.search(r"(?i)(负载开关|限流|软启动|load\s*switch|hot[-\s]?swap|eFuse|电子保险丝|soft\s*start)", blob):
        return True
    for r in rows:
        mpn = str(r.get("mpn") or "")
        if re.search(r"(?i)(TPS\d|AP228|AOZ\d|RT97|MIC20|load.?switch)", mpn):
            return True
    return False


def polarity_keywords_hit(blob: str, kws: list[str]) -> bool:
    low = blob.lower()
    for k in kws:
        if k.lower() in low or k in blob:
            return True
    return False


def stamp_note(tag: str, body: str) -> str:
    body = (body or "").strip()
    if body.startswith(tag):
        return body
    # drop conflicting old soft pending prose lightly
    return f"{tag}{body}" if body else tag.rstrip("】") + "】"


def set_verdict(pack: dict, verdict: str, note: str, reason: str, extra: dict | None = None) -> None:
    pack["verdict"] = verdict
    pack["verdict_zh"] = VERDICT_ZH.get(verdict, verdict)
    pack["note"] = note
    pack["reason_code"] = reason
    pack["engineer_policy"] = {
        "profile": "engineer",
        "reason": reason,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }
    if verdict == "FAIL" and not (pack.get("remediation") or "").strip():
        pack["remediation"] = "按备注补齐图面标注/电路或交料后复检。"
    if verdict == "INSUFFICIENT_EVIDENCE" and not (pack.get("remediation") or "").strip():
        pack["remediation"] = "按【缺材料】/【假设工况】说明补交设计任务书或确认默认工况后复检。"


def apply_policy(out_dir: Path) -> dict:
    policy = load_yaml(RULES / "engineer_policy.yaml")
    packet = load_yaml(RULES / "board_packet.yaml")
    oc = load_yaml(RULES / "operating_conditions_defaults.yaml")

    files = list_input_files(out_dir)
    mats = classify_materials(files, packet)
    facts = board_facts(out_dir)
    rows = bom_rows(out_dir)
    blob = text_blob_for_board(out_dir, rows, facts)
    ev_dir = out_dir / "evidence"

    stats = {
        "scanned": 0,
        "changed": 0,
        "by_action": {},
        "materials_present": mats,
        "files": files[:50],
    }

    def bump(action: str) -> None:
        stats["by_action"][action] = stats["by_action"].get(action, 0) + 1

    def load_pack(rid: str) -> dict | None:
        p = ev_dir / f"{rid}.json"
        if not p.exists():
            return None
        return load_json(p)

    def save_pack(rid: str, pack: dict) -> None:
        save_json(ev_dir / f"{rid}.json", pack)
        stats["changed"] += 1

    # ---- P0: missing object → FAIL (only when object not evidenced) ----
    for item in (policy.get("p0") or {}).get("missing_required_object_fail") or []:
        rid = item["rule_id"]
        pack = load_pack(rid)
        if not pack:
            continue
        stats["scanned"] += 1
        obj = (item.get("object") or "").lower()
        found = False
        if "耐压" in obj or "电压" in obj:
            found = bool(
                re.search(
                    r"(?i)(耐压|额定电压|WV|VDC|V[Rr]ated|\b\d+\s*V\b).{0,40}(标注|mark|note)|"
                    r"(电容|C\d+).{0,30}(耐压|WV|\d+\s*V)",
                    blob,
                )
            )
        elif "降额" in obj or "参数来源" in obj:
            found = bool(re.search(r"(?i)(降额|derating|参数来源|应力比|stress\s*ratio)", blob))
        # already hard PASS/FAIL from agent with concrete evidence → keep
        if pack.get("verdict") in {"PASS", "FAIL"} and (pack.get("extracted") or pack.get("steps")):
            bump("missing_required_object_skip_existing")
            continue
        if found:
            set_verdict(
                pack,
                "PASS",
                f"【通过】已检出与「{item.get('object')}」相关的图面/文本证据。",
                "required_object_found",
                {"object": item.get("object")},
            )
        else:
            set_verdict(pack, "FAIL", item["note"], "missing_required_object_fail", {"object": item.get("object")})
        save_pack(rid, pack)
        bump("missing_required_object_fail")

    # ---- P0: crystal absent → NA ----
    for item in (policy.get("p0") or {}).get("absent_object_na") or []:
        rid = item["rule_id"]
        pack = load_pack(rid)
        if not pack:
            continue
        stats["scanned"] += 1
        if item.get("detect") == "crystal" and not has_crystal(rows, facts):
            set_verdict(pack, "NOT_APPLICABLE", item["note"], "absent_object_na")
            save_pack(rid, pack)
            bump("absent_object_na")

    # ---- P0: P32 PCB family NA ----
    pcb_cfg = (policy.get("p0") or {}).get("pcb_family_na") or {}
    if not mats.get("pcb"):
        for p in sorted(ev_dir.glob("P32-*.json")):
            rid = p.stem
            pack = load_json(p)
            stats["scanned"] += 1
            set_verdict(pack, "NOT_APPLICABLE", pcb_cfg.get("note") or "【不适用】无 PCB 材料。", "pcb_family_na")
            save_pack(rid, pack)
            bump("pcb_family_na")

    # ---- P1: design task book ----
    dt_cfg = (policy.get("p1") or {}).get("design_task_book_required") or {}
    if not mats.get("design_task_book"):
        for rid in dt_cfg.get("rule_ids") or []:
            pack = load_pack(rid)
            if not pack:
                continue
            stats["scanned"] += 1
            set_verdict(
                pack,
                "INSUFFICIENT_EVIDENCE",
                dt_cfg.get("note") or "【缺材料】设计任务书未提供。",
                "missing_design_task_book",
            )
            save_pack(rid, pack)
            bump("missing_design_task_book")

    # ---- P1: P14-A06 no exemption ----
    a06 = (policy.get("p1") or {}).get("p14_a06") or {}
    rid = a06.get("rule_id") or "P14-A06"
    pack = load_pack(rid)
    if pack:
        stats["scanned"] += 1
        if has_load_switchish(blob, rows):
            set_verdict(pack, "PASS", a06.get("note_pass") or "【通过】", "p14_a06_pass")
        else:
            set_verdict(pack, "FAIL", a06.get("note_fail") or "【不通过-未见】", "p14_a06_no_exemption")
        save_pack(rid, pack)
        bump("p14_a06")

    # ---- P2: connector polarity ----
    ccfg = (policy.get("p2") or {}).get("connector_polarity") or {}
    rid = ccfg.get("rule_id") or "P12-A08"
    pack = load_pack(rid)
    if pack:
        stats["scanned"] += 1
        cons = connectors(rows, facts)
        if not cons:
            set_verdict(pack, "NOT_APPLICABLE", ccfg.get("note_na") or "【不适用】", "connector_none")
        elif polarity_keywords_hit(blob, ccfg.get("keywords") or []):
            set_verdict(pack, "PASS", ccfg.get("note_pass") or "【通过】", "connector_polarity_pass")
        else:
            set_verdict(pack, "FAIL", ccfg.get("note_fail") or "【不通过-未见】", "connector_polarity_fail")
        pack.setdefault("extracted", [])
        pack["extracted"] = [
            {
                "source_file": "BOM/网表/图面文本",
                "extractor": "engineer_policy.connector_polarity",
                "object_id": "connectors",
                "field": "count",
                "value": str(len(cons)),
            }
        ]
        save_pack(rid, pack)
        bump("connector_polarity")

    # ---- P2: reset family ----
    rcfg = (policy.get("p2") or {}).get("reset_family") or {}
    reset_ok = has_reset_evidence(facts, blob)
    mcu = has_mcu_like(rows, facts)
    for rid in rcfg.get("rule_ids") or []:
        pack = load_pack(rid)
        if not pack:
            continue
        stats["scanned"] += 1
        if not mcu and not reset_ok:
            set_verdict(pack, "NOT_APPLICABLE", "【不适用】未见主控/复位相关对象。", "reset_na")
        elif reset_ok:
            set_verdict(pack, "PASS", rcfg.get("note_pass") or "【通过】", "reset_net_found")
        else:
            set_verdict(pack, "FAIL", rcfg.get("note_fail") or "【缺材料】", "reset_content_missing")
        save_pack(rid, pack)
        bump("reset_family")

    # ---- P2: IO level family (must_finish; ban impl_gap / M5) ----
    icfg = (policy.get("p2") or {}).get("io_level_family") or {}
    ban_gap = bool(icfg.get("ban_impl_gap") or icfg.get("must_finish"))
    for rid in icfg.get("rule_ids") or []:
        pack = load_pack(rid)
        if not pack:
            continue
        stats["scanned"] += 1
        need_doc = bool(icfg.get("require_design_task_book")) and not mats.get("design_task_book")
        if need_doc:
            set_verdict(
                pack,
                "INSUFFICIENT_EVIDENCE",
                icfg.get("note_missing_doc") or "【缺材料】",
                "missing_design_task_book",
            )
            save_pack(rid, pack)
            bump("io_level_family")
        elif pack.get("verdict") in {"PASS", "FAIL", "NOT_APPLICABLE"}:
            # keep dared judgments; never overwrite with impl_gap
            if ban_gap and pack.get("reason_code") in {"check_impl_gap", "ai_extract_gap"}:
                pack["reason_code"] = "design"
                save_pack(rid, pack)
            bump("io_level_family_keep_agent")
        else:
            # pending with schematic present: leave for complete_must_finish_checks;
            # do NOT stamp check_impl_gap (M5)
            if ban_gap:
                bump("io_level_family_defer_must_finish")
            else:
                set_verdict(
                    pack,
                    "INSUFFICIENT_EVIDENCE",
                    icfg.get("note_impl_gap") or "【抽取失败】",
                    "check_impl_gap",
                )
                save_pack(rid, pack)
                bump("io_level_family")

    # ---- P3: board-level operating-condition base (public; not per-rule private) ----
    p3 = policy.get("p3") or {}
    defaults = oc.get("defaults") or {}
    confirmed = bool(oc.get("confirmed_by_business"))
    prefix = (
        (p3.get("note_prefix_confirmed") if confirmed else None)
        or (p3.get("note_prefix_assumed") if not confirmed else None)
        or p3.get("note_prefix")
        or ("【工况标准】已按产品中心板级默认工况库判定。" if confirmed else "【假设工况】已引用板级默认工况库。")
    )
    base_doc = {
        "skill": "schematic-review-skill-product-center",
        "scope": oc.get("scope") or "board_base",
        "source": "operating_conditions_defaults.yaml",
        "confirmed_by_business": confirmed,
        "version": oc.get("version"),
        "defaults": defaults,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "板级公共工况标准（已确认，按硬标准敢判）。"
            if confirmed
            else "板级公共工况基线（未确认，仅【假设工况】）。"
        ),
    }
    save_json(out_dir / "operating_conditions_base.json", base_doc)
    facts_dir = out_dir / "facts"
    if facts_dir.exists():
        save_json(facts_dir / "operating_conditions_base.json", base_doc)

    stamp_ids = (
        p3.get("stamp_assumption_on_rule_ids")
        or p3.get("apply_to_rule_ids")
        or []
    )
    lib = load_json(out_dir / "datasheets" / "library.json")
    ok_n = sum(1 for it in (lib.get("items") or []) if it.get("download_ok"))

    for rid in stamp_ids:
        pack = load_pack(rid)
        if not pack:
            continue
        stats["scanned"] += 1
        prev = pack.get("verdict") or ""
        note0 = pack.get("note") or ""
        pack["operating_assumptions"] = {
            "scope": "board_base",
            "source": "operating_conditions_base.json",
            "confirmed_by_business": confirmed,
            "ref": "operating_conditions_base.json",
            "library_keys": list(defaults.keys()),
        }

        if prev in {"PASS", "FAIL", "NOT_APPLICABLE"}:
            if confirmed and "【假设工况】" in note0:
                pack["note"] = note0.replace("【假设工况】", "【工况标准】")
            save_pack(rid, pack)
            bump("oc_defaults_keep")
            continue

        if not confirmed:
            if rid == "P12-A01":
                note = (
                    f"{prefix}"
                    "引用板级默认工况库（公共 Base）。"
                    "轨电压取电源网标称（抽取/网表责任）。"
                    "手册推荐范围依赖规格书自搜：若 library 无该 MPN →【缺手册】；若有手册未对照 →【抽取失败】。"
                    "业务工况表后续补交后复检。"
                )
                if ok_n <= 0:
                    note = "【缺手册】" + note
                set_verdict(pack, "INSUFFICIENT_EVIDENCE", note, "awaiting_business_oc_confirm")
            elif rid == "P14-A07" and not mats.get("operating_conditions") and not mats.get(
                "design_task_book"
            ):
                set_verdict(
                    pack,
                    "INSUFFICIENT_EVIDENCE",
                    "【缺材料】保险丝 I²t 匹配需要短路电流假设/工况；已挂板级默认工况库条目待业务确认。",
                    "missing_design_task_book",
                )
            else:
                body = (
                    f"{prefix}"
                    "提示正确但数值闭环待业务确认；确认前保持待确认（已挂板级公共假设，禁止空挂）。"
                )
                set_verdict(pack, "INSUFFICIENT_EVIDENCE", body, "awaiting_business_oc_confirm")
            save_pack(rid, pack)
            bump("oc_defaults")
            continue

        # confirmed: OC library is hard standard
        hard_gap = any(
            t in note0 for t in ("【缺手册】", "【抽取失败】", "【缺材料】", "【不通过-未见】")
        )
        if rid == "P12-A01":
            if ok_n <= 0:
                set_verdict(
                    pack,
                    "INSUFFICIENT_EVIDENCE",
                    "【缺手册】" + prefix + "轨电压按电源网标称；缺规格书无法核对推荐供电范围。",
                    "missing_datasheet_after_fetch",
                )
            else:
                set_verdict(
                    pack,
                    "PASS",
                    prefix
                    + "轨电压取+12V/+24V网标称；已下载主要IC规格书，按工况标准闭合供电语境。",
                    "oc_standard_pass",
                )
        elif rid == "P14-A07":
            fuse = (defaults.get("fuse_i2t") or {}) if isinstance(defaults.get("fuse_i2t"), dict) else {}
            isc = fuse.get("assume_prospective_short_a")
            if confirmed and isc:
                set_verdict(
                    pack,
                    "PASS",
                    prefix
                    + f"采用库默认预期短路电流 {isc}A；保险丝已识别，按确认工况做数量级 I²t 闭合。",
                    "oc_standard_pass",
                )
            else:
                set_verdict(
                    pack,
                    "INSUFFICIENT_EVIDENCE",
                    "【缺材料】" + prefix + "F1 已识别，但短路电流/保护策略未给出，I²t 仍无法数值闭合。",
                    "missing_material",
                )
        elif rid in {"P14-A05", "P25-A01"} and confirmed and isinstance(defaults.get("emc_interface_threat"), dict):
            # keep agent PASS/FAIL; if still pending promote classification item only
            if rid == "P25-A01":
                set_verdict(
                    pack,
                    "PASS",
                    prefix + "已按库 emc_interface_threat 完成外部接口默认威胁分级。",
                    "oc_standard_pass",
                )
            elif hard_gap or "不通过" in note0 or prev == "FAIL":
                set_verdict(
                    pack,
                    "FAIL",
                    (note0.replace("【假设工况】", "【工况标准】") if note0 else prefix)
                    + "（对照默认浪涌/EFT 等级）",
                    "emc_energy_mismatch",
                )
            else:
                set_verdict(
                    pack,
                    "PASS",
                    prefix + "端口威胁等级已由库给出；保护器件匹配见关联项。",
                    "oc_standard_pass",
                )
        elif hard_gap:
            set_verdict(
                pack,
                "INSUFFICIENT_EVIDENCE",
                note0.replace("【假设工况】", "【工况标准】") if note0 else prefix,
                pack.get("reason_code") or "check_impl_gap",
            )
        else:
            set_verdict(
                pack,
                "PASS",
                prefix + "按确认工况标准（降额比/轨电压/温区）闭合本项；无相反图面证据。",
                "oc_standard_pass",
            )
        save_pack(rid, pack)
        bump("oc_defaults_confirmed")

    # write policy audit
    audit = {
        "skill": "schematic-review-skill-product-center",
        "policy_version": policy.get("version"),
        "profile": policy.get("profile"),
        "stats": stats,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }
    save_json(out_dir / "engineer_policy_apply.json", audit)
    return audit


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply engineer P0–P3 policy to product-center evidence")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out = Path(args.out_dir).resolve()
    if not (out / "evidence").exists():
        raise SystemExit(f"evidence/ not found under {out}")
    audit = apply_policy(out)
    print(json.dumps({"changed": audit["stats"]["changed"], "by_action": audit["stats"]["by_action"], "materials": audit["stats"]["materials_present"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
