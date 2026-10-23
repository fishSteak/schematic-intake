# -*- coding: utf-8 -*-
"""Load product-center playbook helpers."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = ROOT / "rules" / "product_playbook.json"
INDEX = ROOT / "rules" / "product_playbook_index.json"


@lru_cache(maxsize=1)
def load_playbook() -> dict:
    return json.loads(PLAYBOOK.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_index() -> dict:
    if INDEX.exists():
        return json.loads(INDEX.read_text(encoding="utf-8"))
    pb = load_playbook()
    return {
        "version": pb.get("version"),
        "count": pb.get("item_count"),
        "rule_ids": pb.get("rule_ids"),
        "by_id": {it["rule_id"]: it for it in pb["items"]},
    }


def get_item(rule_id: str) -> dict | None:
    return load_index().get("by_id", {}).get(rule_id)


def all_rule_ids() -> list[str]:
    return list(load_playbook().get("rule_ids") or [])
