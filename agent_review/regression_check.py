# -*- coding: utf-8 -*-
"""板级金样例回归（防退步）。不进 Agent Prompt。

用法:
  python -m agent_review.regression_check --fixture agent_review/regression/r14_p11a01_0918.json
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from kernel.util import out_dir, read_json, resolve_board_path


def _p_max_w(card: dict) -> float | None:
    elec = card.get("electrical") or {}
    for k in ("P_max", "P_max_w", "p_max"):
        if elec.get(k) is not None:
            try:
                return float(elec[k])
            except (TypeError, ValueError):
                pass
    return None


def _led_1ma_long_steady_pit(card: dict, analysis: str) -> str | None:
    """已知坑：LED 相关态用 ~1 mA 口算，却未标非长期/站不住/待确认。"""
    del analysis  # 坑以落卡为准；分析仅作人工对照
    excused = re.compile(r"(非长期|非稳态|站不住|不可达|待确认|方程有解但|逻辑会拆)")
    one_ma = re.compile(r"(?:≈\s*)?1\s*mA|约\s*1\s*mA", re.I)
    for i, st in enumerate(card.get("states") or []):
        if not isinstance(st, dict):
            continue
        name = str(st.get("name_zh") or st.get("name") or "")
        conc = str(st.get("结论") or st.get("conclusion") or "")
        blob = "\n".join(
            str(x.get("expr") or "")
            for x in (st.get("formula_lines") or [])
            if isinstance(x, dict)
        )
        text = f"{name}\n{conc}\n{blob}"
        if not re.search(r"LED|绿段|指示灯", text, re.I):
            continue
        if not one_ma.search(text):
            continue
        if excused.search(text):
            continue
        return (
            f"pit led_1mA_as_long_steady: states[{i}] 仍用约 1 mA 且未标非长期/站不住 "
            f"(name={name[:80]!r})"
        )
    return None


def check_fixture(fixture: dict, *, root: Path) -> list[str]:
    errs: list[str] = []
    board = resolve_board_path(fixture["board"])
    o = out_dir(board)
    rid = fixture["rule_id"]
    obj = fixture["object"]
    card_p = o / "agent_jobs" / rid / f"{obj}.json"
    an_p = o / "agent_jobs" / rid / f"{obj}.analysis.md"
    if not card_p.is_file():
        return [f"missing card: {card_p}"]
    card = read_json(card_p)
    analysis = an_p.read_text(encoding="utf-8") if an_p.is_file() else ""

    exp = fixture.get("expect") or {}
    if exp.get("verdict_zh") and card.get("verdict_zh") != exp["verdict_zh"]:
        errs.append(f"verdict_zh want={exp['verdict_zh']!r} got={card.get('verdict_zh')!r}")

    if exp.get("P_limit_W") is not None:
        lim = (card.get("electrical") or {}).get("P_limit")
        try:
            if lim is None or abs(float(lim) - float(exp["P_limit_W"])) > 1e-9:
                errs.append(f"P_limit want={exp['P_limit_W']} got={lim}")
        except (TypeError, ValueError):
            errs.append(f"P_limit unreadable: {lim!r}")

    pm = exp.get("P_max_W") or {}
    if isinstance(pm, dict):
        got = _p_max_w(card)
        if got is None:
            errs.append("P_max missing")
        elif pm.get("approx") is not None:
            want = float(pm["approx"])
            tol = float(pm.get("tol_rel") or 0.2)
            if abs(got - want) > tol * want:
                errs.append(f"P_max want≈{want} (±{tol:.0%}) got={got}")
        else:
            lo = pm.get("min")
            hi = pm.get("max")
            if lo is not None and got < float(lo):
                errs.append(f"P_max below min {lo}: got={got}")
            if hi is not None and got > float(hi):
                errs.append(f"P_max above max {hi}: got={got}")

    for req in exp.get("require_state_P_approx") or []:
        want = float(req["approx"])
        tol = float(req.get("tol_rel") or 0.25)
        hit = False
        for st in card.get("states") or []:
            if not isinstance(st, dict):
                continue
            raw = st.get("P") if st.get("P") is not None else st.get("P_w")
            try:
                pv = float(raw)
            except (TypeError, ValueError):
                continue
            if abs(pv - want) <= tol * want:
                hit = True
                break
        if not hit:
            errs.append(
                f"missing state P≈{want} (±{tol:.0%})"
                + (f" ({req.get('note')})" if req.get("note") else "")
            )

    for pit in fixture.get("pits") or []:
        if pit.get("id") == "led_1mA_as_long_steady":
            msg = _led_1ma_long_steady_pit(card, analysis)
            if msg:
                errs.append(msg)
    return errs


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--fixture",
        default="agent_review/regression/r14_p11a01_0918.json",
        help="金样例 JSON（板级；Agent 不可见）",
    )
    args = ap.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    fix_path = Path(args.fixture)
    if not fix_path.is_absolute():
        fix_path = root / fix_path
    fixture = read_json(fix_path)
    errs = check_fixture(fixture, root=root)
    if errs:
        print(f"FAIL\t{fixture.get('id')}\t{len(errs)}")
        for e in errs:
            print(f"  - {e}")
        return 2
    print(f"OK\t{fixture.get('id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
