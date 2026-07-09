#!/usr/bin/env python3
"""Compatibility shim for the canonical P3/P4 release readiness gate."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    target = Path(__file__).resolve().parent / "gates" / "release" / "check_p3_p4_release_readiness.py"
    runpy.run_path(str(target), run_name="__main__")
