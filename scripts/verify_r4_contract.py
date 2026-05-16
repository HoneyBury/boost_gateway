#!/usr/bin/env python3
"""Run short R4 contract gates without starting long-lived services."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


UNIT_FILTER = (
    "ProtoSchemaTest.*:"
    "V2ServiceBoundaryTest.*Envelope*:"
    "V2ServiceBoundaryTest.DecodeHandlerPayloadExtractsTypedPayload:"
    "V2ServiceBoundaryTest.DecodeHandlerPayloadMarksLegacyRawJsonDeprecated:"
    "V2ServiceBoundaryTest.WrapTypedResponseLeavesLegacyPayloadRaw:"
    "V2ActorRuntimeTest.DispatchAllInterleavesReadyActorsFairly:"
    "V2ActorRuntimeTest.ShutdownDuringFairDispatchStopsOtherReadyActors:"
    "HealthCheckTest.BackendHeartbeatRestoresReadinessAfterUnhealthyMark"
)

INTEGRATION_FILTER = (
    "ServiceBusIntegrity.GatewayBridgeRoutePropagatesTraceAndErrorCode:"
    "ServiceBusIntegrity.GatewayBridgeTypedEnvelopePreservesTraceAndError:"
    "ServiceBusIntegrity.GatewayBridgeRecoversAfterBackendConfigUpdate:"
    "ServiceBusIntegrity.GatewayBridgeTimeoutClosesStaleConnectionAndRecovers:"
    "ServiceBusIntegrity.GatewayBridgeCircuitBreakerHalfOpenProbeRecovers:"
    "ServiceBusIntegrity.ProtoEnvelopeRoundTripsThroughLoginBackend:"
    "ServiceBusIntegrity.ProtoEnvelopeRoundTripsThroughRoomBackend:"
    "ServiceBusIntegrity.ProtoEnvelopeRoundTripsThroughBattleBackend:"
    "ServiceBusIntegrity.ProtoEnvelopeRoundTripsThroughMatchBackend:"
    "ServiceBusIntegrity.ProtoEnvelopeRoundTripsThroughLeaderboardBackend"
)


class StepFailure(Exception):
    def __init__(self, step: dict[str, object]) -> None:
        self.step = step
        super().__init__(str(step.get("name", "unknown step")))


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


def tail(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def write_summary(path: Path, summary: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def run_step(name: str, category: str, cmd: list[str], cwd: Path, timeout_seconds: int) -> dict[str, object]:
    print(f"==> {name}", flush=True)
    started = time.monotonic()
    step: dict[str, object] = {
        "name": name,
        "category": category,
        "command": cmd,
        "cwd": str(cwd),
        "timeout_seconds": timeout_seconds,
        "status": "running",
    }
    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        step.update({
            "status": "timeout",
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(exc.stdout or ""),
            "stderr_tail": tail(exc.stderr or ""),
        })
        raise StepFailure(step) from exc

    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    step.update({
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(completed.stdout),
        "stderr_tail": tail(completed.stderr),
    })
    if completed.returncode != 0:
        raise StepFailure(step)
    return step


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
    parser.add_argument("--skip-arch-baseline", action="store_true")
    parser.add_argument("--build-timeout-seconds", type=int, default=120)
    parser.add_argument("--test-timeout-seconds", type=int, default=60)
    parser.add_argument("--baseline-timeout-seconds", type=int, default=45)
    parser.add_argument("--baseline-iterations", type=int, default=2000)
    parser.add_argument("--baseline-actors", type=int, default=2000)
    parser.add_argument("--baseline-actor-limit", type=int, default=10000)
    parser.add_argument("--baseline-battles", type=int, default=100)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=Path("runtime/validation/r4-contract-summary.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    build_dir = args.build_dir.resolve()
    summary_path = args.summary_path
    if not summary_path.is_absolute():
        summary_path = root / summary_path
    summary: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "build_dir": str(build_dir),
        "configuration": args.configuration,
        "skip_build": args.skip_build,
        "skip_arch_baseline": args.skip_arch_baseline,
        "passed": False,
        "failed_category": "",
        "failed_step": "",
        "steps": [],
    }

    try:
        summary["steps"].append(run_step(
            "v3 proto schema and transport contract",
            "schema",
            [
                sys.executable,
                str(root / "scripts" / "check_v3_proto_schema.py"),
                "--proto-dir",
                str(root / "proto" / "v3"),
                "--require-transport-contract",
            ],
            root,
            args.test_timeout_seconds,
        ))

        if not args.skip_build:
            summary["steps"].append(run_step(
                "build R4 focused targets",
                "build",
                cmake_build_args(
                    args,
                    [
                        "check_v3_proto_transport_contract",
                        "project_v2_unit_tests",
                        "project_v2_integration_tests",
                        "v2_arch_benchmark",
                    ],
                ),
                root,
                args.build_timeout_seconds,
            ))

        unit_tests = find_executable(build_dir, "project_v2_unit_tests")
        integration_tests = find_executable(build_dir, "project_v2_integration_tests")
        summary["steps"].append(run_step(
            "R4 unit gates",
            "unit",
            [str(unit_tests), f"--gtest_filter={UNIT_FILTER}"],
            unit_tests.parent,
            args.test_timeout_seconds,
        ))
        summary["steps"].append(run_step(
            "R4 integration gates",
            "integration",
            [str(integration_tests), f"--gtest_filter={INTEGRATION_FILTER}"],
            integration_tests.parent,
            args.test_timeout_seconds,
        ))

        if not args.skip_arch_baseline:
            summary["steps"].append(run_step(
                "short architecture baseline",
                "baseline",
                [
                    sys.executable,
                    str(root / "scripts" / "collect_v2_arch_baseline.py"),
                    "--build-dir",
                    str(build_dir),
                    "--output-root",
                    str(root / "runtime" / "perf" / "v2-arch-baseline"),
                    "--iterations",
                    str(args.baseline_iterations),
                    "--actors",
                    str(args.baseline_actors),
                    "--actor-limit",
                    str(args.baseline_actor_limit),
                    "--battles",
                    str(args.baseline_battles),
                    "--timeout-seconds",
                    str(args.baseline_timeout_seconds),
                ],
                root,
                args.baseline_timeout_seconds + 10,
            ))
    except StepFailure as exc:
        summary["failed_category"] = str(exc.step.get("category", "unknown"))
        summary["failed_step"] = str(exc.step.get("name", "unknown"))
        summary["steps"].append(exc.step)
        write_summary(summary_path, summary)
        print(f"R4 contract verification failed: {exc}", file=sys.stderr)
        print(f"summary: {summary_path}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        summary["failed_category"] = "discovery"
        summary["failed_step"] = str(exc)
        write_summary(summary_path, summary)
        print(f"R4 contract verification failed: {exc}", file=sys.stderr)
        print(f"summary: {summary_path}", file=sys.stderr)
        return 1

    summary["passed"] = True
    write_summary(summary_path, summary)
    print("R4 contract verification completed.")
    print(f"summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
