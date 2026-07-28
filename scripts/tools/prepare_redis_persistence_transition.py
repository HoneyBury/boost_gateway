#!/usr/bin/env python3
"""Freeze writes and create a verified RDB checkpoint before Redis mode changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

WRITE_SERVICES = (
    "gateway",
    "login-backend",
    "room-backend",
    "battle-backend",
    "matchmaking-backend",
    "leaderboard-backend",
)
WRITE_CONTAINERS = (
    "boost-gateway",
    "boost-login-backend",
    "boost-room-backend",
    "boost-battle-backend",
    "boost-matchmaking-backend",
    "boost-leaderboard-backend",
)
MODES = {"rdb_only", "aof_everysec_rdb"}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SAFE_COMPONENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}\Z")


class TransitionError(RuntimeError):
    """Raised when a data-compatible persistence transition cannot be proven."""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def run(command: list[str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    if timeout_seconds <= 0:
        raise TransitionError("transition deadline expired")
    completed = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=max(1.0, timeout_seconds),
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()[-2000:]
        raise TransitionError(f"command failed ({command[0]}): {detail}")
    return completed


def redis(arguments: list[str], timeout_seconds: float) -> str:
    return run(
        ["docker", "exec", "boost-redis", "redis-cli", "--raw", *arguments],
        timeout_seconds,
    ).stdout


def parse_pairs(content: str, description: str) -> dict[str, str]:
    lines = content.splitlines()
    if len(lines) % 2:
        raise TransitionError(f"{description} returned an odd number of lines")
    return {lines[index]: lines[index + 1] for index in range(0, len(lines), 2)}


def parse_info(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in content.splitlines():
        if ":" not in raw or raw.startswith("#"):
            continue
        key, value = raw.split(":", 1)
        values[key] = value.strip()
    return values


def actual_mode(timeout_seconds: float) -> tuple[str, dict[str, str]]:
    config = parse_pairs(
        redis(["CONFIG", "GET", "appendonly", "appendfsync"], timeout_seconds),
        "Redis CONFIG GET",
    )
    if config.get("appendonly") == "no":
        return "rdb_only", config
    if config.get("appendonly") == "yes" and config.get("appendfsync") == "everysec":
        return "aof_everysec_rdb", config
    return "unknown", config


def active_volume(timeout_seconds: float) -> dict[str, Any]:
    output = run(
        ["docker", "inspect", "--format", "{{json .Mounts}}", "boost-redis"],
        timeout_seconds,
    ).stdout
    try:
        mounts = json.loads(output)
    except json.JSONDecodeError as exc:
        raise TransitionError(f"cannot decode Redis mounts: {exc}") from exc
    matches = [
        item
        for item in mounts
        if isinstance(item, dict)
        and item.get("Type") == "volume"
        and item.get("Destination") == "/data"
        and item.get("RW") is True
    ]
    if len(matches) != 1 or not str(matches[0].get("Name", "")):
        raise TransitionError("Redis has no unique read-write /data volume")
    identity = {
        "type": "volume",
        "name": str(matches[0]["Name"]),
        "driver": str(matches[0].get("Driver", "")),
        "destination": "/data",
        "read_write": True,
    }
    identity["identity_sha256"] = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return identity


def freeze_writes(compose_file: Path, timeout_seconds: float) -> None:
    run(
        [
            "docker",
            "compose",
            "-f",
            str(compose_file),
            "stop",
            "--timeout",
            "30",
            *WRITE_SERVICES,
        ],
        timeout_seconds,
    )
    for service, container in zip(WRITE_SERVICES, WRITE_CONTAINERS, strict=True):
        running = run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Running}}",
                container,
            ],
            timeout_seconds,
        ).stdout.strip()
        if running != "false":
            raise TransitionError(
                f"write-capable container is still running: {service}"
            )


def wait_for_bgsave(deadline: float) -> dict[str, str]:
    while time.monotonic() < deadline:
        info = parse_info(redis(["INFO", "persistence"], deadline - time.monotonic()))
        if info.get("rdb_bgsave_in_progress") == "0":
            if info.get("rdb_last_bgsave_status") != "ok":
                raise TransitionError("Redis reports failed BGSAVE")
            if info.get("rdb_changes_since_last_save") != "0":
                raise TransitionError("Redis retained changes after BGSAVE")
            return info
        time.sleep(0.1)
    raise TransitionError("BGSAVE did not complete before the transition deadline")


def checkpoint(timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    before_lastsave = int(redis(["LASTSAVE"], deadline - time.monotonic()).strip())
    while int(time.time()) <= before_lastsave:
        if time.monotonic() >= deadline:
            raise TransitionError("cannot establish a distinct BGSAVE timestamp")
        time.sleep(0.05)
    response = redis(["BGSAVE"], deadline - time.monotonic()).strip()
    if response != "Background saving started":
        raise TransitionError(f"Redis did not start BGSAVE: {response}")
    info = wait_for_bgsave(deadline)
    after_lastsave = int(redis(["LASTSAVE"], deadline - time.monotonic()).strip())
    if after_lastsave <= before_lastsave:
        raise TransitionError("fresh BGSAVE did not advance LASTSAVE")
    check = run(
        [
            "docker",
            "exec",
            "--user",
            "redis",
            "boost-redis",
            "redis-check-rdb",
            "/data/dump.rdb",
        ],
        deadline - time.monotonic(),
    )
    if "RDB looks OK!" not in check.stdout:
        raise TransitionError("redis-check-rdb did not validate dump.rdb")
    digest_line = run(
        [
            "docker",
            "exec",
            "--user",
            "redis",
            "boost-redis",
            "sha256sum",
            "/data/dump.rdb",
        ],
        deadline - time.monotonic(),
    ).stdout.split()
    if not digest_line or SHA256_RE.fullmatch(digest_line[0]) is None:
        raise TransitionError("dump.rdb SHA-256 is missing or invalid")
    return {
        "lastsave_before": before_lastsave,
        "lastsave_after": after_lastsave,
        "rdb_changes_since_last_save": int(info["rdb_changes_since_last_save"]),
        "rdb_last_bgsave_status": info["rdb_last_bgsave_status"],
        "rdb_sha256": digest_line[0],
        "redis_check_rdb": True,
    }


def prepare_aof_directory(
    source_mode: str,
    target_mode: str,
    transaction_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    if SAFE_COMPONENT_RE.fullmatch(transaction_id) is None:
        raise TransitionError("persistence transaction identity is invalid")
    if source_mode == "aof_everysec_rdb" and target_mode == "rdb_only":
        completed = run(
            [
                "docker",
                "exec",
                "--user",
                "redis",
                "boost-redis",
                "sh",
                "-eu",
                "-c",
                (
                    "test -d /data/appendonlydir; "
                    "test -s /data/appendonlydir/appendonly.aof.manifest; "
                    "sha256sum /data/appendonlydir/appendonly.aof.manifest; "
                    "chmod 0755 /data/appendonlydir; "
                    'test "$(stat -c %a /data/appendonlydir)" = 755'
                ),
            ],
            timeout_seconds,
        )
        digest = completed.stdout.split()
        if not digest or SHA256_RE.fullmatch(digest[0]) is None:
            raise TransitionError("AOF manifest SHA-256 is missing or invalid")
        return {
            "action": "entrypoint-readable",
            "path": "/data/appendonlydir",
            "mode": "0755",
            "manifest_sha256": digest[0],
            "files_deleted": False,
        }

    if source_mode == "rdb_only" and target_mode == "aof_everysec_rdb":
        completed = run(
            [
                "docker",
                "exec",
                "--user",
                "redis",
                "boost-redis",
                "sh",
                "-eu",
                "-c",
                (
                    "if [ ! -e /data/appendonlydir ]; then "
                    "printf 'absent\\n'; exit 0; fi; "
                    "test -d /data/appendonlydir; "
                    "test -s /data/appendonlydir/appendonly.aof.manifest; "
                    "sha256sum /data/appendonlydir/appendonly.aof.manifest"
                ),
            ],
            timeout_seconds,
        )
        fields = completed.stdout.split()
        if fields == ["absent"]:
            return {
                "action": "absent",
                "path": "/data/appendonlydir",
                "files_deleted": False,
            }
        if not fields or SHA256_RE.fullmatch(fields[0]) is None:
            raise TransitionError("stale AOF manifest SHA-256 is missing or invalid")
        raise TransitionError(
            "RDB-to-AOF transition found an existing AOF manifest; "
            f"manual equivalence or merge recovery is required: {fields[0]}"
        )
    raise TransitionError("unsupported AOF directory transition")


def seed_aof_from_rdb(timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds

    def remaining() -> float:
        value = deadline - time.monotonic()
        if value <= 0:
            raise TransitionError("AOF seed deadline expired")
        return value

    before_key_count = int(redis(["DBSIZE"], remaining()).strip())
    for directive, value in (
        ("appendfsync", "everysec"),
        ("aof-use-rdb-preamble", "yes"),
    ):
        response = redis(["CONFIG", "SET", directive, value], remaining()).strip()
        if response != "OK":
            raise TransitionError(f"Redis rejected AOF seed directive: {directive}")
    response = redis(["CONFIG", "SET", "appendonly", "yes"], remaining()).strip()
    if response != "OK":
        raise TransitionError("Redis rejected appendonly activation")

    persistence: dict[str, str] = {}
    while time.monotonic() < deadline:
        persistence = parse_info(redis(["INFO", "persistence"], remaining()))
        if (
            persistence.get("aof_enabled") == "1"
            and persistence.get("aof_rewrite_in_progress") == "0"
            and persistence.get("aof_last_bgrewrite_status") == "ok"
            and int(persistence.get("aof_rewrites", "0")) >= 1
            and int(persistence.get("aof_current_size", "0")) > 0
            and int(persistence.get("aof_base_size", "0")) > 0
        ):
            break
        time.sleep(0.1)
    else:
        raise TransitionError("Redis did not complete the AOF seed rewrite")

    after_key_count = int(redis(["DBSIZE"], remaining()).strip())
    if after_key_count != before_key_count:
        raise TransitionError("Redis key count changed while seeding AOF")
    completed = run(
        [
            "docker",
            "exec",
            "--user",
            "redis",
            "boost-redis",
            "sh",
            "-eu",
            "-c",
            (
                "test -s /data/appendonlydir/appendonly.aof.manifest; "
                "sha256sum /data/appendonlydir/appendonly.aof.manifest"
            ),
        ],
        remaining(),
    )
    digest = completed.stdout.split()
    if not digest or SHA256_RE.fullmatch(digest[0]) is None:
        raise TransitionError("seeded AOF manifest SHA-256 is missing or invalid")
    observed_mode, config = actual_mode(remaining())
    if observed_mode != "aof_everysec_rdb":
        raise TransitionError("seeded Redis runtime did not enter the target AOF mode")
    return {
        "method": "runtime-config-set-and-rewrite",
        "source": "active-rdb-keyspace",
        "key_count_before": before_key_count,
        "key_count_after": after_key_count,
        "manifest_sha256": digest[0],
        "aof_current_size": int(persistence["aof_current_size"]),
        "aof_base_size": int(persistence["aof_base_size"]),
        "aof_rewrites": int(persistence["aof_rewrites"]),
        "effective_config": config,
        "files_deleted": False,
    }


def validate_existing_aof(timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds

    def remaining() -> float:
        value = deadline - time.monotonic()
        if value <= 0:
            raise TransitionError("existing AOF validation deadline expired")
        return value

    before_key_count = int(redis(["DBSIZE"], remaining()).strip())
    persistence = parse_info(redis(["INFO", "persistence"], remaining()))
    observed_mode, config = actual_mode(remaining())
    completed = run(
        [
            "docker",
            "exec",
            "--user",
            "redis",
            "boost-redis",
            "sh",
            "-eu",
            "-c",
            (
                "test -s /data/appendonlydir/appendonly.aof.manifest; "
                "sha256sum /data/appendonlydir/appendonly.aof.manifest"
            ),
        ],
        remaining(),
    )
    digest = completed.stdout.split()
    after_key_count = int(redis(["DBSIZE"], remaining()).strip())
    if (
        observed_mode != "aof_everysec_rdb"
        or persistence.get("aof_enabled") != "1"
        or persistence.get("aof_last_write_status") != "ok"
        or int(persistence.get("aof_current_size", "0")) <= 0
        or not digest
        or SHA256_RE.fullmatch(digest[0]) is None
        or after_key_count != before_key_count
    ):
        raise TransitionError("existing target AOF runtime is incomplete")
    return {
        "method": "runtime-already-target-validated",
        "source": "active-aof-keyspace",
        "key_count_before": before_key_count,
        "key_count_after": after_key_count,
        "manifest_sha256": digest[0],
        "aof_current_size": int(persistence["aof_current_size"]),
        "effective_config": config,
        "files_deleted": False,
    }


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise TransitionError(f"create-only transition summary already exists: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def execute(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + args.timeout_seconds

    def remaining() -> float:
        value = deadline - time.monotonic()
        if value <= 0:
            raise TransitionError("transition deadline expired")
        return value

    if args.source_mode not in MODES or args.target_mode not in MODES:
        raise TransitionError("source and target persistence modes must be governed")
    if args.source_mode == args.target_mode:
        raise TransitionError("persistence transition requires distinct modes")
    observed_mode, config = actual_mode(remaining())
    volume_before = active_volume(remaining())
    if observed_mode == args.target_mode:
        freeze_writes(args.compose_file.resolve(), remaining())
        checkpoint_result = checkpoint(remaining())
        if args.target_mode == "aof_everysec_rdb":
            aof_seed = validate_existing_aof(remaining())
            directory_result = {
                "action": "target-runtime-validated",
                "path": "/data/appendonlydir",
                "manifest_sha256": aof_seed["manifest_sha256"],
                "files_deleted": False,
            }
        else:
            aof_seed = None
            directory_result = prepare_aof_directory(
                args.source_mode,
                args.target_mode,
                args.summary_path.parent.name,
                remaining(),
            )
        volume_after = active_volume(remaining())
        if volume_after != volume_before:
            raise TransitionError(
                "active Redis volume identity changed during checkpoint"
            )
        return {
            "schema_version": 1,
            "generated_at": now(),
            "overall_pass": True,
            "source_mode": args.source_mode,
            "target_mode": args.target_mode,
            "observed_mode": observed_mode,
            "runtime_already_target": True,
            "aof_to_rdb_downgrade": args.source_mode == "aof_everysec_rdb",
            "checkpoint_required": True,
            "writes_frozen": True,
            "checkpoint_verified": True,
            "checkpoint": checkpoint_result,
            "aof_directory_transition": directory_result,
            "aof_seed": aof_seed,
            "active_volume": volume_after,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "production_switched": False,
            "secret_material_recorded": False,
        }
    if observed_mode != args.source_mode:
        raise TransitionError(
            f"active Redis mode differs from source deployment: {observed_mode}"
        )
    freeze_writes(args.compose_file.resolve(), remaining())
    checkpoint_result = checkpoint(remaining())
    directory_result = prepare_aof_directory(
        args.source_mode,
        args.target_mode,
        args.summary_path.parent.name,
        remaining(),
    )
    aof_seed = None
    if args.source_mode == "rdb_only" and args.target_mode == "aof_everysec_rdb":
        aof_seed = seed_aof_from_rdb(remaining())
    volume_after = active_volume(remaining())
    if volume_after != volume_before:
        raise TransitionError("active Redis volume identity changed during checkpoint")
    return {
        "schema_version": 1,
        "generated_at": now(),
        "overall_pass": True,
        "source_mode": args.source_mode,
        "target_mode": args.target_mode,
        "observed_mode": observed_mode,
        "effective_config": config,
        "runtime_already_target": False,
        "aof_to_rdb_downgrade": args.source_mode == "aof_everysec_rdb",
        "checkpoint_required": True,
        "writes_frozen": True,
        "write_services": list(WRITE_SERVICES),
        "checkpoint_verified": True,
        "checkpoint": checkpoint_result,
        "aof_directory_transition": directory_result,
        "aof_seed": aof_seed,
        "active_volume": volume_after,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "production_switched": False,
        "secret_material_recorded": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-file", type=Path, required=True)
    parser.add_argument("--source-mode", required=True)
    parser.add_argument("--target-mode", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--summary-path", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        summary = execute(args)
    except (OSError, TransitionError, ValueError, subprocess.TimeoutExpired) as exc:
        summary = {
            "schema_version": 1,
            "generated_at": now(),
            "overall_pass": False,
            "source_mode": args.source_mode,
            "target_mode": args.target_mode,
            "failure": str(exc),
            "checkpoint_verified": False,
            "checkpoint_required": args.source_mode != args.target_mode,
            "aof_to_rdb_downgrade": args.source_mode == "aof_everysec_rdb",
            "production_switched": False,
            "secret_material_recorded": False,
        }
    atomic_write_json(args.summary_path, summary)
    print(
        "Redis persistence transition checkpoint: "
        f"{'PASS' if summary['overall_pass'] else 'FAIL'}"
    )
    print(f"summary: {args.summary_path}")
    return 0 if summary["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
