#!/usr/bin/env python3
"""Compatibility shim for the operations host admission entrypoint."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    target = root / "scripts/gates/infrastructure/check_operations_host.py"
    runpy.run_path(str(target), run_name="__main__")
