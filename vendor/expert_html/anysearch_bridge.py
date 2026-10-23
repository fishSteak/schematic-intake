# -*- coding: utf-8 -*-
"""Bridge to project-local AnySearch skill CLI (optional soft dependency).

Used by schematic-review-skill-expert-html-v1.0 datasheet fetch for
playbook need_datasheet_query=是 items.

Resolves CLI from (in order):
  1) ANYSEARCH_CLI env
  2) <repo>/.skills/anysearch/runtime.conf
  3) <repo>/.cursor/skills/anysearch/runtime.conf
  4) sibling script paths

Does NOT vendor AnySearch; if missing, callers fall back to heuristics/DDG.
"""
from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

URL_RE = re.compile(r"https?://[^\s\)\]\>\"']+", re.I)
MD_URL_RE = re.compile(r"\*\*URL\*\*:\s*(https?://\S+)", re.I)
TITLE_RE = re.compile(r"^###\s+\d+\.\s+(.+)$", re.M)


def _repo_roots() -> list[Path]:
    here = Path(__file__).resolve()
    cands = [
        here.parents[3] if len(here.parents) > 3 else here.parents[-1],
        here.parents[4] if len(here.parents) > 4 else here.parents[-1],
        Path.cwd(),
    ]
    out: list[Path] = []
    for p in cands:
        if p not in out:
            out.append(p)
    return out


def _parse_runtime_conf(conf: Path) -> list[str] | None:
    if not conf.exists():
        return None
    text = conf.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if line.strip().lower().startswith("command:"):
            cmd = line.split(":", 1)[1].strip()
            if not cmd:
                return None
            parts = cmd.split()
            if len(parts) >= 2 and parts[0].lower() in {"python", "python3", "py"}:
                script = Path(parts[1])
                if not script.is_absolute():
                    script = (conf.parent / script).resolve()
                return [parts[0], str(script)]
            return parts
    script = conf.parent / "scripts" / "anysearch_cli.py"
    if script.exists():
        return ["python", str(script)]
    return None


@lru_cache(maxsize=1)
def resolve_anysearch_cmd() -> tuple[str, ...] | None:
    env = (os.environ.get("ANYSEARCH_CLI") or "").strip()
    if env:
        return tuple(env.split())
    for root in _repo_roots():
        for rel in (".skills/anysearch/runtime.conf", ".cursor/skills/anysearch/runtime.conf"):
            conf = root / rel
            parsed = _parse_runtime_conf(conf)
            if parsed:
                if len(parsed) >= 2 and Path(parsed[1]).exists():
                    return tuple(parsed)
                if Path(parsed[0]).exists():
                    return tuple(parsed)
    for root in _repo_roots():
        for rel in (
            ".skills/anysearch/scripts/anysearch_cli.py",
            ".cursor/skills/anysearch/scripts/anysearch_cli.py",
        ):
            script = root / rel
            if script.exists():
                return ("python", str(script))
    home = Path.home()
    for conf in (
        home / ".agents" / "skills" / "anysearch" / "runtime.conf",
        home / ".cursor" / "skills" / "anysearch" / "runtime.conf",
    ):
        parsed = _parse_runtime_conf(conf)
        if parsed:
            if len(parsed) >= 2 and Path(parsed[1]).exists():
                return tuple(parsed)
            if Path(parsed[0]).exists():
                return tuple(parsed)
    return None


def anysearch_available() -> bool:
    return resolve_anysearch_cmd() is not None


def _extract_urls(md: str) -> list[str]:
    urls: list[str] = []
    for m in MD_URL_RE.finditer(md):
        urls.append(m.group(1).rstrip(".,);]\""))
    for m in URL_RE.finditer(md):
        urls.append(m.group(0).rstrip(".,);]\""))
    uniq = list(dict.fromkeys(urls))
    direct_pdf = [u for u in uniq if u.lower().split("?")[0].endswith(".pdf")]
    pdfish = [
        u
        for u in uniq
        if u not in direct_pdf
        and ("/pdf/" in u.lower() or "lit/ds" in u.lower() or "datasheet" in u.lower())
    ]
    rest = [u for u in uniq if u not in direct_pdf and u not in pdfish]
    return direct_pdf + pdfish + rest


def anysearch_search(query: str, max_results: int = 8, timeout: int = 25) -> str:
    cmd = resolve_anysearch_cmd()
    if not cmd:
        return ""
    full = [*cmd, "search", query, "--max_results", str(max(1, min(10, max_results)))]
    try:
        p = subprocess.run(
            full,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(Path(cmd[1]).parent.parent) if len(cmd) > 1 else None,
        )
    except subprocess.TimeoutExpired:
        print("anysearch timeout", query[:60])
        return ""
    except Exception as ex:
        print("anysearch err", ex)
        return ""
    if p.returncode != 0:
        print("anysearch rc", p.returncode, (p.stderr or "")[:120])
        return p.stdout or ""
    return p.stdout or ""


def anysearch_datasheet_lookup(mpn: str, max_urls: int = 10) -> dict[str, Any]:
    """Structured datasheet URL lookup for one board MPN (single query, fast-fail)."""
    queries = [f"{mpn} datasheet filetype:pdf"]
    used_q = queries[0]
    md = anysearch_search(used_q, max_results=min(8, max(4, max_urls)), timeout=25)
    if not md.strip() or not _extract_urls(md):
        used_q = f"{mpn} datasheet pdf"
        md = anysearch_search(used_q, max_results=6, timeout=20)
    bad = ("javascript:", "google.com/search", "bing.com/search", "duckduckgo.com")
    urls = [u for u in _extract_urls(md) if not any(b in u.lower() for b in bad)][:max_urls]
    titles = TITLE_RE.findall(md)[:max_urls]
    return {
        "backend": "anysearch",
        "mpn": mpn,
        "query": used_q,
        "urls": urls,
        "titles": titles,
        "markdown_head": (md or "")[:1500],
        "ok": bool(urls),
    }


def anysearch_pdf_urls(mpn: str, max_urls: int = 10) -> list[str]:
    return list(anysearch_datasheet_lookup(mpn, max_urls=max_urls).get("urls") or [])


if __name__ == "__main__":
    print("available", anysearch_available(), resolve_anysearch_cmd())
    print(anysearch_datasheet_lookup("LM393DR", max_urls=5))
