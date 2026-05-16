#!/usr/bin/env python3
"""Validate reliability-matrix evidence references against local files."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REQUIRED_SCENARIOS = {
    "backend_timeout_recovery",
    "circuit_breaker_half_open",
    "readiness_heartbeat_recovery",
    "writebehind_drain_failure",
    "proto_transport_contract",
    "stability_soak_gate",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=Path("docs/reliability-matrix.md"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    matrix = args.matrix if args.matrix.is_absolute() else root / args.matrix
    if not matrix.is_file():
        print(f"missing matrix: {matrix}", file=sys.stderr)
        return 1

    content = matrix.read_text(encoding="utf-8")
    missing = [name for name in sorted(REQUIRED_SCENARIOS) if name not in content]
    paths = re.findall(r"`([^`]+\.(?:py|ps1|cpp|h|md|json))`", content)
    broken: list[str] = []
    for path_text in paths:
        if "*" in path_text or "<" in path_text:
            continue
        path = root / Path(path_text.replace("\\", "/"))
        if not path.exists():
            broken.append(path_text)

    if missing or broken:
        if missing:
            print("missing scenarios: " + ", ".join(missing), file=sys.stderr)
        if broken:
            print("broken evidence paths: " + ", ".join(sorted(set(broken))), file=sys.stderr)
        return 1

    print(f"validated reliability matrix: {matrix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
