# -*- coding: utf-8 -*-
"""Init evidence stubs for product-center 85 items (Agent fills verdicts)."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from load_playbook import load_playbook


def stub(item: dict) -> dict:
    rid = item["rule_id"]
    return {
        "rule_id": rid,
        "name": item.get("name"),
        "category": item.get("category"),
        "severity": item.get("severity"),
        "template": "产品中心",
        "verdict": "INSUFFICIENT_EVIDENCE",
        "verdict_zh": "待确认",
        "ready": False,
        "ai_self_check": bool(item.get("ai_self_check")),
        "playbook_filled": bool(item.get("playbook_filled")),
        "expert_playbook": {
            "required_materials": item.get("required_materials") or "",
            "expert_check_method": item.get("expert_check_method") or "",
            "result_format": item.get("result_format") or "",
            "check_process": item.get("check_process") or "",
            "need_datasheet_query": "AI自决",
            "ai_self_check": bool(item.get("ai_self_check")),
        },
        "steps": [],
        "note": (
            "专家过程未填，待 AI 自检" if item.get("ai_self_check") else "待按专家过程执行"
        ),
        "references": [],
        "datasheets_used": [],
        "pages_checked": [],
        "meta": {
            "skill": "schematic-review-skill-product-center",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out = Path(args.out_dir)
    ev = out / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    pb = load_playbook()
    for item in pb["items"]:
        p = ev / f"{item['rule_id']}.json"
        if p.exists():
            continue
        p.write_text(json.dumps(stub(item), ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "product_playbook_used.json").write_text(
        json.dumps(pb, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"stubs ready under {ev} ({len(pb['items'])} items)")


if __name__ == "__main__":
    main()
