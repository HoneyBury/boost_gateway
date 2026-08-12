#!/usr/bin/env python3
"""Run the SDK full-flow example against a real gateway process."""

from __future__ import annotations

import argparse
import json
import platform
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


def safe_print(value: Any = "") -> None:
    text = str(value)
    encoding = sys.stdout.encoding or "utf-8"
    print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def resolve_executable(build_dir: Path, relative: str) -> Path:
    base = build_dir / relative
    if base.exists():
        return base
    raise FileNotFoundError(f"executable not found: {base}")


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
        if value not in seen:
            seen.add(value)
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
        [sys.executable, str(REPO_ROOT / "scripts/tools/gen_certs.py")],
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


def isolated_leaderboard_environment(port: int) -> dict[str, str]:
    return {
        "SERVICE_PORT": str(port),
        "LEADERBOARD_PORT": str(port),
        "BOOST_DISABLE_REDIS_AUTO_CONNECT": "1",
    }


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
