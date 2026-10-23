# -*- coding: utf-8 -*-
"""Datasheet hunt: MPN → search → PDF URL → download (standalone package)."""

from __future__ import annotations

__version__ = "1.0.0"

from .anysearch_bridge import anysearch_available, anysearch_datasheet_lookup

__all__ = [
    "__version__",
    "anysearch_available",
    "anysearch_datasheet_lookup",
]
