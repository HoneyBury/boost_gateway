#!/usr/bin/env python3
"""Run the SDK full-flow example against a real gateway process."""

from __future__ import annotations

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


def safe_print(value: Any = "") -> None:
    text = str(value)
    encoding = sys.stdout.encoding or "utf-8"
    print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def resolve_executable(build_dir: Path, relative: str) -> Path:
    base = build_dir / relative
    candidates = [
        base,
        base.with_suffix(".exe"),
        base / "Release" / (base.name + ".exe"),
        base.parent / "Release" / (base.name + ".exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[1]


def build_command_for_targets(build_dir: Path, targets: list[str]) -> list[str]:
    command = ["cmake", "--build", str(build_dir)]
    if (build_dir / "boost_gateway.sln").exists():
        command.extend(["--config", "Release"])
    command.extend(["--target", *targets])
    return command


def runtime_path_entries(build_dir: Path) -> list[str]:
    candidates = [
        build_dir / "bin/Release",
        build_dir / "_deps/fmt-build/bin/Release",
        build_dir / "_deps/spdlog-build/Release",
        build_dir / "_deps/hiredis-build/Release",
    ]
    return [str(path) for path in candidates if path.exists()]


def process_runtime_path_entries(paths: list[Path]) -> list[str]:
    entries: list[str] = []
    seen: set[str] = set()
    for path in paths:
        parent = path.parent
        if not parent.exists():
            continue
        value = str(parent)
        key = value.lower() if os.name == "nt" else value
        if key not in seen:
            seen.add(key)
            entries.append(value)
    return entries


def write_temp_gateway_config(
    path: Path,
    http_port: int,
    login_port: int,
    room_port: int,
    battle_port: int,
    match_port: int,
    leaderboard_port: int,
    backend_tls: bool = False,
    cert_dir: Path | None = None,
    gateway_tls_verify_mode: str = "none",
    gateway_tls_ca_cert_path: Path | None = None,
) -> None:
    document = {
        "gateway": {
            "http_management_port": http_port,
        },
        "backends": {
            "login": {"host": "127.0.0.1", "port": login_port},
            "room": {"host": "127.0.0.1", "port": room_port},
            "battle": {"host": "127.0.0.1", "port": battle_port},
            "match": {"host": "127.0.0.1", "port": match_port},
            "leaderboard": {"host": "127.0.0.1", "port": leaderboard_port},
        },
    }
    if backend_tls:
        cert_root = cert_dir or (REPO_ROOT / "certs")
        document["feature_flags"] = {
            "v3_tls_enabled": {"enabled": True, "rollout_percentage": 100},
        }
        document["tls"] = {
            "cert_chain_path": str(cert_root / "server.crt"),
            "private_key_path": str(cert_root / "server.key"),
            "ca_cert_path": str(gateway_tls_ca_cert_path or (cert_root / "ca.crt")),
            "verify_mode": gateway_tls_verify_mode,
        }
        document["security_policy"] = {
            "require_tls": True,
            "login": {"tls_required": True, "mtls_required": False},
            "room": {"tls_required": True, "mtls_required": False},
            "battle": {"tls_required": True, "mtls_required": False},
            "match": {"tls_required": True, "mtls_required": False},
            "leaderboard": {"tls_required": True, "mtls_required": False},
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")


def run_command(name: str, command: list[str], checks: list[dict[str, Any]]) -> bool:
    started = time.monotonic()
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    duration = time.monotonic() - started
    passed = result.returncode == 0
    checks.append(
        {
            "name": name,
            "passed": passed,
            "command": command,
            "duration_seconds": round(duration, 3),
            "stdout": (result.stdout or "")[-8000:],
            "stderr": (result.stderr or "")[-8000:],
        }
    )
    return passed


def ensure_dev_certs(checks: list[dict[str, Any]], cert_dir: Path) -> bool:
    certs = [cert_dir / "ca.crt", cert_dir / "server.crt", cert_dir / "server.key"]
    if all(path.exists() for path in certs):
        checks.append(
            {
                "name": "backend-tls-dev-certs-present",
                "passed": True,
                "command": ["check", "certs"],
                "duration_seconds": 0.0,
                "stdout": "",
                "stderr": "",
            }
        )
        return True
    if cert_dir.resolve() != (REPO_ROOT / "certs").resolve():
        checks.append(
            {
                "name": "backend-tls-dev-certs-present",
                "passed": False,
                "command": ["check", str(cert_dir)],
                "duration_seconds": 0.0,
                "stdout": "",
                "stderr": f"missing required TLS files in {cert_dir}",
            }
        )
        return False
    return run_command(
        "generate-backend-tls-dev-certs",
        [sys.executable, str(REPO_ROOT / "scripts" / "gen_certs.py")],
        checks,
    )


def wait_for_port(host: str, port: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def wait_for_process_port(
    proc: subprocess.Popen[str] | None,
    host: str,
    port: int,
    timeout_s: float,
) -> tuple[bool, str]:
    if proc is None:
        return False, "process did not start"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return (
                False,
                f"process exited before opening TCP port {port}, exit_code={proc.returncode}",
            )
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True, ""
        except OSError:
            time.sleep(0.1)
    if proc.poll() is not None:
        return (
            False,
            f"process exited before opening TCP port {port}, exit_code={proc.returncode}",
        )
    return False, f"TCP port {port} did not open within {timeout_s:g}s"


def process_output_snapshot(proc: subprocess.Popen[str] | None) -> tuple[str, str]:
    if proc is None or proc.poll() is None:
        stdout_path = (
            getattr(proc, "_boost_stdout_path", None) if proc is not None else None
        )
        stderr_path = (
            getattr(proc, "_boost_stderr_path", None) if proc is not None else None
        )
        stdout_file = (
            getattr(proc, "_boost_stdout_file", None) if proc is not None else None
        )
        stderr_file = (
            getattr(proc, "_boost_stderr_file", None) if proc is not None else None
        )
        for handle in (stdout_file, stderr_file):
            if handle is not None:
                try:
                    handle.flush()
                except OSError:
                    pass
        return read_process_log_tail(stdout_path), read_process_log_tail(stderr_path)
    try:
        stdout, stderr = proc.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        return "", ""
    return (stdout or "")[-30000:], (stderr or "")[-30000:]


def read_process_log_tail(path: Path | None, limit: int = 30000) -> str:
    if path is None or not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


def reserve_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def fetch_json(url: str, timeout_s: float = 3.0) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        parsed = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(parsed, dict):
        raise ValueError("expected JSON object")
    return parsed


def wait_for_http(url: str, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            fetch_json(url, timeout_s=0.5)
            return True
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            time.sleep(0.1)
    return False


def start_process(
    name: str,
    command: list[str],
    env: dict[str, str],
    checks: list[dict[str, Any]],
) -> subprocess.Popen[str] | None:
    log_dir = REPO_ROOT / "runtime/validation/process-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{int(time.time() * 1000)}-{name}"
    stdout_path = log_dir / f"{stamp}.stdout.log"
    stderr_path = log_dir / f"{stamp}.stderr.log"
    stdout_file = stdout_path.open("w+", encoding="utf-8", errors="replace")
    stderr_file = stderr_path.open("w+", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=stdout_file,
            stderr=stderr_file,
        )
        proc._boost_stdout_path = stdout_path  # type: ignore[attr-defined]
        proc._boost_stderr_path = stderr_path  # type: ignore[attr-defined]
        proc._boost_stdout_file = stdout_file  # type: ignore[attr-defined]
        proc._boost_stderr_file = stderr_file  # type: ignore[attr-defined]
        return proc
    except OSError as exc:
        stdout_file.close()
        stderr_file.close()
        checks.append(
            {
                "name": f"start-{name}",
                "passed": False,
                "command": command,
                "stdout": "",
                "stderr": str(exc),
            }
        )
        return None


def terminate_process(
    name: str, proc: subprocess.Popen[str], checks: list[dict[str, Any]]
) -> None:
    proc.terminate()
    try:
        proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=5)
    stdout_file = getattr(proc, "_boost_stdout_file", None)
    stderr_file = getattr(proc, "_boost_stderr_file", None)
    for handle in (stdout_file, stderr_file):
        if handle is not None:
            try:
                handle.flush()
                handle.close()
            except OSError:
                pass
    stdout = read_process_log_tail(getattr(proc, "_boost_stdout_path", None))
    stderr = read_process_log_tail(getattr(proc, "_boost_stderr_path", None))
    checks.append(
        {
            "name": f"{name}-shutdown",
            "passed": True,
            "command": [f"terminate-{name}"],
            "stdout": stdout[-30000:],
            "stderr": stderr[-30000:],
        }
    )


def add_backend_metric_check(
    checks: list[dict[str, Any]], diagnostics_url: str
) -> None:
    try:
        doc = fetch_json(diagnostics_url)
        backend_metrics = doc.get("backend_metrics", {})
        if not isinstance(backend_metrics, dict):
            raise ValueError("backend_metrics is not an object")
        expected = ["login", "room", "battle", "matchmaking", "leaderboard"]
        missing = []
        for service in expected:
            snap = backend_metrics.get(service)
            if not isinstance(snap, dict) or int(snap.get("total_requests", 0)) <= 0:
                missing.append(service)
        leaderboard_requests = 0
        leaderboard_snap = backend_metrics.get("leaderboard")
        if isinstance(leaderboard_snap, dict):
            leaderboard_requests = int(leaderboard_snap.get("total_requests", 0))
        if leaderboard_requests < 6:
            missing.append("leaderboard>=6_requests")
        checks.append(
            {
                "name": "backend-metrics-cover-six-service-flow",
                "passed": not missing,
                "command": ["GET", diagnostics_url],
                "stdout": json.dumps(backend_metrics, indent=2, sort_keys=True)[-8000:],
                "stderr": (
                    ""
                    if not missing
                    else "missing positive requests for: " + ", ".join(missing)
                ),
            }
        )
    except Exception as exc:  # noqa: BLE001 - recorded into validation summary
        checks.append(
            {
                "name": "backend-metrics-cover-six-service-flow",
                "passed": False,
                "command": ["GET", diagnostics_url],
                "stdout": "",
                "stderr": str(exc),
            }
        )


def add_sdk_flow_output_check(
    checks: list[dict[str, Any]],
    python_package_client: bool = False,
    client_check_name: str = "run-sdk-full-flow-client",
) -> None:
    client_check = next(
        (check for check in checks if check.get("name") == client_check_name),
        None,
    )
    output = (client_check or {}).get("stdout", "")
    expected_fragments = (
        [
            "Both connected.",
            "Alice logged in",
            "Echo:",
            "Match join/status/leave OK.",
            "Match found:",
            "Room auto-created:",
            "Battle auto-started.",
            "Battle finished (surrender).",
            "Manual leaderboard submit path OK.",
            "Leaderboard rank query path OK.",
            "Both left room.",
            "=== ALL TESTS PASSED ===",
        ]
        if not python_package_client
        else [
            "Both connected.",
            "Echo:",
            "Match join/status/leave OK.",
            "Both ready.",
            "Auto settlement leaderboard and manual submit paths OK.",
            "Both left.",
            "=== ALL TESTS PASSED ===",
        ]
    )
    missing = [fragment for fragment in expected_fragments if fragment not in output]
    checks.append(
        {
            "name": "sdk-output-covers-full-business-flow",
            "passed": client_check is not None
            and bool(client_check.get("passed"))
            and not missing,
            "command": ["inspect", client_check_name, "stdout"],
            "stdout": output[-8000:],
            "stderr": (
                "" if not missing else "missing output fragments: " + ", ".join(missing)
            ),
        }
    )


def add_backend_tls_metric_check(
    checks: list[dict[str, Any]], diagnostics_url: str
) -> None:
    try:
        doc = fetch_json(diagnostics_url)
        backend_metrics = doc.get("backend_metrics", {})
        login_snap = backend_metrics.get("login")
        login_success = (
            isinstance(login_snap, dict)
            and int(login_snap.get("total_successes", 0)) > 0
        )
        checks.append(
            {
                "name": "backend-tls-full-flow-success-metrics",
                "passed": login_success,
                "command": ["GET", diagnostics_url],
                "stdout": json.dumps(backend_metrics, indent=2, sort_keys=True)[-8000:],
                "stderr": (
                    ""
                    if login_success
                    else "missing TLS success metrics for login; other business paths may use gateway fast-path routing without bridge metrics"
                ),
            }
        )
    except Exception as exc:  # noqa: BLE001 - recorded into validation summary
        checks.append(
            {
                "name": "backend-tls-full-flow-success-metrics",
                "passed": False,
                "command": ["GET", diagnostics_url],
                "stdout": "",
                "stderr": str(exc),
            }
        )


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
                {
                    "SERVICE_PORT": str(leaderboard_port),
                    "LEADERBOARD_PORT": str(leaderboard_port),
                },
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
