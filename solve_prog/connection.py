# -*- coding: utf-8 -*-
"""任意器件的连接卡。不写情况、功耗、是/否。"""

from __future__ import annotations

from typing import Any

from solve_prog.netclass import attachments, components, net_kind, nets, pin_pairs, supply_volts


def connection_card(kg: dict[str, Any], ref: str) -> dict[str, Any]:
    comp = components(kg).get(ref) or {}
    net_table = nets(kg)
    pins = []
    seen: list[str] = []
    for pin, name in pin_pairs(comp):
        pins.append({"pin": pin, "net": name})
        if name and name not in seen:
            seen.append(name)
    net_cards = []
    for name in seen:
        kind = net_kind(name, net_table.get(name) or {})
        card: dict[str, Any] = {
            "net": name,
            "kind": kind,
            "pins": attachments(name, kg),
            "voltage_in_file": False,
        }
        if kind == "supply":
            card["voltage_v"] = supply_volts(net_table.get(name) or {})
            card["voltage_in_file"] = card["voltage_v"] is not None
        elif kind == "ground":
            card["voltage_v"] = 0.0
            card["voltage_in_file"] = True
        else:
            card["voltage_v"] = None
        net_cards.append(card)
    return {"object": ref, "pins": pins, "nets": net_cards}
