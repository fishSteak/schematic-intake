# -*- coding: utf-8 -*-
"""电容 Vr 争取：验 PDF → 下手册 → PDF 抽出 → LCSC 参数/联网查询。必须带出处。"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any

from kernel.lcsc import (
    lookup_product,
    named_param,
    power_from_product,
    supply_range_from_product,
    voltage_from_product,
)
from kernel.pintable import extract_pdf, safe_mpn_dir
from kernel.rated_fields import field_present, is_regulator_mpn, parse_rated_field, write_rated
from kernel.util import out_dir

PLACEHOLDER_MPN = re.compile(r"^(cap|res|tbd|nc|n/?a)$", re.I)
STUB_MARKERS = (b"LCSC Datasheet Notice", b"Datasheet Notice")
VR_TEXT = re.compile(
    r"(?:rated|working)\s+voltage[^0-9]{0,48}(\d+(?:\.\d+)?)\s*V"
    r"|额定(?:耐压|电压)[^0-9]{0,24}(\d+(?:\.\d+)?)\s*V"
    r"|Voltage\s*Rating[^0-9]{0,24}(\d+(?:\.\d+)?)\s*V"
    r"|(\d{2,4})\s*=\s*(\d+(?:\.\d+)?)\s*V(?:DC)?",
    re.I,
)
MPN_CODE_VR = re.compile(r"(\d{3})\s*=\s*(\d+(?:\.\d+)?)\s*V(?:DC)?", re.I)

# TDK CGA / Murata GRM 等 MLCC：容差字母后、端子码前的三位电压码（500=50V、101=100V）
_MPN_VR_MAP: dict[str, float] = {
    "6R3": 6.3,
    "010": 10.0,
    "100": 10.0,
    "160": 16.0,
    "250": 25.0,
    "350": 35.0,
    "500": 50.0,
    "630": 63.0,
    "101": 100.0,
    "201": 200.0,
    "251": 250.0,
    "301": 300.0,
    "501": 500.0,
}
_MPN_VR_TAIL = re.compile(r"([0-9]{3}|6R3)(?:FT|DTS|JT|AT|CT|NT|BN)\b", re.I)


def decode_mpn_voltage_code(mpn: str) -> tuple[float | None, str]:
    """从料号解出 MLCC 额定耐压线索（无需手册正文）。返回 (V, cite) 或 (None, '')."""
    s = (mpn or "").strip()
    if not s:
        return None, ""
    m = _MPN_VR_TAIL.search(s)
    if not m:
        return None, ""
    code = m.group(1).upper()
    if re.fullmatch(r"6R\d", code, re.I):
        code = "6R3"
    v = _MPN_VR_MAP.get(code)
    if v is None:
        return None, ""
    cite = f"料号电压码 {code}={v:g}VDC（MLCC 行业编码惯例；{s}）"
    return float(v), cite


def is_placeholder_mpn(mpn: str) -> bool:
    s = (mpn or "").strip()
    return (not s) or len(s) < 4 or bool(PLACEHOLDER_MPN.match(s))


def pdf_is_stub(path: Path) -> bool:
    if not path.is_file():
        return True
    data = path.read_bytes()
    if len(data) < 4000 or data[:4] != b"%PDF":
        return True
    head = data[:8192]
    if any(m in head for m in STUB_MARKERS):
        return True
    return False


def _mpn_tokens(mpn: str) -> list[str]:
    parts = re.findall(r"[A-Za-z]{3,}|[A-Za-z]+\d+[A-Za-z0-9]*|\d{4,}[A-Za-z]+", mpn or "")
    return [p for p in parts if len(p) >= 4][:6]


def pdf_mentions_mpn(text: str, mpn: str) -> bool:
    t = text or ""
    if mpn and mpn.lower() in t.lower():
        return True
    toks = _mpn_tokens(mpn)
    if not toks:
        return False
    hit = sum(1 for tok in toks if tok.lower() in t.lower())
    return hit >= max(1, min(2, len(toks)))


def extract_vr_from_text(text: str, mpn: str = "") -> tuple[float | None, str]:
    if not text:
        return None, ""
    # MPN 三位电压码对照表：500=50VDC、250=25VDC
    code = None
    mcode = re.search(r"(\d{3})(?:FT|DTS|JT|AT)\b", mpn or "", re.I)
    if mcode:
        code = mcode.group(1)
        for cm in MPN_CODE_VR.finditer(text):
            if cm.group(1) == code:
                v = float(cm.group(2))
                return v, f"{code}={cm.group(2)}V (MPN {mpn})"
    m = VR_TEXT.search(text)
    if m:
        nums = [g for g in m.groups() if g]
        if len(nums) >= 2 and nums[0].isdigit() and len(nums[0]) == 3:
            v = float(nums[1])
        else:
            v = float(nums[-1])
        if 1 <= v <= 2000:
            return v, m.group(0)[:160]
    return None, ""


def _pdf_text(path: Path, max_pages: int = 12) -> str:
    try:
        body = extract_pdf(path, max_pages=max_pages)
    except Exception:
        return ""
    bits = [str(h.get("snippet") or "") for h in (body.get("hits") or [])]
    params = body.get("params") or {}
    if params.get("rated_voltage_v") is not None:
        bits.append(f"rated_voltage_v={params.get('rated_voltage_v')}")
    return "\n".join(bits)


def _download_pdf(url: str, dest: Path, timeout: int = 25) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/pdf,*/*"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except Exception:
        tmp.unlink(missing_ok=True)
        return False
    if len(data) < 4000 or data[:4] != b"%PDF":
        return False
    if any(m in data[:8192] for m in STUB_MARKERS):
        return False
    dest.write_bytes(data)
    return True


def _jlc_by_mpn(board: Path) -> dict[str, dict[str, Any]]:
    p = out_dir(board) / "jlc_datasheet_urls.json"
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    by: dict[str, dict[str, Any]] = {}
    for rec in doc.get("records") or []:
        if not isinstance(rec, dict):
            continue
        mpn = str(rec.get("mpn") or "").strip()
        if mpn and mpn not in by:
            by[mpn] = rec
    return by


def _quarantine(pdf: Path) -> None:
    junk = pdf.with_name("datasheet.junk.pdf")
    try:
        if junk.is_file():
            junk.unlink()
        pdf.replace(junk)
    except Exception:
        pass


def _anysearch_pdf_and_web(mpn: str, fields: list[str] | None = None) -> tuple[list[str], list[dict[str, str]]]:
    urls: list[str] = []
    snippets: list[dict[str, str]] = []
    fields = list(fields or [])
    try:
        import sys

        # intake 桥接在 vendor/datasheet-fetch；兼容旧路径 vendor/expert_html
        root = Path(__file__).resolve().parents[1]
        for rel in ("vendor/datasheet-fetch", "vendor/expert_html"):
            p = root / rel
            if (p / "anysearch_bridge.py").is_file():
                if str(p) not in sys.path:
                    sys.path.insert(0, str(p))
                break
        from anysearch_bridge import anysearch_datasheet_lookup, anysearch_search  # type: ignore
    except Exception:
        return [], []
    try:
        hit = anysearch_datasheet_lookup(mpn, max_urls=6)
    except Exception:
        hit = {}
    urls = [u for u in (hit.get("urls") or []) if str(u).startswith("http")]
    queries = [f"{mpn} 额定电压 Voltage Rating"]
    if any(f in _IO_FIELDS for f in fields):
        queries.append(f"{mpn} VOH VOL VIH VIL electrical characteristics")
    if any(f in _SURGE_FIELDS for f in fields):
        queries.append(f"{mpn} VRWM clamping voltage Ipp")
    if any(str(f).startswith("reset_") for f in fields):
        queries.append(f"{mpn} reset threshold VIH VIL")
    for query in queries:
        try:
            md = anysearch_search(query, max_results=5, timeout=20)
        except Exception:
            md = ""
        if md:
            snippets.append({"url": (urls[0] if urls else ""), "quote": md[:500], "query": query})
    return urls, snippets


def _reject_voltage_code_as_volts(mpn: str, value: float) -> bool:
    """PDF/表误把三位电压码 500 当成 500V（真值常为 50V）时否决。"""
    v_code, _ = decode_mpn_voltage_code(mpn)
    if v_code is None:
        return False
    m = _MPN_VR_TAIL.search(mpn or "")
    if not m:
        return False
    raw = m.group(1)
    if not raw.isdigit():
        return False
    code_as_v = float(raw)
    # 抽出值≈码面数字，且与解码真值差一数量级以上
    if abs(float(value) - code_as_v) <= 0.05 and abs(float(value) - float(v_code)) > 1.0:
        return True
    return False


def hunt_capacitor_vr(mpn: str, board: Path) -> dict[str, Any] | None:
    """Return {value, source, pdf} or None. Never invent without source."""
    mpn = (mpn or "").strip()
    if is_placeholder_mpn(mpn):
        return None
    o = out_dir(board)
    slot = board / "cache" / "mpn" / safe_mpn_dir(mpn)
    slot.mkdir(parents=True, exist_ok=True)
    pdf = slot / "datasheet.pdf"
    jlc = _jlc_by_mpn(board).get(mpn) or {}
    code = str(jlc.get("lcsc_code") or "")
    lcsc = lookup_product(code, o / "lcsc_cache") if code else {}

    def from_pdf(path: Path, url: str, backend: str) -> dict[str, Any] | None:
        text = _pdf_text(path)
        if text and not pdf_mentions_mpn(text, mpn) and "capacitor" not in text.lower() and "电容" not in text:
            return None
        try:
            body = extract_pdf(path, max_pages=12)
            blob = text + "\n" + json.dumps(body.get("hits") or [], ensure_ascii=False)
            v, quote = extract_vr_from_text(blob, mpn)
            if v is None:
                vr = (body.get("params") or {}).get("rated_voltage_v")
                if vr is not None:
                    v = float(vr)
                    quote = f"pintable rated_voltage_v={v}"
            if v is None:
                return None
            if _reject_voltage_code_as_volts(mpn, float(v)):
                return None
            page = 1
            return {
                "value": v,
                "pdf": str(path),
                "source": {
                    "kind": "datasheet_page",
                    "path": str(path.relative_to(board)) if str(path).startswith(str(board)) else str(path),
                    "page": page,
                    "quote": quote,
                    "url": url,
                    "mpn": mpn,
                    "backend": backend,
                },
            }
        except Exception:
            return None

    # 1) 声明页 / 明显错手册（总线开关当电容）→ 隔离后重下
    _wrong_ic = re.compile(r"bus switch|QS3126|HIGH-SPEED CMOS QUADRUPLE", re.I)
    if pdf.is_file():
        if pdf_is_stub(pdf):
            _quarantine(pdf)
        else:
            text0 = _pdf_text(pdf)
            if text0 and _wrong_ic.search(text0) and not pdf_mentions_mpn(text0, mpn):
                _quarantine(pdf)

    # 2) 优先 LCSC / JLC 手册 URL
    url = str(jlc.get("datasheet_url") or (lcsc or {}).get("pdf_url") or "")
    if url.startswith("http") and (not pdf.is_file() or pdf_is_stub(pdf)):
        if _download_pdf(url, pdf):
            hit = from_pdf(pdf, url, "lcsc_pdfUrl")
            if hit:
                return hit

    if pdf.is_file() and not pdf_is_stub(pdf):
        hit = from_pdf(pdf, url, "local_pdf")
        # 抽出值与立创参数打架时，立创是本颗料号商品页，不用 PDF 里系列表第一行
        vr_lcsc, _q = voltage_from_product(lcsc)
        if hit and vr_lcsc is not None and abs(float(hit["value"]) - float(vr_lcsc)) > 0.05:
            hit = None
        if hit:
            return hit

    # 3) LCSC 商品参数「额定电压」（立创详情页，带 URL）
    vr, quote = voltage_from_product(lcsc)
    if vr is not None and not _reject_voltage_code_as_volts(mpn, float(vr)):
        return {
            "value": vr,
            "pdf": str(pdf) if pdf.is_file() else "",
            "source": {
                "kind": "lcsc_product",
                "url": (lcsc or {}).get("product_url") or "",
                "quote": quote,
                "mpn": mpn,
                "lcsc_code": code,
                "backend": "lcsc_paramVOList",
            },
        }

    # 4) AnySearch 下 PDF，正文须对上料号
    a_urls, snippets = _anysearch_pdf_and_web(mpn)
    for au in a_urls[:5]:
        if pdf.is_file() and not pdf_is_stub(pdf):
            break
        if _download_pdf(au, pdf):
            text = _pdf_text(pdf)
            if pdf_is_stub(pdf) or (text and not pdf_mentions_mpn(text, mpn)):
                _quarantine(pdf)
                continue
            hit = from_pdf(pdf, au, "anysearch_pdf")
            if hit:
                return hit

    # 5) 联网摘要扫 Vr（必须带 URL）
    for sn in snippets:
        v, q = extract_vr_from_text(sn.get("quote") or "", mpn)
        u = sn.get("url") or ""
        if v is not None and u.startswith("http") and not _reject_voltage_code_as_volts(mpn, float(v)):
            return {
                "value": v,
                "pdf": "",
                "source": {
                    "kind": "web_page",
                    "url": u,
                    "quote": q or (sn.get("quote") or "")[:160],
                    "mpn": mpn,
                    "backend": "anysearch_snippet",
                },
            }

    # 料号电压码 / Agent 查参不在此返回——二者是线索层，见 collect_capacitor_vr_clues
    return None


# 可写入密封 rated_voltage_v 的出处
SEALABLE_VR_KINDS = frozenset({"datasheet_page", "lcsc_product", "web_page", "agent_lookup"})
# agent_lookup 仅 confidence=high 且有 cite 时可密封（见 _agent_may_seal）


def _vr_close(a: float | None, b: float | None, tol: float = 0.05) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def _agent_may_seal(rec: dict[str, Any] | None) -> bool:
    if not isinstance(rec, dict):
        return False
    if rec.get("value_v") is None:
        return False
    cite = str(rec.get("cite") or "").strip()
    conf = str(rec.get("confidence") or "").strip().lower()
    return bool(cite) and conf == "high"


def agent_vr_cache_path(board: Path, mpn: str) -> Path:
    return out_dir(board) / "cache" / "mpn" / safe_mpn_dir(mpn) / "vr_agent.json"


def read_agent_vr_cache(board: Path, mpn: str) -> dict[str, Any] | None:
    p = agent_vr_cache_path(board, mpn)
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    if doc.get("value_v") is None and not doc.get("error"):
        return None
    return doc


def write_agent_vr_cache(board: Path, mpn: str, rec: dict[str, Any]) -> Path:
    p = agent_vr_cache_path(board, mpn)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(rec)
    payload.setdefault("mpn", mpn)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def collect_capacitor_vr_clues(
    mpn: str,
    board: Path,
    *,
    sealed_v: float | None = None,
) -> dict[str, Any]:
    """料号码 + Agent 缓存线索；不写入密封。返回 clues / conflict_note。"""
    clues: dict[str, Any] = {}
    notes: list[str] = []
    v_code, cite_code = decode_mpn_voltage_code(mpn)
    if v_code is not None:
        clues["mpn_voltage_code"] = {
            "value_v": float(v_code),
            "cite": cite_code,
            "kind": "mpn_voltage_code",
        }
        if sealed_v is not None and not _vr_close(sealed_v, v_code):
            notes.append(
                f"存疑：密封 Vr={sealed_v:g}V 与料号电压码 {v_code:g}V 不一致"
            )
    agent = read_agent_vr_cache(board, mpn)
    if agent and agent.get("value_v") is not None:
        clues["agent_lookup"] = {
            "value_v": float(agent["value_v"]),
            "cite": str(agent.get("cite") or ""),
            "confidence": str(agent.get("confidence") or ""),
            "kind": "agent_lookup",
        }
        av = float(agent["value_v"])
        if sealed_v is not None and not _vr_close(sealed_v, av):
            notes.append(f"存疑：密封 Vr={sealed_v:g}V 与 Agent 查参 {av:g}V 不一致")
        if v_code is not None and not _vr_close(v_code, av):
            notes.append(f"存疑：料号电压码 {v_code:g}V 与 Agent 查参 {av:g}V 不一致")
    return {"clues": clues, "conflict_note": "；".join(notes)}


def _apply_clues_to_params(params: dict[str, Any], pack: dict[str, Any]) -> None:
    clues = pack.get("clues") or {}
    mpn_c = clues.get("mpn_voltage_code") if isinstance(clues, dict) else None
    if isinstance(mpn_c, dict) and mpn_c.get("value_v") is not None:
        params["v_rated_mpn_code_v"] = float(mpn_c["value_v"])
        params["v_rated_mpn_code_cite"] = str(mpn_c.get("cite") or "")
    ag = clues.get("agent_lookup") if isinstance(clues, dict) else None
    if isinstance(ag, dict) and ag.get("value_v") is not None:
        params["v_rated_agent_v"] = float(ag["value_v"])
        params["v_rated_agent_cite"] = str(ag.get("cite") or "")
        params["v_rated_agent_confidence"] = str(ag.get("confidence") or "")
    conflict = str(pack.get("conflict_note") or "").strip()
    if conflict:
        params["v_rated_conflict"] = conflict
        prev = str(params.get("extract_note") or "").strip()
        if conflict not in prev:
            params["extract_note"] = (prev + "；" + conflict).strip("；") if prev else conflict


def apply_capacitor_vr(
    components: dict[str, Any],
    board: Path,
    *,
    allow_agent: bool | None = None,
) -> dict[str, Any]:
    """电容 Vr 瀑布：手册/立创/网页可密封；料号码与 Agent 为线索层。

    Agent 仅在硬出处皆空时触发；high+cite 可密封，否则只进线索字段。
    设 RATED_HUNT_SKIP_AGENT=1 可跳过 Agent。
    """
    import os

    if allow_agent is None:
        allow_agent = os.environ.get("RATED_HUNT_SKIP_AGENT", "").strip().lower() not in {
            "1",
            "true",
            "yes",
        }
    stats = {
        "tried": 0,
        "hit": 0,
        "skip_placeholder": 0,
        "clue_mpn": 0,
        "clue_agent": 0,
        "agent_sealed": 0,
        "conflict": 0,
    }
    hard_cache: dict[str, dict[str, Any] | None] = {}
    need_agent: list[str] = []

    for ref, c in components.items():
        if not isinstance(c, dict):
            continue
        if str(c.get("type") or "") != "capacitor":
            continue
        mpn = str(c.get("mpn") or "").strip()
        pt = c.setdefault("pintable", {})
        params = dict(pt.get("params") or {})
        if is_placeholder_mpn(mpn):
            stats["skip_placeholder"] += 1
            continue
        sealed = params.get("rated_voltage_v")
        try:
            sealed_f = float(sealed) if sealed is not None else None
        except (TypeError, ValueError):
            sealed_f = None
        prev_src_kind = str((params.get("rated_voltage_source") or {}).get("kind") or "")
        # 无硬出处的旧密封（如错档 6.3）允许被手册/立创覆盖
        weak_seal = sealed_f is not None and prev_src_kind not in {
            "datasheet_page",
            "lcsc_product",
            "web_page",
            "agent_lookup",
        }

        if sealed_f is None or weak_seal:
            stats["tried"] += 1
            if mpn not in hard_cache:
                hard_cache[mpn] = hunt_capacitor_vr(mpn, board)
            hit = hard_cache[mpn]
            if hit and hit.get("value") is not None:
                kind = str((hit.get("source") or {}).get("kind") or "")
                if kind in SEALABLE_VR_KINDS and kind != "agent_lookup":
                    new_v = float(hit["value"])
                    if sealed_f is None or not _vr_close(sealed_f, new_v):
                        params["rated_voltage_v"] = new_v
                        src = hit.get("source") or {}
                        params["rated_voltage_source"] = src
                        note = (
                            f"{src.get('kind')}: {src.get('quote') or ''} @ "
                            f"{src.get('url') or src.get('path') or ''}"
                        )
                        params["extract_note"] = note.strip(" :@")
                        if hit.get("pdf"):
                            pt["pdf"] = hit["pdf"]
                        if pt.get("status") in (None, "unknown"):
                            pt["status"] = "hunted"
                        sealed_f = new_v
                        stats["hit"] += 1
                        print(
                            f"HUNT\t{ref}\t{mpn}\t{hit['value']}V\t{src.get('kind')}\t"
                            f"{src.get('url') or src.get('path') or ''}"
                        )
                    else:
                        sealed_f = float(params["rated_voltage_v"])

        if sealed_f is None and allow_agent and mpn not in need_agent:
            if read_agent_vr_cache(board, mpn) is None:
                need_agent.append(mpn)

        pack = collect_capacitor_vr_clues(mpn, board, sealed_v=sealed_f)
        # 硬出处仍空：尝试用已有 Agent 缓存密封
        if sealed_f is None:
            ag_raw = read_agent_vr_cache(board, mpn)
            if _agent_may_seal(ag_raw):
                params["rated_voltage_v"] = float(ag_raw["value_v"])
                params["rated_voltage_source"] = {
                    "kind": "agent_lookup",
                    "quote": str(ag_raw.get("cite") or ""),
                    "mpn": mpn,
                    "confidence": "high",
                    "backend": "agent_mpn_vr",
                }
                params["extract_note"] = (
                    f"agent_lookup: {ag_raw.get('cite')} (confidence=high)"
                )
                sealed_f = float(ag_raw["value_v"])
                stats["agent_sealed"] += 1
                stats["hit"] += 1
                print(f"HUNT\t{ref}\t{mpn}\t{sealed_f}V\tagent_lookup\thigh")
                pack = collect_capacitor_vr_clues(mpn, board, sealed_v=sealed_f)

        _apply_clues_to_params(params, pack)
        if pack.get("clues", {}).get("mpn_voltage_code"):
            stats["clue_mpn"] += 1
        if pack.get("clues", {}).get("agent_lookup"):
            stats["clue_agent"] += 1
        if pack.get("conflict_note"):
            stats["conflict"] += 1
        pt["params"] = params

    if allow_agent and need_agent:
        try:
            from agent_review.mpn_vr import run_mpn_vr_jobs

            run_mpn_vr_jobs(board, need_agent)
        except Exception as exc:
            print(f"WARN\tmpn_vr agent batch failed: {exc}")
        # 二轮：写入刚产出的 Agent 缓存
        for ref, c in components.items():
            if not isinstance(c, dict) or str(c.get("type") or "") != "capacitor":
                continue
            mpn = str(c.get("mpn") or "").strip()
            if mpn not in need_agent:
                continue
            pt = c.setdefault("pintable", {})
            params = dict(pt.get("params") or {})
            sealed = params.get("rated_voltage_v")
            try:
                sealed_f = float(sealed) if sealed is not None else None
            except (TypeError, ValueError):
                sealed_f = None
            if sealed_f is None:
                ag_raw = read_agent_vr_cache(board, mpn)
                if _agent_may_seal(ag_raw):
                    params["rated_voltage_v"] = float(ag_raw["value_v"])
                    params["rated_voltage_source"] = {
                        "kind": "agent_lookup",
                        "quote": str(ag_raw.get("cite") or ""),
                        "mpn": mpn,
                        "confidence": "high",
                        "backend": "agent_mpn_vr",
                    }
                    params["extract_note"] = (
                        f"agent_lookup: {ag_raw.get('cite')} (confidence=high)"
                    )
                    sealed_f = float(ag_raw["value_v"])
                    stats["agent_sealed"] += 1
                    stats["hit"] += 1
                    print(f"HUNT\t{ref}\t{mpn}\t{sealed_f}V\tagent_lookup\thigh")
            pack = collect_capacitor_vr_clues(mpn, board, sealed_v=sealed_f)
            _apply_clues_to_params(params, pack)
            if pack.get("clues", {}).get("agent_lookup"):
                stats["clue_agent"] += 1
            if pack.get("conflict_note"):
                stats["conflict"] += 1
            pt["params"] = params

    return stats


_IO_FIELDS = ["voh_min_v", "vol_max_v", "vih_min_v", "vil_max_v"]
_RESET_FIELDS = ["reset_vih_v", "reset_vil_v", "reset_default"]
_SURGE_FIELDS = ["vrwm_v", "vc_v", "ipp_a"]
_MOS_FIELDS = ["vds_rated_v", "id_rated_a"]
_BJT_FIELDS = ["vceo_v", "ic_max_a"]
_CONN_FIELDS = ["i_rated_a"]
_PARAM_HUNT_FIELDS = (
    _IO_FIELDS + _RESET_FIELDS + _SURGE_FIELDS + _MOS_FIELDS + _BJT_FIELDS + _CONN_FIELDS
    + ["isat", "t_max_c", "t_op_min_c", "t_op_max_c", "p_rated_w"]
)
_SURGE_MPN = re.compile(r"TVS|ESD|SMBJ|SMAJ|SMCJ|varistor|压敏|GDT", re.I)
_MOS_MPN = re.compile(r"MOSFET|MOS\b|AO\d{4}|SI\d{3,}|2N700|IRL|IRF|BSS\d", re.I)
_SNIPPET_FIELD = {
    "voh_min_v": re.compile(r"\bVOH\b[^0-9]{0,16}(\d+(?:\.\d+)?)\s*V", re.I),
    "vol_max_v": re.compile(r"\bVOL\b[^0-9]{0,16}(\d+(?:\.\d+)?)\s*V", re.I),
    "vih_min_v": re.compile(r"\bVIH\b[^0-9]{0,16}(\d+(?:\.\d+)?)\s*V", re.I),
    "vil_max_v": re.compile(r"\bVIL\b[^0-9]{0,16}(\d+(?:\.\d+)?)\s*V", re.I),
    "vrwm_v": re.compile(r"\bV_?RWM\b[^0-9]{0,16}(\d+(?:\.\d+)?)\s*V", re.I),
    "vc_v": re.compile(r"(?:clamping voltage|V_?CL)\b[^0-9]{0,16}(\d+(?:\.\d+)?)\s*V", re.I),
    "ipp_a": re.compile(r"\bI_?PP\b[^0-9]{0,16}(\d+(?:\.\d+)?)\s*A", re.I),
    "reset_vih_v": re.compile(r"reset[^.]{0,24}(?:high|VIH)[^0-9]{0,12}(\d+(?:\.\d+)?)\s*V", re.I),
    "reset_vil_v": re.compile(r"reset[^.]{0,24}(?:low|VIL)[^0-9]{0,12}(\d+(?:\.\d+)?)\s*V", re.I),
    "isat": re.compile(r"\bI_?sat\b[^0-9]{0,16}(\d+(?:\.\d+)?)\s*A", re.I),
    "vds_rated_v": re.compile(r"\bV_?DS\b[^0-9]{0,20}(\d+(?:\.\d+)?)\s*V", re.I),
    "id_rated_a": re.compile(r"\bI_?D\b[^0-9]{0,20}(\d+(?:\.\d+)?)\s*A", re.I),
    "vceo_v": re.compile(r"\bV_?CEO\b[^0-9]{0,16}(\d+(?:\.\d+)?)\s*V", re.I),
    "ic_max_a": re.compile(r"\bI_?C\b[^0-9]{0,16}(\d+(?:\.\d+)?)\s*A", re.I),
    "i_rated_a": re.compile(r"(?:rated\s+current|额定电流)[^0-9]{0,16}(\d+(?:\.\d+)?)\s*A", re.I),
    "p_rated_w": re.compile(
        r"(?:rated\s+power|power\s+dissipation|额定功率|Ptot)[^0-9]{0,20}(\d+(?:\.\d+)?)\s*(mW|W)",
        re.I,
    ),
}

try:
    from kernel.rated_registry import TYPE_FIELDS as FIELDS_BY_TYPE
except Exception:  # pragma: no cover
    FIELDS_BY_TYPE = {
        "capacitor": ["rated_voltage_v"],
        "ic": ["recommended.vcc_min", "recommended.vcc_max", "abs_max.vcc_max", *_IO_FIELDS],
        "resistor": ["p_rated_w"],
        "inductor": ["isat", "t_max_c"],
        "crystal": ["cl_pf"],
    }


def wanted_fields(comp: dict[str, Any]) -> list[str]:
    typ = str(comp.get("type") or "")
    fields = list(FIELDS_BY_TYPE.get(typ) or [])
    # 仅并入「全器件」检查项字段（如 P12-A03 温区）；勿把 TVS/MOS 项摊到所有二极管/三极管
    try:
        from kernel.rated_registry import RULE_RATED

        for spec in RULE_RATED.values():
            types = spec.get("comp_types") or []
            if "*" not in types:
                continue
            for item in spec.get("fields") or []:
                f = str(item.get("field") or "")
                if f and f not in fields:
                    fields.append(f)
    except Exception:
        pass
    blob = " ".join(str(comp.get(k) or "") for k in ("mpn", "value", "desc"))
    if typ == "ic" and is_regulator_mpn(str(comp.get("mpn") or "")):
        # 稳压器用 VI/VO，不用逻辑电平 / 通用 VCC 窗
        fields = [
            f
            for f in fields
            if f not in _IO_FIELDS
            and f
            not in {
                "recommended.vcc_min",
                "recommended.vcc_max",
                "abs_max.vcc_max",
            }
        ]
        for name in (
            "recommended.vi_min",
            "recommended.vi_max",
            "recommended.vo_min",
            "recommended.vo_max",
            "abs_max.vcc_max",  # 绝对最大仍常标 VI
        ):
            if name not in fields:
                fields.append(name)
    if _SURGE_MPN.search(blob) and not re.search(r"\b1N52|zener|稳压", blob, re.I):
        for name in _SURGE_FIELDS:
            if name not in fields:
                fields.append(name)
    if typ == "transistor" and _MOS_MPN.search(blob):
        for name in _MOS_FIELDS:
            if name not in fields:
                fields.append(name)
    # 信号二极管不追齐纳 Vz
    if typ == "diode" and not re.search(r"1N52|zener|稳压|BZX|MMSZ", blob, re.I):
        fields = [f for f in fields if f != "vz_max_v"]
    return fields


def _hit(value: Any, source: dict[str, Any], pdf: str = "") -> dict[str, Any]:
    return {"value": value, "source": source, "pdf": pdf}


def _fields_from_body(body: dict[str, Any], fields: list[str], pdf: str, url: str) -> dict[str, dict[str, Any]]:
    rec = body.get("recommended") if isinstance(body.get("recommended"), dict) else {}
    abs_m = body.get("abs_max") if isinstance(body.get("abs_max"), dict) else {}
    params = body.get("params") if isinstance(body.get("params"), dict) else {}
    values: dict[str, Any] = {
        "recommended.vcc_min": rec.get("vcc_min"),
        "recommended.vcc_max": rec.get("vcc_max"),
        "recommended.vi_min": rec.get("vi_min"),
        "recommended.vi_max": rec.get("vi_max"),
        "recommended.vo_min": rec.get("vo_min"),
        "recommended.vo_max": rec.get("vo_max"),
        "abs_max.vcc_max": abs_m.get("vcc_max"),
        "abs_max.vcc_min": abs_m.get("vcc_min"),
        "rated_voltage_v": params.get("rated_voltage_v"),
        "isat": params.get("isat"),
        "t_max_c": params.get("t_max_c"),
        "t_op_min_c": params.get("t_op_min_c"),
        "t_op_max_c": params.get("t_op_max_c"),
        "cl_pf": params.get("cl_pf"),
        "p_rated_w": params.get("p_rated_w") or params.get("ptot_w"),
        "vds_rated_v": params.get("vds_rated_v"),
        "id_rated_a": params.get("id_rated_a"),
        "vceo_v": params.get("vceo_v"),
        "ic_max_a": params.get("ic_max_a"),
        "i_rated_a": params.get("i_rated_a"),
    }
    for key in _PARAM_HUNT_FIELDS:
        if values.get(key) is None:
            values[key] = params.get(key)
    out: dict[str, dict[str, Any]] = {}
    for field in fields:
        val = values.get(field)
        if val is None:
            continue
        own = params.get(f"{field}_source") if isinstance(params.get(f"{field}_source"), dict) else None
        if own and own.get("kind"):
            src = dict(own)
            src.setdefault("path", pdf)
            src.setdefault("url", url)
            src["backend"] = "extract_pdf"
        else:
            quote = (
                params.get(f"{field}_quote")
                or rec.get("quote")
                or rec.get("vi_quote")
                or rec.get("vo_quote")
                or abs_m.get("quote")
                or str(val)
            )
            page = params.get(f"{field}_page") or rec.get("page") or abs_m.get("page") or 1
            src = {
                "kind": "datasheet_page",
                "path": pdf,
                "page": page,
                "quote": str(quote)[:160],
                "url": url,
                "backend": "extract_pdf",
            }
        out[field] = _hit(val, src, pdf)
    return out


def hunt_rated(mpn: str, fields: list[str], board: Path) -> dict[str, dict[str, Any]]:
    """按字段争取额定参数。电容 Vr 走原 hunt_capacitor_vr。"""
    mpn = (mpn or "").strip()
    out: dict[str, dict[str, Any]] = {}
    need = [f for f in fields if f]
    if not mpn or is_placeholder_mpn(mpn) or not need:
        return out
    if "rated_voltage_v" in need:
        cap = hunt_capacitor_vr(mpn, board)
        if cap:
            out["rated_voltage_v"] = cap
        need = [f for f in need if f != "rated_voltage_v"]
    if not need:
        return out

    o = out_dir(board)
    slot = board / "cache" / "mpn" / safe_mpn_dir(mpn)
    slot.mkdir(parents=True, exist_ok=True)
    pdf = slot / "datasheet.pdf"
    jlc = _jlc_by_mpn(board).get(mpn) or {}
    code = str(jlc.get("lcsc_code") or "")
    lcsc = lookup_product(code, o / "lcsc_cache") if code else {}
    if pdf.is_file() and pdf_is_stub(pdf):
        _quarantine(pdf)
    url = str(jlc.get("datasheet_url") or (lcsc or {}).get("pdf_url") or "")
    if url.startswith("http") and (not pdf.is_file() or pdf_is_stub(pdf)):
        _download_pdf(url, pdf)

    def take_from_pdf(path: Path, backend: str, u: str) -> None:
        nonlocal need
        if not path.is_file() or pdf_is_stub(path):
            return
        body = extract_pdf(path, mpn=mpn, max_pages=40)
        got = _fields_from_body(body, need, str(path), u)
        for field, rec in got.items():
            rec["source"]["backend"] = backend
            out[field] = rec
        need = [f for f in need if f not in out]

    if pdf.is_file() and not pdf_is_stub(pdf):
        take_from_pdf(pdf, "local_pdf", url)

    if any(f.startswith("recommended") for f in need):
        lo, hi, quote = supply_range_from_product(lcsc)
        if lo is not None and hi is not None:
            src = {
                "kind": "lcsc_product",
                "url": (lcsc or {}).get("product_url") or "",
                "quote": quote,
                "mpn": mpn,
                "lcsc_code": code,
                "backend": "lcsc_paramVOList",
            }
            if "recommended.vcc_min" in need:
                out["recommended.vcc_min"] = _hit(lo, src, str(pdf) if pdf.is_file() else "")
            if "recommended.vcc_max" in need:
                out["recommended.vcc_max"] = _hit(hi, src, str(pdf) if pdf.is_file() else "")
            need = [f for f in need if f not in out]

    if "p_rated_w" in need:
        pw, pq = power_from_product(lcsc)
        if pw is not None:
            src = {
                "kind": "lcsc_product",
                "url": (lcsc or {}).get("product_url") or "",
                "quote": pq,
                "mpn": mpn,
                "lcsc_code": code,
                "backend": "lcsc_paramVOList",
            }
            out["p_rated_w"] = _hit(pw, src, str(pdf) if pdf.is_file() else "")
            need = [f for f in need if f != "p_rated_w"]

    for field in list(need):
        if field not in _PARAM_HUNT_FIELDS and field not in {
            "p_rated_w",
            "isat",
            "t_max_c",
            "t_op_min_c",
            "t_op_max_c",
        }:
            continue
        val, quote = named_param(lcsc, field)
        if val is None or not quote:
            continue
        src = {
            "kind": "lcsc_product",
            "url": (lcsc or {}).get("product_url") or "",
            "quote": quote,
            "mpn": mpn,
            "lcsc_code": code,
            "backend": "lcsc_paramVOList",
        }
        out[field] = _hit(val, src, str(pdf) if pdf.is_file() else "")
        need = [f for f in need if f not in out]

    if need:
        a_urls, snippets = _anysearch_pdf_and_web(mpn, need)
        local_kept = pdf.is_file() and not pdf_is_stub(pdf)
        web_pdf = slot / "web.pdf"
        for au in a_urls[:5]:
            if not need:
                break
            target = web_pdf if local_kept else pdf
            if not _download_pdf(au, target):
                continue
            text = _pdf_text(target)
            if pdf_is_stub(target) or (text and not pdf_mentions_mpn(text, mpn)):
                if target == pdf:
                    _quarantine(pdf)
                else:
                    try:
                        target.unlink()
                    except OSError:
                        pass
                continue
            take_from_pdf(target, "anysearch_pdf", au)
        for sn in snippets:
            u = sn.get("url") or ""
            q = sn.get("quote") or ""
            if not any(f.startswith("recommended") for f in need):
                m = None
            else:
                m = re.search(
                    r"(\d+(?:\.\d+)?)\s*V\s*(?:to|–|-|~|至)\s*(\d+(?:\.\d+)?)\s*V",
                    q,
                    re.I,
                )
            if m and u.startswith("http"):
                lo, hi = float(m.group(1)), float(m.group(2))
                if 0 <= lo < hi <= 80:
                    src = {"kind": "web_page", "url": u, "quote": q[:160], "mpn": mpn, "backend": "anysearch_snippet"}
                    if "recommended.vcc_min" in need:
                        out["recommended.vcc_min"] = _hit(lo, src)
                    if "recommended.vcc_max" in need:
                        out["recommended.vcc_max"] = _hit(hi, src)
                    need = [f for f in need if f not in out]
            if re.search(r"absolute maximum", q, re.I):
                continue
            if not pdf_mentions_mpn(q, mpn):
                continue
            for field, pat in _SNIPPET_FIELD.items():
                if field not in need:
                    continue
                sm = pat.search(q)
                if not sm or not u.startswith("http"):
                    continue
                try:
                    val = float(sm.group(1))
                except (TypeError, ValueError):
                    continue
                if field == "p_rated_w" and sm.lastindex and sm.lastindex >= 2:
                    if str(sm.group(2)).lower() == "mw":
                        val = val / 1000.0
                src = {
                    "kind": "web_page",
                    "url": u,
                    "quote": sm.group(0)[:160],
                    "mpn": mpn,
                    "backend": "anysearch_snippet",
                }
                out[field] = _hit(val, src)
            need = [f for f in need if f not in out]
    return out


def _apply_hit(pt: dict[str, Any], field: str, hit: dict[str, Any]) -> None:
    src = hit.get("source") or {}
    val = hit.get("value")
    if parse_rated_field(field):
        write_rated(pt, field, val, src)
    else:
        params = pt.setdefault("params", {})
        params[field] = val
        params[f"{field}_source"] = src
        note = f"{src.get('kind')}: {src.get('quote') or ''} @ {src.get('url') or src.get('path') or ''}"
        if not params.get("extract_note"):
            params["extract_note"] = note.strip(" :@")
    if hit.get("pdf"):
        pt["pdf"] = hit["pdf"]
    if pt.get("status") in (None, "unknown"):
        pt["status"] = "hunted"


def apply_rated_fields(
    components: dict[str, Any],
    board: Path,
    *,
    allow_agent: bool | None = None,
) -> dict[str, Any]:
    """按器件类补缺额定字段。电容走 Vr 专用阶梯；其它走 hunt_rated + 可选 Agent。

    字段集合见 kernel.rated_registry（按检查项/器件类型注册，允许不同逻辑）。
    """
    import os

    if allow_agent is None:
        # 默认开启材料 Agent 查参；仅调试硬路径时设 RATED_HUNT_SKIP_AGENT=1
        # 注意：不跑深审 ≠ 关闭本兜底
        allow_agent = os.environ.get("RATED_HUNT_SKIP_AGENT", "").strip().lower() not in {
            "1",
            "true",
            "yes",
        }
    print(f"RATED_HUNT\tallow_agent={allow_agent}")
    cap_stats = apply_capacitor_vr(components, board, allow_agent=allow_agent)
    stats: dict[str, Any] = {
        "tried": 0,
        "hit": 0,
        "skip_placeholder": 0,
        "agent_sealed": 0,
        "agent_jobs": 0,
        "cap": cap_stats,
    }
    cache: dict[tuple[str, tuple[str, ...]], dict[str, dict[str, Any]]] = {}
    need_agent: list[tuple[str, str]] = []

    try:
        from kernel.rated_registry import agent_fields_for_type
    except Exception:
        agent_fields_for_type = lambda _t: []  # noqa: E731

    for ref, c in components.items():
        if not isinstance(c, dict):
            continue
        typ = str(c.get("type") or "")
        # 电容 Vr 已在 apply_capacitor_vr；其余额定（温区等）仍走共用瀑布
        fields = wanted_fields(c)
        if typ == "capacitor":
            fields = [f for f in fields if f != "rated_voltage_v"]
            if not fields:
                continue
        if not fields:
            continue
        pt = c.setdefault("pintable", {})
        missing = [f for f in fields if not field_present(pt, f)]
        mpn = str(c.get("mpn") or "").strip()
        if not missing:
            continue
        if is_placeholder_mpn(mpn):
            stats["skip_placeholder"] += 1
            continue
        stats["tried"] += 1
        key = (mpn, tuple(missing))
        if key not in cache:
            cache[key] = hunt_rated(mpn, missing, board)
        got = cache[key]
        n = 0
        for field, hit in got.items():
            if field not in missing:
                continue
            _apply_hit(pt, field, hit)
            n += 1
            print(f"HUNT\t{ref}\t{mpn}\t{field}={hit.get('value')}\t{(hit.get('source') or {}).get('kind')}")
        if n:
            stats["hit"] += 1
        still = [f for f in missing if not field_present(pt, f)]
        agent_ok = set(agent_fields_for_type(typ))
        for f in still:
            if f not in agent_ok:
                continue
            # 先吃缓存
            try:
                from agent_review.mpn_param import agent_may_seal, read_agent_param_cache

                rec = read_agent_param_cache(board, mpn, f)
            except Exception:
                rec = None
            if agent_may_seal(rec):
                _apply_hit(
                    pt,
                    f,
                    {
                        "value": float(rec["value"]),
                        "source": {
                            "kind": "agent_lookup",
                            "quote": str(rec.get("cite") or ""),
                            "mpn": mpn,
                            "confidence": "high",
                            "backend": "agent_mpn_param",
                        },
                    },
                )
                stats["agent_sealed"] += 1
                stats["hit"] += 1
                print(f"HUNT\t{ref}\t{mpn}\t{f}={rec['value']}\tagent_lookup")
            elif allow_agent:
                need_agent.append((mpn, f))

    if allow_agent and need_agent:
        try:
            from agent_review.mpn_param import (
                agent_may_seal,
                read_agent_param_cache,
                run_mpn_param_jobs,
            )

            uniq = sorted({(m, f) for m, f in need_agent})
            stats["agent_jobs"] = len(uniq)
            print(f"RATED_HUNT\tagent_param_jobs={len(uniq)}")
            run_mpn_param_jobs(board, need_agent)
            for ref, c in components.items():
                if not isinstance(c, dict):
                    continue
                typ = str(c.get("type") or "")
                if typ == "capacitor":
                    continue
                mpn = str(c.get("mpn") or "").strip()
                pt = c.setdefault("pintable", {})
                for f in wanted_fields(c):
                    if field_present(pt, f):
                        continue
                    if (mpn, f) not in need_agent and (mpn, f) not in {
                        (a, b) for a, b in need_agent
                    }:
                        continue
                    rec = read_agent_param_cache(board, mpn, f)
                    if agent_may_seal(rec):
                        _apply_hit(
                            pt,
                            f,
                            {
                                "value": float(rec["value"]),
                                "source": {
                                    "kind": "agent_lookup",
                                    "quote": str(rec.get("cite") or ""),
                                    "mpn": mpn,
                                    "confidence": "high",
                                    "backend": "agent_mpn_param",
                                },
                            },
                        )
                        stats["agent_sealed"] += 1
                        print(f"HUNT\t{ref}\t{mpn}\t{f}={rec['value']}\tagent_lookup")
        except Exception as exc:
            print(f"WARN\tmpn_param agent batch failed: {exc}")

    return stats
