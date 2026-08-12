"""Pre-production recovery responsibility module: recovery_drill_runtime."""

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
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.lib.evidence_provenance import build_evidence_provenance
from scripts.lib.recovery_evidence import (
    write_command_summary,
    write_drill_record as _write_drill_record,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_IMAGE_BINARIES = {
    "gateway": ("v2_gateway_demo", "/app/bin/v2_gateway_demo"),
    "login-backend": ("v2_login_backend", "/app/bin/backend"),
    "room-backend": ("v2_room_backend", "/app/bin/backend"),
    "battle-backend": ("v2_battle_backend", "/app/bin/backend"),
    "matchmaking-backend": ("v2_match_backend", "/app/bin/backend"),
    "leaderboard-backend": ("v2_leaderboard_backend", "/app/bin/backend"),
}



def tail(value: str | bytes | None, max_chars: int = 6000) -> str:
    if value is None:
        return ""
    text = (
        value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    )
    return text if len(text) <= max_chars else text[-max_chars:]


def emit_text(text: str, *, stderr: bool = False) -> None:
    stream = sys.stderr if stderr else sys.stdout
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        stream.buffer.write(text.encode(encoding, errors="replace"))


def run_step(
    name: str, category: str, command: list[str], timeout_seconds: int
) -> dict[str, Any]:
    print(f"==> {name}", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
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
            "command": command,
            "status": "timeout",
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(exc.stdout),
            "stderr_tail": tail(exc.stderr),
        }

    if completed.stdout:
        emit_text(completed.stdout)
    if completed.stderr:
        emit_text(completed.stderr, stderr=True)
    return {
        "name": name,
        "category": category,
        "command": command,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(completed.stdout),
        "stderr_tail": tail(completed.stderr),
    }


def start_background_step(
    name: str,
    category: str,
    command: list[str],
) -> tuple[subprocess.Popen[str] | None, dict[str, Any]]:
    print(f"==> {name}", flush=True)
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        return None, {
            "name": name,
            "category": category,
            "command": command,
            "status": "failed",
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": "",
            "stderr_tail": str(exc),
        }
    return process, {
        "name": name,
        "category": category,
        "command": command,
        "status": "passed",
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": f"started pid={process.pid}",
        "stderr_tail": "",
    }


def wait_background_step(
    name: str,
    category: str,
    command: list[str],
    process: subprocess.Popen[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    print(f"==> {name}", flush=True)
    started = time.monotonic()
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        status = "passed" if process.returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        status = "timeout"
    if stdout:
        emit_text(stdout)
    if stderr:
        emit_text(stderr, stderr=True)
    return {
        "name": name,
        "category": category,
        "command": command,
        "status": status,
        "returncode": process.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }


def wait_for_prometheus_alert_firing(
    process: subprocess.Popen[str],
    alert_name: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + timeout_seconds
    last_state = "inactive"
    last_error = ""
    while time.monotonic() < deadline:
        returncode = process.poll()
        if returncode is not None:
            return {
                "name": "R5 wait for Redis dependency alert firing",
                "category": "prometheus_alert_runtime",
                "command": ["GET", "http://127.0.0.1:9090/api/v1/alerts"],
                "status": "failed",
                "returncode": returncode,
                "duration_seconds": round(time.monotonic() - started, 3),
                "stdout_tail": "",
                "stderr_tail": "Prometheus alert verifier exited before the fault window completed",
            }
        try:
            document = fetch_json("http://127.0.0.1:9090/api/v1/alerts", timeout_s=5.0)
            data = document.get("data")
            if not isinstance(data, dict):
                raise ValueError("Prometheus alerts response data must be an object")
            alerts = data.get("alerts", [])
            if not isinstance(alerts, list):
                raise ValueError(
                    "Prometheus alerts response data.alerts must be a list"
                )
            matching = [
                alert
                for alert in alerts
                if isinstance(alert, dict)
                and isinstance(alert.get("labels"), dict)
                and alert["labels"].get("alertname") == alert_name
            ]
            last_state = (
                str(matching[0].get("state", "inactive")) if matching else "inactive"
            )
            if last_state == "firing":
                return {
                    "name": "R5 wait for Redis dependency alert firing",
                    "category": "prometheus_alert_runtime",
                    "command": ["GET", "http://127.0.0.1:9090/api/v1/alerts"],
                    "status": "passed",
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "stdout_tail": json.dumps(matching[0], sort_keys=True),
                    "stderr_tail": "",
                }
        except (
            OSError,
            urllib.error.URLError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            last_error = str(exc)
        time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
    return {
        "name": "R5 wait for Redis dependency alert firing",
        "category": "prometheus_alert_runtime",
        "command": ["GET", "http://127.0.0.1:9090/api/v1/alerts"],
        "status": "failed",
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": json.dumps({"last_state": last_state}),
        "stderr_tail": (
            f"timed out after {timeout_seconds} seconds waiting for {alert_name} to fire"
            + (f": {last_error}" if last_error else "")
        ),
    }


def terminate_background_process(process: subprocess.Popen[str]) -> dict[str, Any]:
    started = time.monotonic()
    if process.poll() is None:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
    else:
        stdout, stderr = process.communicate()
    return {
        "name": "R5 stop Prometheus alert verifier after interrupted drill",
        "category": "cleanup",
        "command": ["terminate", str(process.pid)],
        "status": "passed",
        "returncode": process.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }


def run_expected_failure_step(
    name: str,
    category: str,
    command: list[str],
    timeout_seconds: int,
    required_output_tokens: tuple[str, ...] = (),
) -> dict[str, Any]:
    step = run_step(name, category, command, timeout_seconds)
    observed_status = str(step.get("status", "failed"))
    combined_output = (
        str(step.get("stdout_tail", "")) + "\n" + str(step.get("stderr_tail", ""))
    ).casefold()
    missing_tokens = [
        token
        for token in required_output_tokens
        if token.casefold() not in combined_output
    ]
    step["observed_status"] = observed_status
    step["required_output_tokens"] = list(required_output_tokens)
    step["missing_output_tokens"] = missing_tokens
    step["expected_failure_observed"] = (
        observed_status == "failed" and not missing_tokens
    )
    if observed_status == "failed" and not missing_tokens:
        step["status"] = "passed"
        step["stderr_tail"] = "expected failure observed\n" + str(
            step.get("stderr_tail", "")
        )
    else:
        step["status"] = "failed"
        step["stderr_tail"] = (
            "failure did not prove the expected dependency degradation; "
            f"observed_status={observed_status}, missing_tokens={missing_tokens}"
        )
    return step


def run_step_expect_stdout(
    name: str,
    category: str,
    command: list[str],
    timeout_seconds: int,
    expected_stdout: str,
) -> dict[str, Any]:
    step = run_step(name, category, command, timeout_seconds)
    observed = str(step.get("stdout_tail", "")).strip()
    step["expected_stdout"] = expected_stdout
    step["observed_stdout"] = observed
    if step.get("status") == "passed" and observed != expected_stdout:
        step["status"] = "failed"
        step["stderr_tail"] = (
            f"expected stdout {expected_stdout!r}, observed {observed!r}"
        )
    return step


def run_step_with_retry(
    name: str,
    category: str,
    command: list[str],
    timeout_seconds: int,
    attempts: int,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for attempt in range(1, max(1, attempts) + 1):
        result = run_step(
            f"{name} (attempt {attempt})",
            category,
            command,
            timeout_seconds,
        )
        results.append(result)
        if result.get("status") == "passed":
            result["name"] = name
            result["attempts"] = attempt
            return result
        if attempt < max(1, attempts):
            time.sleep(min(30.0, 5.0 * (2 ** (attempt - 1))))
    results[-1]["name"] = name
    results[-1]["attempts"] = len(results)
    return results[-1]


def fetch_json(url: str, timeout_s: float = 2.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout_s) as response:
        parsed = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(parsed, dict):
        raise ValueError("expected JSON object")
    return parsed


def wait_for_ready(url: str, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            doc = fetch_json(url)
            if doc.get("ready") is True or doc.get("status") in {"pass", "ok"}:
                return doc
        except (
            OSError,
            urllib.error.URLError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            last_error = str(exc)
        time.sleep(1.0)
    raise TimeoutError(f"timed out waiting for {url}: {last_error}")


def docker_compose_command() -> list[str]:
    try:
        probe = subprocess.run(
            ["docker", "compose", "version"],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        if probe.returncode == 0:
            return ["docker", "compose"]
    except (OSError, subprocess.TimeoutExpired):
        pass
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    return ["docker", "compose"]
