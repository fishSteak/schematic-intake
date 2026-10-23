# -*- coding: utf-8 -*-
"""Content audit for product-center review_out.

Business materials (Excel required_materials) vs AI-fetched datasheet context.
Writes content_audit_report.json — blocking issues must be cleared before delivery.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_json(p: Path) -> dict:
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8-sig"))


def save_json(p: Path, obj: object) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def list_input_files(out: Path) -> list[str]:
    names: list[str] = []
    inv = load_json(out / "input_inventory.json")
    names.extend(list(inv.get("files") or []))
    parent = out.parent
    if parent.is_dir():
        names.extend(p.name for p in parent.iterdir() if p.is_file() and not p.name.startswith("_"))
    seen: set[str] = set()
    out_n: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out_n.append(n)
    return out_n


def classify_business(files: list[str]) -> dict[str, bool]:
    present = {
        "schematic": False,
        "bom": False,
        "netlist": False,
        "drc": False,
        "design_task_book": False,
        "operating_conditions": False,
        "pcb": False,
    }
    for name in files:
        low = name.lower()
        if low.endswith(".pdf") and not low.endswith((".xlsx",)):
            present["schematic"] = True
        if low.endswith((".dsn", ".opj")) or "原理" in name:
            present["schematic"] = True
        if "bom" in low:
            present["bom"] = True
        if low.endswith((".net", ".tel")):
            present["netlist"] = True
        if "drc" in low:
            present["drc"] = True
        if "热设计" in name or "功耗" in name:
            present["operating_conditions"] = True
        if ("任务书" in name or "需求" in name) and "热设计" not in name:
            present["design_task_book"] = True
        if "pcb" in low or "布局" in name:
            present["pcb"] = True
    return present


def playbook_kinds(req: str) -> list[str]:
    raw = req or ""
    kinds: list[str] = []
    if "原理图" in raw:
        kinds.append("schematic")
    if re.search(r"(?i)\bBOM\b|物料", raw):
        kinds.append("bom")
    if "网表" in raw:
        kinds.append("netlist")
    if re.search(r"(?i)DRC|ERC", raw):
        kinds.append("drc")
    if "任务书" in raw or "接口需求" in raw:
        kinds.append("design_task_book")
    if "工况" in raw or "热设计" in raw:
        kinds.append("operating_conditions")
    if "PCB" in raw or "布局" in raw:
        kinds.append("pcb")
    return kinds


def needs_datasheet(pack: dict) -> bool:
    blob = " ".join(
        [
            str(pack.get("name") or ""),
            str((pack.get("expert_playbook") or {}).get("expert_check_method") or ""),
            str((pack.get("expert_playbook") or {}).get("check_process") or ""),
        ]
    )
    for k in ("规格书", "数据手册", "器件数据手册", "手册要求", "下载规格书", "datasheet"):
        if k.lower() in blob.lower() if k.isascii() else k in blob:
            return True
    return False


def audit(out: Path) -> dict:
    mats = classify_business(list_input_files(out))
    issues: list[dict] = []
    note_map: dict[str, list[str]] = defaultdict(list)

    for path in sorted((out / "evidence").glob("P*-A*.json")):
        pack = load_json(path)
        rid = pack.get("rule_id") or path.stem
        note = (pack.get("note") or "").strip()
        note_map[note].append(rid)
        ep = pack.get("expert_playbook") or {}
        req = str(ep.get("required_materials") or "")
        kinds = playbook_kinds(req)
        missing = [k for k in kinds if not mats.get(k)]
        rc = str(pack.get("reason_code") or "")
        v = pack.get("verdict")
        refs = pack.get("references") or []
        ds = pack.get("datasheets_used") or pack.get("datasheets") or []

        claims_missing = "【缺材料】" in note or rc in {
            "missing_material",
            "missing_design_task_book",
            "missing_materials",
        }
        task_claim = "任务书" in note or rc == "missing_design_task_book"
        if claims_missing and task_claim and "design_task_book" not in kinds and mats.get("schematic"):
            issues.append(
                {
                    "rule_id": rid,
                    "code": "M1_fake_task_book",
                    "severity": "P0",
                    "message": "playbook 未要求任务书且原理图已齐，却写缺任务书/缺材料",
                }
            )
        elif claims_missing and kinds and not missing and "【缺材料】" in note:
            issues.append(
                {
                    "rule_id": rid,
                    "code": "M1_fake_missing",
                    "severity": "P0",
                    "message": f"业务材料已齐 {kinds} 仍标缺材料",
                }
            )
        if "【缺材料】" in note and any(w in note for w in ("规格书", "数据手册")):
            issues.append(
                {
                    "rule_id": rid,
                    "code": "M1_datasheet_as_business",
                    "severity": "P0",
                    "message": "把规格书当成业务缺材料",
                }
            )

        # Adjudication M2 does not exempt N/A — require datasheets or 【缺手册】
        if needs_datasheet(pack) and not ds and "【缺手册】" not in note:
            issues.append(
                {
                    "rule_id": rid,
                    "code": "M2_no_datasheet_context",
                    "severity": "P0",
                    "message": "需手册上下文但未挂 datasheets",
                }
            )

        if v in {"PASS", "FAIL"} and not refs:
            issues.append(
                {
                    "rule_id": rid,
                    "code": "M3_empty_refs",
                    "severity": "P0",
                    "message": "是/否项引用为空",
                }
            )

    for note, rids in note_map.items():
        if note and len(rids) >= 3:
            for rid in rids:
                issues.append(
                    {
                        "rule_id": rid,
                        "code": "M4_identical_note",
                        "severity": "P0",
                        "message": f"备注与 {len(rids)} 项完全相同",
                        "group_size": len(rids),
                    }
                )

    by_code = Counter(i["code"] for i in issues)
    report = {
        "skill": "schematic-review-skill-product-center",
        "out_dir": str(out),
        "materials_present": mats,
        "issue_count": len(issues),
        "by_code": dict(by_code),
        "blocking": len(issues) > 0,
        "issues": issues,
        "policy": {
            "business_materials": "Excel required_materials only",
            "datasheets": "AI self-fetch context — never business 缺材料",
        },
        "audited_at": datetime.now(timezone.utc).isoformat(),
    }
    save_json(out / "content_audit_report.json", report)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Content audit for schematic-review-skill-product-center"
    )
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out = Path(args.out_dir).resolve()
    report = audit(out)
    print(
        json.dumps(
            {
                "skill": "schematic-review-skill-product-center",
                "blocking": report["blocking"],
                "issue_count": report["issue_count"],
                "by_code": report["by_code"],
                "report": str(out / "content_audit_report.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if report["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
