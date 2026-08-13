#!/usr/bin/env python3
"""Run the SDK full-flow example against a real gateway process."""

from __future__ import annotations
if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        parent for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "__init__.py").is_file()
    )
    sys.path.insert(0, str(repo_import_root))


import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]

from scripts.lib.sdk_full_flow_runtime import *  # noqa: E402,F401,F403

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=REPO_ROOT / "build/default")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--http-port", type=int, default=0)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument(
        "--python-package-client",
        type=Path,
        help="Use this Python interpreter with the installed wheel full-flow example",
    )
    parser.add_argument(
        "--backend-tls",
        action="store_true",
        help="Run gateway->backend traffic through the opt-in backend TLS profile",
    )
    parser.add_argument("--tls-cert-dir", type=Path, default=REPO_ROOT / "certs")
    parser.add_argument(
        "--gateway-tls-verify-mode",
        choices=["none", "server", "mutual"],
        default="none",
    )
    parser.add_argument("--gateway-tls-ca-cert-path", type=Path)
    parser.add_argument(
        "--backend-tls-verify-mode", choices=["none", "mutual"], default="none"
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/sdk-full-flow-client-summary.json",
    )
    parser.add_argument("--backend-ready-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--gateway-ready-timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--restart-gateway",
        action="store_true",
        help="Run the full flow, restart only the native gateway process, then run it again.",
    )
    args = parser.parse_args()

    gateway = resolve_executable(
        args.build_dir, "examples/v2_gateway_demo/v2_gateway_demo"
    )
    login_backend = resolve_executable(
        args.build_dir, "examples/v2_login_backend/v2_login_backend"
    )
    room_backend = resolve_executable(
        args.build_dir, "examples/v2_room_backend/v2_room_backend"
    )
    battle_backend = resolve_executable(
        args.build_dir, "examples/v2_battle_backend/v2_battle_backend"
    )
    match_backend = resolve_executable(
        args.build_dir, "examples/v2_match_backend/v2_match_backend"
    )
    leaderboard_backend = resolve_executable(
        args.build_dir, "examples/v2_leaderboard_backend/v2_leaderboard_backend"
    )
    client = resolve_executable(args.build_dir, "sdk/examples/sdk_full_flow_client")
    gateway_port = args.port if args.port > 0 else reserve_free_port(args.host)
    http_port = args.http_port if args.http_port > 0 else reserve_free_port(args.host)
    login_port = reserve_free_port(args.host)
    room_port = reserve_free_port(args.host)
    battle_port = reserve_free_port(args.host)
    match_port = reserve_free_port(args.host)
    leaderboard_port = reserve_free_port(args.host)
    checks: list[dict[str, Any]] = []
    gateway_restart_rto_seconds: float | None = None

    required_binaries = [
        gateway,
        login_backend,
        room_backend,
        battle_backend,
        match_backend,
        leaderboard_backend,
        *([] if args.python_package_client else [client]),
    ]
    missing_binaries = [path for path in required_binaries if not path.exists()]

    if not args.skip_build or missing_binaries:
        build_ok = run_command(
            "build-sdk-full-flow-targets",
            build_command_for_targets(
                args.build_dir,
                [
                    "v2_gateway_demo",
                    "v2_login_backend",
                    "v2_room_backend",
                    "v2_battle_backend",
                    "v2_match_backend",
                    "v2_leaderboard_backend",
                    "sdk_full_flow_client",
                ],
            ),
            checks,
        )
        if not build_ok:
            failed = [check for check in checks if not check["passed"]]
            return write_summary(args.summary_path, checks, failed)

        gateway = resolve_executable(
            args.build_dir, "examples/v2_gateway_demo/v2_gateway_demo"
        )
        login_backend = resolve_executable(
            args.build_dir, "examples/v2_login_backend/v2_login_backend"
        )
        room_backend = resolve_executable(
            args.build_dir, "examples/v2_room_backend/v2_room_backend"
        )
        battle_backend = resolve_executable(
            args.build_dir, "examples/v2_battle_backend/v2_battle_backend"
        )
        match_backend = resolve_executable(
            args.build_dir, "examples/v2_match_backend/v2_match_backend"
        )
        leaderboard_backend = resolve_executable(
            args.build_dir, "examples/v2_leaderboard_backend/v2_leaderboard_backend"
        )
        client = resolve_executable(args.build_dir, "sdk/examples/sdk_full_flow_client")

    processes: list[tuple[str, subprocess.Popen[str]]] = []
    try:
        base_env = os.environ.copy()
        base_env["V2_BACKEND_CONNECTION_POOL_SIZE"] = "1"
        extra_paths = process_runtime_path_entries(
            required_binaries
        ) + runtime_path_entries(args.build_dir)
        if extra_paths:
            base_env["PATH"] = os.pathsep.join(extra_paths + [base_env.get("PATH", "")])
        tls_cert_dir = (
            args.tls_cert_dir
            if args.tls_cert_dir.is_absolute()
            else REPO_ROOT / args.tls_cert_dir
        )
        gateway_tls_ca_cert_path = args.gateway_tls_ca_cert_path
        if (
            gateway_tls_ca_cert_path is not None
            and not gateway_tls_ca_cert_path.is_absolute()
        ):
            gateway_tls_ca_cert_path = REPO_ROOT / gateway_tls_ca_cert_path
        if args.backend_tls and not ensure_dev_certs(checks, tls_cert_dir):
            failed = [check for check in checks if not check["passed"]]
            return write_summary(
                args.summary_path,
                checks,
                failed,
                backend_tls=args.backend_tls,
                tls_cert_dir=tls_cert_dir,
                gateway_tls_verify_mode=args.gateway_tls_verify_mode,
                backend_tls_verify_mode=args.backend_tls_verify_mode,
            )
        temp_gateway_config = (
            REPO_ROOT / "runtime/validation/sdk-full-flow-temp-gateway.json"
        )
        write_temp_gateway_config(
            temp_gateway_config,
            http_port=http_port,
            login_port=login_port,
            room_port=room_port,
            battle_port=battle_port,
            match_port=match_port,
            leaderboard_port=leaderboard_port,
            backend_tls=args.backend_tls,
            cert_dir=tls_cert_dir,
            gateway_tls_verify_mode=args.gateway_tls_verify_mode,
            gateway_tls_ca_cert_path=gateway_tls_ca_cert_path,
        )
        backend_specs = [
            (
                "login",
                login_backend,
                login_port,
                [str(login_port)],
                {"SERVICE_PORT": str(login_port)},
            ),
            (
                "room",
                room_backend,
                room_port,
                [str(room_port)],
                {"SERVICE_PORT": str(room_port)},
            ),
            (
                "battle",
                battle_backend,
                battle_port,
                [str(battle_port)],
                {"SERVICE_PORT": str(battle_port)},
            ),
            (
                "matchmaking",
                match_backend,
                match_port,
                [str(match_port)],
                {"SERVICE_PORT": str(match_port), "MATCH_PORT": str(match_port)},
            ),
            (
                "leaderboard",
                leaderboard_backend,
                leaderboard_port,
                [str(leaderboard_port)],
                isolated_leaderboard_environment(leaderboard_port),
            ),
        ]
        for name, executable, port, extra_args, extra_env in backend_specs:
            env = dict(base_env)
            env.update(extra_env)
            if args.backend_tls:
                env.update(
                    {
                        "BACKEND_TLS_ENABLED": "true",
                        "BACKEND_TLS_CERT_CHAIN_PATH": str(tls_cert_dir / "server.crt"),
                        "BACKEND_TLS_PRIVATE_KEY_PATH": str(
                            tls_cert_dir / "server.key"
                        ),
                        "BACKEND_TLS_CA_CERT_PATH": str(tls_cert_dir / "ca.crt"),
                        "BACKEND_TLS_VERIFY_MODE": args.backend_tls_verify_mode,
                    }
                )
            proc = start_process(name, [str(executable), *extra_args], env, checks)
            if proc is not None:
                processes.append((name, proc))
            ready, ready_error = wait_for_process_port(
                proc,
                args.host,
                port,
                args.backend_ready_timeout_seconds,
            )
            ready_stdout, ready_stderr = ("", "")
            if not ready:
                ready_stdout, ready_stderr = process_output_snapshot(proc)
            checks.append(
                {
                    "name": f"{name}-backend-ready",
                    "passed": ready,
                    "command": [str(executable), *extra_args],
                    "duration_seconds": 0.0,
                    "stdout": ready_stdout,
                    "stderr": (
                        ""
                        if ready
                        else f"{name} backend did not become ready: {ready_error}; {ready_stderr}"
                    ),
                }
            )
            if not ready:
                failed = [check for check in checks if not check["passed"]]
                return write_summary(args.summary_path, checks, failed)

        gateway_env = dict(base_env)
        gateway_env["CONFIG_PATH"] = str(temp_gateway_config)
        gateway_command = [
            str(gateway),
            "--port",
            str(gateway_port),
            "--http-port",
            str(http_port),
            "--login-port",
            str(login_port),
            "--room-port",
            str(room_port),
            "--battle-port",
            str(battle_port),
            "--matchmaking-port",
            str(match_port),
            "--leaderboard-port",
            str(leaderboard_port),
        ]
        gateway_proc = start_process("gateway", gateway_command, gateway_env, checks)
        if gateway_proc is not None:
            processes.append(("gateway", gateway_proc))
        ready, ready_error = wait_for_process_port(
            gateway_proc,
            args.host,
            gateway_port,
            args.gateway_ready_timeout_seconds,
        )
        http_ready = ready and wait_for_http(
            f"http://{args.host}:{http_port}/health",
            args.gateway_ready_timeout_seconds,
        )
        ready_stdout, ready_stderr = ("", "")
        if not ready or not http_ready:
            ready_stdout, ready_stderr = process_output_snapshot(gateway_proc)
        checks.append(
            {
                "name": "gateway-ready",
                "passed": ready and http_ready,
                "command": [str(gateway), "--http-port", str(http_port)],
                "duration_seconds": 0.0,
                "stdout": ready_stdout,
                "stderr": (
                    ""
                    if ready and http_ready
                    else f"gateway TCP or HTTP endpoint did not become ready: {ready_error}; {ready_stderr}"
                ),
            }
        )
        if ready and http_ready:
            client_command = [str(client), args.host, str(gateway_port)]
            if args.python_package_client:
                client_command = [
                    str(args.python_package_client),
                    str(REPO_ROOT / "sdk/examples/python_full_flow.py"),
                    args.host,
                    str(gateway_port),
                ]
            first_client_name = (
                "run-sdk-full-flow-client-before-gateway-restart"
                if args.restart_gateway
                else "run-sdk-full-flow-client"
            )
            run_command(first_client_name, client_command, checks)
            time.sleep(8)
            add_backend_metric_check(
                checks,
                f"http://{args.host}:{http_port}/metrics/diagnostics/json",
            )
            add_sdk_flow_output_check(
                checks,
                python_package_client=bool(args.python_package_client),
                client_check_name=first_client_name,
            )
            if args.backend_tls:
                add_backend_tls_metric_check(
                    checks,
                    f"http://{args.host}:{http_port}/metrics/diagnostics/json",
                )
            if args.restart_gateway and not any(
                not check["passed"] for check in checks
            ):
                restart_started = time.monotonic()
                terminate_process("gateway-before-restart", gateway_proc, checks)
                processes = [
                    (name, proc) for name, proc in processes if proc is not gateway_proc
                ]
                gateway_proc = start_process(
                    "gateway-after-restart", gateway_command, gateway_env, checks
                )
                if gateway_proc is not None:
                    processes.append(("gateway-after-restart", gateway_proc))
                restarted, restart_error = wait_for_process_port(
                    gateway_proc,
                    args.host,
                    gateway_port,
                    args.gateway_ready_timeout_seconds,
                )
                restarted_http = restarted and wait_for_http(
                    f"http://{args.host}:{http_port}/health",
                    args.gateway_ready_timeout_seconds,
                )
                gateway_restart_rto_seconds = time.monotonic() - restart_started
                checks.append(
                    {
                        "name": "gateway-ready-after-native-restart",
                        "passed": restarted and restarted_http,
                        "command": gateway_command,
                        "duration_seconds": round(gateway_restart_rto_seconds, 3),
                        "stdout": "",
                        "stderr": "" if restarted and restarted_http else restart_error,
                    }
                )
                if restarted and restarted_http:
                    run_command(
                        "run-sdk-full-flow-client-after-gateway-restart",
                        client_command,
                        checks,
                    )
                    add_sdk_flow_output_check(
                        checks,
                        python_package_client=bool(args.python_package_client),
                        client_check_name="run-sdk-full-flow-client-after-gateway-restart",
                    )
    finally:
        for name, proc in reversed(processes):
            terminate_process(name, proc, checks)
        temp_gateway_config = (
            REPO_ROOT / "runtime/validation/sdk-full-flow-temp-gateway.json"
        )
        if temp_gateway_config.exists():
            temp_gateway_config.unlink()

    failed = [check for check in checks if not check["passed"]]
    return write_summary(
        args.summary_path,
        checks,
        failed,
        backend_tls=args.backend_tls,
        tls_cert_dir=tls_cert_dir if args.backend_tls else None,
        gateway_tls_verify_mode=args.gateway_tls_verify_mode,
        backend_tls_verify_mode=args.backend_tls_verify_mode,
        native_gateway_restart=args.restart_gateway,
        gateway_restart_rto_seconds=gateway_restart_rto_seconds,
    )


