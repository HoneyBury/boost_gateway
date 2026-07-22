#!/usr/bin/env python3
"""Compatibility entrypoint for the canonical JWKS rotation gate."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    target = Path(__file__).resolve().parent / "gates/security/verify_jwks_rotation.py"
    runpy.run_path(str(target), run_name="__main__")
