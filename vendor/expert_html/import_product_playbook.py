# -*- coding: utf-8 -*-
"""Import product-center 85 playbook from revised Excel into rules/."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_XLSX = ROOT / "references" / "产品中心85_专家列修订稿.xlsx"
OUT = ROOT / "rules"

FIELD_MAP = {
    "新序号": "seq",
    "原序号": "legacy_seq",
    "原子规则ID": "rule_id",
    "华为检查标准": "huawei_standard",
    "检查大类": "category",
    "原子检查项": "name",
    "出错后严重度": "severity",
    "输入文件/资料": "required_materials",
    "建议检查方式": "expert_check_method",
    "期望输出的格式": "result_format",
    "检查过程（详细说明）": "check_process",
}


def clean(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v.replace("\r\n", "\n").strip()
    return v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default=str(DEFAULT_XLSX))
    ap.add_argument("--sheet", default="产品中心85_修订后")
    args = ap.parse_args()
    path = Path(args.xlsx)
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[args.sheet] if args.sheet in wb.sheetnames else wb[wb.sheetnames[0]]
    headers = [ws.cell(2, c).value for c in range(1, 12)]
    items = []
    for r in range(3, ws.max_row + 1):
        raw = {headers[c - 1]: ws.cell(r, c).value for c in range(1, 12)}
        rid = str(raw.get("原子规则ID") or "").strip()
        if not re.match(r"^P\d+-A\d+$", rid):
            continue
        item = {f: clean(raw.get(h)) for h, f in FIELD_MAP.items()}
        item["rule_id"] = rid
        try:
            item["seq"] = int(item["seq"])
        except Exception:
            item["seq"] = len(items) + 1
        mats = item["required_materials"]
        proc = item["check_process"]
        item["playbook_filled"] = bool(mats and proc)
        item["ai_self_check"] = not item["playbook_filled"]
        item["need_datasheet_query"] = "AI自决"
        item["need_human"] = "AI自决"
        items.append(item)
    if len(items) != 85:
        raise SystemExit(f"expected 85 items, got {len(items)}")
    playbook = {
        "version": "product-center-v1.0",
        "skill": "schematic-review-skill-product-center",
        "template": "产品中心",
        "item_count": 85,
        "source_file": str(path),
        "authority_note": (
            "产品中心85项。专家列：材料/方式/输出/过程。"
            "空列→AI自检。规格书AI自搜下载。与v1.0/181隔离。"
        ),
        "policy": {
            "empty_expert_cols": "ai_self_check",
            "datasheet": "ai_search_and_download",
            "judgment": "dare_to_judge_occasionally_strict",
            "materials_are_user_side_only": True,
            "do_not_mix_with_v1_0": True,
        },
        "items": items,
        "rule_ids": [it["rule_id"] for it in items],
        "summary": {
            "total": 85,
            "playbook_filled": sum(1 for it in items if it["playbook_filled"]),
            "ai_self_check": sum(1 for it in items if it["ai_self_check"]),
            "categories": dict(Counter(it["category"] for it in items)),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "product_playbook.json").write_text(
        json.dumps(playbook, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "product_playbook_index.json").write_text(
        json.dumps(
            {
                "version": playbook["version"],
                "count": 85,
                "rule_ids": playbook["rule_ids"],
                "by_id": {it["rule_id"]: it for it in items},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"OK {len(items)} items → {OUT / 'product_playbook.json'}")
    print("filled", playbook["summary"]["playbook_filled"], "ai_self", playbook["summary"]["ai_self_check"])


if __name__ == "__main__":
    main()
