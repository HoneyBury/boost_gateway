#!/usr/bin/env python3
"""Validate N6 v3 proto/gRPC PoC evidence and production decision boundaries."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str, category: str = "decision") -> None:
    checks.append({"name": name, "category": category, "passed": passed, "detail": detail})


def run_step(name: str, category: str, cmd: list[str], timeout_seconds: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            cmd,
            cwd=ROOT,
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
            "status": "timeout",
            "command": cmd,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": (exc.stdout or "")[-4000:],
            "stderr_tail": (exc.stderr or "")[-4000:],
        }
    return {
        "name": name,
        "category": category,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "command": cmd,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def validate_static_boundaries(checks: list[dict[str, Any]]) -> None:
    adr = read_text(ROOT / "docs/archive/process/v3-proto-grpc-adr.md")
    current = read_text(ROOT / "docs/current-state.md")
    proto_readme = read_text(ROOT / "proto/README.md")
    cmake = read_text(ROOT / "src/v3/CMakeLists.txt")
    root_cmake = read_text(ROOT / "CMakeLists.txt")
    all_cmake = root_cmake + "\n" + cmake

    add(checks, "adr default transport deferred", "Deferred for default transport" in adr, "ADR must not approve gRPC as default transport")
    add(checks, "adr names tcp baseline", "TCP + BackendEnvelope" in adr, "ADR must preserve default TCP + BackendEnvelope chain")
    add(checks, "adr has decision criteria", "判定条件" in adr and "继续推进" in adr and "暂停" in adr, "ADR must record continue/pause criteria")
    add(checks, "proto readme documents generated boundary", "generated protobuf / gRPC" in proto_readme and "还不是默认唯一传输路径" in proto_readme, "proto README must document generated boundary")
    add(checks, "current state keeps grpc out of default chain", "generated gRPC transport PoC" in current and "默认生产主链仍是 SDK + TCP gateway" in current, "current-state must keep default chain explicit")
    add(checks, "cmake exposes proto schema target", "check_v3_proto_schema" in all_cmake, "CMake target check_v3_proto_schema exists")
    add(checks, "cmake exposes transport contract target", "check_v3_proto_transport_contract" in all_cmake, "CMake target check_v3_proto_transport_contract exists")
    add(checks, "cmake exposes generation target", "generate_v3_proto_cpp" in all_cmake, "CMake target generate_v3_proto_cpp exists")
    grpc_source = read_text(ROOT / "src/v2/grpc/gateway_grpc_server.cpp")
    grpc_header = read_text(ROOT / "src/v2/grpc/gateway_grpc_server.h")
    grpc_server_header = read_text(ROOT / "src/v2/grpc/grpc_server.h")
    gateway_proto = read_text(ROOT / "proto/v3/gateway.proto")
    add(checks, "grpc scope includes room base flows", "RoomCreateCallData" in grpc_source and "RoomJoinCallData" in grpc_source and "RoomLeaveCallData" in grpc_source and "RoomReadyCallData" in grpc_source, "experimental gateway gRPC scope now includes room create/join/leave/ready")
    add(checks, "grpc scope includes match and leaderboard base flows", "MatchJoinCallData" in grpc_source and "MatchLeaveCallData" in grpc_source and "MatchStatusCallData" in grpc_source and "LeaderboardSubmitCallData" in grpc_source and "LeaderboardTopCallData" in grpc_source and "LeaderboardRankCallData" in grpc_source, "experimental gateway gRPC scope now includes match and leaderboard base flows")
    add(checks, "grpc scope includes battle base flows", "BattleCreateCallData" in grpc_source and "BattleInputCallData" in grpc_source and "BattleStateCallData" in grpc_source and "BattleFinishCallData" in grpc_source, "experimental gateway gRPC scope now includes battle create/input/state/finish")
    grpc_adapter = read_text(ROOT / "src/v2/grpc/grpc_adapter.h")
    add(
        checks,
        "grpc adapter uses real backend bridge routing",
        "GatewayServiceBridge" in grpc_adapter
        and "bridge_->route(" in grpc_adapter
        and "Accept all; real auth would validate the token" not in grpc_adapter,
        "grpc adapter routes requests via GatewayServiceBridge-backed callbacks instead of the old allow-all-only stub path",
    )
    grpc_sdk = read_text(ROOT / "sdk/src/grpc_client.cpp")
    grpc_sdk_header = read_text(ROOT / "sdk/include/boost_gateway/sdk/grpc_client.h")
    grpc_e2e = read_text(ROOT / "tests/v2/integration/grpc_gateway_adapter_e2e_test.cpp")
    add(checks, "grpc scope includes cancellable rate-limited battle streaming", "StreamBattleState" in gateway_proto and "update_interval_ms" in gateway_proto and "BattleStateStreamCallData" in grpc_source and "AsyncNotifyWhenDone" in grpc_source and "kMinimumIntervalMs" in grpc_source and "subscribe_battle_state" in grpc_sdk, "experimental gateway gRPC exposes cancellable, rate-limited Battle state server streaming")
    add(
        checks,
        "grpc security profile includes tls rbac and mtls evidence",
        "SslServerCredentials" in grpc_server_header
        and "require_authenticated_principal" in grpc_header
        and "GrpcClientTlsOptions" in grpc_sdk_header
        and "connect_secure" in grpc_sdk
        and "GrpcGatewayRbacE2ETest" in grpc_e2e
        and "GrpcGatewayTlsE2ETest" in grpc_e2e
        and "GrpcGatewayMtlsE2ETest" in grpc_e2e,
        "experimental gateway gRPC now has TLS server credentials, trusted-principal RBAC, SDK TLS client credentials, and TLS/mTLS E2E coverage",
    )
    add(
        checks,
        "grpc production profile still incomplete",
        "OpenTelemetry" not in grpc_source
        and "未进入独立安装包" in current
        and "defer_default_transport" in current,
        "gRPC still lacks observability production-path coverage and an installed SDK distribution contract, so default transport remains deferred",
    )
    grpc_benchmark = read_text(ROOT / "tests/perf/grpc_vs_tcp_perf_test.cpp")
    add(checks, "grpc benchmark uses real tcp io", "run_tcp_benchmark(std::uint16_t port" in grpc_benchmark and "BackendConnection conn" in grpc_benchmark and "conn.send_request(req)" in grpc_benchmark, "grpc vs tcp perf test uses real TCP backend requests")
    add(checks, "grpc benchmark uses real grpc io", "run_grpc_benchmark(std::uint16_t port" in grpc_benchmark and "Gateway::NewStub" in grpc_benchmark and "stub->RequestLogin(&ctx, req, &resp)" in grpc_benchmark, "grpc vs tcp perf test uses real gRPC RequestLogin calls")
    add(checks, "grpc benchmark remains login-only scope", "make_login_backend()" in grpc_benchmark and "GatewayGrpcServer" in grpc_benchmark, "grpc benchmark is real I/O but still limited to the currently implemented login path")
    mainline_plan = read_text(ROOT / "docs/mainline-execution-plan.md")
    add(
        checks,
        "grpc next evidence stays deferred behind remaining delivery gaps",
        "defer_default_transport" in mainline_plan
        and "OTel/外部观测" in mainline_plan
        and "fixed-runner `BOOST_BUILD_GRPC=ON` summary" in mainline_plan,
        "mainline plan must keep default transport deferred until observability and fixed-runner delivery gaps are closed",
    )
    conan_validate = read_text(ROOT / ".github/workflows/conan-validate.yml")
    production_evidence = read_text(ROOT / ".github/workflows/production-evidence.yml")
    add(
        checks,
        "grpc workflow still opt-in",
        '-o "&:with_grpc=False"' in conan_validate
        and '-o "&:with_grpc=False"' in production_evidence,
        "Conan-using fixed-runner workflows keep gRPC disabled in the default dependency graph",
    )
    add(
        checks,
        "grpc not default cmake option",
        'option(BOOST_BUILD_GRPC' in root_cmake and 'OFF)' in root_cmake,
        "root CMake keeps BOOST_BUILD_GRPC default OFF",
    )


def validate_tcp_baseline(checks: list[dict[str, Any]], baseline_path: Path) -> None:
    summary = load_json(baseline_path)
    if not summary:
        add(
            checks,
            "tcp baseline evidence optional for local decision gate",
            True,
            "missing local baseline summary does not promote gRPC; decision remains deferred",
            "baseline",
        )
        return
    add(checks, "tcp baseline summary exists", True, str(baseline_path), "baseline")
    release_gates = summary.get("release_gates", {})
    if isinstance(release_gates, dict):
        add(
            checks,
            "tcp baseline release gates pass",
            release_gates.get("overall_pass") is True,
            f"release_gates.overall_pass={release_gates.get('overall_pass')}",
            "baseline",
        )
    cases = summary.get("cases", [])
    aggregates = summary.get("case_aggregates", [])
    case_count = len(cases) if isinstance(cases, list) else 0
    aggregate_count = len(aggregates) if isinstance(aggregates, list) else 0
    add(
        checks,
        "tcp baseline has scenario data",
        case_count > 0 or aggregate_count > 0,
        f"cases={case_count} case_aggregates={aggregate_count}",
        "baseline",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/default")
    parser.add_argument("--skip-build-targets", action="store_true")
    parser.add_argument(
        "--tcp-baseline-summary",
        type=Path,
        default=ROOT / "runtime/perf/release-baseline/summary.json",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/n6-v3-grpc-poc-decision-summary.json",
    )
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    validate_static_boundaries(checks)
    validate_tcp_baseline(checks, args.tcp_baseline_summary)

    steps: list[dict[str, Any]] = [
        run_step(
            "v3 proto schema contract",
            "proto_contract",
            [sys.executable, str(ROOT / "scripts/check_v3_proto_schema.py"), "--proto-dir", "proto/v3"],
            60,
        ),
        run_step(
            "v3 transport contract",
            "proto_contract",
            [
                sys.executable,
                str(ROOT / "scripts/check_v3_proto_schema.py"),
                "--proto-dir",
                "proto/v3",
                "--require-transport-contract",
            ],
            60,
        ),
    ]
    if not args.skip_build_targets:
        steps.append(
            run_step(
                "v3 CMake proto contract targets",
                "build_contract",
                [
                    "cmake",
                    "--build",
                    str(args.build_dir),
                    "--target",
                    "check_v3_proto_schema",
                    "check_v3_proto_transport_contract",
                ],
                180,
            )
        )

    failed_check = next((check for check in checks if not check["passed"]), None)
    failed_step = next((step for step in steps if step.get("status") != "passed"), None)
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    decision = {
        "status": "defer_default_transport",
        "default_transport": "sdk_tcp_gateway_backend_envelope",
        "grpc_profile": "experimental_only",
        "next_action": "continue_poc_only_after_generated_transport_full_flow_and_benchmark_exist",
    }
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": failed_check is None and failed_step is None,
        "passed": failed_check is None and failed_step is None,
        "failed_category": (failed_check or failed_step or {}).get("category", ""),
        "failed_step": (failed_check or failed_step or {}).get("name", ""),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "decision": decision,
        "artifacts": {
            "summary_path": str(summary_path),
            "tcp_baseline_summary_path": str(args.tcp_baseline_summary),
            "adr": str(ROOT / "docs/archive/process/v3-proto-grpc-adr.md"),
        },
        "checks": checks,
        "steps": steps,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"N6 v3 gRPC PoC decision gate: {'PASS' if summary['passed'] else 'FAIL'}")
    print(f"decision: {decision['status']} ({decision['grpc_profile']})")
    print(f"summary: {summary_path}")
    if failed_check:
        print(f"failed check: {failed_check['name']} - {failed_check['detail']}")
    if failed_step:
        print(f"failed step: {failed_step['name']}")
        if failed_step.get("stderr_tail"):
            print(failed_step["stderr_tail"])
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
