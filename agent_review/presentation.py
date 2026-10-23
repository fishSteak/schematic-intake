"""Business wording and selection of the one explanatory extreme case."""
from __future__ import annotations

import math
import re
from typing import Any


def business_text(value: str) -> str:
    """Keep source pin identities internally, describe voltage spans to readers."""
    value = re.sub(
        r"V\(\s*([A-Za-z]+\d+)\.[\w]+\s*\)\s*[−－-]\s*V\(\s*\1\.[\w]+\s*\)",
        lambda m: f"V({m[1]}) 两端电压差", value,
    )
    return re.sub(
        r"\b([A-Za-z]+\d+)\.[\w]+\s*[−－-]\s*\1\.[\w]+\b",
        lambda m: f"V({m[1]}) 两端电压差", value,
    )


def business_copy(value: Any) -> Any:
    if isinstance(value, str):
        return business_text(value)
    if isinstance(value, list):
        return [business_copy(item) for item in value]
    if isinstance(value, dict):
        return {key: business_copy(item) for key, item in value.items()}
    return value


def primary_state_index(states: list[dict[str, Any]]) -> int | None:
    # Explicit primary, then the declared extreme candidate, then a confirmed
    # extreme. Never guess from numeric magnitudes or select an ambiguous tag.
    for roles in ({'primary'}, {'worst_candidate','best_candidate'}, {'worst','best'}):
        hits=[i for i,state in enumerate(states) if state.get('role') in roles]
        if hits:return hits[0] if len(hits)==1 else None
    return 0 if len(states)==1 else None
