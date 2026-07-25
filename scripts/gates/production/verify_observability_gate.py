#!/usr/bin/env python3
"""Run the P4 observability and rate-limit release gate."""

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


ROOT_AUDIT_METRICS_FILTER = (
    "DiagnosticsManagerTest.ToJsonIsValidJson:"
    "DiagnosticsManagerTest.WireFromShadowBridge:"
    "DiagnosticsManagerTest.ToJsonNoBackendsIsValid:"
    "V2DemoServerSmokeTest.DiagnosticsHttpEndpointReturnsStructuredSnapshot:"
    "V2DemoServerSmokeTest.MetricsExposeBackendRouteLatencyHistogram:"
    "IdentityRegisterTest.RegisterNewAccount"
)

RATE_LIMIT_FILTER = (
    "RateLimiterTest.AllowsFirstRequest:"
    "RateLimiterTest.BlocksAfterExhaustingConnectionLimit:"
    "RateLimiterTest.BlocksAfterExhaustingIpLimit:"
    "RateLimiterTest.BlocksAfterExhaustingMessageTypeLimit:"
    "RateLimiterTest.BlocksAfterExhaustingUserLimit:"
    "RateLimiterTest.RateLimitsLoginAggressively:"
    "V2GatewayBridgeTest.RateLimitPolicyCanRejectRequest"
)

TRACE_OTEL_UNIT_FILTER = (
    "V2TraceContextTest.TraceContextFromHeader:"
    "V2TraceContextTest.TraceContextApplyToHeader:"
    "V2TraceContextTest.SpanChildInheritsTraceId:"
    "OtelExporterTest.ExportSingleSpan:"
    "OtelExporterTest.CustomExportFunction:"
    "OtelExporterTest.FlushRequeuesWhenExportFails:"
    "OtelExporterTest.SpanRecordFields:"
    "EventStoreTest.TraceIdPropagation"
)

TRACE_INTEGRATION_FILTER = (
    "ServiceBusIntegrity.TraceContextPropagationAcrossEnvelope:"
    "ServiceBusIntegrity.GatewayBridgeRoutePropagatesTraceAndErrorCode:"
    "ServiceBusIntegrity.GatewayBridgeTypedEnvelopePreservesTraceAndError"
)

METRICS_OTEL_INTEGRATION_FILTER = (
    "V2BackendHealthTest.MetricsCountersAfterSuccessfulRoute:"
    "V2BackendHealthTest.MetricsCountersAfterUnavailable:"
    "V2BackendHealthTest.DiagnosticsJsonIncludesBackendMetrics:"
    "V2BackendRoutingTest.OtelExporterReceivesSpanOnSuccessfulRoute:"
    "V2BackendRoutingTest.OtelExporterReceivesSpanOnFailedRoute:"
    "V2BackendRoutingTest.OtelExporterNoCrashWhenNotConfigured"
)

OTEL_COLLECTOR_FILTER = (
    "V2BackendRoutingTest.OtelExporterPostsSpanToCollectorEndpoint"
)


def exe_name(base: str) -> str:
    return f"{base}.exe" if os.name == "nt" else base


