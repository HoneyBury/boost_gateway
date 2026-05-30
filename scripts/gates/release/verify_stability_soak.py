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
    },
    "short": {
        "iterations": "5000",
        "actors": "5000",
        "actor_limit": "50000",
        "battles": "250",
        "expected_window": "15m-30m",
    },
    "medium": {
        "iterations": "10000",
        "actors": "10000",
        "actor_limit": "100000",
        "battles": "500",
        "expected_window": "30m-60m",
    },
    "long": {
        "iterations": "20000",
        "actors": "20000",
        "actor_limit": "200000",
        "battles": "1000",
        "expected_window": "2h",
    },
    "overnight": {
        "iterations": "40000",
        "actors": "40000",
        "actor_limit": "400000",
        "battles": "2000",
        "expected_window": "8h",
    },
}


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


def run_step(name: str, category: str, cmd: list[str], cwd: Path, timeout_seconds: int) -> dict[str, object]:
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
    if stdout:
        print(stdout, end="")
    if stderr:
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
    parser.add_argument("--build-timeout-seconds", type=int, default=120)
    parser.add_argument("--test-timeout-seconds", type=int, default=60)
    parser.add_argument("--baseline-timeout-seconds", type=int, default=45)
    parser.add_argument("--baseline-profile", choices=["debug", "release"], default="debug")
    parser.add_argument("--soak-profile", choices=sorted(SOAK_PROFILES), default="smoke")
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/stability-soak-summary.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[3]
    build_dir = args.build_dir.resolve()
    summary_path = args.summary_path if args.summary_path.is_absolute() else root / args.summary_path
    soak_profile = SOAK_PROFILES[args.soak_profile]
    summary: dict[str, object] = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "build_dir": str(build_dir),
        "configuration": args.configuration,
        "baseline_profile": args.baseline_profile,
        "soak_profile": args.soak_profile,
        "expected_window": soak_profile.get("expected_window"),
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
    if args.soak_profile in {"long", "overnight"}:
        summary["artifacts"]["notes"] = "This profile is intended for fixed runners with expanded timeouts and dedicated machine access."

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
            run_step(
                "short arch baseline with external gates",
                "baseline",
                [
                    sys.executable,
                    str(root / "scripts" / "collect_v2_arch_baseline.py"),
                    "--build-dir",
                    str(build_dir),
                    "--output-root",
                    str(root / "runtime" / "perf" / "v2-stability-soak"),
                    "--iterations",
                    soak_profile["iterations"],
                    "--actors",
                    soak_profile["actors"],
                    "--actor-limit",
                    soak_profile["actor_limit"],
                    "--battles",
                    soak_profile["battles"],
                    "--timeout-seconds",
                    str(args.baseline_timeout_seconds),
                    "--gate-profile",
                    args.baseline_profile,
                ],
                root,
                args.baseline_timeout_seconds + 10,
            ),
        ]
        summary["steps"].extend(steps)
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

