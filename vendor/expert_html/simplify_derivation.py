# -*- coding: utf-8 -*-
"""Slim EvidencePack narration for human-readable HTML.

Keeps: core verdict note, substantive derivation steps (no fixed step cap),
focus shot. Drops/collapses: boilerplate observations, verbose web_research cards.
Step count follows check complexity (1–3 simple, 5–7 complex OK); ban padding only.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REF_RE = re.compile(r"\b([A-Z]{1,3}\d{1,4})\b")
# Soft ceiling disabled (0). Preserve professional long-form narration.
SOFT_MAX_STEPS = 0
BOILER_ACT = ("声明：", "汇总本板证据", "材料盘点", "全量列举")


def load_json(p: Path) -> dict:
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8-sig"))


def save_json(p: Path, obj: object) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def clip(s: str, n: int = 220) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def _is_boiler(act: str, obs: str) -> bool:
    blob = f"{act} {obs}"
    if any(b in blob for b in BOILER_ACT):
        return True
    if not act.strip() and not obs.strip():
        return True
    return False


def slim_steps(pack: dict) -> list[dict]:
    note = (pack.get("note") or "").strip()
    verdict = pack.get("verdict") or ""
    pages = pack.get("pages_checked") or []
    focus = next((p for p in pages if p.get("crop") and p.get("screenshot")), None)
    shot = (focus or {}).get("screenshot")
    page_lab = (focus or {}).get("page") or (focus or {}).get("region") or ""

    refs = REF_RE.findall(note)
    ref_txt = "、".join(refs[:8]) if refs else "本项相关对象"
    zh = {"PASS": "通过", "FAIL": "不通过", "NOT_APPLICABLE": "不适用", "INSUFFICIENT_EVIDENCE": "待确认"}.get(
        verdict, verdict
    )

    out: list[dict] = []
    for s in pack.get("steps") or []:
        if not isinstance(s, dict):
            continue
        act = (s.get("action") or "").strip()
        obs = (s.get("observation") or "").strip()
        if _is_boiler(act, obs):
            continue
        if len(act) > 100:
            act = act[:99] + "…"
        out.append(
            {
                "step": len(out) + 1,
                "action": act or f"核对：{ref_txt}",
                # Preserve full professional observation — no char cap
                "observation": (obs or note).strip(),
                "decision": s.get("decision") or "continue",
                "screenshot": s.get("screenshot") if out else shot,
            }
        )
        if SOFT_MAX_STEPS > 0 and len(out) >= SOFT_MAX_STEPS - 1:
            break

    if not out:
        out.append(
            {
                "step": 1,
                "action": f"定位并核对：{ref_txt}",
                "observation": (
                    f"检图：{page_lab or '见截图'}；对象：{ref_txt}。"
                    + (f"要点：{note}" if note else "")
                ).strip(),
                "decision": "continue",
                "screenshot": shot,
            }
        )
    elif shot and not any(x.get("screenshot") for x in out):
        out[0]["screenshot"] = shot

    # Ensure terminal conclusion step once
    last_dec = (out[-1].get("decision") or "").upper()
    allow_more = SOFT_MAX_STEPS <= 0 or len(out) < SOFT_MAX_STEPS
    if last_dec not in {"PASS", "FAIL", "NOT_APPLICABLE", "INSUFFICIENT_EVIDENCE"} and allow_more:
        out.append(
            {
                "step": len(out) + 1,
                "action": "结论",
                "observation": f"{zh}。{note}".strip(),
                "decision": verdict,
                "screenshot": None,
            }
        )
    else:
        # renumber
        for i, row in enumerate(out, 1):
            row["step"] = i
        if last_dec not in {"PASS", "FAIL", "NOT_APPLICABLE", "INSUFFICIENT_EVIDENCE"}:
            out[-1]["decision"] = verdict
            if note and len((out[-1].get("observation") or "")) < 40:
                out[-1]["observation"] = f"{zh}。{note}".strip()

    for i, row in enumerate(out, 1):
        row["step"] = i
    return out


def slim_extracted(pack: dict) -> list[dict]:
    rows = []
    seen: set[str] = set()
    note = pack.get("note") or ""
    if note:
        v = clip(note, 280)
        seen.add(v)
        rows.append(
            {
                "source_file": "判定备注",
                "extractor": "core",
                "object_id": pack.get("rule_id"),
                "field": "finding",
                "value": v,
            }
        )
    for r in pack.get("extracted") or []:
        if not isinstance(r, dict):
            continue
        val = str(r.get("value") or "")
        if len(val) < 8:
            continue
        if val.count("；") > 8 or "合格样例" in val:
            continue
        v = clip(val, 160)
        if v in seen or (note and v in note):
            continue
        seen.add(v)
        rows.append(
            {
                "source_file": r.get("source_file") or "",
                "extractor": r.get("extractor") or "fact",
                "object_id": r.get("object_id") or "",
                "field": r.get("field") or "",
                "value": v,
            }
        )
        if len(rows) >= 5:
            break
    return rows


def slim_one(pack: dict) -> dict:
    # Preserve hardware-expert / check-specific rebuilds; only clear verbose web_research.
    profile = str(pack.get("derivation_profile") or "")
    if profile.startswith("hw_expert_pro") or profile.startswith("check_specific_from_playbook"):
        p = dict(pack)
        p["web_research"] = []
        return p
    p = dict(pack)
    p["steps"] = slim_steps(p)
    p["extracted"] = slim_extracted(p)
    p["web_research"] = []
    if p.get("remediation"):
        p["remediation"] = clip(str(p["remediation"]), 160)
    p["derivation_profile"] = "core_only_v2_no_step_cap"
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out = Path(args.out_dir).resolve()
    n = 0
    kept = 0
    for path in sorted((out / "evidence").glob("P*-A*.json")):
        pack = load_json(path)
        prof = str(pack.get("derivation_profile") or "")
        if prof.startswith("hw_expert_pro") or prof.startswith("check_specific_from_playbook"):
            kept += 1
        save_json(path, slim_one(pack))
        n += 1
    print(
        json.dumps(
            {
                "simplified": n,
                "preserved_profiles": kept,
                "profile": "core_only_v2_no_step_cap|hw_expert_pro|check_specific_from_playbook*",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