def find_executable(build_dir: Path, base_name: str) -> Path:
    names = {exe_name(base_name), base_name}
    matches = sorted(p for p in build_dir.rglob("*") if p.is_file() and p.name in names)
    direct_matches = [
        p for p in matches
        if "build" not in p.relative_to(build_dir).parts[:-1]
    ]
    if direct_matches:
        matches = sorted(direct_matches, key=lambda p: (len(p.relative_to(build_dir).parts), str(p)))
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
            "stdout_tail": tail(exc.stdout),
            "stderr_tail": tail(exc.stderr),
        }

    stdout = normalize_output(completed.stdout)
    stderr = normalize_output(completed.stderr)
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    status = "passed" if completed.returncode == 0 else "failed"
    if completed.returncode == 0 and "0 tests from 0 test suites ran" in stdout:
        status = "failed"
        stderr = (stderr + "\n" if stderr else "") + "gtest filter matched zero tests"
    return {
        "name": name,
        "category": category,
        "command": cmd,
        "cwd": str(cwd),
        "timeout_seconds": timeout_seconds,
        "status": status,
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
    parser.add_argument("--build-dir", type=Path, default=Path("build/contributor-debug"))
    parser.add_argument("--configuration", default="Debug")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--include-otel-collector", action="store_true")
    parser.add_argument("--include-runtime-http", action="store_true")
    parser.add_argument("--build-timeout-seconds", type=int, default=180)
    parser.add_argument("--test-timeout-seconds", type=int, default=120)
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/observability-gate-summary.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[3]
    build_dir = args.build_dir.resolve()
    summary_path = args.summary_path if args.summary_path.is_absolute() else root / args.summary_path
    summary: dict[str, object] = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "build_dir": str(build_dir),
        "configuration": args.configuration,
        "include_otel_collector": args.include_otel_collector,
        "include_runtime_http": args.include_runtime_http,
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
            "runtime_http_summary_path": str(summary_path.parent / "gateway-observability-runtime-summary.json"),
        },
    }

    try:
        if not args.skip_build:
            step = run_step(
                "build P4 observability targets",
                "build",
                cmake_build_args(args, ["project_v2_unit_tests", "project_v2_integration_tests"]),
                root,
                args.build_timeout_seconds,
            )
            summary["steps"].append(step)
            if step["status"] != "passed":
                raise RuntimeError(step["name"])

        v2_unit_tests = find_executable(build_dir, "project_v2_unit_tests")
        v2_integration_tests = find_executable(build_dir, "project_v2_integration_tests")

        summary["steps"].append(run_step(
            "rate-limit key path coverage",
            "rate_limit",
            [str(v2_unit_tests), f"--gtest_filter={RATE_LIMIT_FILTER}"],
            v2_unit_tests.parent,
            args.test_timeout_seconds,
        ))
        summary["steps"].append(run_step(
            "trace and OTel unit coverage",
            "trace",
            [str(v2_unit_tests), f"--gtest_filter={TRACE_OTEL_UNIT_FILTER}"],
            v2_unit_tests.parent,
            args.test_timeout_seconds,
        ))
        summary["steps"].append(run_step(
            "metrics and audit coverage",
            "audit_metrics",
            [str(v2_unit_tests), f"--gtest_filter={ROOT_AUDIT_METRICS_FILTER}"],
            v2_unit_tests.parent,
            args.test_timeout_seconds,
        ))
        summary["steps"].append(run_step(
            "gateway trace propagation coverage",
            "trace",
            [str(v2_integration_tests), f"--gtest_filter={TRACE_INTEGRATION_FILTER}"],
            v2_integration_tests.parent,
            args.test_timeout_seconds,
        ))
        summary["steps"].append(run_step(
            "backend RED metrics and OTel route coverage",
            "metrics",
            [str(v2_integration_tests), f"--gtest_filter={METRICS_OTEL_INTEGRATION_FILTER}"],
            v2_integration_tests.parent,
            args.test_timeout_seconds,
        ))
        if args.include_otel_collector:
            summary["steps"].append(run_step(
                "OTel collector POST coverage",
                "otel_collector",
                [str(v2_integration_tests), f"--gtest_filter={OTEL_COLLECTOR_FILTER}"],
                v2_integration_tests.parent,
                args.test_timeout_seconds,
            ))
        if args.include_runtime_http:
            runtime_summary = summary_path.parent / "gateway-observability-runtime-summary.json"
            summary["steps"].append(run_step(
                "gateway runtime HTTP observability coverage",
                "runtime_http",
                [
                    sys.executable,
                    str(root / "scripts/gates/production/verify_gateway_observability_runtime.py"),
                    "--build-dir",
                    str(args.build_dir),
                    "--summary-path",
                    str(runtime_summary),
                    *([] if not args.skip_build else ["--skip-build"]),
                ],
                root,
                args.test_timeout_seconds,
            ))
    except (FileNotFoundError, RuntimeError) as exc:
        failed = next((step for step in summary["steps"] if step.get("status") != "passed"), None)
        if failed:
            summary["failed_category"] = str(failed.get("category", "unknown"))
            summary["failed_step"] = str(failed.get("name", "unknown"))
        else:
            summary["failed_category"] = "discovery"
            summary["failed_step"] = str(exc)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"observability gate failed: {exc}", file=sys.stderr)
        print(f"summary: {summary_path}", file=sys.stderr)
        return 1

    failed = next((step for step in summary["steps"] if step.get("status") != "passed"), None)
    if failed:
        summary["failed_category"] = str(failed.get("category", "unknown"))
        summary["failed_step"] = str(failed.get("name", "unknown"))
    else:
        summary["overall_pass"] = True
        summary["passed"] = True
    summary["duration_seconds"] = round(
        sum(float(step.get("duration_seconds", 0.0)) for step in summary["steps"] if isinstance(step, dict)),
        3,
    )

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
