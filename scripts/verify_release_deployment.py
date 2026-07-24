#!/usr/bin/env python3
"""Stable entrypoint for release deployment and SDK full-flow verification."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    runpy.run_path(
        str(root / "scripts/tools/verify_release_deployment.py"), run_name="__main__"
    )
