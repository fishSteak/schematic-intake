# -*- coding: utf-8 -*-
"""Paths for product-center skill (isolated from v1.0 / 181 yaml)."""
from __future__ import annotations

from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]  # schematic-intake repo root


def product_root() -> Path:
    return PRODUCT_ROOT


def playbook_path() -> Path:
    # Prefer local 15-item rules; fall back to vendored snapshot if present
    p15 = PRODUCT_ROOT / "rules" / "xlsx_detail_15.json"
    if p15.is_file():
        return p15
    return PRODUCT_ROOT / "vendor" / "expert_html" / "product_playbook.json"


# Compatibility aliases used by copied build_expert_html.py
def expert_root() -> Path:
    return PRODUCT_ROOT


def allcheck_root() -> Path:
    """Not used for item list in product-center; kept for import compatibility."""
    return PRODUCT_ROOT