def write_summary(
    path: Path,
    checks: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    backend_tls: bool = False,
    tls_cert_dir: Path | None = None,
    gateway_tls_verify_mode: str = "none",
    backend_tls_verify_mode: str = "none",
    native_gateway_restart: bool = False,
    gateway_restart_rto_seconds: float | None = None,
) -> int:
    summary = {
        "summary_version": 2,
        "passed": not failed,
        "backend_tls": backend_tls,
        "tls_cert_dir": str(tls_cert_dir or ""),
        "gateway_tls_verify_mode": gateway_tls_verify_mode,
        "backend_tls_verify_mode": backend_tls_verify_mode,
        "native_gateway_restart": native_gateway_restart,
        "gateway_restart_rto_seconds": (
            round(gateway_restart_rto_seconds, 3)
            if gateway_restart_rto_seconds is not None
            else None
        ),
        "native_platform": {
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "failed_step": failed[0]["name"] if failed else "",
        "checks": checks,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    safe_print(
        f"sdk full-flow client: {'PASS' if summary['passed'] else 'FAIL'} "
        f"({len(checks) - len(failed)}/{len(checks)} checks)"
    )
    if failed:
        for check in failed:
            safe_print(f"  - {check['name']}")
            if check.get("stdout"):
                safe_print(check["stdout"])
            if check.get("stderr"):
                safe_print(check["stderr"])
        return 1
    safe_print(f"summary: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
