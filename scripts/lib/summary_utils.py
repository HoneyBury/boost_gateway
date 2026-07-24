#!/usr/bin/env python3
"""Atomic JSON summary helpers shared by validation entrypoints."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_json_summary(path: Path, payload: dict[str, Any]) -> None:
    """Write a summary atomically so cancellation cannot leave partial JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, path)
