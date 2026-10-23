# -*- coding: utf-8 -*-
"""组装交付 JSON。文件只写在本程序目录。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from solve_prog.connection import connection_card
from solve_prog.gates import evaluate
from solve_prog.mna import expand_resistors, solve_state
from solve_prog.netclass import components
from solve_prog.spice import compare
from solve_prog.stimulus import limit_w, name_stimulus

OUT_DIR = Path(__file__).resolve().parent / "out"


def run(kg: dict[str, Any], rule_id: str, ref: str) -> dict[str, Any]:
    card = connection_card(kg, ref)
    stimulus = name_stimulus(kg, rule_id, ref)
    if stimulus is None:
        payload = {
            "rule_id": rule_id,
            "object": ref,
            "connection": card,
            "deliverable": False,
            "source": "formula",
            "conclusion": "未过门禁",
            "gates": [{"id": "G7", "pass": False, "detail": "没有这种检查项的点名方式"}],
            "objects": {},
        }
        return payload
    extra = []
    for state in stimulus["states"]:
        for source in state["sources"]:
            extra.append(str(source["plus"]))
            extra.append(str(source["minus"]))
    resistors = expand_resistors(stimulus["group"], kg, extra)
    solved = [solve_state(resistors, state) for state in stimulus["states"]]
    gates = evaluate(kg, stimulus, resistors, solved)
    spice = compare(resistors, solved)
    passed = all(item["pass"] for item in gates) and spice["matched"]
    objects: dict[str, Any] = {}
    conclusion = "未过门禁"
    if passed:
        words = []
        for obj in stimulus["objects"]:
            limit = limit_w(rule_id, components(kg).get(obj) or {})
            if limit is None:
                objects[obj] = {"conclusion": "未过门禁", "detail": "没有限额"}
                words.append("未过门禁")
                continue
            over = False
            for state in solved:
                if not state["participates"]:
                    continue
                if float(state["powers_w"].get(obj, 0.0)) > limit:
                    over = True
            objects[obj] = {"conclusion": "否" if over else "是", "limit_w": limit}
            words.append(objects[obj]["conclusion"])
        if words and all(word == "是" for word in words):
            conclusion = "是"
        elif any(word == "否" for word in words):
            conclusion = "否"
        else:
            conclusion = "未过门禁"
            passed = False
    return {
        "rule_id": rule_id,
        "object": ref,
        "connection": card,
        "stimulus": {
            "kind": stimulus["kind"],
            "group_id": stimulus["group_id"],
            "objects": stimulus["objects"],
            "states": [
                {"id": state["id"], "name": state["name"], "participates": state["participates"]}
                for state in stimulus["states"]
            ],
            "model": stimulus["model"],
        },
        "states": [
            {
                "id": state["state_id"],
                "name": state["name"],
                "participates": state["participates"],
                "dropped": state["dropped"],
                "powers_w": state["powers_w"],
                "steps": state["steps"],
            }
            for state in solved
        ],
        "gates": gates,
        "spice": spice,
        "source": spice["source"] if passed else "formula",
        "deliverable": passed,
        "conclusion": conclusion,
        "objects": objects if passed else {},
    }


def write_delivery(payload: dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "delivery.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
