#!/usr/bin/env python3
"""Compatibility shim — delegates to the canonical script."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    target = Path(__file__).resolve().parent / "gates/production/verify_data_recovery_gate.py"
    runpy.run_path(str(target), run_name="__main__")
