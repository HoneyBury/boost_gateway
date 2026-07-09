#!/usr/bin/env python3
"""Collect short-running v2 architecture micro-baseline data."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def is_windows() -> bool:
    return os.name == "nt"


def exe_name(base: str) -> str:
    return f"{base}.exe" if is_windows() else base


def resolve_executable(build_dir: Path, base_name: str) -> Path:
    target_names = {exe_name(base_name), base_name}
    matches = sorted(
        p for p in build_dir.rglob("*")
        if p.is_file() and p.name in target_names
    )
    if is_windows():
        preferred = [
            p for p in matches
            if any(part.lower() in {"debug", "release", "relwithdebinfo", "minsizerel"} for part in p.parts)
        ]
        if preferred:
            matches = preferred
    if not matches:
        raise FileNotFoundError(f"{exe_name(base_name)} not found under {build_dir}")
    return matches[0]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def result_by_name(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("name")): item for item in report.get("results", [])}


DEFAULT_CHECKS = {
    "actor_local_tell_dispatch": {"metric": "p99_us", "threshold": 1000.0, "direction": "max"},
    "actor_cross_core_tell_drain_dispatch": {"metric": "p99_us", "threshold": 10000.0, "direction": "max"},
    "actor_create": {"metric": "p99_us", "threshold": 10000.0, "direction": "max"},
    "actor_shutdown_per_actor": {"metric": "p99_us", "threshold": 5000.0, "direction": "max"},
    "bump_arena_alloc": {"metric": "p99_us", "threshold": 10.0, "direction": "max"},
    "object_pool_acquire_release": {"metric": "p99_us", "threshold": 50.0, "direction": "max"},
    "spsc_queue_enqueue_dequeue": {"metric": "p99_us", "threshold": 10.0, "direction": "max"},
    "battle_world_tick_100_entities": {"metric": "p99_us", "threshold": 5000.0, "direction": "max"},
    "actor_fan_in_throughput": {"metric": "throughput_ops_per_sec", "threshold": 300_000.0, "direction": "min"},
    "multi_battle_tick_100_entities": {"metric": "p99_us", "threshold": 5000.0, "direction": "max"},
    "backend_envelope_json_roundtrip": {"metric": "p99_us", "threshold": 1000.0, "direction": "max"},
    "typed_envelope_json_roundtrip": {"metric": "p99_us", "threshold": 1000.0, "direction": "max"},
    "backend_typed_adapter_roundtrip": {"metric": "p99_us", "threshold": 1000.0, "direction": "max"},
}


def load_gate_profile(config_path: Path, profile: str) -> dict[str, dict[str, Any]]:
    if not config_path.is_file():
        return DEFAULT_CHECKS
    config = load_json(config_path)
    profiles = config.get("profiles", {})
    selected = profiles.get(profile)
    if not isinstance(selected, dict):
        raise ValueError(f"gate profile '{profile}' not found in {config_path}")
    return selected


def evaluate_gates(
    report: dict[str, Any],
    expected_actor_limit: int,
    checks_config: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    results = result_by_name(report)

    gate_results: list[dict[str, Any]] = []
    passed = True
    for name, check in checks_config.items():
        metric = str(check["metric"])
        threshold = float(check["threshold"])
        direction = str(check["direction"])
        value = float(results.get(name, {}).get(metric, 0.0))
        samples = int(results.get(name, {}).get("samples", 0))
        if direction == "min":
            ok = samples > 0 and value >= threshold
        else:
            ok = samples > 0 and value <= threshold
        passed = passed and ok
        gate_results.append({
            "name": name,
            "metric": metric,
            "value": value,
            "threshold": threshold,
            "direction": direction,
            "samples": samples,
            "passed": ok,
        })

    actor_limit = results.get("actor_100k_create_smoke", {})
    actor_limit_samples = int(actor_limit.get("samples", 0))
    actor_limit_ok = actor_limit_samples >= expected_actor_limit
    passed = passed and actor_limit_ok
    gate_results.append({
        "name": "actor_100k_create_smoke",
        "metric": "samples",
        "value": actor_limit_samples,
        "threshold": expected_actor_limit,
        "direction": "min",
        "samples": actor_limit_samples,
        "passed": actor_limit_ok,
    })

    return {
        "passed": passed,
        "checks": gate_results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build/windows-msvc-debug"))
    parser.add_argument("--output-root", type=Path, default=Path("runtime/perf/v2-arch-baseline"))
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--actors", type=int, default=10000)
    parser.add_argument("--actor-limit", type=int, default=100000)
    parser.add_argument("--battles", type=int, default=500)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--gate-config", type=Path, default=Path("config/perf/v2_arch_baseline_gates.json"))
    parser.add_argument("--gate-profile", choices=["debug", "release"], default="debug")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_root = args.output_root
    output_root.mkdir(parents=True, exist_ok=True)
    gate_config = args.gate_config
    if not gate_config.is_absolute():
        gate_config = Path.cwd() / gate_config
    gate_checks = load_gate_profile(gate_config, args.gate_profile)

    benchmark = resolve_executable(args.build_dir, "v2_arch_benchmark")
    raw_path = output_root / "v2_arch_benchmark.json"
    cmd = [
        str(benchmark),
        "--iterations", str(args.iterations),
        "--actors", str(args.actors),
        "--actor-limit", str(args.actor_limit),
        "--battles", str(args.battles),
        "--output", str(raw_path),
    ]
    completed = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=args.timeout_seconds,
        check=False,
    )
    (output_root / "stdout.log").write_text(completed.stdout, encoding="utf-8")
    (output_root / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        print(completed.stderr, file=sys.stderr)
        return completed.returncode

    report = load_json(raw_path)
    summary = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "build_dir": str(args.build_dir),
        "benchmark": str(benchmark),
        "iterations": args.iterations,
        "actors": args.actors,
        "actor_limit": args.actor_limit,
        "battles": args.battles,
        "gate_profile": args.gate_profile,
        "gate_config": str(gate_config),
        "raw_result": str(raw_path),
        "release_gates": evaluate_gates(report, args.actor_limit, gate_checks),
        "results": report.get("results", []),
    }
    summary_path = output_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({
        "summary": str(summary_path),
        "passed": summary["release_gates"]["passed"],
    }, indent=2))
    return 0 if summary["release_gates"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

