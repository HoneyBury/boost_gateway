#!/usr/bin/env python3
"""Write Docker restart counts for the governed Compose containers."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
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
CONTAINER_ID_RE = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class ContainerSample:
    container_id: str
    cgroup_id: str
    restart_count: int


def read_cgroup_id(pid: int) -> str:
    lines = Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8").splitlines()
    paths: list[str] = []
    for line in lines:
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[2].startswith("/"):
            paths.append(fields[2])
            if fields[0] == "0":
                return fields[2]
    if len(set(paths)) == 1:
        return paths[0]
    raise ValueError(f"container PID {pid} has no unambiguous cgroup path")


def prometheus_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def collect_container_samples() -> tuple[dict[str, ContainerSample], list[str]]:
    samples: dict[str, ContainerSample] = {}
    missing: list[str] = []
    for container in CONTAINERS:
        completed = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Id}} {{.RestartCount}} {{.State.Pid}}",
                container,
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        fields = completed.stdout.split()
        if (
            completed.returncode
            or len(fields) != 3
            or CONTAINER_ID_RE.fullmatch(fields[0]) is None
            or not fields[1].isdecimal()
            or not fields[2].isdecimal()
            or int(fields[2]) <= 0
        ):
            missing.append(container)
            continue
        try:
            cgroup_id = read_cgroup_id(int(fields[2]))
        except (OSError, ValueError):
            missing.append(container)
            continue
        samples[container] = ContainerSample(fields[0], cgroup_id, int(fields[1]))
    return samples, missing


def render_metrics(
    samples: dict[str, ContainerSample], missing: list[str], timestamp: int
) -> str:
    lines = [
        "# HELP boost_gateway_container_restart_count Docker restart count by governed container.",
        "# TYPE boost_gateway_container_restart_count gauge",
    ]
    lines.extend(
        f'boost_gateway_container_restart_count{{container="{container}"}} '
        f"{sample.restart_count}"
        for container, sample in sorted(samples.items())
    )
    lines.extend(
        [
            "# HELP boost_gateway_container_info Governed container to Docker cgroup identity mapping.",
            "# TYPE boost_gateway_container_info gauge",
        ]
    )
    lines.extend(
        f'boost_gateway_container_info{{container="{container}",'
        f'container_id="{sample.container_id}",'
        f'id="{prometheus_label(sample.cgroup_id)}"}} 1'
        for container, sample in sorted(samples.items())
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
    samples, missing = collect_container_samples()
    atomic_write(args.output, render_metrics(samples, missing, int(time.time())))
    print(
        f"container restart metrics: {'PASS' if not missing else 'PARTIAL'} "
        f"({len(samples)}/{len(CONTAINERS)} containers)"
    )
    # A partial sample is itself observable through the success gauge. Keep the
    # timer healthy so it can recover automatically as containers appear.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
