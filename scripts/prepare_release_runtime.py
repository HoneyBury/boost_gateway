#!/usr/bin/env python3
"""Stable entrypoint for immutable release runtime staging."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    target = repo_root / "scripts/tools/prepare_release_runtime.py"
    runpy.run_path(str(target), run_name="__main__")
