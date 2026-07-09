#!/usr/bin/env python3
"""Read a section from the versioned GitHub Actions runner matrix config."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / ".github" / "runner-matrix.json"


def resolve_path(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--path", required=True, help="Dot path inside the JSON config.")
    args = parser.parse_args()

    config_path = args.config if args.config.is_absolute() else ROOT / args.config
    data = json.loads(config_path.read_text(encoding="utf-8"))
    value = resolve_path(data, args.path)
    print(json.dumps(value, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
