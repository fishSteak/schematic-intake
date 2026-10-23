# -*- coding: utf-8 -*-
"""Rebuild per-check derivation steps from playbook check_process + board evidence.

Adjudication B5: steps must follow EACH check's real path — never a global 3-step skeleton.
Product-center: loads rules/product_playbook.json (85 items).
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_json(p: Path) -> dict:
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8-sig"))


def save_json(p: Path, obj: object) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def playbook_by_rid() -> dict[str, dict]:
    pb = load_json(ROOT / "rules" / "product_playbook.json")
    return {it.get("rule_id"): it for it in (pb.get("items") or []) if it.get("rule_id")}


def split_process(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    # Split on numbered lines: 1、 1. 1) （1）
    parts = re.split(r"(?:^|\n)\s*(?:\d+[\.、\)]|（\d+）)\s*", raw)
    lines = [re.sub(r"\s+", " ", p).strip(" ；;") for p in parts if p and p.strip()]
    if len(lines) >= 2:
        return lines
    # fallback: sentence split
    sents = [s.strip() for s in re.split(r"[。；\n]+", raw) if s.strip()]
    return sents if sents else [raw]


def board_bits(out: Path) -> dict:
    bom = load_json(out / "facts" / "bom.json")
    net = load_json(out / "facts" / "netlist.json")
    drc = load_json(out / "facts" / "drc.json")
    rows = list(bom.get("rows") or [])
    nets = net.get("nets") if isinstance(net.get("nets"), dict) else {}
    rails = [n for n in nets if re.search(r"(?i)VCC|VIN|VDD|3V3|5V|ISO", str(n))]
    empty = [str(r.get("ref")) for r in rows if not str(r.get("mpn") or "").strip()][:10]
    ics = [f"{r.get('ref')}={r.get('mpn')}" for r in rows if str(r.get("ref", "")).upper().startswith("U")][:8]
    warns = list(drc.get("warnings") or [])[:5]
    return {
        "bom_n": len(rows),
        "net_n": net.get("net_count") or len(nets),
        "rails": rails,
        "empty": empty,
        "ics": ics,
        "warns": warns,
    }


def focus_shot(pack: dict) -> str | None:
    for p in pack.get("pages_checked") or []:
        if isinstance(p, dict) and p.get("crop") and p.get("screenshot"):
            return p.get("screenshot")
    for p in pack.get("pages_checked") or []:
        if isinstance(p, dict) and p.get("screenshot"):
            return p.get("screenshot")
    return None


def page_obs(pack: dict) -> str:
    bits = []
    for p in pack.get("pages_checked") or []:
        if not isinstance(p, dict):
            continue
        if p.get("crop"):
            kws = "、".join(str(x) for x in (p.get("focus_keywords") or [])[:6])
            bits.append(
                f"检图 {p.get('page') or p.get('region') or ''}："
                f"{(p.get('note') or '').strip()}"
                + (f"；关键词 {kws}" if kws else "")
            )
            break
    return "；".join(bits) if bits else ""


def ds_obs(pack: dict) -> str:
    names = []
    for d in pack.get("datasheets") or []:
        if isinstance(d, dict):
            names.append(str(d.get("mpn") or d.get("file") or ""))
        else:
            names.append(str(d))
    if not names:
        names = [str(x) for x in (pack.get("datasheets_used") or [])[:4]]
    names = [x for x in names if x][:4]
    return f"已对照手册：{'、'.join(names)}。" if names else ""


def step_observation(
    idx: int,
    total: int,
    process_line: str,
    pack: dict,
    ctx: dict,
    note: str,
) -> str:
    """Bind this process line to board facts — not a copy of the same boilerplate."""
    rid = pack.get("rule_id") or ""
    name = pack.get("name") or ""
    v = pack.get("verdict") or ""
    chunks: list[str] = [f"专家过程第{idx}步要求：{process_line}"]

    if idx == 1:
        po = page_obs(pack)
        if po:
            chunks.append(po)
        if rid.startswith("P07") and ctx.get("warns"):
            chunks.append("DRC Warning：" + "；".join(w.strip() for w in ctx["warns"][:3]))
        elif rid.startswith(("P14", "P16")) and ctx.get("rails"):
            chunks.append("本板电源相关网络：" + "、".join(ctx["rails"][:12]))
        elif rid.startswith("P05") or "BOM" in name or "空" in name:
            if ctx.get("empty"):
                chunks.append("BOM 空型号/NC 位号：" + "、".join(ctx["empty"]))
            else:
                chunks.append(f"BOM {ctx.get('bom_n')} 行已加载。")
        elif "幅面" in name or "图框" in name or rid.startswith("P03"):
            chunks.append("以原理图 PDF 标题栏/图框字段为对象（非网表）。")
        elif ctx.get("ics") and ("器件" in name or "选型" in name or rid.startswith("P0") or rid.startswith("P1")):
            chunks.append("关键 IC：" + "、".join(ctx["ics"][:6]))

    elif idx < total:
        dso = ds_obs(pack)
        if dso and any(k in process_line for k in ("规格", "手册", "数据", "参数", "计算", "对照", "电平")):
            chunks.append(dso)
        elif "网表" in process_line or "连接" in process_line:
            chunks.append(f"网表约 {ctx.get('net_n')} 网；结合 pages_checked 核对连接。")
        elif "BOM" in process_line or "物料" in process_line:
            chunks.append(f"BOM {ctx.get('bom_n')} 行；空值位号 {ctx.get('empty') or '无'}。")
        elif rid.startswith("P07"):
            chunks.append("核对 Warning 是否关闭或有豁免记录。")
        else:
            # use short slice of note as mid evidence if rich
            if len(note) > 40:
                chunks.append("本板已见要点：" + note[:160] + ("…" if len(note) > 160 else ""))
            else:
                chunks.append(f"按「{name}」在图面/文件继续取证。")

    else:
        # terminal: full engineering conclusion
        chunks = [note or f"结论：{v}"]
        dso = ds_obs(pack)
        if dso and v in {"PASS", "FAIL"} and "手册" not in note:
            chunks.append(dso)

    # keep calculations hint
    calcs = pack.get("calculations") or []
    if calcs and idx == total:
        bits = []
        for c in calcs[:3]:
            if isinstance(c, dict):
                bits.append(
                    f"{c.get('name') or '计算'}："
                    f"{c.get('formula') or c.get('expression') or ''} → {c.get('result') or ''}"
                )
        if bits:
            chunks.append("公式/门限：" + "；".join(bits))

    text = " ".join(x for x in chunks if x).strip()
    if text and not text.endswith(("。", "！", "；")):
        text += "。"
    return text


def focus_tag(name: str, rid: str) -> str:
    """Short disambiguator appended at end of action — never a check-title prefix.

    Prefer a domain keyword from the name; always fall back to full rule_id so
    sibling A01 items with shared playbook boilerplate do not collide (B5).
    """
    n = name or ""
    for key in (
        "阻值", "精度", "封装", "温度", "电压", "电流", "功率", "上拉", "下拉",
        "复位", "晶振", "幅面", "图框", "防反", "ESD", "隔离", "掉电", "上电",
        "电平", "跨域", "反灌", "开漏", "VOH", "VOL", "VIH", "VIL",
        "PinMap", "实测", "回流", "钽", "保险丝", "负载开关", "调试", "JTAG", "SWD",
        "BOM", "网表", "DRC", "位号",
    ):
        if key in n:
            return f"{key}/{rid}" if rid else key
    return rid or "item"


def build_steps(pack: dict, item: dict, ctx: dict) -> list[dict]:
    note = (pack.get("note") or "").strip()
    v = pack.get("verdict") or ""
    name = pack.get("name") or item.get("name") or pack.get("rule_id") or ""
    rid = pack.get("rule_id") or item.get("rule_id") or ""
    tag = focus_tag(name, rid)
    shot = focus_shot(pack)
    process = item.get("check_process") or ""
    method = (item.get("expert_check_method") or "").strip()
    lines = split_process(process)

    if v == "NOT_APPLICABLE":
        return [
            {
                "step": 1,
                "action": f"确认本轮输入是否存在该项检查对象（{tag}）",
                "observation": note,
                "decision": "NOT_APPLICABLE",
                "screenshot": shot,
            }
        ]

    if not lines:
        lines = [
            "在原理图/BOM/网表定位本项检查对象",
            f"按建议方式执行：{method or '图面与文件核对'}",
            "给出判定结论",
        ]

    if len(lines) == 1:
        lines = [lines[0], "汇总判定结论"]

    steps: list[dict] = []
    total = len(lines)
    for i, line in enumerate(lines, 1):
        # No check-title prefix. Trailing （tag） only disambiguates sibling process lines.
        if i == total:
            action = f"判定结论（{tag}）"
            decision = v
        else:
            base = line[:72] + ("…" if len(line) > 72 else "")
            action = f"{base}（{tag}）"
            decision = "continue"
        steps.append(
            {
                "step": i,
                "action": action,
                "observation": step_observation(i, total, line, pack, ctx, note),
                "decision": decision,
                "screenshot": shot if i == 1 else None,
            }
        )
    return steps


def maybe_seed_calculations(pack: dict, item: dict) -> None:
    """Only keep existing calculations; do not invent fake formulas.
    If note already embeds numeric thresholds, leave as-is in steps."""
    if pack.get("calculations"):
        return
    # no auto-invent — calculations stay empty unless previously filled


def rebuild_one(pack: dict, item: dict, ctx: dict) -> dict:
    pack = dict(pack)
    pack["steps"] = build_steps(pack, item, ctx)
    maybe_seed_calculations(pack, item)
    pack["derivation_profile"] = "check_specific_from_playbook_v1"
    pack.setdefault("meta", {})["check_specific_steps_at"] = datetime.now(timezone.utc).isoformat()
    pack["meta"]["steps_need_check_specific_rewrite"] = False
    return pack


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Rebuild check-specific steps for schematic-review-skill-product-center"
    )
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out = Path(args.out_dir).resolve()
    by = playbook_by_rid()
    ctx = board_bits(out)
    n = 0
    fps: dict[str, int] = {}
    for path in sorted((out / "evidence").glob("P*-A*.json")):
        pack = load_json(path)
        rid = pack.get("rule_id") or path.stem
        item = by.get(rid) or {
            "rule_id": rid,
            "name": pack.get("name") or rid,
            "check_process": "",
            "expert_check_method": "",
        }
        pack = rebuild_one(pack, item, ctx)
        acts = "|".join(str(s.get("action") or "") for s in pack["steps"])
        fps[acts] = fps.get(acts, 0) + 1
        save_json(path, pack)
        n += 1
    dup = sum(1 for c in fps.values() if c >= 3)
    print(
        json.dumps(
            {
                "skill": "schematic-review-skill-product-center",
                "rebuilt": n,
                "unique_action_fingerprints": len(fps),
                "fingerprints_shared_ge3": dup,
                "profile": "check_specific_from_playbook_v1",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
