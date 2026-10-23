"""Step 5: BoardKG — merge tel/BOM/PDF/JLC with source tags and conflicts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from kernel.util import out_dir, write_json

GROUND_NETS = {"GND", "AGND", "DGND", "PGND"}

# CD4066 pin map (SOIC-14): switch pairs + control
_CD4066_SWITCHES = [
    {"a": "1", "b": "2", "ctrl": "13"},  # switch A
    {"a": "3", "b": "4", "ctrl": "5"},   # switch B
    {"a": "9", "b": "8", "ctrl": "6"},   # switch C
    {"a": "11", "b": "10", "ctrl": "12"},  # switch D
]
_CD4066_VDD = "14"
_CD4066_VSS = "7"


def _pdf_page_map(sch: dict[str, Any]) -> dict[str, int]:
    """pageName → PDF 1-based page from sch_index page_detail."""
    out: dict[str, int] = {}
    for row in sch.get("page_detail") or []:
        if not isinstance(row, dict):
            continue
        page = row.get("page")
        prev = str(row.get("preview") or "")
        m = re.search(r"图页\s*\n\s*(P\d+[A-Z0-9_]*)", prev)
        if not m:
            m = re.search(r"\b(P\d{2}_[A-Z0-9_]+)\b", prev)
        if m and isinstance(page, int):
            out[m.group(1)] = page
    return out


def _enrich_location(location: Any, page_map: dict[str, int], module_titles: dict[str, str]) -> Any:
    if not location:
        return location
    locs = location if isinstance(location, list) else [location]
    enriched = []
    for loc in locs:
        if not isinstance(loc, dict):
            enriched.append(loc)
            continue
        d = dict(loc)
        pn = str(d.get("pageName") or "")
        if pn and "pdf_page" not in d:
            d["pdf_page"] = page_map.get(pn)
        if pn and "module_title" not in d:
            d["module_title"] = module_titles.get(pn) or ""
        enriched.append(d)
    return enriched


def _module_titles_from_jlc(jlc: dict[str, Any] | None) -> dict[str, str]:
    """pageName → Chinese module title from annotations."""
    out: dict[str, str] = {}
    if not jlc:
        return out
    for ann in jlc.get("annotations") or []:
        if not isinstance(ann, dict):
            continue
        val = str(ann.get("value") or "")
        loc = ann.get("location") or {}
        pn = str(loc.get("pageName") or "")
        if not pn or not val:
            continue
        # skip designators / nets
        if re.match(r"^[A-Z]+\d+", val) or val.startswith("N_") or val.startswith("+"):
            continue
        if "网络" in val or "检测" in val or "接口" in val or "缓冲" in val or "开关" in val or "电源" in val or "指示" in val or "放大" in val or "整形" in val or "传感器" in val or "运放" in val or "总览" in val:
            out.setdefault(pn, val)
    return out


def _pin_net_map(comp: dict[str, Any]) -> dict[str, str]:
    m: dict[str, str] = {}
    for p in comp.get("pins") or []:
        if isinstance(p, dict):
            pin = str(p.get("pin") or p.get("name") or "")
            net = str(p.get("net") or "")
            if pin:
                # normalize U5.4 style
                if "." in pin:
                    pin = pin.split(".")[-1]
                m[pin] = net
    return m


def _close_rail(nets: dict[str, Any], net: str, value: float, source: dict[str, Any]) -> None:
    if not net or net in GROUND_NETS:
        return
    # 仅拒绝无电压语义的裸 netname；netname_voltage 为业务闭合源
    if isinstance(source, dict) and source.get("kind") == "netname":
        return
    nd = nets.setdefault(net, {"name": net, "pins": [], "refs": [], "kind": "signal", "rail": {}, "source": "inferred"})
    rail = nd.setdefault("rail", {})
    existing_src = rail.get("source")
    existing_kind = existing_src if isinstance(existing_src, str) else (existing_src or {}).get("kind")
    # do not overwrite oc/task closed with lower priority unless empty
    if rail.get("status") == "closed" and existing_kind in {"oc", "task", "oc_task"}:
        return
    if rail.get("status") == "closed" and rail.get("value") is not None:
        # regulator / zener / switch 可覆盖纯网名电压；网名不覆盖已有硬件配方
        if existing_kind and existing_kind != "netname_voltage":
            try:
                old = float(rail["value"])
                if source.get("kind") == "zener_clamp" and existing_kind == "zener_clamp":
                    rail["value"] = value
                    rail["status"] = "closed"
                    rail["source"] = source
                    return
                if abs(old - value) > 1e-6 and source.get("kind") == "zener_clamp":
                    rail["value"] = value
                    rail["status"] = "closed"
                    rail["source"] = source
                    return
            except (TypeError, ValueError):
                pass
            return
        # existing is netname_voltage: allow stronger source (regulator etc.) to overwrite
        if source.get("kind") == "netname_voltage":
            return
    rail["value"] = value
    rail["status"] = "closed"
    rail["source"] = source


def _apply_regulator_rails(components: dict[str, Any], nets: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    for ref, c in components.items():
        mpn = str(c.get("mpn") or "")
        m = re.match(r"^L78L?(\d{2})", mpn, re.I)
        if not m:
            continue
        vout = float(m.group(1))
        pmap = _pin_net_map(c)
        # L78Lxx TO-92: pin1=OUT pin2=GND pin3=IN (ST naming)
        out_net = pmap.get("1") or pmap.get("OUT") or pmap.get("VOUT")
        if out_net:
            _close_rail(
                nets,
                out_net,
                vout,
                {"kind": "regulator", "ref": ref, "mpn": mpn, "note": f"{mpn} Vout nom {vout}V"},
            )
            events.append({"ref": ref, "net": out_net, "v": vout, "kind": "regulator"})
    return events


def _apply_zener_clamps(components: dict[str, Any], nets: dict[str, Any], jlc: dict[str, Any] | None) -> list[dict[str, Any]]:
    from kernel.knowledge import apply_series_params, match_series

    events = []
    jlc_pins: dict[str, list] = {}
    if jlc:
        for inst in jlc.get("instances") or []:
            ref = inst.get("ref")
            if ref:
                jlc_pins[ref] = inst.get("pins") or []
    for ref, c in components.items():
        if (c.get("type") or "") != "diode" and not re.match(r"^D\d", ref, re.I):
            continue
        mpn = str(c.get("mpn") or "")
        tags = []
        # jlc tags on symbols
        series = match_series(mpn)
        params = apply_series_params(mpn, (c.get("pintable") or {}).get("params") or {})
        vz = params.get("vz_max_v")
        if vz is None and series:
            vz = series.get("vz_max_v")
        if vz is None and re.match(r"^1N52\d{2}", mpn, re.I):
            # default from series table only
            continue
        if vz is None:
            continue
        # find A/K nets from jlc pin names or tel pins
        anode = cathode = None
        for p in jlc_pins.get(ref) or []:
            if not isinstance(p, dict):
                continue
            pname = str(p.get("pinName") or "").upper()
            net = str(p.get("net") or "")
            if pname in {"A", "ANODE"}:
                anode = net
            if pname in {"K", "CATHODE"}:
                cathode = net
        if anode is None or cathode is None:
            pmap = _pin_net_map(c)
            # tel often pin1=A pin2=K for DO-35
            anode = anode or pmap.get("1")
            cathode = cathode or pmap.get("2")
        if cathode and anode in GROUND_NETS:
            nom = params.get("vz_nom_v")
            vmin = params.get("vz_min_v")
            note = (
                f"{ref}（{mpn}）阳极 {anode}、阴极 {cathode}，"
                f"V({cathode})=Vz_max={float(vz):g} V"
            )
            if nom is not None and vmin is not None:
                note += (
                    f"（标称 {float(nom):g} V，范围 {float(vmin):g}–{float(vz):g} V，取上限）"
                )
            _close_rail(
                nets,
                cathode,
                float(vz),
                {
                    "kind": "zener_clamp",
                    "ref": ref,
                    "mpn": mpn,
                    "anode": anode,
                    "cathode": cathode,
                    "vz_nom_v": None if nom is None else float(nom),
                    "vz_min_v": None if vmin is None else float(vmin),
                    "vz_max_v": float(vz),
                    "note": note,
                },
            )
            events.append({"ref": ref, "net": cathode, "v": float(vz), "kind": "zener_clamp"})
            # stash params on component
            pt = c.setdefault("pintable", {})
            pt_params = pt.setdefault("params", {})
            pt_params.update({k: v for k, v in params.items() if v is not None})
    return events


def _apply_always_on_switches(components: dict[str, Any], nets: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    for ref, c in components.items():
        mpn = str(c.get("mpn") or "")
        if not re.match(r"^CD4066", mpn, re.I):
            continue
        pmap = _pin_net_map(c)
        vdd_net = pmap.get(_CD4066_VDD)
        vdd = None
        if vdd_net:
            rail = (nets.get(vdd_net) or {}).get("rail") or {}
            if rail.get("status") == "closed" and rail.get("value") is not None:
                vdd = float(rail["value"])
            elif rail.get("candidate_v") is not None:
                vdd = float(rail["candidate_v"])
        if vdd is None:
            vdd = 12.0  # only for VIH threshold when VDD candidate; do not close VDD from this
        vih = 0.7 * vdd  # conservative vs CD4066 table
        for sw in _CD4066_SWITCHES:
            ctrl_net = pmap.get(sw["ctrl"])
            if not ctrl_net:
                continue
            ctrl_rail = (nets.get(ctrl_net) or {}).get("rail") or {}
            ctrl_v = None
            if ctrl_rail.get("status") == "closed" and ctrl_rail.get("value") is not None:
                ctrl_v = float(ctrl_rail["value"])
            elif ctrl_net.startswith("+") and ctrl_rail.get("candidate_v") is not None:
                # control tied to power flag +12V with regulator-closed sibling
                # require the named rail itself closed or same candidate as closed VDD
                if ctrl_net == vdd_net and (nets.get(vdd_net) or {}).get("rail", {}).get("status") == "closed":
                    ctrl_v = float((nets[vdd_net]["rail"]["value"]))
                elif (nets.get(ctrl_net) or {}).get("rail", {}).get("status") == "closed":
                    ctrl_v = float(ctrl_rail["value"])
                else:
                    # if control net is literally the same as closed VDD net name
                    for n2, nd2 in nets.items():
                        r2 = nd2.get("rail") or {}
                        if r2.get("status") == "closed" and n2 == ctrl_net:
                            ctrl_v = float(r2["value"])
            # Power flag +12V: if regulator closed +12V, treat control on +12V as ON
            if ctrl_v is None and ctrl_net.startswith("+"):
                closed_same = _closed_or_none(nets, ctrl_net)
                if closed_same is not None:
                    ctrl_v = closed_same
                else:
                    # candidate power flag with matching regulator-closed net
                    for n2, nd2 in nets.items():
                        r2 = nd2.get("rail") or {}
                        if n2 == ctrl_net and r2.get("status") == "closed":
                            ctrl_v = float(r2["value"])
            if ctrl_v is None:
                # special: control pin net equals a closed power net name
                cv = _closed_or_none(nets, ctrl_net)
                ctrl_v = cv
            if ctrl_v is None or ctrl_v < vih:
                continue
            na = pmap.get(sw["a"])
            nb = pmap.get(sw["b"])
            va = _closed_or_none(nets, na) if na else None
            vb = _closed_or_none(nets, nb) if nb else None
            if va is not None and nb and vb is None:
                _close_rail(
                    nets,
                    nb,
                    va,
                    {
                        "kind": "always_on_switch",
                        "ref": ref,
                        "mpn": mpn,
                        "via": na,
                        "ctrl": ctrl_net,
                        "note": f"{ref} switch ON ctrl={ctrl_net}",
                    },
                )
                events.append({"ref": ref, "net": nb, "v": va, "kind": "always_on_switch", "from": na})
            elif vb is not None and na and va is None:
                _close_rail(
                    nets,
                    na,
                    vb,
                    {
                        "kind": "always_on_switch",
                        "ref": ref,
                        "mpn": mpn,
                        "via": nb,
                        "ctrl": ctrl_net,
                        "note": f"{ref} switch ON ctrl={ctrl_net}",
                    },
                )
                events.append({"ref": ref, "net": na, "v": vb, "kind": "always_on_switch", "from": nb})
    return events


def _closed_or_none(nets: dict[str, Any], net: str | None) -> float | None:
    if not net or net in GROUND_NETS:
        return 0.0 if net in GROUND_NETS else None
    rail = (nets.get(net) or {}).get("rail") or {}
    if rail.get("status") == "closed" and rail.get("value") is not None:
        try:
            return float(rail["value"])
        except (TypeError, ValueError):
            return None
    return None


def _apply_series_ratings_to_components(components: dict[str, Any], board: Path) -> None:
    from kernel.knowledge import apply_series_params, parse_power_from_text, parse_vz_from_text

    lcsc_dir = out_dir(board) / "lcsc_cache"
    for ref, c in components.items():
        mpn = str(c.get("mpn") or "")
        pt = c.setdefault("pintable", {})
        params = apply_series_params(mpn, pt.get("params") or {})
        # LCSC product_name cache
        for cache_file in lcsc_dir.glob("C*.json") if lcsc_dir.is_dir() else []:
            try:
                doc = json.loads(cache_file.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if str(doc.get("product_model") or "") != mpn:
                continue
            name = str(doc.get("product_name") or "")
            pw = parse_power_from_text(name)
            if pw is not None and params.get("p_rated_w") is None:
                params["p_rated_w"] = pw
                params["p_rated_source"] = f"lcsc:{cache_file.stem}"
                params["extract_note"] = name
            vz = parse_vz_from_text(name)
            if vz is not None and params.get("vz_max_v") is None and "zener" in name.lower():
                params["vz_nom_v"] = vz
                params["vz_max_v"] = vz * 1.05  # loose if only nom in title
                params["vz_source"] = f"lcsc:{cache_file.stem}"
            break
        if params:
            pt["params"] = params
            if pt.get("status") in (None, "unknown"):
                pt["status"] = "knowledge" if params.get("p_rated_w") or params.get("vz_max_v") else pt.get("status")


def _prefix(ref: str) -> str:
    m = re.match(r"^([A-Za-z]+)", ref)
    return m.group(1) if m else ""


def _type_of(ref: str) -> str:
    return {
        "R": "resistor",
        "RN": "resistor",
        "C": "capacitor",
        "U": "ic",
        "D": "diode",
        "Q": "transistor",
        "F": "fuse",
        "CN": "connector",
        "H": "header",
        "LED": "led",
        "L": "inductor",
        "Y": "crystal",
        "X": "crystal",
        "J": "connector",
        "P": "connector",
        "TB": "connector",
    }.get(_prefix(ref), "unknown")


def _rail_voltage(name: str) -> dict[str, Any]:
    """电源网名含显式电压 → 业务规则直接闭合（无需工况/任务书签字）。

    例：VCC24V、+24V、VDD3.3V。裸名 VCC/VDD 不含数字则不闭合。
    """
    if name in GROUND_NETS:
        return {"value": 0.0, "status": "closed", "source": "ground", "candidate_v": 0.0}
    m = re.match(r"^\+(\d+(?:\.\d+)?)V$", name, re.I)
    if not m:
        m = re.match(r"^(?:VCC|VDD|VIN|VBAT)(\d+(?:\.\d+)?)V$", name, re.I)
    if m:
        v = float(m.group(1))
        return {
            "value": v,
            "status": "closed",
            "source": {
                "kind": "netname_voltage",
                "note": "业务规则：电源网名含电压即按该电压闭合，无需工况/任务书签字",
            },
            "candidate_v": v,
        }
    return {"value": None, "status": "unknown", "source": "none", "candidate_v": None}


def _collect_numeric(
    docs: list[tuple[dict[str, Any], str]],
    key: str,
    num_field: str,
) -> dict[str, list[tuple[float, str]]]:
    by_net: dict[str, list[tuple[float, str]]] = {}
    for doc, src in docs:
        for row in doc.get(key) or []:
            if not isinstance(row, dict):
                continue
            net = str(row.get("net") or "")
            raw = row.get(num_field)
            if not net or raw is None:
                continue
            try:
                fv = float(raw)
            except (TypeError, ValueError):
                continue
            by_net.setdefault(net, []).append((fv, src))
    return by_net


def _apply_written_rails(
    nets: dict[str, Any],
    oc: dict[str, Any],
    task: dict[str, Any],
    conflicts: list[dict[str, Any]],
) -> None:
    """工况/任务书写明的轨压才闭合；两源打架则保持未闭合。"""
    by_net = _collect_numeric([(oc, "oc"), (task, "task")], "rails", "v")
    for net, vals in by_net.items():
        if net not in nets:
            continue
        uniq = {round(v, 6) for v, _ in vals}
        rail = nets[net].setdefault("rail", {})
        if len(uniq) > 1:
            rail["value"] = None
            rail["status"] = "conflict"
            rail["source"] = "oc_task_conflict"
            rail["conflict_values"] = [{"v": v, "source": s} for v, s in vals]
            conflicts.append({"net": net, "field": "rail", "note": "工况与任务书轨压打架，不闭合"})
            continue
        rail["value"] = next(iter(uniq))
        rail["status"] = "closed"
        rail["source"] = vals[0][1]


def _apply_written_currents(
    nets: dict[str, Any],
    oc: dict[str, Any],
    task: dict[str, Any],
    conflicts: list[dict[str, Any]],
) -> None:
    by_net = _collect_numeric([(oc, "oc"), (task, "task")], "currents", "i")
    for net, vals in by_net.items():
        if net not in nets:
            continue
        uniq = {round(v, 6) for v, _ in vals}
        slot = nets[net].setdefault("i_work", {})
        if len(uniq) > 1:
            slot["value"] = None
            slot["status"] = "conflict"
            slot["source"] = "oc_task_conflict"
            conflicts.append({"net": net, "field": "i_work", "note": "工况与任务书电流打架，不闭合"})
            continue
        slot["value"] = next(iter(uniq))
        slot["status"] = "closed"
        slot["source"] = vals[0][1]


def _load_jlc(board: Path, graph: dict[str, Any]) -> dict[str, Any] | None:
    meta = graph.get("jlc") or {}
    path = meta.get("path")
    if not path:
        p = out_dir(board) / "jlc_project.json"
        if not p.is_file():
            return None
        path = str(p)
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def run(
    board: Path,
    materials: dict[str, Any],
    graph: dict[str, Any],
    ds: dict[str, Any],
    extract: dict[str, Any],
    sch: dict[str, Any],
    *, allow_agent: bool | None = None,
) -> dict[str, Any]:
    conflicts: list[dict[str, Any]] = []
    components: dict[str, Any] = {}
    jlc = _load_jlc(board, graph)
    jlc_by_ref: dict[str, Any] = {}
    jlc_symbol_refs: list[str] = []
    jlc_symbols: list[dict[str, Any]] = []
    if jlc:
        for inst in jlc.get("instances") or []:
            ref = inst.get("ref")
            if ref:
                jlc_by_ref[ref] = inst
        for sym in jlc.get("symbols") or []:
            r = sym.get("ref")
            if r:
                jlc_symbol_refs.append(r)
                jlc_symbols.append(
                    {
                        "ref": r,
                        "unit": sym.get("unit") or sym.get("unitId"),
                        "deviceId": sym.get("deviceId") or sym.get("device"),
                        "hierarchy": sym.get("hierarchy") or sym.get("path") or "",
                    }
                )

    for ref, gcomp in (graph.get("components") or {}).items():
        value_tel = gcomp.get("value_tel") or ""
        value_bom = gcomp.get("value_bom") or ""
        mpn = gcomp.get("mpn") or ""
        sources = {
            "value_tel": {"value": value_tel, "source": "tel"},
            "value_bom": {"value": value_bom, "source": "bom"},
            "mpn": {"value": mpn, "source": "bom"},
            "footprint": {"value": gcomp.get("footprint") or "", "source": "tel|bom"},
        }
        jinst = jlc_by_ref.get(ref)
        location = None
        pins = list(gcomp.get("pins") or [])
        if jinst:
            sources["value_jlc"] = {"value": jinst.get("value") or "", "source": "jlc"}
            sources["mpn_jlc"] = {"value": jinst.get("mpn") or "", "source": "jlc"}
            location = jinst.get("locations")
            # prefer tel pins for electrical; keep jlc pins as annotation
            jpins = jinst.get("pins") or []
            if jpins and not pins:
                pins = [
                    {"pin": (p.get("object") or "").split(".")[-1], "net": p.get("net"), "source": "jlc"}
                    for p in jpins
                ]
            if jinst.get("mpn") and mpn and jinst.get("mpn") != mpn:
                conflicts.append(
                    {
                        "ref": ref,
                        "field": "mpn",
                        "tel_bom": mpn,
                        "jlc": jinst.get("mpn"),
                    }
                )
            if jinst.get("value") and value_tel and jinst.get("value") != value_tel:
                conflicts.append(
                    {
                        "ref": ref,
                        "field": "value",
                        "tel": value_tel,
                        "jlc": jinst.get("value"),
                    }
                )

        components[ref] = {
            "ref": ref,
            "type": _type_of(ref),
            "value": value_bom or value_tel or (jinst or {}).get("value") or "",
            "mpn": mpn or (jinst or {}).get("mpn") or "",
            "footprint": gcomp.get("footprint") or (jinst or {}).get("package") or "",
            "manufacturer": gcomp.get("manufacturer") or (jinst or {}).get("manufacturer") or "",
            "in_tel": bool(gcomp.get("in_tel")),
            "in_bom": bool(gcomp.get("in_bom")),
            "in_jlc": jinst is not None,
            "pins": pins,
            "location": location,
            "sources": sources,
            "status": "ok",
        }

    page_map = _pdf_page_map(sch)
    module_titles = _module_titles_from_jlc(jlc)

    # JLC-only refs (present in symbols/instances but not tel/bom)
    for ref, jinst in jlc_by_ref.items():
        if ref in components:
            continue
        components[ref] = {
            "ref": ref,
            "type": _type_of(ref),
            "value": jinst.get("value") or "",
            "mpn": jinst.get("mpn") or "",
            "footprint": jinst.get("package") or "",
            "manufacturer": jinst.get("manufacturer") or "",
            "in_tel": False,
            "in_bom": False,
            "in_jlc": True,
            "pins": jinst.get("pins") or [],
            "location": jinst.get("locations"),
            "sources": {"origin": {"value": "jlc_only", "source": "jlc"}},
            "status": "jlc_only",
        }
        conflicts.append({"ref": ref, "field": "presence", "note": "仅出现在嘉立创工程，不在网表/BOM"})

    nets: dict[str, Any] = {}
    for name, nd in (graph.get("nets") or {}).items():
        nets[name] = {
            "name": name,
            "pins": nd.get("pins") or [],
            "refs": nd.get("refs") or [],
            "kind": "ground" if name in GROUND_NETS else ("power" if name.startswith("+") or name.startswith("V") else "signal"),
            "rail": _rail_voltage(name),
            "source": "tel",
        }

    if jlc and jlc.get("connectivity"):
        conn = jlc["connectivity"]
        raw_networks = conn.get("networks") or []
        # Vendor shape: list[{names:[...], pins:[...], locations:[...]}]
        if isinstance(raw_networks, dict):
            iterable = [
                {"names": [k], "pins": (v.get("pins") if isinstance(v, dict) else v), "raw": v}
                for k, v in raw_networks.items()
            ]
        else:
            iterable = list(raw_networks)
        for entry in iterable:
            if not isinstance(entry, dict):
                continue
            names = entry.get("names") or []
            net_name = names[0] if names else None
            if not net_name:
                continue
            if net_name not in nets:
                nets[net_name] = {
                    "name": net_name,
                    "pins": [],
                    "refs": [],
                    "kind": "signal",
                    "rail": _rail_voltage(net_name),
                    "source": "jlc",
                }
            nets[net_name]["jlc_members"] = entry
            nets[net_name]["jlc_pins"] = entry.get("pins") or []
            nets[net_name]["jlc_alias_names"] = names

    annotations = (jlc or {}).get("annotations") or []
    connection_issues = (jlc or {}).get("connectionIssues") or []

    def _sidecar(name: str) -> dict[str, Any]:
        p = out_dir(board) / name
        if not p.is_file():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}

    oc = _sidecar("operating_conditions.json")
    task = _sidecar("design_task.json")
    _apply_written_rails(nets, oc, task, conflicts)
    _apply_written_currents(nets, oc, task, conflicts)

    # series ratings → pintable params (before topology so Vz available)
    _apply_series_ratings_to_components(components, board)

    topology_events: list[dict[str, Any]] = []
    topology_events.extend(_apply_regulator_rails(components, nets))
    topology_events.extend(_apply_zener_clamps(components, nets, jlc))
    # pass-2: always-on switches may need regulator-closed VDD first
    for _ in range(2):
        topology_events.extend(_apply_always_on_switches(components, nets))

    # enrich locations with pdf_page + module_title
    for ref, comp in components.items():
        if comp.get("location"):
            comp["location"] = _enrich_location(comp["location"], page_map, module_titles)

    pt_by_mpn: dict[str, Any] = {}
    for e in extract.get("entries") or []:
        mpn = e.get("mpn")
        if mpn:
            pt_by_mpn[mpn] = e
    for ref, comp in components.items():
        mpn = comp.get("mpn") or ""
        pt = pt_by_mpn.get(mpn)
        if not pt:
            # keep knowledge-only pintable
            continue
        existing = comp.get("pintable") or {}
        merged_params = dict(existing.get("params") or {})
        merged_params.update(pt.get("params") or {})
        from kernel.knowledge import apply_series_params

        merged_params = apply_series_params(mpn, merged_params)
        rec = pt.get("recommended") or (pt.get("params") or {}).get("recommended") or existing.get("recommended") or {}
        abs_m = pt.get("abs_max") or (pt.get("params") or {}).get("abs_max") or existing.get("abs_max") or {}
        if isinstance(merged_params.get("recommended"), dict) and not rec:
            rec = merged_params["recommended"]
        if isinstance(merged_params.get("abs_max"), dict) and not abs_m:
            abs_m = merged_params["abs_max"]
        comp["pintable"] = {
            "status": pt.get("status") or existing.get("status"),
            "path": pt.get("path") or existing.get("path"),
            "pdf": pt.get("pdf") or existing.get("pdf"),
            "params": merged_params,
            "recommended": rec if isinstance(rec, dict) else {},
            "abs_max": abs_m if isinstance(abs_m, dict) else {},
            "hit_families": pt.get("hit_families") or existing.get("hit_families") or [],
            "pages": pt.get("pages") or existing.get("pages"),
        }
        from kernel.rated_fields import normalize_pintable

        normalize_pintable(comp["pintable"])

    from kernel.rated_hunt import apply_rated_fields

    hunt_stats = apply_rated_fields(components, board, allow_agent=allow_agent)

    kg = {
        "board": materials.get("board_name"),
        "product": materials.get("product"),
        "rule_ids": materials.get("rule_ids"),
        "stats": {
            "components": len(components),
            "nets": len(nets),
            "conflicts": len(conflicts),
            "jlc_annotations": len(annotations),
            "jlc_connection_issues": len(connection_issues),
            "jlc_symbol_count": len(jlc_symbol_refs),
            "sch_refs_found": sch.get("refs_found"),
            "datasheet_miss": len(ds.get("miss") or []),
            "datasheet_hit": len(ds.get("hit") or []),
            "pintable_entries": extract.get("count"),
            "pintable_computed": extract.get("computed"),
            "topology_closes": len(topology_events),
            "cap_vr_hunt": hunt_stats,
        },
        "components": components,
        "nets": nets,
        "conflicts": conflicts,
        "annotations": annotations,
        "jlc_connection_issues": connection_issues,
        "jlc_symbols": jlc_symbols,
        "jlc_symbols_note": "P42-A01 不可仅用 instances 分组数证明唯一性，须结合 symbols 设备/单元身份",
        "operating_conditions": oc,
        "design_task": task,
        "topology_events": topology_events,
        "page_map": page_map,
        "sources": {
            "tel": materials.get("netlist"),
            "bom": materials.get("bom"),
            "pdf": materials.get("schematic_pdf"),
            "eprj2": materials.get("eprj2"),
            "operating_conditions": materials.get("operating_conditions"),
            "design_task_book": materials.get("design_task_book"),
            "jlc_json": str(out_dir(board) / "jlc_project.json") if jlc else None,
        },
    }
    write_json(out_dir(board) / "board_kg.json", kg)
    return kg
