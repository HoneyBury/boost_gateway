#!/usr/bin/env python3
"""Write Docker restart counts for the governed Compose containers."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import time
from pathlib import Path


CONTAINERS = (
    "boost-gateway",
    "boost-login-backend",
    "boost-room-backend",
    "boost-battle-backend",
    "boost-matchmaking-backend",
    "boost-leaderboard-backend",
    "boost-redis",
    "boost-redis-exporter",
    "boost-node-exporter",
    "boost-cadvisor",
    "boost-prometheus",
    "boost-alertmanager",
    "boost-grafana",
)
DEFAULT_OUTPUT = Path(
    "/var/lib/boost-gateway-evidence/metrics/container-restarts.prom"
)


def collect_restart_counts() -> tuple[dict[str, int], list[str]]:
    counts: dict[str, int] = {}
    missing: list[str] = []
    for container in CONTAINERS:
        completed = subprocess.run(
            ["docker", "inspect", "--format", "{{.RestartCount}}", container],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        value = completed.stdout.strip()
        if completed.returncode or not value.isdecimal():
            missing.append(container)
            continue
        counts[container] = int(value)
    return counts, missing


def render_metrics(counts: dict[str, int], missing: list[str], timestamp: int) -> str:
    lines = [
        "# HELP boost_gateway_container_restart_count Docker restart count by governed container.",
        "# TYPE boost_gateway_container_restart_count gauge",
    ]
    lines.extend(
        f'boost_gateway_container_restart_count{{container="{container}"}} {count}'
        for container, count in sorted(counts.items())
    )
    lines.extend(
        [
            "# HELP boost_gateway_container_restart_collection_success Whether every governed container was inspected.",
            "# TYPE boost_gateway_container_restart_collection_success gauge",
            f"boost_gateway_container_restart_collection_success {0 if missing else 1}",
            "# HELP boost_gateway_container_restart_collection_timestamp_seconds Last successful collector execution time.",
            "# TYPE boost_gateway_container_restart_collection_timestamp_seconds gauge",
            f"boost_gateway_container_restart_collection_timestamp_seconds {timestamp}",
            "",
        ]
    )
    return "\n".join(lines)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    counts, missing = collect_restart_counts()
    atomic_write(args.output, render_metrics(counts, missing, int(time.time())))
    print(
        f"container restart metrics: {'PASS' if not missing else 'PARTIAL'} "
        f"({len(counts)}/{len(CONTAINERS)} containers)"
    )
    # A partial sample is itself observable through the success gauge. Keep the
    # timer healthy so it can recover automatically as containers appear.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
