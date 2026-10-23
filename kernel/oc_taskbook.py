# -*- coding: utf-8 -*-
"""工况 / 设计任务书：只抽取写明的字段，不补默认库。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from kernel.util import write_json

NET_VOLT = re.compile(
    r"(?P<net>[A-Za-z][A-Za-z0-9_+.-]{0,31})\s*[=:：]\s*(?P<v>\d+(?:\.\d+)?)\s*[Vv]"
)
TAMB = re.compile(
    r"(?:T_?amb|环境温度|Tamb)\s*[=:：]?\s*(-?\d+(?:\.\d+)?)\s*[℃Cc]?",
    re.I,
)
CURRENT = re.compile(
    r"(?P<net>[A-Za-z][A-Za-z0-9_+.-]{0,31})\s*[=:：]\s*(?P<i>\d+(?:\.\d+)?)\s*(?:A|mA|Amp)",
    re.I,
)
EXEMPT = re.compile(r"(?:豁免|exemption)\s*[:：]?\s*([^\n]{2,80})", re.I)
IFACE = re.compile(r"(?:接口|connector|CN\d+|J\d+)\s*[:：]\s*([^\n]{2,80})", re.I)


def _read_docx(path: Path) -> str:
    import zipfile

    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    xml = xml.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return xml


def _read_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return _read_docx(path)
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def _empty_mapping() -> dict[str, Any]:
    return {
        "rails": [],
        "currents": [],
        "t_amb_c": None,
        "exemptions": [],
        "interfaces": [],
        "charset_exceptions": [],
    }


def _is_default_oc_library(obj: Any, path: Path) -> bool:
    name = path.name.lower()
    if "operating_conditions_defaults" in name or "defaults.yaml" in name:
        return True
    if not isinstance(obj, dict):
        return False
    if obj.get("scope") == "board_base":
        return True
    defaults = obj.get("defaults")
    if isinstance(defaults, dict) and "logic_rails_v" in defaults and "rails" not in obj:
        return True
    return False


def _from_mapping(obj: Any) -> dict[str, Any]:
    rails: list[dict[str, Any]] = []
    currents: list[dict[str, Any]] = []
    tamb = None
    exemptions: list[str] = []
    interfaces: list[str] = []
    if not isinstance(obj, dict):
        return {
            "rails": rails,
            "currents": currents,
            "t_amb_c": tamb,
            "exemptions": exemptions,
            "interfaces": interfaces,
            "charset_exceptions": [],
        }

    rail_src = obj.get("rails") or obj.get("轨") or obj.get("rail_table") or obj.get("voltages")
    if isinstance(rail_src, dict):
        for k, v in rail_src.items():
            try:
                rails.append({"net": str(k), "v": float(v), "source": "file"})
            except (TypeError, ValueError):
                continue
    elif isinstance(rail_src, list):
        for row in rail_src:
            if not isinstance(row, dict):
                continue
            net = row.get("net") or row.get("name") or row.get("网络")
            v = row.get("v") or row.get("voltage") or row.get("电压")
            try:
                if net is not None and v is not None:
                    rails.append({"net": str(net), "v": float(v), "source": "file"})
            except (TypeError, ValueError):
                continue

    cur_src = obj.get("currents") or obj.get("电流")
    if isinstance(cur_src, dict):
        for k, v in cur_src.items():
            try:
                currents.append({"net": str(k), "i": float(v), "source": "file"})
            except (TypeError, ValueError):
                continue
    elif isinstance(cur_src, list):
        for row in cur_src:
            if not isinstance(row, dict):
                continue
            net = row.get("net") or row.get("name")
            i = row.get("i") or row.get("current") or row.get("I")
            try:
                if net is not None and i is not None:
                    currents.append({"net": str(net), "i": float(i), "source": "file"})
            except (TypeError, ValueError):
                continue

    for key in ("t_amb_c", "Tamb", "tamb", "环境温度"):
        if obj.get(key) is not None:
            try:
                tamb = float(obj[key])
                break
            except (TypeError, ValueError):
                pass

    ex = obj.get("exemptions") or obj.get("豁免") or []
    if isinstance(ex, list):
        exemptions = [str(x) for x in ex if x]
    elif isinstance(ex, str) and ex.strip():
        exemptions = [ex.strip()]

    ifc = obj.get("interfaces") or obj.get("接口") or []
    if isinstance(ifc, list):
        interfaces = [str(x) for x in ifc if x]
    charset = obj.get("charset_exceptions") or obj.get("网名字符例外") or []
    if not isinstance(charset, list):
        charset = []
    specs = obj.get("interface_specs") or obj.get("接口规格") or []
    if not isinstance(specs, list):
        specs = []
    out = {
        "rails": rails,
        "currents": currents,
        "t_amb_c": tamb,
        "exemptions": exemptions,
        "interfaces": interfaces,
        "charset_exceptions": [str(x) for x in charset],
    }
    if specs:
        out["interface_specs"] = [dict(x) if isinstance(x, dict) else x for x in specs]
    if obj.get("note"):
        out["source_note"] = str(obj.get("note"))
    return out


def parse_structured_file(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    suf = path.suffix.lower()
    parsed_from = "text"
    mapping: dict[str, Any] = {}
    if suf in {".json"}:
        try:
            obj = json.loads(text)
            mapping = _from_mapping(obj)
            parsed_from = "json"
        except Exception:
            mapping = {}
    elif suf in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore

            obj = yaml.safe_load(text)
        except Exception:
            obj = None
        if _is_default_oc_library(obj if isinstance(obj, dict) else {}, path):
            rejected = _empty_mapping()
            rejected["source_file"] = str(path)
            rejected["parsed_from"] = "defaults_library_rejected"
            rejected["note"] = "提交文件为公共默认工况库，不按签字轨表闭合；轨压/电流/Tamb 保持空"
            rejected["library_rejected"] = True
            return rejected
        mapping = _from_mapping(obj if isinstance(obj, dict) else {})
        parsed_from = "yaml"

    if not mapping:
        mapping = _empty_mapping()
        parsed_from = "text"

    if not mapping["rails"]:
        seen = set()
        for m in NET_VOLT.finditer(text):
            net = m.group("net")
            if net in seen:
                continue
            seen.add(net)
            mapping["rails"].append({"net": net, "v": float(m.group("v")), "source": "text"})
    if mapping["t_amb_c"] is None:
        tm = TAMB.search(text)
        if tm:
            mapping["t_amb_c"] = float(tm.group(1))
    if not mapping["currents"]:
        seen_i = set()
        for m in CURRENT.finditer(text):
            net = m.group("net")
            if net in seen_i:
                continue
            seen_i.add(net)
            mapping["currents"].append({"net": net, "i": float(m.group("i")), "source": "text"})
    if not mapping["exemptions"]:
        mapping["exemptions"] = [m.group(1).strip() for m in EXEMPT.finditer(text)][:20]
    if not mapping["interfaces"]:
        mapping["interfaces"] = [m.group(1).strip() for m in IFACE.finditer(text)][:20]

    mapping["source_file"] = str(path)
    mapping["parsed_from"] = parsed_from
    if mapping.get("source_note"):
        mapping["note"] = mapping.pop("source_note")
    else:
        mapping["note"] = "只收录文件写明的量；空字段保持空，不接默认工况库"
    return mapping


def write_oc_and_task(out: Path, oc_path: Path | None, task_path: Path | None) -> dict[str, Any]:
    oc = parse_structured_file(oc_path) if oc_path else {}
    task = parse_structured_file(task_path) if task_path else {}
    write_json(out / "operating_conditions.json", oc)
    write_json(out / "design_task.json", task)
    return {"operating_conditions": oc, "design_task": task}
