"""Step 2: BOM MPN 队列 + url_hints 预取 + AnySearch 补齐 IC/二极管/晶体管/晶振/电感/电阻功率/电容耐压手册。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from kernel.pintable import library_pdf_name, safe_mpn_dir
from kernel.util import ROOT, out_dir, write_json

VENDOR_FETCH = ROOT / "vendor" / "datasheet-fetch" / "search_and_fetch_datasheets.py"
FETCH_PY = VENDOR_FETCH
HINTS = ROOT / "rules" / "datasheet_url_hints.json"
HUNT_PREFIX = re.compile(r"^(U|Y|X|Q|D|L|LED|CN|H|J|F)\d", re.I)
IC_MPN = re.compile(
    r"^(LM|L78|L79|MC|CD4|TLV|TPS|SN74|MMBD|LL|1N|2S|S90|ESD|STM|SGM|XC|AMS|AP|MP|RT)",
    re.I,
)


def _facts_bom(graph: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for ref, comp in (graph.get("components") or {}).items():
        mpn = (comp.get("mpn") or "").strip()
        if not mpn:
            continue
        rows.append(
            {
                "ref": ref,
                "mpn": mpn,
                "value": comp.get("value_bom") or comp.get("value_tel") or "",
                "footprint": comp.get("footprint") or "",
                "desc": "",
            }
        )
    return {"source": graph.get("bom"), "row_count": len(rows), "rows": rows}


def _queue_from_graph(board: Path, graph: dict[str, Any]) -> tuple[list[dict[str, str]], list[str]]:
    cache = board / "cache" / "mpn"
    cache.mkdir(parents=True, exist_ok=True)
    queue: list[dict[str, str]] = []
    seen: set[str] = set()
    for ref, comp in (graph.get("components") or {}).items():
        mpn = (comp.get("mpn") or "").strip()
        if not mpn or mpn.upper() in {"CAP", "RES", "TBD", "NC"}:
            continue
        if mpn in seen:
            continue
        seen.add(mpn)
        slot = cache / safe_mpn_dir(mpn)
        slot.mkdir(parents=True, exist_ok=True)
        queue.append({"mpn": mpn, "ref_sample": ref, "cache": str(slot)})
    return queue, sorted(seen)


def _hunt_priority(ref: str, mpn: str) -> int:
    if HUNT_PREFIX.match(ref or "") or IC_MPN.search(mpn or ""):
        return 0
    if re.match(r"^R\d", ref or "", re.I):
        return 1
    if re.match(r"^C\d", ref or "", re.I):
        return 1
    if re.search(r"UBC|Tantal|electro", mpn or "", re.I):
        return 1
    return 9


def _hunt_mpns(graph: dict[str, Any]) -> list[str]:
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for ref, comp in (graph.get("components") or {}).items():
        mpn = (comp.get("mpn") or "").strip()
        if not mpn or mpn in seen:
            continue
        seen.add(mpn)
        pr = _hunt_priority(ref, mpn)
        if pr >= 9:
            continue
        ranked.append((pr, mpn))
    ranked.sort()
    return [m for _, m in ranked]


def _seed_url_hints(ds_dir: Path, graph: dict[str, Any] | None = None) -> dict[str, str]:
    ds_dir.mkdir(parents=True, exist_ok=True)
    dest = ds_dir / "url_hints.json"
    seeded: dict[str, Any] = {}
    if HINTS.is_file():
        raw = json.loads(HINTS.read_text(encoding="utf-8-sig"))
        seeded = raw.get("urls") if isinstance(raw, dict) and "urls" in raw else raw
    existing: dict[str, Any] = {}
    if dest.is_file():
        raw = json.loads(dest.read_text(encoding="utf-8-sig"))
        existing = raw.get("urls") if isinstance(raw, dict) and "urls" in raw else raw
    urls: dict[str, str] = {}
    if isinstance(existing, dict):
        urls.update({k: str(v) for k, v in existing.items() if v})
    if isinstance(seeded, dict):
        urls.update({k: str(v) for k, v in seeded.items() if v})
    # knowledge series + jlc_datasheet_urls (LCSC pdfUrl)
    try:
        from kernel.knowledge import expand_series_hints_for_mpns
    except Exception:
        expand_series_hints_for_mpns = None  # type: ignore
    mpns = []
    if graph:
        for c in (graph.get("components") or {}).values():
            m = (c.get("mpn") or "").strip()
            if m:
                mpns.append(m)
    if expand_series_hints_for_mpns and mpns:
        urls.update(expand_series_hints_for_mpns(mpns))
    jlc_urls = ds_dir.parent / "jlc_datasheet_urls.json"
    if jlc_urls.is_file():
        try:
            jdoc = json.loads(jlc_urls.read_text(encoding="utf-8-sig"))
            rows = (
                jdoc.get("records")
                or jdoc.get("rows")
                or jdoc.get("items")
                or []
            )
            for row in rows:
                if not isinstance(row, dict):
                    continue
                mpn = (row.get("mpn") or "").strip()
                u = (row.get("datasheet_url") or "").strip()
                if mpn and u.startswith("http") and mpn not in urls:
                    urls[mpn] = u
        except Exception:
            pass
    write_json(dest, {"urls": urls})
    return urls


def _prefetch_hint_pdfs(ds_dir: Path, urls: dict[str, str]) -> dict[str, Any]:
    ok, fail = [], []
    for mpn, url in (urls or {}).items():
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        if mpn.startswith("__"):
            continue
        dest = ds_dir / library_pdf_name(str(mpn))
        if dest.is_file() and dest.stat().st_size >= 4000:
            head = dest.read_bytes()[:4]
            if head == b"%PDF":
                ok.append(mpn)
                continue
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.unlink(missing_ok=True)
        cmd = [
            "curl.exe",
            "-L",
            "--fail",
            "--connect-timeout",
            "8",
            "--max-time",
            "25",
            "-A",
            "Mozilla/5.0",
            "-H",
            "Accept: application/pdf,*/*",
            "-o",
            str(tmp),
            url,
        ]
        try:
            subprocess.run(cmd, check=False, capture_output=True, timeout=35)
        except Exception:
            fail.append(mpn)
            continue
        if tmp.is_file() and tmp.stat().st_size >= 4000 and tmp.read_bytes()[:4] == b"%PDF":
            tmp.replace(dest)
            ok.append(mpn)
        else:
            tmp.unlink(missing_ok=True)
            fail.append(mpn)
    return {"ok": ok, "fail": fail}


def _mirror_pdfs(out: Path, queue: list[dict[str, str]]) -> dict[str, str]:
    lib_path = out / "datasheets" / "library.json"
    by_mpn: dict[str, dict[str, Any]] = {}
    if lib_path.is_file():
        lib = json.loads(lib_path.read_text(encoding="utf-8-sig"))
        for it in lib.get("items") or []:
            if it.get("mpn"):
                by_mpn[it["mpn"]] = it
    mirrored: dict[str, str] = {}
    for item in queue:
        mpn = item["mpn"]
        rec = by_mpn.get(mpn) or {}
        rel = rec.get("local_path") or ""
        src = (out / rel) if rel else None
        if src is None or not src.is_file():
            named = out / "datasheets" / library_pdf_name(mpn)
            src = named if named.is_file() else None
        if src is None or not src.is_file():
            cand = list((out / "datasheets").glob(f"{safe_mpn_dir(mpn)}*.pdf"))
            src = cand[0] if cand else None
        if src is None or not src.is_file():
            continue
        dest = Path(item["cache"]) / "datasheet.pdf"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.resolve() != src.resolve():
            shutil.copy2(src, dest)
        write_json(
            dest.parent / "source.json",
            {
                "mpn": mpn,
                "pdf": str(dest),
                "library": rel,
                "source_url": rec.get("source_url") or "",
                "search_backend": rec.get("search_backend") or "",
            },
        )
        mirrored[mpn] = str(dest)
    return mirrored


def _run_fetch(out: Path, *, compensate: bool = False, fetch_passives: bool = False) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "ran": False,
        "rc": None,
        "reason": "",
        "compensate": compensate,
        "fetch_passives": fetch_passives,
    }
    script = FETCH_PY if FETCH_PY.is_file() else VENDOR_FETCH
    meta["script"] = str(script)
    if not script.is_file():
        meta["reason"] = f"missing {FETCH_PY} and {VENDOR_FETCH}"
        return meta
    env = os.environ.copy()
    pp = str(script.parent)
    env["PYTHONPATH"] = pp + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [
        sys.executable,
        str(script),
        "--out-dir",
        str(out),
        "--url-timeout",
        "18",
        "--mpn-budget-s",
        "40",
        "--global-budget-s",
        "600",
        "--no-ddg",
    ]
    if compensate:
        cmd.append("--compensate")
    if fetch_passives:
        cmd.append("--fetch-passives")
    proc = subprocess.run(
        cmd,
        cwd=str(script.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
    )
    meta["ran"] = True
    meta["rc"] = proc.returncode
    meta["stdout_tail"] = (proc.stdout or "")[-4000:]
    meta["stderr_tail"] = (proc.stderr or "")[-2000:]
    return meta


def run(board: Path, graph: dict[str, Any]) -> dict[str, Any]:
    o = out_dir(board)
    facts = o / "facts"
    facts.mkdir(parents=True, exist_ok=True)
    queue, all_mpns = _queue_from_graph(board, graph)
    hunt = _hunt_mpns(graph)
    bom = _facts_bom(graph)
    write_json(facts / "bom.json", bom)
    write_json(
        facts / "board_mpn_whitelist.json",
        {"mpns": hunt, "count": len(hunt), "all_mpns": len(all_mpns), "note": "搜 IC/二极管/晶体管/晶振/电感/电阻功率/电容耐压/极性电容"},
    )
    mats = {}
    try:
        mats = json.loads((o / "materials.json").read_text(encoding="utf-8-sig"))
    except Exception:
        pass
    rule_ids = list(mats.get("rule_ids") or [])
    fetch_passives = "P11-A01" in rule_ids
    urls = _seed_url_hints(o / "datasheets", graph)
    prefetch = _prefetch_hint_pdfs(o / "datasheets", urls)
    fetch_meta = _run_fetch(o, fetch_passives=fetch_passives)
    fetch_comp = _run_fetch(o, compensate=True, fetch_passives=fetch_passives)
    mirrored = _mirror_pdfs(o, queue)
    hit = [q["mpn"] for q in queue if (Path(q["cache"]) / "datasheet.pdf").is_file()]
    miss = [q["mpn"] for q in queue if q["mpn"] not in hit]
    data = {
        "hit": hit,
        "miss": miss,
        "queue": queue,
        "hunt_mpns": hunt,
        "prefetch": prefetch,
        "fetch": fetch_meta,
        "fetch_compensate": fetch_comp,
        "mirrored": len(mirrored),
        "library": str(o / "datasheets" / "library.json"),
        "fetch_passives": fetch_passives,
        "note": "url_hints(含LCSC/系列)预取；P11-A01 时电阻不 skip；失败不阻断流水线",
    }
    write_json(o / "datasheet_miss.json", data)
    write_json(o / "datasheet_queue.json", {"queue": queue, "hunt": hunt})
    write_json(o / "datasheet_fetch_log.json", {"prefetch": prefetch, "fetch": fetch_meta, "compensate": fetch_comp})
    return data
