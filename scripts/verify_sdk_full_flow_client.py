#!/usr/bin/env python3
"""Run the SDK full-flow example against a real gateway process."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_command(name: str, command: list[str], checks: list[dict[str, Any]]) -> bool:
    started = time.monotonic()
    result = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True)
    duration = time.monotonic() - started
    passed = result.returncode == 0
    checks.append(
        {
            "name": name,
            "passed": passed,
            "command": command,
            "duration_seconds": round(duration, 3),
            "stdout": result.stdout[-8000:],
            "stderr": result.stderr[-8000:],
        }
    )
    return passed


def wait_for_port(host: str, port: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


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
    try:
        return subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
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


def terminate_process(name: str, proc: subprocess.Popen[str], checks: list[dict[str, Any]]) -> None:
    proc.terminate()
    try:
        stdout, stderr = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate(timeout=5)
    checks.append(
        {
            "name": f"{name}-shutdown",
            "passed": True,
            "command": [f"terminate-{name}"],
            "stdout": stdout[-8000:],
            "stderr": stderr[-8000:],
        }
    )


def add_backend_metric_check(checks: list[dict[str, Any]], diagnostics_url: str) -> None:
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
                "stderr": "" if not missing else "missing positive requests for: " + ", ".join(missing),
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=REPO_ROOT / "build/default")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--http-port", type=int, default=0)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/sdk-full-flow-client-summary.json",
    )
    args = parser.parse_args()

    gateway = args.build_dir / "examples/v2_gateway_demo/v2_gateway_demo"
    login_backend = args.build_dir / "examples/v2_login_backend/v2_login_backend"
    room_backend = args.build_dir / "examples/v2_room_backend/v2_room_backend"
    battle_backend = args.build_dir / "examples/v2_battle_backend/v2_battle_backend"
    match_backend = args.build_dir / "examples/v2_match_backend/v2_match_backend"
    leaderboard_backend = args.build_dir / "examples/v2_leaderboard_backend/v2_leaderboard_backend"
    client = args.build_dir / "sdk/examples/sdk_full_flow_client"
    gateway_port = args.port if args.port > 0 else reserve_free_port(args.host)
    http_port = args.http_port if args.http_port > 0 else reserve_free_port(args.host)
    login_port = reserve_free_port(args.host)
    room_port = reserve_free_port(args.host)
    battle_port = reserve_free_port(args.host)
    match_port = reserve_free_port(args.host)
    leaderboard_port = reserve_free_port(args.host)
    checks: list[dict[str, Any]] = []

    if not args.skip_build:
        build_ok = run_command(
            "build-sdk-full-flow-targets",
            [
                "cmake",
                "--build",
                str(args.build_dir),
                "--target",
                "v2_gateway_demo",
                "v2_login_backend",
                "v2_room_backend",
                "v2_battle_backend",
                "v2_match_backend",
                "v2_leaderboard_backend",
                "sdk_full_flow_client",
            ],
            checks,
        )
        if not build_ok:
            failed = [check for check in checks if not check["passed"]]
            return write_summary(args.summary_path, checks, failed)

    processes: list[tuple[str, subprocess.Popen[str]]] = []
    try:
        base_env = os.environ.copy()
        no_config = str(REPO_ROOT / "runtime/validation/sdk-full-flow-no-config.json")

        backend_specs = [
            ("login", login_backend, login_port, {"SERVICE_PORT": str(login_port)}),
            ("room", room_backend, room_port, {"SERVICE_PORT": str(room_port)}),
            ("battle", battle_backend, battle_port, {"SERVICE_PORT": str(battle_port)}),
            ("matchmaking", match_backend, match_port, {"SERVICE_PORT": str(match_port), "MATCH_PORT": str(match_port)}),
            ("leaderboard", leaderboard_backend, leaderboard_port, {"SERVICE_PORT": str(leaderboard_port), "LEADERBOARD_PORT": str(leaderboard_port)}),
        ]
        for name, executable, port, extra_env in backend_specs:
            env = dict(base_env)
            env.update(extra_env)
            env["CONFIG_PATH"] = no_config
            proc = start_process(name, [str(executable)], env, checks)
            if proc is not None:
                processes.append((name, proc))
            ready = proc is not None and wait_for_port(args.host, port, 10.0)
            checks.append(
                {
                    "name": f"{name}-backend-ready",
                "passed": ready,
                "command": [str(executable)],
                "duration_seconds": 0.0,
                "stdout": "",
                "stderr": "" if ready else f"{name} backend did not open TCP port {port} within 10s",
                }
            )
            if not ready:
                failed = [check for check in checks if not check["passed"]]
                return write_summary(args.summary_path, checks, failed)

        gateway_env = dict(base_env)
        gateway_env["CONFIG_PATH"] = no_config
        gateway_proc = start_process(
            "gateway",
            [
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
            ],
            gateway_env,
            checks,
        )
        if gateway_proc is not None:
            processes.append(("gateway", gateway_proc))
        ready = wait_for_port(args.host, gateway_port, 10.0)
        http_ready = wait_for_http(f"http://{args.host}:{http_port}/health", 10.0)
        checks.append(
            {
                "name": "gateway-ready",
                "passed": ready and http_ready,
                "command": [str(gateway), "--http-port", str(http_port)],
                "duration_seconds": 0.0,
                "stdout": "",
                "stderr": "" if ready and http_ready else "gateway TCP or HTTP endpoint did not become ready",
            }
        )
        if ready and http_ready:
            run_command(
                "run-sdk-full-flow-client",
                [str(client), args.host, str(gateway_port)],
                checks,
            )
            add_backend_metric_check(
                checks,
                f"http://{args.host}:{http_port}/metrics/diagnostics/json",
            )
    finally:
        for name, proc in reversed(processes):
            terminate_process(name, proc, checks)

    failed = [check for check in checks if not check["passed"]]
    return write_summary(args.summary_path, checks, failed)


def write_summary(path: Path, checks: list[dict[str, Any]], failed: list[dict[str, Any]]) -> int:
    summary = {
        "passed": not failed,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"sdk full-flow client: {'PASS' if summary['passed'] else 'FAIL'} "
        f"({len(checks) - len(failed)}/{len(checks)} checks)"
    )
    if failed:
        for check in failed:
            print(f"  - {check['name']}")
            if check.get("stdout"):
                print(check["stdout"])
            if check.get("stderr"):
                print(check["stderr"])
        return 1
    print(f"summary: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
