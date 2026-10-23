# -*- coding: utf-8 -*-
"""Apply adjudication rework instructions against a review_out (product-center).

Reads review_out/adjudication/rework_latest.json and runs targeted fixes:
- reattach_schematic_shot
- simplify_derivation
- apply_engineer_policy
- rebuild_html
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PC_SCRIPTS = Path(__file__).resolve().parent
ADJ_SCRIPTS = Path(__file__).resolve().parents[2] / "schematic-review-adjudication" / "scripts"


def run(cmd: list[str]) -> int:
    print("+", " ".join(cmd))
    return subprocess.run(cmd).returncode


def load_json(p: Path) -> dict:
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8-sig"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--prior-round", type=int, default=0, help="stamp meta.prior_adjudication_round")
    args = ap.parse_args()
    out = Path(args.out_dir).resolve()
    rework = load_json(out / "adjudication" / "rework_latest.json")
    if rework.get("passed"):
        print(json.dumps({"skipped": True, "reason": "already_passed"}, ensure_ascii=False))
        return 0

    by_fix = rework.get("by_fix") or {}
    force = rework.get("force_by_rule") or {}
    actions = []

    if "reattach_schematic_shot" in by_fix or force:
        rules = sorted(set((by_fix.get("reattach_schematic_shot") or []) + list(force.keys())))
        only = ",".join(rules)
        rc = run(
            [
                sys.executable,
                str(PC_SCRIPTS / "attach_schematic_shots.py"),
                "--out-dir",
                str(out),
                "--rework-json",
                str(out / "adjudication" / "rework_latest.json"),
                "--only-rules",
                only,
            ]
        )
        actions.append({"attach": rc, "rules": len(rules)})

    if "simplify_derivation" in by_fix or "add_note_tag" in by_fix:
        # note tags need agent; still slim narration
        rc = run([sys.executable, str(PC_SCRIPTS / "simplify_derivation.py"), "--out-dir", str(out)])
        actions.append({"simplify": rc})

    if "apply_engineer_policy" in by_fix:
        rc = run([sys.executable, str(PC_SCRIPTS / "apply_engineer_policy.py"), "--out-dir", str(out)])
        actions.append({"policy": rc})
        run([sys.executable, str(PC_SCRIPTS / "enrich_product_evidence.py"), "--out-dir", str(out)])
        run([sys.executable, str(PC_SCRIPTS / "simplify_derivation.py"), "--out-dir", str(out)])

    # Hardening remediations for adjudication B5/M2/M4/M5 (content + must-finish + check-specific steps)
    need_content = any(
        k.startswith("M") or k in {"attach_datasheet_context", "uniquify_note", "fix_fake_missing"}
        for k in by_fix
    ) or any(
        str((rework.get("force_by_rule") or {}).get(rid, {}).get("fix") or "").startswith("M")
        for rid in (rework.get("rule_ids") or [])
    )
    # Also trigger when defects mention M2/M4 in rework gate list
    gates = {str(x) for x in (rework.get("gates") or [])}
    for d in rework.get("defects") or []:
        if isinstance(d, dict) and d.get("gate"):
            gates.add(str(d["gate"]))
    if need_content or gates.intersection({"M1", "M2", "M3", "M4", "M5"}):
        rem = PC_SCRIPTS / "apply_content_remediation.py"
        if rem.exists():
            actions.append({"content_remediation": run([sys.executable, str(rem), "--out-dir", str(out)])})
    if gates.intersection({"M5", "B5"}) or "complete_check_no_impl_gap" in by_fix:
        must = PC_SCRIPTS / "complete_must_finish_checks.py"
        if must.exists():
            actions.append({"must_finish": run([sys.executable, str(must), "--out-dir", str(out)])})
    if gates.intersection({"B5"}) or "rewrite_check_specific_steps" in by_fix:
        rebuild = PC_SCRIPTS / "rebuild_check_specific_steps.py"
        if rebuild.exists():
            actions.append({"rebuild_steps": run([sys.executable, str(rebuild), "--out-dir", str(out)])})
            slim = PC_SCRIPTS / "simplify_derivation.py"
            if slim.exists():
                run([sys.executable, str(slim), "--out-dir", str(out)])

    # Always rebuild HTML after remediations
    rc_html = run([sys.executable, str(PC_SCRIPTS / "build_product_html.py"), "--out-dir", str(out)])
    actions.append({"html": rc_html})

    if args.prior_round:
        meta_stamp = {
            "prior_adjudication_round": args.prior_round,
            "rework_from": f"round_{args.prior_round}",
        }
        (out / "adjudication" / "last_remediation.json").write_text(
            json.dumps({"actions": actions, **meta_stamp}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # stamp evidence packs that were in rework
        for rid in rework.get("rule_ids") or []:
            p = out / "evidence" / f"{rid}.json"
            if not p.exists():
                continue
            pack = load_json(p)
            pack.setdefault("meta", {})
            pack["meta"]["prior_adjudication_round"] = args.prior_round
            p.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"remediated": True, "actions": actions}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
