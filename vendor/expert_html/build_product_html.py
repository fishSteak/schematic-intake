# -*- coding: utf-8 -*-
"""Product-center HTML entry: reuse expert-html SPA style via build_expert_html.py."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# Delegate to the expert-html style builder (adapted for 85-item playbook).
from build_expert_html import build_html, load_product_items  # noqa: E402


def export_csv(out_dir: Path) -> Path:
    items = {it["rule_id"]: it for it in load_product_items()}
    rows = []
    for p in sorted((out_dir / "evidence").glob("P*-A*.json")):
        try:
            pack = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        rid = pack.get("rule_id") or p.stem
        it = items.get(rid) or {}
        vmap = {
            "PASS": "是",
            "FAIL": "否",
            "INSUFFICIENT_EVIDENCE": "待确认",
            "NOT_APPLICABLE": "不适用",
            "REVIEW_REQUIRED": "待确认",
        }
        rows.append(
            [
                rid,
                it.get("category") or pack.get("category") or "",
                it.get("name") or pack.get("name") or "",
                vmap.get(pack.get("verdict"), pack.get("verdict_zh") or pack.get("verdict")),
                "是" if pack.get("ai_self_check") else "否",
                (pack.get("note") or "")[:800],
            ]
        )
    dest = out_dir / "checklist_85_判定回填.csv"
    with dest.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["原子规则ID", "检查大类", "原子检查项", "判定", "AI自检", "备注"])
        w.writerows(rows)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    html_doc = build_html(out_dir)
    dest = out_dir / "expert_review.html"
    dest.write_text(html_doc, encoding="utf-8")
    csv_path = export_csv(out_dir)
    print(f"wrote {dest} ({len(html_doc)} bytes)")
    print(f"wrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
