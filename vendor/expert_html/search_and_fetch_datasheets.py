# -*- coding: utf-8 -*-
"""Generic datasheet search + download for THIS board BOM MPNs.

Also used by schematic-review-skill-product-center:
  datasheet policy = AI search & download (AnySearch), independent of expert 是否查规格书 column.

Internet-style resilience (never block the review pipeline):
  - per-URL short timeout + limited retries
  - per-MPN time budget; global wall-clock budget → skip rest as deferred
  - circuit breakers for DDG / hostile hosts (403/429/timeout storms)
  - skip commodity passives that rarely have useful standalone PDFs
  - always exit 0 after writing library (partial OK); deferred → datasheets/deferred.json

Search order:
  1) url_hints.json
  2) AnySearch (preferred)
  3) Heuristic CDN (IC families only)
  4) DuckDuckGo (optional; trips breaker on network death)

Usage:
  python search_and_fetch_datasheets.py --out-dir <review_out>
  python search_and_fetch_datasheets.py --out-dir <review_out> --compensate
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

try:
    from anysearch_bridge import anysearch_available, anysearch_datasheet_lookup
except Exception:  # pragma: no cover

    def anysearch_available() -> bool:
        return False

    def anysearch_datasheet_lookup(mpn: str, max_urls: int = 10) -> dict:
        return {"backend": "anysearch", "mpn": mpn, "query": "", "urls": [], "ok": False}


SAFE = re.compile(r"[^A-Za-z0-9._\-]+")
SKIP_MPN = re.compile(
    r"^(TBD|N/?A|NC)$|CONN|HDR|TERMINAL|PH2\.0|DB\d|XY-|SLSB|LED-SEG|CAP-SMD_BD",
    re.I,
)
# Chip resistors / passives — public PDF hunt rarely worth blocking the pipeline
PASSIVE_MPN = re.compile(
    r"^(?:\d{4})?(?:WAF|WAF\d|RC\d|ERJ|CRCW|AC\d{4}|RC0603|RC0402|RC0805)"
    r"|^\d{4}WAF"
    r"|^[Rr]\d+$"
    r"|^(?:\d{4})(?:X7R|X5R|C0G|NP0)",
    re.I,
)
IC_PREFIX = re.compile(
    r"^(LM|L78|L79|MC|CD4|TLV|TPS|SN74|MMBD|LL|1N|2S|ESD|ACST|STM|SGM|TD|XC|AMS|AP|MP|RT)",
    re.I,
)


@dataclass
class CircuitBreaker:
    name: str
    fail_threshold: int = 3
    cooldown_s: float = 120.0
    fails: int = 0
    open_until: float = 0.0
    trips: int = 0

    def allow(self) -> bool:
        if time.time() < self.open_until:
            return False
        return True

    def success(self) -> None:
        self.fails = 0

    def failure(self) -> None:
        self.fails += 1
        if self.fails >= self.fail_threshold:
            self.open_until = time.time() + self.cooldown_s
            self.trips += 1
            self.fails = 0
            print(f"[breaker] OPEN {self.name} for {self.cooldown_s:.0f}s (trip#{self.trips})")


@dataclass
class FetchStats:
    ok: int = 0
    miss: int = 0
    skipped_passive: int = 0
    deferred_budget: int = 0
    url_tries: int = 0
    url_fail: int = 0
    soft_errors: list[str] = field(default_factory=list)

    def note_err(self, msg: str, limit: int = 12) -> None:
        if len(self.soft_errors) < limit:
            self.soft_errors.append(msg)


def is_pdf_bytes(data: bytes) -> bool:
    return len(data) >= 4000 and data[:4] == b"%PDF"


def is_pdf(path: Path) -> bool:
    return path.exists() and is_pdf_bytes(path.read_bytes())


def pdf_looks_stub(path: Path) -> bool:
    try:
        head = path.read_bytes()[:8192]
    except Exception:
        return True
    return b"LCSC Datasheet Notice" in head or b"Datasheet Notice" in head


def safe_name(mpn: str) -> str:
    base = SAFE.sub("_", (mpn or "part").strip()).strip("._")[:80] or "part"
    return base if base.lower().endswith(".pdf") else base + ".pdf"


def clean_mpn(mpn: str) -> str:
    m = re.sub(r"[^\w./+\-]", "", mpn or "")
    return m.strip("._") or (mpn or "").strip()


def should_skip(mpn: str) -> bool:
    if not mpn or len(mpn) < 3:
        return True
    if SKIP_MPN.search(mpn):
        return True
    if mpn.count("-") >= 3 and not IC_PREFIX.search(mpn):
        if re.search(r"P\d|TH_|SMD0805|5\.08|2\.0-5", mpn, re.I):
            return True
    return False


def is_passive_skip(mpn: str, *, fetch_passives: bool = False) -> bool:
    """Commodity R/C — skip live hunt unless url_hints / --fetch-passives."""
    if fetch_passives:
        return False
    c = clean_mpn(mpn)
    if PASSIVE_MPN.search(c):
        return True
    # 0603WAF1003T5E style Uniohm / thick-film
    if re.match(r"^\d{4}WAF\d", c, re.I):
        return True
    return False


def host_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""


def curl_get(
    url: str,
    dest: Path,
    *,
    timeout: int = 18,
    retries: int = 1,
    host_breakers: dict[str, CircuitBreaker],
    stats: FetchStats,
    quiet: bool = True,
) -> bool:
    host = host_of(url)
    br = host_breakers.get(host)
    if br is None and host:
        br = CircuitBreaker(name=host, fail_threshold=4, cooldown_s=180)
        host_breakers[host] = br
    if br and not br.allow():
        stats.note_err(f"skip_host_open:{host}")
        return False

    stats.url_tries += 1
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    cmd = [
        "curl.exe",
        "-L",
        "--fail",
        "--retry",
        str(max(0, retries)),
        "--retry-delay",
        "1",
        "--retry-all-errors",
        "--connect-timeout",
        "8",
        "--max-time",
        str(timeout),
        "-A",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "-H",
        "Accept: application/pdf,*/*",
        "-o",
        str(tmp),
        url,
    ]
    try:
        subprocess.run(cmd, check=False, capture_output=True, timeout=timeout + 10)
    except Exception as ex:
        stats.url_fail += 1
        if br:
            br.failure()
        stats.note_err(f"curl_timeout:{dest.name}:{type(ex).__name__}")
        tmp.unlink(missing_ok=True)
        return False

    if tmp.exists() and is_pdf_bytes(tmp.read_bytes()):
        tmp.replace(dest)
        print("OK", dest.name, dest.stat().st_size, "from", url[:80])
        if br:
            br.success()
        return True
    tmp.unlink(missing_ok=True)

    # One short urllib attempt only (no long hang)
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/pdf,*/*"},
        )
        with urllib.request.urlopen(req, timeout=min(12, timeout)) as r:
            data = r.read()
        if is_pdf_bytes(data):
            dest.write_bytes(data)
            print("OK(urllib)", dest.name, dest.stat().st_size)
            if br:
                br.success()
            return True
    except Exception as ex:
        stats.url_fail += 1
        if br:
            br.failure()
        code = getattr(ex, "code", None)
        if code in (403, 429) or "10060" in str(ex) or "timed out" in str(ex).lower():
            if br:
                br.failure()
        if not quiet:
            print("fail", dest.name, ex)
        else:
            stats.note_err(f"dl_fail:{dest.name}:{code or type(ex).__name__}")
    return False


def heuristic_urls(mpn: str) -> list[str]:
    """IC-family CDN templates only — no AllDatasheet spam for random passives."""
    m = clean_mpn(mpn)
    low = m.lower()
    if not IC_PREFIX.search(m):
        return []
    stem = re.sub(r"(dr|drg4|nopb|/nopb|mx/nopb)$", "", low, flags=re.I)
    stem = stem.replace("/", "").replace("_", "")
    cands: list[str] = []
    if re.match(r"^(lm|tlv|tps|sn74|cd4)", low):
        base = re.split(r"(dr|pw|d|n|pwr|bidr|mx)$", stem)[0]
        for b in {stem, base, m.lower().split("/")[0]}:
            if len(b) >= 4:
                cands.append(f"https://www.ti.com/lit/ds/symlink/{b}.pdf")
    if low.startswith("l78") or low.startswith("l79"):
        cands.append("https://www.st.com/resource/en/datasheet/l78l.pdf")
        cands.append("https://www.st.com/resource/en/datasheet/l78.pdf")
    if low.startswith("mc") or low.startswith("mmbd") or low.startswith("1n") or low.startswith("ll"):
        fam = re.sub(r"(dr2g|drg|dtr2g|g)$", "", stem)
        for s in {stem, fam}:
            if len(s) >= 4:
                cands.append(f"https://www.onsemi.com/pdf/datasheet/{s}-d.pdf")
        if low.startswith("mmbd4148") or low.startswith("ll4148"):
            cands.append("https://www.diodes.com/assets/Datasheets/ds30096.pdf")
    return list(dict.fromkeys(cands))[:4]


def ddg_pdf_urls(mpn: str, *, timeout: int = 8, max_urls: int = 4, breaker: CircuitBreaker) -> list[str]:
    if not breaker.allow():
        return []
    q = f"{clean_mpn(mpn)} datasheet filetype:pdf"
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": q})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", errors="replace")
        breaker.success()
    except Exception as ex:
        breaker.failure()
        print(f"[ddg] skip ({type(ex).__name__}) — breaker fails={breaker.fails} trips={breaker.trips}")
        return []
    urls = []
    for m in re.finditer(r'href="(https?://[^"]+)"', html):
        u = urllib.parse.unquote(m.group(1))
        if "uddg=" in u:
            u2 = urllib.parse.parse_qs(urllib.parse.urlparse(u).query).get("uddg", [""])[0]
            u = u2 or u
        if not u.lower().endswith(".pdf") and "datasheet" not in u.lower() and "lit/ds" not in u.lower():
            continue
        if any(x in u.lower() for x in ("duckduckgo.", "google.", "bing.", "javascript:")):
            continue
        urls.append(u)
        if len(urls) >= max_urls:
            break
    for m in re.finditer(r"https?://[^\s\"'<>]+\.pdf", html, re.I):
        urls.append(m.group(0))
    return list(dict.fromkeys(urls))[:max_urls]


def load_board_mpns(out: Path) -> list[tuple[str, str]]:
    wl = {}
    wp = out / "facts" / "board_mpn_whitelist.json"
    if wp.exists():
        wl = json.loads(wp.read_text(encoding="utf-8-sig"))
    bom = {}
    bp = out / "facts" / "bom.json"
    if bp.exists():
        bom = json.loads(bp.read_text(encoding="utf-8-sig"))
    ref_of = {}
    for r in bom.get("rows") or []:
        m = (r.get("mpn") or "").strip()
        if m and m not in ref_of:
            ref_of[m] = (r.get("ref") or "").strip()
    mpns = wl.get("mpns") or list(ref_of.keys())
    rows = []
    for m in mpns:
        m = (m or "").strip()
        if should_skip(m):
            continue
        rows.append((m, ref_of.get(m, "")))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.25)
    ap.add_argument("--no-anysearch", action="store_true")
    ap.add_argument("--no-ddg", action="store_true", help="Never use DuckDuckGo fallback")
    ap.add_argument("--url-timeout", type=int, default=18, help="Per-URL download max seconds")
    ap.add_argument("--url-retries", type=int, default=1, help="curl --retry count (keep small)")
    ap.add_argument("--max-urls-per-mpn", type=int, default=5)
    ap.add_argument("--mpn-budget-s", type=float, default=40.0, help="Max seconds spent per MPN")
    ap.add_argument(
        "--global-budget-s",
        type=float,
        default=480.0,
        help="Max seconds for whole fetch stage; leftover MPNs → deferred",
    )
    ap.add_argument(
        "--compensate",
        action="store_true",
        help="Only retry MPNs listed in datasheets/deferred.json",
    )
    ap.add_argument(
        "--fetch-passives",
        action="store_true",
        help="Do not SKIP commodity R/C; hunt passives (P11-A01 needs P_rated)",
    )
    args = ap.parse_args()
    out = Path(args.out_dir)
    ds = out / "datasheets"
    ds.mkdir(parents=True, exist_ok=True)

    use_any = (not args.no_anysearch) and anysearch_available()
    print(
        f"[fetch] anysearch={'on' if use_any else 'off'} "
        f"ddg={'off' if args.no_ddg else 'on'} "
        f"url_timeout={args.url_timeout}s mpn_budget={args.mpn_budget_s}s "
        f"global_budget={args.global_budget_s}s"
    )

    hints = {}
    hp = ds / "url_hints.json"
    if hp.exists():
        raw = json.loads(hp.read_text(encoding="utf-8-sig"))
        hints = raw.get("urls") if isinstance(raw, dict) and "urls" in raw else raw

    targets = load_board_mpns(out)
    if args.compensate:
        deferred_prev = {}
        dp = ds / "deferred.json"
        if dp.exists():
            deferred_prev = json.loads(dp.read_text(encoding="utf-8-sig"))
        want = {(x.get("mpn") or "") for x in (deferred_prev.get("items") or []) if x.get("mpn")}
        targets = [(m, r) for m, r in targets if m in want]
        print(f"[fetch] compensate mode: {len(targets)} deferred MPNs")
    if args.limit:
        targets = targets[: args.limit]

    ddg_breaker = CircuitBreaker("duckduckgo", fail_threshold=2, cooldown_s=600)
    host_breakers: dict[str, CircuitBreaker] = {}
    stats = FetchStats()
    t0 = time.time()

    library = []
    search_log: list[dict] = []
    deferred: list[dict] = []
    any_hits = 0

    for mpn, ref in targets:
        if time.time() - t0 >= args.global_budget_s:
            deferred.append(
                {
                    "mpn": mpn,
                    "ref": ref,
                    "reason": "global_budget_exceeded",
                    "retryable": True,
                }
            )
            stats.deferred_budget += 1
            continue

        fname = safe_name(clean_mpn(mpn) or mpn)
        dest = ds / fname
        used_url = ""
        search_backend = ""
        any_meta: dict = {}
        skip_reason = ""
        ok = is_pdf(dest) and not pdf_looks_stub(dest)

        if ok:
            print("have", dest.name)
            search_backend = "local"
            stats.ok += 1
        elif is_passive_skip(mpn, fetch_passives=bool(args.fetch_passives)) and not (
            isinstance(hints, dict) and hints.get(mpn)
        ):
            skip_reason = "passive_skip"
            search_backend = "skipped_passive"
            stats.skipped_passive += 1
            print(f"SKIP passive {mpn}")
            deferred.append(
                {
                    "mpn": mpn,
                    "ref": ref,
                    "reason": skip_reason,
                    "retryable": False,
                    "note": "commodity R/C; provide url_hints or local PDF to compensate",
                }
            )
        else:
            mpn_t0 = time.time()
            candidates: list[str] = []
            if isinstance(hints, dict) and hints.get(mpn):
                candidates.append(str(hints[mpn]))
                search_backend = "url_hints"
            if use_any:
                try:
                    any_meta = anysearch_datasheet_lookup(mpn, max_urls=6)
                except Exception as ex:
                    any_meta = {"urls": [], "query": "", "ok": False}
                    stats.note_err(f"anysearch_ex:{mpn}:{type(ex).__name__}")
                a_urls = list(any_meta.get("urls") or [])
                search_log.append(
                    {
                        "mpn": mpn,
                        "ref": ref,
                        "backend": "anysearch",
                        "query": any_meta.get("query") or "",
                        "urls": a_urls[:8],
                        "ok": bool(a_urls),
                    }
                )
                if a_urls:
                    any_hits += 1
                    # LCSC / url_hints 优先，AnySearch 垫后（避免错手册盖住立创 pdfUrl）
                    candidates = list(dict.fromkeys([*candidates, *a_urls]))
                    if search_backend != "url_hints":
                        search_backend = "anysearch"
            candidates.extend(heuristic_urls(mpn))
            candidates = [u for u in dict.fromkeys(candidates) if u.startswith("http")]
            candidates = candidates[: args.max_urls_per_mpn]

            for url in candidates:
                if time.time() - mpn_t0 >= args.mpn_budget_s:
                    skip_reason = "mpn_budget_exceeded"
                    break
                if time.time() - t0 >= args.global_budget_s:
                    skip_reason = "global_budget_exceeded"
                    break
                if curl_get(
                    url,
                    dest,
                    timeout=args.url_timeout,
                    retries=args.url_retries,
                    host_breakers=host_breakers,
                    stats=stats,
                ):
                    ok = True
                    used_url = url
                    break
                time.sleep(args.sleep)

            if (
                not ok
                and not args.no_ddg
                and skip_reason == ""
                and (time.time() - mpn_t0) < args.mpn_budget_s
            ):
                for url in ddg_pdf_urls(mpn, breaker=ddg_breaker):
                    if time.time() - mpn_t0 >= args.mpn_budget_s:
                        break
                    if curl_get(
                        url,
                        dest,
                        timeout=args.url_timeout,
                        retries=args.url_retries,
                        host_breakers=host_breakers,
                        stats=stats,
                    ):
                        ok = True
                        used_url = url
                        search_backend = "duckduckgo"
                        break
                    time.sleep(args.sleep)
                if not search_backend:
                    search_backend = "duckduckgo_miss"

            if ok:
                stats.ok += 1
            else:
                stats.miss += 1
                reason = skip_reason or "no_downloadable_pdf"
                print(f"MISS {mpn} ({reason})")
                deferred.append(
                    {
                        "mpn": mpn,
                        "ref": ref,
                        "reason": reason,
                        "retryable": reason
                        in {
                            "mpn_budget_exceeded",
                            "global_budget_exceeded",
                            "no_downloadable_pdf",
                        },
                        "tried_backends": search_backend,
                    }
                )

        library.append(
            {
                "mpn": mpn,
                "ref": ref,
                "title": mpn,
                "manufacturer": "",
                "local_path": f"datasheets/{fname}" if ok else "",
                "source_url": used_url,
                "product_page": "",
                "download_ok": ok,
                "download_error": None
                if ok
                else (skip_reason or "auto-search found no downloadable PDF"),
                "search_used": True,
                "search_backend": search_backend,
                "anysearch_query": any_meta.get("query") or "",
                "skipped": bool(skip_reason == "passive_skip"),
            }
        )
        # Count remaining deferred from global budget loop continuation
        if not ok and skip_reason == "global_budget_exceeded" and not any(
            d.get("mpn") == mpn for d in deferred
        ):
            pass
        time.sleep(args.sleep)

    # MPNs never reached due to continue-only path already in deferred;
    # mark any leftover not in library (shouldn't happen)

    elapsed = round(time.time() - t0, 1)
    (ds / "search_log.json").write_text(
        json.dumps(
            {
                "anysearch_enabled": use_any,
                "anysearch_mpn_hits": any_hits,
                "ddg_breaker_trips": ddg_breaker.trips,
                "elapsed_s": elapsed,
                "entries": search_log,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (ds / "deferred.json").write_text(
        json.dumps(
            {
                "count": len(deferred),
                "retryable": sum(1 for x in deferred if x.get("retryable")),
                "items": deferred,
                "hint": "Re-run with --compensate after adding url_hints or when network recovers",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (ds / "library.json").write_text(
        json.dumps(
            {
                "count": len(library),
                "ok": stats.ok,
                "miss": stats.miss,
                "skipped_passive": stats.skipped_passive,
                "deferred_budget": stats.deferred_budget,
                "anysearch": use_any,
                "anysearch_mpn_hits": any_hits,
                "search_engine": "anysearch+heuristic+ddg" if use_any else "heuristic+ddg",
                "elapsed_s": elapsed,
                "url_tries": stats.url_tries,
                "url_fail": stats.url_fail,
                "soft_errors_sample": stats.soft_errors,
                "items": library,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary = {
        "fetched_ok": stats.ok,
        "miss": stats.miss,
        "skipped_passive": stats.skipped_passive,
        "deferred": len(deferred),
        "elapsed_s": elapsed,
        "anysearch": use_any,
        "anysearch_mpn_hits": any_hits,
        "ddg_breaker_trips": ddg_breaker.trips,
        "pipeline_continue": True,
    }
    print(json.dumps(summary, ensure_ascii=False))
    # Never block pipeline: soft-fail always 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
