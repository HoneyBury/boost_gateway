#!/usr/bin/env python3
"""Run SDK full-flow against an already deployed Kubernetes gateway Service."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout)


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_port(host: str, port: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=3) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("expected JSON object")
    return data


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str, command: list[str] | None = None) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail[-4000:], "command": command or []})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/default")
    parser.add_argument("--namespace", default="boost-gateway")
    parser.add_argument("--service", default="gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--gateway-port", type=int, default=0)
    parser.add_argument("--http-port", type=int, default=0)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--summary-path", type=Path, default=ROOT / "runtime/validation/k8s-full-flow-summary.json")
    args = parser.parse_args()

    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    gateway_port = args.gateway_port or reserve_port()
    http_port = args.http_port or reserve_port()
    client = args.build_dir / "sdk/examples/sdk_full_flow_client"
    checks: list[dict[str, Any]] = []

    if not args.skip_build:
        build = run(["cmake", "--build", str(args.build_dir), "--target", "sdk_full_flow_client"], timeout=180)
        add(checks, "build-sdk-full-flow-client", build.returncode == 0, build.stdout + build.stderr, build.args)
        if build.returncode != 0:
            return write_summary(summary_path, checks)

    rollout = run(["kubectl", "-n", args.namespace, "rollout", "status", f"deployment/{args.service}", "--timeout=120s"], timeout=140)
    add(checks, "gateway-rollout-ready", rollout.returncode == 0, rollout.stdout + rollout.stderr, rollout.args)
    if rollout.returncode != 0:
        return write_summary(summary_path, checks)

    port_forward_cmd = [
        "kubectl",
        "-n",
        args.namespace,
        "port-forward",
        f"svc/{args.service}",
        f"{gateway_port}:9201",
        f"{http_port}:9080",
    ]
    proc = subprocess.Popen(port_forward_cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        ready = wait_for_port(args.host, gateway_port, 20.0) and wait_for_port(args.host, http_port, 20.0)
        add(checks, "port-forward-ready", ready, f"gateway={gateway_port}, http={http_port}", port_forward_cmd)
        if ready:
            health = fetch_json(f"http://{args.host}:{http_port}/health")
            add(checks, "gateway-health", health.get("status") in {"pass", "ok", "warn"}, json.dumps(health, sort_keys=True))
            full_flow = run([str(client), args.host, str(gateway_port)], timeout=120)
            add(checks, "sdk-full-flow-against-k8s", full_flow.returncode == 0, full_flow.stdout + full_flow.stderr, full_flow.args)
            diagnostics = fetch_json(f"http://{args.host}:{http_port}/metrics/diagnostics/json")
            backend_metrics = diagnostics.get("backend_metrics", {})
            covered = [
                svc for svc in ("login", "room", "battle", "matchmaking", "leaderboard")
                if isinstance(backend_metrics, dict)
                and isinstance(backend_metrics.get(svc), dict)
                and int(backend_metrics[svc].get("total_requests", 0)) > 0
            ]
            add(checks, "backend-metrics-cover-business-flow", len(covered) == 5, ",".join(covered))
    finally:
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate(timeout=5)
        add(checks, "port-forward-shutdown", True, (stdout or "") + (stderr or ""), ["terminate-port-forward"])

    return write_summary(summary_path, checks)


def write_summary(path: Path, checks: list[dict[str, Any]]) -> int:
    failed = [check for check in checks if not check["passed"]]
    summary = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "passed": not failed,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"k8s full-flow: {'PASS' if summary['passed'] else 'FAIL'} ({len(checks)-len(failed)}/{len(checks)} checks)")
    print(f"summary: {path}")
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
