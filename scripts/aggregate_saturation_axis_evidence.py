#!/usr/bin/env python3
"""Compatibility shim for the canonical saturation-axis aggregate gate."""

from __future__ import annotations

import runpy
from pathlib import Path


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "gates" / "release" / "aggregate_saturation_axis_evidence.py"
    runpy.run_path(str(target), run_name="__main__")
