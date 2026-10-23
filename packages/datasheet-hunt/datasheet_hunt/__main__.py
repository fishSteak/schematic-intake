# -*- coding: utf-8 -*-
"""python -m datasheet_hunt --out-dir <dir> --mpn LM393DR"""

from __future__ import annotations

import sys

from ._fetch_core import main

if __name__ == "__main__":
    raise SystemExit(main())
