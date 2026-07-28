#!/usr/bin/env python3
"""Write fail-closed Redis persistence metrics for the governed container."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import time
from pathlib import Path

DEFAULT_OUTPUT = Path("/var/lib/boost-gateway-evidence/metrics/redis-persistence.prom")
EXPECTED_CONFIG = {
    "appendonly": "yes",
    "appendfsync": "everysec",
    "no-appendfsync-on-rewrite": "no",
    "aof-load-truncated": "no",
    "aof-use-rdb-preamble": "yes",
    "maxmemory-policy": "noeviction",
    "dir": "/data",
    "save": "300 100 60 10000",
    "stop-writes-on-bgsave-error": "yes",
}


class CollectionError(RuntimeError):
    """Raised when Redis persistence state cannot be collected exactly."""


def run_redis(arguments: list[str]) -> str:
    completed = subprocess.run(
        ["docker", "exec", "boost-redis", "redis-cli", "--raw", *arguments],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()[-1000:]
        raise CollectionError(f"redis-cli failed: {detail}")
    return completed.stdout


def parse_info(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key] = value.strip()
    return values


def parse_config(content: str) -> dict[str, str]:
    lines = content.splitlines()
    if len(lines) % 2:
        raise CollectionError("Redis CONFIG GET returned an odd number of lines")
    return {lines[index]: lines[index + 1] for index in range(0, len(lines), 2)}


def integer(values: dict[str, str], key: str, *, optional: bool = False) -> int | None:
    raw = values.get(key)
    if raw is None and optional:
        return None
    if raw is None or not raw.isdecimal():
        raise CollectionError(f"Redis INFO field is missing or invalid: {key}")
    return int(raw)


def status(values: dict[str, str], key: str) -> int:
    raw = values.get(key)
    if raw == "ok":
        return 1
    if raw == "err":
        return 0
    raise CollectionError(f"Redis INFO status is missing or invalid: {key}")


def collect() -> dict[str, int]:
    info = parse_info(run_redis(["INFO", "persistence"]))
    config = parse_config(run_redis(["CONFIG", "GET", *EXPECTED_CONFIG]))
    delayed = integer(info, "aof_delayed_fsync", optional=True)
    config_complete = set(config) == set(EXPECTED_CONFIG)
    config_valid = config_complete and all(
        config.get(key) == expected for key, expected in EXPECTED_CONFIG.items()
    )
    return {
        "aof_enabled": integer(info, "aof_enabled") or 0,
        "aof_delayed_fsync": delayed or 0,
        "aof_delayed_fsync_counter_present": int(delayed is not None),
        "aof_last_write_status": status(info, "aof_last_write_status"),
        "aof_last_bgrewrite_status": status(info, "aof_last_bgrewrite_status"),
        "rdb_last_bgsave_status": status(info, "rdb_last_bgsave_status"),
        "rdb_changes_since_last_save": integer(info, "rdb_changes_since_last_save")
        or 0,
        "effective_config_valid": int(config_valid),
    }


def render_metrics(values: dict[str, int] | None, timestamp: int) -> str:
    lines = [
        "# HELP boost_gateway_redis_persistence_collection_success Whether Redis persistence state was collected completely.",
        "# TYPE boost_gateway_redis_persistence_collection_success gauge",
        f"boost_gateway_redis_persistence_collection_success {1 if values is not None else 0}",
        "# HELP boost_gateway_redis_persistence_collection_timestamp_seconds Last Redis persistence collection attempt.",
        "# TYPE boost_gateway_redis_persistence_collection_timestamp_seconds gauge",
        f"boost_gateway_redis_persistence_collection_timestamp_seconds {timestamp}",
    ]
    if values is not None:
        metrics = {
            "boost_gateway_redis_aof_enabled": values["aof_enabled"],
            "boost_gateway_redis_aof_delayed_fsync_total": values["aof_delayed_fsync"],
            "boost_gateway_redis_aof_delayed_fsync_counter_present": values[
                "aof_delayed_fsync_counter_present"
            ],
            "boost_gateway_redis_aof_last_write_status": values[
                "aof_last_write_status"
            ],
            "boost_gateway_redis_aof_last_bgrewrite_status": values[
                "aof_last_bgrewrite_status"
            ],
            "boost_gateway_redis_rdb_last_bgsave_status": values[
                "rdb_last_bgsave_status"
            ],
            "boost_gateway_redis_rdb_changes_since_last_save": values[
                "rdb_changes_since_last_save"
            ],
            "boost_gateway_redis_persistence_effective_config_valid": values[
                "effective_config_valid"
            ],
        }
        for name, value in metrics.items():
            lines.extend(
                [
                    f"# TYPE {name} gauge",
                    f"{name} {value}",
                ]
            )
    lines.append("")
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
    try:
        values = collect()
        result = "PASS"
    except CollectionError as exc:
        values = None
        result = f"PARTIAL: {exc}"
    atomic_write(args.output, render_metrics(values, int(time.time())))
    print(f"Redis persistence metrics: {result}")
    # Failure is represented in the metric so the timer can recover automatically.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
