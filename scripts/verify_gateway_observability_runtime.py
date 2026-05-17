#!/usr/bin/env python3
"""Run a real gateway process and verify its HTTP observability endpoints."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_command(name: str, command: list[str], checks: list[dict[str, Any]]) -> bool:
    result = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True)
    passed = result.returncode == 0
    checks.append(
        {
            "name": name,
            "passed": passed,
            "command": command,
            "stdout": result.stdout[-8000:],
            "stderr": result.stderr[-8000:],
        }
    )
    return passed


def reserve_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def wait_for_port(host: str, port: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def fetch_url(url: str, timeout_s: float = 3.0) -> tuple[int, str, str]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            content_type = response.headers.get("content-type", "")
            return response.status, response.read().decode("utf-8", errors="replace"), content_type
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace"), exc.headers.get("content-type", "")


def wait_for_http_ok(url: str, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            status, _, _ = fetch_url(url, timeout_s=0.5)
            if status == 200:
                return True
        except OSError:
            pass
        time.sleep(0.1)
    return False


def add_http_check(
    checks: list[dict[str, Any]],
    name: str,
    url: str,
    validator: Any,
) -> None:
    status, body, content_type = fetch_url(url)
    passed = False
    error = ""
    try:
        passed = status == 200 and bool(validator(body, content_type))
    except Exception as exc:  # noqa: BLE001 - recorded into validation summary
        error = str(exc)
    checks.append(
        {
            "name": name,
            "passed": passed,
            "command": ["GET", url],
            "http_status": status,
            "content_type": content_type,
            "stdout": body[-8000:],
            "stderr": error,
        }
    )


def json_doc(body: str) -> dict[str, Any]:
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("expected JSON object")
    return parsed


def positive_int(doc: dict[str, Any], key: str) -> bool:
    value = doc.get(key)
    return isinstance(value, int) and value > 0


def write_summary(path: Path, checks: list[dict[str, Any]]) -> int:
    failed = [check for check in checks if not check["passed"]]
    summary = {
        "passed": not failed,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"gateway observability runtime: {'PASS' if summary['passed'] else 'FAIL'} "
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=REPO_ROOT / "build/default")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--gateway-port", type=int, default=9201)
    parser.add_argument("--http-port", type=int, default=0)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/gateway-observability-runtime-summary.json",
    )
    args = parser.parse_args()

    gateway = args.build_dir / "examples/v2_gateway_demo/v2_gateway_demo"
    client = args.build_dir / "sdk/examples/sdk_full_flow_client"
    http_port = args.http_port if args.http_port > 0 else reserve_free_port(args.host)
    checks: list[dict[str, Any]] = []

    if not args.skip_build:
        build_ok = run_command(
            "build-runtime-observability-targets",
            ["cmake", "--build", str(args.build_dir), "--target", "v2_gateway_demo", "sdk_full_flow_client"],
            checks,
        )
        if not build_ok:
            return write_summary(args.summary_path, checks)

    gateway_proc: subprocess.Popen[str] | None = None
    try:
        gateway_proc = subprocess.Popen(
            [str(gateway), "--http-port", str(http_port)],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        gateway_ready = wait_for_port(args.host, args.gateway_port, 10.0)
        http_ready = wait_for_http_ok(f"http://{args.host}:{http_port}/health", 10.0)
        checks.append(
            {
                "name": "gateway-and-http-ready",
                "passed": gateway_ready and http_ready,
                "command": [str(gateway), "--http-port", str(http_port)],
                "stdout": "",
                "stderr": "" if gateway_ready and http_ready else "gateway TCP or HTTP endpoint did not become ready",
            }
        )
        if gateway_ready and http_ready:
            run_command(
                "run-sdk-business-traffic",
                [str(client), args.host, str(args.gateway_port)],
                checks,
            )

            base = f"http://{args.host}:{http_port}"
            add_http_check(
                checks,
                "health-endpoint",
                f"{base}/health",
                lambda body, _: json_doc(body).get("status") in {"pass", "ok", "warn"},
            )
            add_http_check(
                checks,
                "ready-endpoint",
                f"{base}/ready",
                lambda body, _: json_doc(body).get("ready") is True,
            )
            add_http_check(
                checks,
                "prometheus-metrics-endpoint",
                f"{base}/metrics",
                lambda body, content_type: "text/plain" in content_type
                and "gateway_accepted_sessions_total" in body
                and "gateway_outbound_dispatches_total" in body,
            )
            add_http_check(
                checks,
                "json-metrics-runtime-counters",
                f"{base}/metrics/json",
                lambda body, _: positive_int(json_doc(body), "total_accepted_sessions")
                and positive_int(json_doc(body), "total_outbound_dispatches"),
            )
            add_http_check(
                checks,
                "diagnostics-json-runtime-shape",
                f"{base}/metrics/diagnostics/json",
                lambda body, _: isinstance(json_doc(body).get("io_cores"), list)
                and isinstance(json_doc(body).get("backend_metrics"), dict),
            )
    finally:
        if gateway_proc is not None:
            gateway_proc.terminate()
            try:
                stdout, stderr = gateway_proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                gateway_proc.kill()
                stdout, stderr = gateway_proc.communicate(timeout=5)
            checks.append(
                {
                    "name": "gateway-shutdown",
                    "passed": True,
                    "command": ["terminate-gateway"],
                    "stdout": stdout[-8000:],
                    "stderr": stderr[-8000:],
                }
            )

    return write_summary(args.summary_path, checks)


if __name__ == "__main__":
    raise SystemExit(main())
