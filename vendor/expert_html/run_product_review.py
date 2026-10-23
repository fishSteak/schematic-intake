# -*- coding: utf-8 -*-
"""Product-center review bootstrap: dirs, playbook snapshot, datasheet search, stubs, HTML.

Agent must still execute per-item checks per SKILL.md (dare to judge).
This script prepares the mechanical skeleton and triggers datasheet AI search/download.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent


def run(cmd: list[str], cwd: Path | None = None) -> int:
    print("+", " ".join(cmd))
    p = subprocess.run(cmd, cwd=str(cwd or SCRIPTS))
    return p.returncode


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="board materials folder")
    ap.add_argument("--out-dir", default="", help="default: <input>/review_out")
    ap.add_argument("--skip-datasheet", action="store_true")
    ap.add_argument(
        "--with-adjudication",
        action="store_true",
        help="after polish, run detect↔adjudication loop (max 3) before customer stamp",
    )
    args = ap.parse_args()

    inp = Path(args.input).resolve()
    if not inp.exists():
        raise SystemExit(f"input not found: {inp}")
    out = Path(args.out_dir).resolve() if args.out_dir else inp / "review_out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "evidence").mkdir(exist_ok=True)
    (out / "datasheets").mkdir(exist_ok=True)
    (out / "facts").mkdir(exist_ok=True)

    # snapshot playbook
    pb = json.loads((ROOT / "rules" / "product_playbook.json").read_text(encoding="utf-8"))
    (out / "product_playbook_used.json").write_text(
        json.dumps(pb, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # inventory hint
    inventory = {
        "input_dir": str(inp),
        "files": sorted([p.name for p in inp.iterdir() if p.is_file()])[:200],
        "note": "Agent should parse BOM/schematic; optional sibling allcheck for facts",
    }
    (out / "input_inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # evidence stubs
    run([sys.executable, str(SCRIPTS / "init_evidence_stubs.py"), "--out-dir", str(out)])

    # datasheet search/download — AI path (AnySearch)
    if not args.skip_datasheet:
        ds = SCRIPTS / "search_and_fetch_datasheets.py"
        if ds.exists():
            # script expects review_out with facts/BOM cues; still run for library bootstrap
            rc = run([sys.executable, str(ds), "--out-dir", str(out)])
            print("datasheet search exit", rc, "(partial OK expected)")
        else:
            print("WARN: search_and_fetch_datasheets.py missing")

    # Schematic PDF → evidence/shots + pages_checked (HTML section ②)
    shot = SCRIPTS / "attach_schematic_shots.py"
    if shot.exists():
        rc_shot = run([sys.executable, str(shot), "--out-dir", str(out)])
        print("attach_schematic_shots exit", rc_shot, "(0 required for section ② images)")
    else:
        print("WARN: attach_schematic_shots.py missing — HTML section ② will warn 未实际检图")

    # Expand thin verdict-only packs → expert-html derivation schema (③⑤)
    enrich = SCRIPTS / "enrich_product_evidence.py"
    if enrich.exists():
        rc_en = run([sys.executable, str(enrich), "--out-dir", str(out)])
        print("enrich_product_evidence exit", rc_en)
    else:
        print("WARN: enrich_product_evidence.py missing")

    # P0–P3 engineer policy (dare-to-judge; rewrite verdicts)
    pol = SCRIPTS / "apply_engineer_policy.py"
    if pol.exists():
        rc_pol = run([sys.executable, str(pol), "--out-dir", str(out)])
        print("apply_engineer_policy exit", rc_pol)
        # re-enrich narration after verdict rewrites
        if enrich.exists():
            run([sys.executable, str(enrich), "--out-dir", str(out)])
    else:
        print("WARN: apply_engineer_policy.py missing")

    slim = SCRIPTS / "simplify_derivation.py"
    html_builder = SCRIPTS / "build_product_html.py"
    if not html_builder.exists():
        html_builder = SCRIPTS / "build_expert_html.py"

    # Content audit: business materials vs AI datasheet context (M1–M4 precursor)
    audit = SCRIPTS / "content_audit.py"
    if audit.exists():
        rc_audit = run([sys.executable, str(audit), "--out-dir", str(out)])
        if rc_audit != 0:
            rem = SCRIPTS / "apply_content_remediation.py"
            if rem.exists():
                run([sys.executable, str(rem), "--out-dir", str(out)])
                run([sys.executable, str(audit), "--out-dir", str(out)])

    # EE engineer narration scrub (does NOT invent uniform 3-step templates)
    ee = SCRIPTS / "rewrite_ee_narrative.py"
    if ee.exists():
        run([sys.executable, str(ee), "--out-dir", str(out)])

    # B5/M5: complete must-finish checks (no capability-gap escape)
    must = SCRIPTS / "complete_must_finish_checks.py"
    if must.exists():
        run([sys.executable, str(must), "--out-dir", str(out)])

    # B5: rebuild steps from each check's playbook check_process
    rebuild = SCRIPTS / "rebuild_check_specific_steps.py"
    if rebuild.exists():
        run([sys.executable, str(rebuild), "--out-dir", str(out)])

    if slim.exists():
        run([sys.executable, str(slim), "--out-dir", str(out)])
    run([sys.executable, str(html_builder), "--out-dir", str(out)])

    adj_loop = None
    if args.with_adjudication:
        loop = (
            ROOT.parents[0]
            / "schematic-review-adjudication"
            / "scripts"
            / "run_adjudication_loop.py"
        )
        if loop.exists():
            adj_loop = run(
                [
                    sys.executable,
                    str(loop),
                    "--out-dir",
                    str(out),
                    "--max-rounds",
                    "3",
                    "--expected-items",
                    "85",
                    "--skip-initial-polish",
                ]
            )
            print("adjudication loop exit", adj_loop)
        else:
            print("WARN: adjudication loop missing", loop)

    summary = {
        "skill": "schematic-review-skill-product-center",
        "template": "产品中心",
        "items": 85,
        "input": str(inp),
        "out_dir": str(out),
        "datasheet_policy": "ai_search_and_download",
        "hardening": [
            "content_audit",
            "rewrite_ee_narrative",
            "complete_must_finish_checks",
            "rebuild_check_specific_steps",
            "adjudication_B5_M5",
        ],
        "empty_expert_cols": "ai_self_check",
        "isolated_from": [
            "schematic-review-skill-expert-html-v1.0",
            "schematic-review-skill-central-research",
        ],
        "with_adjudication": bool(args.with_adjudication),
        "adjudication_exit": adj_loop,
        "next": "Agent must fill evidence/*.json per SKILL.md then rebuild HTML; delivery only if adjudication PASSED",
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    (out / "pipeline_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nDONE skeleton. Agent: follow SKILL.md to judge 85 items, then:")
    print(f"  python {html_builder} --out-dir \"{out}\"")


if __name__ == "__main__":
    main()
