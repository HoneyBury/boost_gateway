#!/usr/bin/env python3
"""Run bounded stability/soak checks for P2-P5 without long-lived terminals."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


IO_FILTER = (
    "V2IoEngineTest.AcceptPolicyRoundRobinDistributesAcrossCores:"
    "V2IoEngineTest.AcceptPolicyLeastLoadedPicksIdleCore:"
    "V2IoEngineTest.AcceptPolicyLeastLoadedBalancesAcrossCores:"
    "V2IoEngineTest.MultiAcceptorCreatedWhenReusePortSet:"
    "V2IoEngineTest.MultiAcceptorHasPortOnAllCores:"
    "V2IoEngineTest.SingleAcceptorWhenReusePortFalse"
)

RECOVERY_FILTER = (
    "ServiceBusIntegrity.GatewayBridgeTimeoutClosesStaleConnectionAndRecovers:"
    "ServiceBusIntegrity.GatewayBridgeCircuitBreakerHalfOpenProbeRecovers:"
    "ServiceBusIntegrity.GatewayBridgeRecoversAfterBackendConfigUpdate"
)

DATA_FILTER = (
    "V2WriteBehindStoreTest.WriteBehindMultipleWritesAllFlushed:"
    "V2WriteBehindStoreTest.WriteBehindDestructorFlushesRemaining:"
    "V2WriteBehindStoreTest.WriteBehindFlushReportsDelegateFailures:"
    "V2WriteBehindStoreTest.WriteBehindDestructorDrainsLargePendingQueue"
)

SOAK_PROFILES = {
    "smoke": {
        "iterations": "2000",
        "actors": "2000",
        "actor_limit": "10000",
        "battles": "100",
        "expected_window": "bounded-smoke",
        "minimum_duration_seconds": 0,
    },
    "short": {
        "iterations": "5000",
        "actors": "5000",
        "actor_limit": "50000",
        "battles": "250",
        "expected_window": "15m-30m",
        "minimum_duration_seconds": 0,
    },
    "medium": {
        "iterations": "10000",
        "actors": "10000",
        "actor_limit": "100000",
        "battles": "500",
        "expected_window": "30m-60m",
        "minimum_duration_seconds": 0,
    },
    "long": {
        "iterations": "20000",
        "actors": "20000",
        "actor_limit": "200000",
        "battles": "1000",
        "expected_window": "2h",
        "minimum_duration_seconds": 7200,
    },
    "overnight": {
        "iterations": "40000",
        "actors": "40000",
        "actor_limit": "400000",
        "battles": "2000",
        "expected_window": "8h",
        "minimum_duration_seconds": 28800,
    },
}

PROFILE_TIMEOUTS = {
    "smoke": {"build": 300, "test": 120, "baseline": 120},
    "short": {"build": 1800, "test": 300, "baseline": 1800},
    "medium": {"build": 1800, "test": 300, "baseline": 3600},
    "long": {"build": 1800, "test": 300, "baseline": 10800},
    "overnight": {"build": 1800, "test": 300, "baseline": 32400},
}

SUSTAINED_GATE_MAX_FAILURE_RATE = 0.01
SUSTAINED_GATE_MAX_DEVIATION_RATIO = 0.20


def exe_name(base: str) -> str:
    return f"{base}.exe" if os.name == "nt" else base


def find_executable(build_dir: Path, base_name: str) -> Path:
    names = {exe_name(base_name), base_name}
    matches = sorted(p for p in build_dir.rglob("*") if p.is_file() and p.name in names)
    if os.name == "nt":
        preferred = [
            p for p in matches
            if any(part.lower() in {"debug", "release", "relwithdebinfo", "minsizerel"} for part in p.parts)
        ]
        if preferred:
            matches = preferred
    if not matches:
        raise FileNotFoundError(f"{exe_name(base_name)} not found under {build_dir}")
    return matches[0]


def normalize_output(text: str | bytes | None) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    return text


def tail(text: str | bytes | None, max_chars: int = 4000) -> str:
    text = normalize_output(text)
    return text if len(text) <= max_chars else text[-max_chars:]


def run_step(
    name: str,
    category: str,
    cmd: list[str],
    cwd: Path,
    timeout_seconds: int,
    *,
    emit_output: bool = True,
) -> dict[str, object]:
    if emit_output:
        print(f"==> {name}", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "category": category,
            "command": cmd,
            "cwd": str(cwd),
            "timeout_seconds": timeout_seconds,
            "status": "timeout",
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(exc.stdout or ""),
            "stderr_tail": tail(exc.stderr or ""),
        }

    stdout = normalize_output(completed.stdout)
    stderr = normalize_output(completed.stderr)
    if stdout and emit_output:
        print(stdout, end="")
    if stderr and emit_output:
        print(stderr, end="", file=sys.stderr)
    return {
        "name": name,
        "category": category,
        "command": cmd,
        "cwd": str(cwd),
        "timeout_seconds": timeout_seconds,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }


def cmake_build_args(args: argparse.Namespace, targets: list[str]) -> list[str]:
    cmd = ["cmake", "--build", str(args.build_dir)]
    if args.configuration:
        cmd.extend(["--config", args.configuration])
    cmd.extend(["--target", *targets])
    return cmd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build/windows-msvc-debug"))
    parser.add_argument("--configuration", default="Debug")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--build-timeout-seconds", type=int)
    parser.add_argument("--test-timeout-seconds", type=int)
    parser.add_argument("--baseline-timeout-seconds", type=int)
    parser.add_argument(
        "--minimum-duration-seconds",
        type=int,
        default=None,
        help="Override the profile's required sustained architecture-baseline duration (0 keeps a bounded run).",
    )
    parser.add_argument("--baseline-profile", choices=["debug", "release"], default="debug")
    parser.add_argument("--soak-profile", choices=sorted(SOAK_PROFILES), default="smoke")
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/stability-soak-summary.json"))
    return parser.parse_args()


def arch_baseline_command(
    root: Path,
    build_dir: Path,
    output_root: Path,
    soak_profile: dict[str, str | int],
    baseline_timeout_seconds: int,
    baseline_profile: str,
) -> list[str]:
    return [
        sys.executable,
        str(root / "scripts" / "collect_v2_arch_baseline.py"),
        "--build-dir", str(build_dir),
        "--output-root", str(output_root),
        "--iterations", str(soak_profile["iterations"]),
        "--actors", str(soak_profile["actors"]),
        "--actor-limit", str(soak_profile["actor_limit"]),
        "--battles", str(soak_profile["battles"]),
        "--timeout-seconds", str(baseline_timeout_seconds),
        "--gate-profile", baseline_profile,
    ]


def failed_arch_checks(summary_path: Path) -> list[dict[str, object]]:
    try:
        parsed = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    gates = parsed.get("release_gates")
    if not isinstance(gates, dict):
        return []
    checks = gates.get("checks")
    if not isinstance(checks, list):
        return []
    return [check for check in checks if isinstance(check, dict) and check.get("passed") is False]


def sustained_failure_violations(
    failures: dict[str, dict[str, object]], completed_runs: int
) -> list[dict[str, object]]:
    violations: list[dict[str, object]] = []
    for entry in failures.values():
        threshold = float(entry["threshold"])
        worst_observed = float(entry["worst_observed"])
        failure_rate = int(entry["failed_runs"]) / max(1, completed_runs)
        direction = str(entry["direction"])
        deviation_ratio = (
            (worst_observed - threshold) / threshold
            if direction == "max"
            else (threshold - worst_observed) / threshold
        )
        entry["failure_rate"] = round(failure_rate, 6)
        entry["worst_deviation_ratio"] = round(deviation_ratio, 6)
        entry["accepted_as_transient"] = (
            failure_rate <= SUSTAINED_GATE_MAX_FAILURE_RATE
            and deviation_ratio <= SUSTAINED_GATE_MAX_DEVIATION_RATIO
        )
        if not entry["accepted_as_transient"]:
            violations.append(entry)
    return sorted(violations, key=lambda check: str(check["name"]))


def run_sustained_arch_baseline(
    root: Path,
    build_dir: Path,
    output_root: Path,
    soak_profile: dict[str, str | int],
    baseline_timeout_seconds: int,
    baseline_profile: str,
    minimum_duration_seconds: int,
) -> dict[str, object]:
    name = "sustained arch baseline with external gates"
    started = time.monotonic()
    completed_runs = 0
    last_run: dict[str, object] = {}
    failures: dict[str, dict[str, object]] = {}
    summary_path = output_root / "summary.json"
    while completed_runs == 0 or time.monotonic() - started < minimum_duration_seconds:
        completed_runs += 1
        last_run = run_step(
            f"{name} (pass {completed_runs})",
            "baseline",
            arch_baseline_command(
                root, build_dir, output_root, soak_profile, baseline_timeout_seconds, baseline_profile
            ),
            root,
            baseline_timeout_seconds + 10,
            emit_output=completed_runs == 1,
        )
        if last_run["status"] != "passed":
            sample_failures = failed_arch_checks(summary_path)
            # A benchmark gate failure is evidence to aggregate across the full soak,
            # while a missing/invalid summary or process failure must stop immediately.
            if last_run.get("returncode") == 2 and sample_failures:
                for check in sample_failures:
                    name_key = str(check.get("name", "unknown"))
                    entry = failures.setdefault(name_key, {
                        "name": name_key,
                        "metric": check.get("metric"),
                        "threshold": check.get("threshold"),
                        "direction": check.get("direction"),
                        "failed_runs": 0,
                        "last_observed": None,
                    })
                    entry["failed_runs"] = int(entry["failed_runs"]) + 1
                    entry["last_observed"] = check.get("value")
                    observed = float(check["value"])
                    worst_observed = entry.get("worst_observed")
                    if worst_observed is None or (
                        str(entry["direction"]) == "max" and observed > float(worst_observed)
                    ) or (
                        str(entry["direction"]) == "min" and observed < float(worst_observed)
                    ):
                        entry["worst_observed"] = observed
                continue
            if completed_runs > 1:
                if last_run.get("stdout_tail"):
                    print(last_run["stdout_tail"], end="")
                if last_run.get("stderr_tail"):
                    print(last_run["stderr_tail"], end="", file=sys.stderr)
            return {
                "name": name,
                "category": "baseline",
                "status": last_run["status"],
                "duration_seconds": round(time.monotonic() - started, 3),
                "minimum_duration_seconds": minimum_duration_seconds,
                "completed_runs": completed_runs,
                "last_run": last_run,
                "failed_checks": sorted(failures.values(), key=lambda check: str(check["name"])),
            }
    violating_checks = sustained_failure_violations(failures, completed_runs)
    status = "failed" if violating_checks else "passed"
    return {
        "name": name,
        "category": "baseline",
        "status": status,
        "duration_seconds": round(time.monotonic() - started, 3),
        "minimum_duration_seconds": minimum_duration_seconds,
        "completed_runs": completed_runs,
        "last_run": last_run,
        "failed_checks": sorted(failures.values(), key=lambda check: str(check["name"])),
        "violating_checks": violating_checks,
        "transient_failure_policy": {
            "max_failure_rate": SUSTAINED_GATE_MAX_FAILURE_RATE,
            "max_deviation_ratio": SUSTAINED_GATE_MAX_DEVIATION_RATIO,
        },
    }


def main() -> int:
    args = parse_args()
    profile_timeouts = PROFILE_TIMEOUTS[args.soak_profile]
    if args.build_timeout_seconds is None:
        args.build_timeout_seconds = profile_timeouts["build"]
    if args.test_timeout_seconds is None:
        args.test_timeout_seconds = profile_timeouts["test"]
    if args.baseline_timeout_seconds is None:
        args.baseline_timeout_seconds = profile_timeouts["baseline"]
    root = Path(__file__).resolve().parents[3]
    build_dir = args.build_dir.resolve()
    summary_path = args.summary_path if args.summary_path.is_absolute() else root / args.summary_path
    soak_profile = SOAK_PROFILES[args.soak_profile]
    minimum_duration_seconds = (
        int(soak_profile["minimum_duration_seconds"])
        if args.minimum_duration_seconds is None
        else max(0, args.minimum_duration_seconds)
    )
    summary: dict[str, object] = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "build_dir": str(build_dir),
        "configuration": args.configuration,
        "baseline_profile": args.baseline_profile,
        "soak_profile": args.soak_profile,
        "expected_window": soak_profile.get("expected_window"),
        "minimum_duration_seconds": minimum_duration_seconds,
        "sustained_duration_seconds": 0.0,
        "sustained_completed_runs": 0,
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "overall_pass": False,
        "passed": False,
        "failed_category": "",
        "failed_step": "",
        "steps": [],
        "artifacts": {
            "summary_path": str(summary_path),
            "arch_baseline_output_root": str(root / "runtime" / "perf" / "v2-stability-soak"),
        },
    }
    if minimum_duration_seconds > 0:
        summary["artifacts"]["notes"] = (
            "This profile repeats the architecture baseline until the required wall-clock duration is reached; "
            "it is intended for fixed runners with dedicated machine access."
        )

    try:
        if not args.skip_build:
            step = run_step(
                "build stability focused targets",
                "build",
                cmake_build_args(args, ["project_v2_unit_tests", "project_v2_integration_tests", "v2_arch_benchmark"]),
                root,
                args.build_timeout_seconds,
            )
            summary["steps"].append(step)
            if step["status"] != "passed":
                raise RuntimeError(step["name"])

        unit_tests = find_executable(build_dir, "project_v2_unit_tests")
        integration_tests = find_executable(build_dir, "project_v2_integration_tests")
        steps = [
            run_step("I/O policy and bounded accept checks", "io", [str(unit_tests), f"--gtest_filter={IO_FILTER}"], unit_tests.parent, args.test_timeout_seconds),
            run_step("WriteBehind drain/failure checks", "data", [str(unit_tests), f"--gtest_filter={DATA_FILTER}"], unit_tests.parent, args.test_timeout_seconds),
            run_step("backend timeout/recovery checks", "recovery", [str(integration_tests), f"--gtest_filter={RECOVERY_FILTER}"], integration_tests.parent, args.test_timeout_seconds),
            run_sustained_arch_baseline(
                root,
                build_dir,
                root / "runtime" / "perf" / "v2-stability-soak",
                soak_profile,
                args.baseline_timeout_seconds,
                args.baseline_profile,
                minimum_duration_seconds,
            ),
        ]
        summary["steps"].extend(steps)
        sustained_step = next(step for step in steps if step["name"] == "sustained arch baseline with external gates")
        summary["sustained_duration_seconds"] = sustained_step["duration_seconds"]
        summary["sustained_completed_runs"] = sustained_step["completed_runs"]
        for step in steps:
            if step["status"] != "passed":
                raise RuntimeError(step["name"])
    except (FileNotFoundError, RuntimeError) as exc:
        failed = next((s for s in summary["steps"] if s.get("status") != "passed"), None)
        if failed:
            summary["failed_category"] = str(failed.get("category", "unknown"))
            summary["failed_step"] = str(failed.get("name", "unknown"))
        else:
            summary["failed_category"] = "discovery"
            summary["failed_step"] = str(exc)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"stability soak failed: {exc}", file=sys.stderr)
        print(f"summary: {summary_path}", file=sys.stderr)
        return 1

    summary["passed"] = True
    summary["overall_pass"] = True
    summary["duration_seconds"] = round(
        sum(float(step.get("duration_seconds", 0.0)) for step in summary["steps"] if isinstance(step, dict)),
        3,
    )
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("stability soak completed.")
    print(f"summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
