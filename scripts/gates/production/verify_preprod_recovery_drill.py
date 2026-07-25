#!/usr/bin/env python3
"""Run or validate R5 pre-production recovery drill evidence."""

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

REPO_ROOT = Path(__file__).resolve().parents[3]
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


def docker_compose_pull_command(
    compose_command: list[str], compose_file: Path
) -> list[str]:
    if compose_command == ["docker", "compose"]:
        return [*compose_command, "--parallel", "1", "-f", str(compose_file), "pull"]
    return [*compose_command, "-f", str(compose_file), "pull"]


def resolve_compose_image_requirements(
    compose_command: list[str], compose_file: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    command = [*compose_command, "-f", str(compose_file), "config", "--format", "json"]
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
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (
            {
                "name": "R5 resolve Docker Compose image requirements",
                "category": "docker_image_preflight",
                "command": command,
                "status": (
                    "timeout"
                    if isinstance(exc, subprocess.TimeoutExpired)
                    else "failed"
                ),
                "duration_seconds": round(time.monotonic() - started, 3),
                "stdout_tail": "",
                "stderr_tail": str(exc),
                "required_image_count": 0,
            },
            [],
        )
    requirements: list[dict[str, Any]] = []
    error = ""
    if completed.returncode == 0:
        try:
            document = json.loads(completed.stdout)
            project_name = str(document.get("name", ""))
            services = document.get("services")
            if not project_name or not isinstance(services, dict):
                raise ValueError("compose config must contain name and services")
            for service_name, raw_service in sorted(services.items()):
                if not isinstance(raw_service, dict):
                    raise ValueError(f"service {service_name} must be an object")
                build_backed = bool(raw_service.get("build"))
                image = str(raw_service.get("image", ""))
                if not image and build_backed:
                    image = f"{project_name}-{service_name}"
                if not image:
                    raise ValueError(
                        f"service {service_name} has neither image nor build"
                    )
                requirements.append(
                    {
                        "service": str(service_name),
                        "image": image,
                        "source": "build" if build_backed else "registry",
                        "pullable": not build_backed,
                    }
                )
        except (json.JSONDecodeError, ValueError) as exc:
            error = str(exc)
    else:
        error = tail(completed.stderr or completed.stdout)

    passed = completed.returncode == 0 and not error and bool(requirements)
    return (
        {
            "name": "R5 resolve Docker Compose image requirements",
            "category": "docker_image_preflight",
            "command": command,
            "status": "passed" if passed else "failed",
            "returncode": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(completed.stdout),
            "stderr_tail": error or tail(completed.stderr),
            "required_image_count": len(requirements),
        },
        requirements,
    )


def inspect_required_images(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inspected: dict[str, dict[str, Any]] = {}
    inventory: list[dict[str, Any]] = []
    for requirement in requirements:
        image = str(requirement["image"])
        if image not in inspected:
            try:
                completed = subprocess.run(
                    ["docker", "image", "inspect", image],
                    cwd=REPO_ROOT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=20,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                inspected[image] = {
                    "present": False,
                    "image_id": "",
                    "repo_digests": [],
                    "repo_tags": [],
                    "created": "",
                    "os": "",
                    "architecture": "",
                    "error": str(exc),
                }
                inventory.append({**requirement, **inspected[image]})
                continue
            item: dict[str, Any] = {
                "present": False,
                "image_id": "",
                "repo_digests": [],
                "repo_tags": [],
                "created": "",
                "os": "",
                "architecture": "",
                "error": tail(completed.stderr or completed.stdout, 1000),
            }
            if completed.returncode == 0:
                try:
                    parsed = json.loads(completed.stdout)
                    metadata = parsed[0] if isinstance(parsed, list) and parsed else {}
                    if not isinstance(metadata, dict):
                        raise ValueError(
                            "docker image inspect did not return an object"
                        )
                    item.update(
                        {
                            "present": True,
                            "image_id": str(metadata.get("Id", "")),
                            "repo_digests": metadata.get("RepoDigests") or [],
                            "repo_tags": metadata.get("RepoTags") or [],
                            "created": str(metadata.get("Created", "")),
                            "os": str(metadata.get("Os", "")),
                            "architecture": str(metadata.get("Architecture", "")),
                            "error": "",
                        }
                    )
                except (json.JSONDecodeError, ValueError) as exc:
                    item["error"] = str(exc)
            inspected[image] = item
        inventory.append({**requirement, **inspected[image]})
    return inventory


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def repository_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def resolve_sdk_shared_library(build_dir: Path, configuration: str) -> Path:
    library_name = (
        "boost_gateway_sdk.dll" if os.name == "nt" else "libboost_gateway_sdk.so"
    )
    candidates = [
        build_dir / "sdk" / library_name,
        build_dir / "sdk" / configuration / library_name,
    ]
    if sys.platform == "darwin":
        candidates = [path.with_suffix(".dylib") for path in candidates]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"SDK shared library not found; searched: {', '.join(str(path) for path in candidates)}"
    )


def run_sdk_leaderboard_probe(host: str, port: int, sdk_library: Path) -> int:
    os.environ["BOOST_GATEWAY_SDK_LIBRARY"] = str(sdk_library.resolve())
    module_path = REPO_ROOT / "sdk/python/__init__.py"
    spec = importlib.util.spec_from_file_location(
        "boost_gateway_sdk_probe", module_path
    )
    if spec is None or spec.loader is None:
        print(
            "leaderboard SDK probe could not load the Python wrapper", file=sys.stderr
        )
        return 2
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        client = module.SdkClient()
        user_id = f"redis_probe_{time.monotonic_ns()}"
        if not client.connect(host, port, 5000):
            print("leaderboard SDK probe connect failed", file=sys.stderr)
            return 1
        login = client.login(user_id, f"token:{user_id}", 5000)
        if not login.get("ok"):
            print(f"leaderboard SDK probe login failed: {login}", file=sys.stderr)
            return 1
        submit = client.leaderboard_submit(user_id, "Redis Probe", 9_999_999_999, 5000)
        if not submit.get("ok"):
            print(f"leaderboard SDK probe submit failed: {submit}", file=sys.stderr)
            return 1
        rank = client.leaderboard_rank(user_id, 5000)
        if not rank.get("ok") or user_id not in str(rank.get("body", "")):
            print(f"leaderboard SDK probe rank failed: {rank}", file=sys.stderr)
            return 1
        client.disconnect()
    except Exception as exc:  # noqa: BLE001 - probe failures are evidence
        print(f"leaderboard SDK probe failed: {exc}", file=sys.stderr)
        return 1
    print("leaderboard SDK probe passed")
    return 0


def inspect_build_image_manifests(
    inventory: list[dict[str, Any]],
    candidate_revision: str,
) -> list[dict[str, Any]]:
    lockfile_setting = os.environ.get(
        "BOOST_GATEWAY_CONAN_LOCKFILE",
        "conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock",
    )
    lockfile_path = Path(lockfile_setting)
    if not lockfile_path.is_absolute():
        lockfile_path = REPO_ROOT / lockfile_path
    expected_lockfile = (
        str(lockfile_path.relative_to(REPO_ROOT))
        if lockfile_path.is_relative_to(REPO_ROOT)
        else str(lockfile_path)
    )
    expected_lockfile_sha256 = (
        sha256_file(lockfile_path) if lockfile_path.is_file() else ""
    )

    inspected: list[dict[str, Any]] = []
    for raw_item in inventory:
        item = dict(raw_item)
        if item.get("source") != "build" or item.get("present") is not True:
            inspected.append(item)
            continue
        image = str(item["image"])
        command = [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/cat",
            image,
            "/app/build-manifest.json",
        ]
        error = ""
        manifest: dict[str, Any] = {}
        try:
            completed = subprocess.run(
                command,
                cwd=REPO_ROOT,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
            if completed.returncode != 0:
                error = tail(completed.stderr or completed.stdout, 1000)
            else:
                parsed = json.loads(completed.stdout)
                if not isinstance(parsed, dict):
                    raise ValueError("build manifest must be a JSON object")
                manifest = parsed
        except (
            OSError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            error = str(exc)

        checks = {
            "schema_version": manifest.get("schema_version") == 1,
            "git_revision": manifest.get("git_revision") == candidate_revision,
            "dependency_provider": manifest.get("dependency_provider") == "conan",
            "worktree_clean": manifest.get("worktree_clean") is True,
            "conan_lockfile": manifest.get("conan_lockfile") == expected_lockfile,
            "conan_lockfile_sha256": bool(expected_lockfile_sha256)
            and manifest.get("conan_lockfile_sha256") == expected_lockfile_sha256,
        }
        service = str(item.get("service", ""))
        expected_binary = BUILD_IMAGE_BINARIES.get(service)
        binaries = manifest.get("binaries")
        binary_entries = binaries if isinstance(binaries, list) else []
        binary_names = [
            entry.get("name") for entry in binary_entries if isinstance(entry, dict)
        ]
        checks["binary_manifest_unique"] = len(binary_names) == len(set(binary_names))
        checks["expected_binary"] = expected_binary is not None
        actual_binary_sha256 = ""
        if expected_binary is not None:
            binary_name, binary_path = expected_binary
            matching_entries = [
                entry
                for entry in binary_entries
                if isinstance(entry, dict) and entry.get("name") == binary_name
            ]
            checks["binary_manifest_entry"] = len(matching_entries) == 1
            if not error and len(matching_entries) == 1:
                sha_command = [
                    "docker",
                    "run",
                    "--rm",
                    "--entrypoint",
                    "/usr/bin/sha256sum",
                    image,
                    binary_path,
                ]
                try:
                    sha_completed = subprocess.run(
                        sha_command,
                        cwd=REPO_ROOT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=30,
                        check=False,
                    )
                    if sha_completed.returncode == 0:
                        actual_binary_sha256 = sha_completed.stdout.split()[0]
                    else:
                        error = tail(sha_completed.stderr or sha_completed.stdout, 1000)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    error = str(exc)
                checks["binary_sha256"] = (
                    bool(actual_binary_sha256)
                    and matching_entries[0].get("sha256") == actual_binary_sha256
                )
            else:
                checks["binary_sha256"] = False
        if not error and not all(checks.values()):
            failed_checks = ", ".join(
                name for name, passed in checks.items() if not passed
            )
            error = f"build manifest does not match candidate: {failed_checks}"
        item.update(
            {
                "build_manifest": manifest,
                "build_manifest_checks": checks,
                "build_manifest_valid": not error and all(checks.values()),
                "build_manifest_error": error,
                "actual_binary_sha256": actual_binary_sha256,
            }
        )
        inspected.append(item)
    return inspected


def image_inventory_step(
    name: str,
    inventory: list[dict[str, Any]],
    *,
    fail_on_missing: bool,
    target_platform: str = "linux/amd64",
) -> dict[str, Any]:
    expected_os, expected_architecture = target_platform.split("/", 1)
    missing = sorted(
        {str(item["image"]) for item in inventory if item.get("present") is not True}
    )
    wrong_platform = sorted(
        {
            str(item["image"])
            for item in inventory
            if item.get("present") is True
            and (item.get("os"), item.get("architecture"))
            != (expected_os, expected_architecture)
        }
    )
    stale_build_images = sorted(
        {
            str(item["image"])
            for item in inventory
            if item.get("source") == "build"
            and item.get("present") is True
            and item.get("build_manifest_valid") is False
        }
    )
    passed = not fail_on_missing or (
        not missing and not wrong_platform and not stale_build_images
    )
    return {
        "name": name,
        "category": "docker_image_preflight",
        "command": ["docker", "image", "inspect", "<compose-required-images>"],
        "status": "passed" if passed else "failed",
        "duration_seconds": 0.0,
        "stdout_tail": json.dumps(
            {
                "required": len(inventory),
                "present": sum(1 for item in inventory if item.get("present") is True),
                "missing_images": missing,
                "wrong_platform_images": wrong_platform,
                "stale_build_images": stale_build_images,
            },
            sort_keys=True,
        ),
        "stderr_tail": (
            ""
            if passed
            else "; ".join(
                message
                for message in (
                    (
                        "required Docker images are missing: " + ", ".join(missing)
                        if missing
                        else ""
                    ),
                    (
                        f"Docker images do not match {target_platform}: "
                        + ", ".join(wrong_platform)
                        if wrong_platform
                        else ""
                    ),
                    (
                        "build images do not match the candidate: "
                        + ", ".join(stale_build_images)
                        if stale_build_images
                        else ""
                    ),
                )
                if message
            )
        ),
        "missing_images": missing,
        "wrong_platform_images": wrong_platform,
        "stale_build_images": stale_build_images,
    }


def run_docker_image_preflight(
    compose_command: list[str],
    compose_file: Path,
    *,
    pull_policy: str,
    pull_attempts: int,
    timeout_seconds: int,
    candidate_revision: str = "",
    target_platform: str = "linux/amd64",
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    requirement_step, requirements = resolve_compose_image_requirements(
        compose_command, compose_file
    )
    steps.append(requirement_step)
    if requirement_step["status"] != "passed":
        return {
            "passed": False,
            "pull_policy": pull_policy,
            "target_platform": target_platform,
            "requirements": requirements,
            "inventory": [],
            "missing_images": [],
            "steps": steps,
        }

    initial_inventory = inspect_required_images(requirements)
    steps.append(
        image_inventory_step(
            "R5 inspect Docker images before pull policy",
            initial_inventory,
            fail_on_missing=False,
            target_platform=target_platform,
        )
    )
    missing_pullable = sorted(
        {
            str(item["image"])
            for item in initial_inventory
            if item.get("present") is not True and item.get("pullable") is True
        }
    )

    if pull_policy == "always":
        steps.append(
            run_step_with_retry(
                "R5 docker compose pull (policy=always)",
                "docker_image_pull",
                docker_compose_pull_command(compose_command, compose_file),
                timeout_seconds,
                pull_attempts,
            )
        )
    elif pull_policy == "missing" and missing_pullable:
        for image in missing_pullable:
            steps.append(
                run_step_with_retry(
                    f"R5 pull missing Docker image {image}",
                    "docker_image_pull",
                    ["docker", "pull", "--platform", target_platform, image],
                    timeout_seconds,
                    pull_attempts,
                )
            )
    else:
        steps.append(
            {
                "name": f"R5 Docker pull skipped (policy={pull_policy})",
                "category": "docker_image_pull",
                "command": [],
                "status": "passed",
                "duration_seconds": 0.0,
                "stdout_tail": (
                    "all required registry images are cached"
                    if pull_policy == "missing"
                    else "network access disabled by policy"
                ),
                "stderr_tail": "",
            }
        )

    final_inventory = inspect_required_images(requirements)
    if candidate_revision:
        final_inventory = inspect_build_image_manifests(
            final_inventory, candidate_revision
        )
    final_step = image_inventory_step(
        "R5 verify required Docker images after pull policy",
        final_inventory,
        fail_on_missing=True,
        target_platform=target_platform,
    )
    missing_build_images = sorted(
        {
            str(item["image"])
            for item in final_inventory
            if item.get("present") is not True and item.get("source") == "build"
        }
    )
    if missing_build_images:
        final_step["stderr_tail"] += (
            "; build-backed images cannot be pulled and must be prebuilt with "
            "docker compose -f env/docker/docker-compose.yml build: "
            + ", ".join(missing_build_images)
        )
    prior_failure = next(
        (step for step in steps if step.get("status") != "passed"), None
    )
    if prior_failure is not None and final_step["status"] == "passed":
        final_step["status"] = "failed"
        final_step["stderr_tail"] = (
            f"pull policy step failed: {prior_failure.get('name', 'unknown')}"
        )
    steps.append(final_step)
    failed = next((step for step in steps if step.get("status") != "passed"), None)
    return {
        "passed": failed is None,
        "pull_policy": pull_policy,
        "target_platform": target_platform,
        "requirements": requirements,
        "inventory": final_inventory,
        "missing_images": final_step["missing_images"],
        "wrong_platform_images": final_step["wrong_platform_images"],
        "missing_build_images": missing_build_images,
        "stale_build_images": final_step["stale_build_images"],
        "candidate_revision": candidate_revision,
        "steps": steps,
    }


def wait_for_prometheus_targets_up(
    compose_command: list[str], compose_file: Path, timeout_seconds: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        completed = subprocess.run(
            [
                *compose_command,
                "-f",
                str(compose_file),
                "exec",
                "-T",
                "prometheus",
                "wget",
                "-qO-",
                "http://127.0.0.1:9090/api/v1/targets?state=active",
            ],
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        if completed.returncode == 0:
            try:
                doc = json.loads(completed.stdout)
                targets = doc.get("data", {}).get("activeTargets", [])
                if isinstance(targets, list) and targets:
                    if all(target.get("health") == "up" for target in targets):
                        return {
                            "active_target_count": len(targets),
                            "targets": [
                                {
                                    "job": target.get("labels", {}).get("job"),
                                    "health": target.get("health"),
                                }
                                for target in targets
                            ],
                        }
                    last_error = json.dumps(
                        [
                            {
                                "job": target.get("labels", {}).get("job"),
                                "health": target.get("health"),
                            }
                            for target in targets
                        ],
                        ensure_ascii=False,
                    )
                else:
                    last_error = "no active targets returned"
            except json.JSONDecodeError as exc:
                last_error = f"invalid prometheus JSON: {exc}"
        else:
            last_error = tail(completed.stderr or completed.stdout)
        time.sleep(2.0)
    raise TimeoutError(
        f"timed out waiting for Prometheus targets to become healthy: {last_error}"
    )


def wait_for_compose_redis(
    compose_command: list[str], compose_file: Path, timeout_seconds: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        completed = subprocess.run(
            [
                *compose_command,
                "-f",
                str(compose_file),
                "exec",
                "-T",
                "redis",
                "redis-cli",
                "ping",
            ],
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip() == "PONG":
            return {"attempts": attempts, "response": "PONG"}
        last_error = tail(completed.stderr or completed.stdout, 1000)
        time.sleep(1.0)
    raise TimeoutError(f"timed out waiting for Redis recovery: {last_error}")


def compose_build_images_present(
    compose_command: list[str], compose_file: Path
) -> bool:
    step, requirements = resolve_compose_image_requirements(
        compose_command, compose_file
    )
    if step.get("status") != "passed":
        return False
    build_requirements = [
        item for item in requirements if item.get("source") == "build"
    ]
    inventory = inspect_required_images(build_requirements)
    return bool(inventory) and all(item.get("present") is True for item in inventory)


def write_image_preflight_summary(
    path: Path,
    result: dict[str, Any],
    *,
    configuration: str,
) -> None:
    passed = result.get("passed") is True
    failed = next(
        (step for step in result.get("steps", []) if step.get("status") != "passed"),
        None,
    )
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "provenance": build_evidence_provenance(
            REPO_ROOT,
            build_configuration=configuration,
        ),
        "overall_pass": passed,
        "passed": passed,
        "failed_category": str(failed.get("category", "")) if failed else "",
        "failed_step": str(failed.get("name", "")) if failed else "",
        "scope": {
            "image_preflight_only": True,
            "real_docker_compose_drill": False,
            "docker_pull_policy": result.get("pull_policy", ""),
            "docker_target_platform": result.get("target_platform", ""),
        },
        "target_platform": result.get("target_platform", ""),
        "required_images": result.get("requirements", []),
        "image_inventory": result.get("inventory", []),
        "missing_images": result.get("missing_images", []),
        "missing_build_images": result.get("missing_build_images", []),
        "wrong_platform_images": result.get("wrong_platform_images", []),
        "stale_build_images": result.get("stale_build_images", []),
        "candidate_revision": result.get("candidate_revision", ""),
        "steps": result.get("steps", []),
        "artifacts": {"summary_path": str(path)},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


def write_command_summary(path: Path, name: str, step: dict[str, Any]) -> None:
    passed = step.get("status") == "passed"
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "overall_pass": passed,
        "passed": passed,
        "failed_category": "" if passed else str(step.get("category", "")),
        "failed_step": "" if passed else name,
        "steps": [step],
        "artifacts": {"summary_path": str(path)},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


def write_drill_record(
    path: Path,
    production_recovery_summary: Path,
    sdk_summary: Path,
    redis_alert_summary: Path,
    docker_snapshot_summary: Path,
    monitoring_summary: Path,
    passed: bool,
    *,
    include_redis_recovery: bool,
    verify_redis_alert_transition: bool,
    failure_started_at: datetime | None,
    failure_ended_at: datetime | None,
    measured_rto_seconds: float | None,
    mode: str = "docker-compose",
) -> None:
    now = datetime.now(UTC)
    failure_started = failure_started_at or now
    failure_ended = failure_ended_at or now
    native_process = mode == "native-process"
    recovery_actions = (
        [
            "start native backend and gateway processes",
            "run SDK full-flow before restart",
            "terminate and restart the native gateway process",
            "wait for gateway TCP and HTTP health",
            "run SDK full-flow after restart",
        ]
        if native_process
        else [
            "start compose stack from existing images",
            "run SDK full-flow before restart",
            "restart gateway container",
            "wait for gateway /ready",
            "run SDK full-flow after restart",
        ]
    )
    if include_redis_recovery:
        recovery_actions.extend(
            [
                "seed Redis persistence marker",
                "stop Redis and require SDK degradation",
                "start Redis and verify persisted marker",
                "run SDK full-flow after Redis recovery",
            ]
        )
    if not native_process:
        recovery_actions.append("collect Docker production snapshot")
    record = {
        "summary_version": 1,
        "template": False,
        "drill_id": (
            "r5-native-gateway-restart"
            if native_process
            else (
                "r5-compose-gateway-redis-recovery"
                if include_redis_recovery
                else "r5-compose-gateway-restart"
            )
        ),
        "executed_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "operator": "codex-local-runner",
        "environment": {
            "type": "native-process" if native_process else "docker-compose",
            "name": (
                f"native-{platform.system().lower()}-{platform.machine()}"
                if native_process
                else "local-docker-preprod"
            ),
            "git_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            ).stdout.strip(),
            "image_tag_before": (
                "not-applicable:native-process"
                if native_process
                else "boost-gateway-v332-gateway:latest"
            ),
            "image_tag_after": (
                "not-applicable:native-process"
                if native_process
                else "boost-gateway-v332-gateway:latest"
            ),
            "native_system": platform.system(),
            "native_machine": platform.machine(),
        },
        "scenario": ("redis_recovery" if include_redis_recovery else "gateway_restart"),
        "failure_injection": {
            "method": (
                "terminate and restart native gateway process"
                if native_process
                else (
                    "docker compose restart gateway; docker compose stop/start redis"
                    if include_redis_recovery
                    else "docker compose -f env/docker/docker-compose.yml restart gateway"
                )
            ),
            "started_at": failure_started.isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
            "ended_at": failure_ended.isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
        },
        "recovery": {
            "actions": recovery_actions,
            "rto_seconds": round(measured_rto_seconds or 0.0, 3),
            "rpo_seconds": 0,
            "data_consistency_risk": "none observed in SDK full-flow validation",
        },
        "observability": {
            "alerts_observed": (
                [
                    "BoostGatewayRedisUnavailable: inactive -> pending -> firing -> resolved"
                ]
                if verify_redis_alert_transition
                else ["local drill did not evaluate external alert firing"]
            ),
            "metrics_checked": (
                ["gateway TCP readiness", "gateway HTTP health", "gateway diagnostics"]
                if native_process
                else [
                    "gateway /ready",
                    "gateway diagnostics",
                    "Prometheus targets",
                    "Grafana health",
                ]
            ),
            "log_sources": (
                ["runtime/validation/process-logs/*.log"]
                if native_process
                else ["docker compose -f env/docker/docker-compose.yml logs gateway"]
            ),
        },
        "verification": {
            "production_recovery_summary": str(production_recovery_summary),
            "sdk_full_flow_summary": str(sdk_summary),
            "redis_alert_runtime_summary": (
                str(redis_alert_summary) if verify_redis_alert_transition else ""
            ),
            "docker_snapshot_summary": (
                "" if native_process else str(docker_snapshot_summary)
            ),
            "k8s_full_flow_summary": "",
            "monitoring_summary": str(monitoring_summary),
            "passed": passed,
        },
        "notes": (
            "R5 native process recovery drill for a production-native platform boundary."
            if native_process
            else "R5 Docker Compose recovery drill for a Linux production boundary."
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=REPO_ROOT / "build/release")
    parser.add_argument("--configuration", default="Release")
    parser.add_argument(
        "--mode",
        choices=["auto", "docker-compose", "native-process", "bounded-local"],
        default="auto",
    )
    parser.add_argument("--leave-running", action="store_true")
    parser.add_argument(
        "--include-redis-recovery",
        action="store_true",
        help="Stop and recover Compose Redis while validating SDK degradation and persisted data.",
    )
    parser.add_argument(
        "--verify-redis-alert-transition",
        action="store_true",
        help="During Redis recovery, verify the leaderboard dependency alert inactive/pending/firing/resolved states.",
    )
    parser.add_argument(
        "--redis-alert-firing-timeout-seconds",
        type=float,
        default=240.0,
        help="Maximum seconds to keep Redis down while waiting for the 2m alert to fire.",
    )
    parser.add_argument("--step-timeout-seconds", type=int, default=300)
    parser.add_argument("--docker-pull-attempts", type=int, default=3)
    parser.add_argument(
        "--docker-target-platform",
        choices=["linux/amd64", "linux/arm64"],
        default="linux/amd64",
        help="OCI platform required for every image in a Docker Compose R5 drill.",
    )
    parser.add_argument(
        "--docker-pull-policy",
        choices=["always", "missing", "never"],
        default="missing",
        help="Control registry access before the R5 Compose drill.",
    )
    parser.add_argument(
        "--image-preflight-only",
        action="store_true",
        help="Validate the Compose image cache without running the recovery drill.",
    )
    parser.add_argument(
        "--image-preflight-summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/r5-docker-image-preflight-summary.json",
    )
    parser.add_argument(
        "--candidate-revision",
        default=os.environ.get("BOOST_GATEWAY_CANDIDATE_REVISION")
        or repository_revision(),
        help="Require build-backed Docker image manifests to match this candidate revision.",
    )
    parser.add_argument(
        "--sdk-leaderboard-probe", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument("--sdk-library", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--gateway-host", default="127.0.0.1", help=argparse.SUPPRESS)
    parser.add_argument(
        "--gateway-port", type=int, default=9201, help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/preprod-recovery-drill-summary.json",
    )
    args = parser.parse_args()

    if args.sdk_leaderboard_probe:
        if args.sdk_library is None:
            parser.error("--sdk-leaderboard-probe requires --sdk-library")
        return run_sdk_leaderboard_probe(
            args.gateway_host,
            args.gateway_port,
            args.sdk_library,
        )

    summary_path = (
        args.summary_path
        if args.summary_path.is_absolute()
        else REPO_ROOT / args.summary_path
    )
    build_dir = (
        args.build_dir if args.build_dir.is_absolute() else REPO_ROOT / args.build_dir
    )
    validation_dir = summary_path.parent
    compose_file = REPO_ROOT / "env/docker/docker-compose.yml"
    image_preflight_summary = (
        args.image_preflight_summary_path
        if args.image_preflight_summary_path.is_absolute()
        else REPO_ROOT / args.image_preflight_summary_path
    )
    steps: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    mode = args.mode
    compose_command = (
        docker_compose_command() if mode in {"auto", "docker-compose"} else []
    )
    if mode == "auto":
        mode = (
            "docker-compose"
            if compose_build_images_present(compose_command, compose_file)
            else "bounded-local"
        )
        if mode == "bounded-local":
            compose_command = []

    if args.include_redis_recovery and mode != "docker-compose":
        parser.error(
            "--include-redis-recovery requires --mode docker-compose or cached Compose images"
        )
    if mode == "docker-compose" and not args.candidate_revision:
        parser.error(
            "docker-compose mode requires --candidate-revision or a resolvable Git HEAD"
        )
    if mode == "docker-compose":
        os.environ["DOCKER_DEFAULT_PLATFORM"] = args.docker_target_platform
    if args.verify_redis_alert_transition and not args.include_redis_recovery:
        parser.error(
            "--verify-redis-alert-transition requires --include-redis-recovery"
        )
    if (
        args.verify_redis_alert_transition
        and args.redis_alert_firing_timeout_seconds <= 0
    ):
        parser.error("--redis-alert-firing-timeout-seconds must be positive")

    if args.image_preflight_only:
        if mode != "docker-compose":
            parser.error(
                "--image-preflight-only requires Docker Compose images or --mode docker-compose"
            )
        image_preflight = run_docker_image_preflight(
            compose_command,
            compose_file,
            pull_policy=args.docker_pull_policy,
            pull_attempts=args.docker_pull_attempts,
            timeout_seconds=args.step_timeout_seconds,
            candidate_revision=args.candidate_revision,
            target_platform=args.docker_target_platform,
        )
        write_image_preflight_summary(
            image_preflight_summary,
            image_preflight,
            configuration=args.configuration,
        )
        print(
            "R5 Docker image preflight: "
            f"{'PASS' if image_preflight.get('passed') is True else 'FAIL'}"
        )
        print(f"summary: {image_preflight_summary}")
        return 0 if image_preflight.get("passed") is True else 1

    recovery_summary = validation_dir / "r5-production-recovery-summary.json"
    monitoring_summary = (
        REPO_ROOT / "runtime/validation/monitoring-operability-summary.json"
    )
    steps.append(
        run_step(
            "R5 N3 production recovery static gate",
            "recovery_gate",
            [
                sys.executable,
                str(REPO_ROOT / "scripts/gates/production/check_production_recovery_gate.py"),
                "--summary-path",
                str(recovery_summary),
            ],
            120,
        )
    )
    steps.append(
        run_step(
            "R5 monitoring operability static gate",
            "monitoring_operability",
            [
                sys.executable,
                str(REPO_ROOT / "scripts/gates/production/check_monitoring_operability.py"),
                "--summary-path",
                str(monitoring_summary),
            ],
            120,
        )
    )

    sdk_summary = validation_dir / "r5-post-recovery-sdk-full-flow-summary.json"
    redis_sdk_summary = validation_dir / "r5-redis-recovery-sdk-full-flow-summary.json"
    redis_alert_summary = validation_dir / "r5-redis-alert-runtime-summary.json"
    docker_snapshot_summary = (
        REPO_ROOT / "runtime/perf/docker-production-snapshot/summary.json"
    )
    record_path = validation_dir / "r5-preprod-recovery-drill-record.json"
    record_check_summary = (
        validation_dir / "r5-recovery-drill-record-check-summary.json"
    )
    cleanup_needed = False
    redis_fault_active = False
    alert_verifier: subprocess.Popen[str] | None = None
    alert_verifier_command: list[str] = []
    failure_started_at: datetime | None = None
    failure_ended_at: datetime | None = None
    measured_rto_seconds: float | None = None
    fault_started_monotonic: float | None = None
    image_preflight: dict[str, Any] = {
        "passed": mode != "docker-compose",
        "pull_policy": args.docker_pull_policy,
        "target_platform": args.docker_target_platform,
        "requirements": [],
        "inventory": [],
        "missing_images": [],
        "missing_build_images": [],
        "stale_build_images": [],
        "candidate_revision": args.candidate_revision,
        "steps": [],
    }

    try:
        if mode == "docker-compose":
            image_preflight = run_docker_image_preflight(
                compose_command,
                compose_file,
                pull_policy=args.docker_pull_policy,
                pull_attempts=args.docker_pull_attempts,
                timeout_seconds=args.step_timeout_seconds,
                candidate_revision=args.candidate_revision,
                target_platform=args.docker_target_platform,
            )
            steps.extend(image_preflight["steps"])
            write_image_preflight_summary(
                image_preflight_summary,
                image_preflight,
                configuration=args.configuration,
            )
            if steps[-1]["status"] == "passed":
                steps.append(
                    run_step(
                        "R5 docker compose up from existing images",
                        "docker_compose",
                        [
                            *compose_command,
                            "-f",
                            str(compose_file),
                            "up",
                            "-d",
                            "--no-build",
                        ],
                        args.step_timeout_seconds,
                    )
                )
                cleanup_needed = True
            if steps[-1]["status"] == "passed":
                ready_started = time.monotonic()
                try:
                    ready_doc = wait_for_ready("http://127.0.0.1:9080/ready", 90.0)
                    steps.append(
                        {
                            "name": "R5 gateway ready before restart",
                            "category": "docker_compose",
                            "command": ["GET", "http://127.0.0.1:9080/ready"],
                            "status": "passed",
                            "duration_seconds": round(
                                time.monotonic() - ready_started, 3
                            ),
                            "stdout_tail": json.dumps(ready_doc, sort_keys=True)[
                                -6000:
                            ],
                            "stderr_tail": "",
                        }
                    )
                except (
                    Exception
                ) as exc:  # noqa: BLE001 - captured in validation summary
                    steps.append(
                        {
                            "name": "R5 gateway ready before restart",
                            "category": "docker_compose",
                            "command": ["GET", "http://127.0.0.1:9080/ready"],
                            "status": "failed",
                            "duration_seconds": round(
                                time.monotonic() - ready_started, 3
                            ),
                            "stdout_tail": "",
                            "stderr_tail": str(exc),
                        }
                    )

            client = build_dir / "sdk/examples/sdk_full_flow_client"
            sdk_library = resolve_sdk_shared_library(build_dir, args.configuration)
            if steps[-1]["status"] == "passed":
                pre_step = run_step(
                    "R5 SDK full-flow before gateway restart",
                    "sdk_full_flow",
                    [str(client), "127.0.0.1", "9201"],
                    args.step_timeout_seconds,
                )
                steps.append(pre_step)

            if steps[-1]["status"] == "passed":
                failure_started_at = datetime.now(UTC)
                gateway_recovery_started = time.monotonic()
                steps.append(
                    run_step(
                        "R5 docker compose restart gateway",
                        "recovery_drill",
                        [
                            *compose_command,
                            "-f",
                            str(compose_file),
                            "restart",
                            "gateway",
                        ],
                        args.step_timeout_seconds,
                    )
                )

            if steps[-1]["status"] == "passed":
                ready_started = time.monotonic()
                try:
                    ready_doc = wait_for_ready("http://127.0.0.1:9080/ready", 90.0)
                    failure_ended_at = datetime.now(UTC)
                    measured_rto_seconds = time.monotonic() - gateway_recovery_started
                    steps.append(
                        {
                            "name": "R5 gateway ready after restart",
                            "category": "recovery_drill",
                            "command": ["GET", "http://127.0.0.1:9080/ready"],
                            "status": "passed",
                            "duration_seconds": round(
                                time.monotonic() - ready_started, 3
                            ),
                            "stdout_tail": json.dumps(ready_doc, sort_keys=True)[
                                -6000:
                            ],
                            "stderr_tail": "",
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    steps.append(
                        {
                            "name": "R5 gateway ready after restart",
                            "category": "recovery_drill",
                            "command": ["GET", "http://127.0.0.1:9080/ready"],
                            "status": "failed",
                            "duration_seconds": round(
                                time.monotonic() - ready_started, 3
                            ),
                            "stdout_tail": "",
                            "stderr_tail": str(exc),
                        }
                    )

            if steps[-1]["status"] == "passed":
                post_step = run_step(
                    "R5 SDK full-flow after gateway restart",
                    "sdk_full_flow",
                    [str(client), "127.0.0.1", "9201"],
                    args.step_timeout_seconds,
                )
                steps.append(post_step)
                write_command_summary(
                    sdk_summary, "R5 SDK full-flow after gateway restart", post_step
                )

            if steps[-1]["status"] == "passed" and args.include_redis_recovery:
                marker_key = f"boost_gateway:r5:recovery:{int(time.time())}"
                marker_value = build_evidence_provenance(
                    REPO_ROOT,
                    build_configuration=args.configuration,
                ).get("candidate_revision", "unknown")
                redis_cli = [
                    *compose_command,
                    "-f",
                    str(compose_file),
                    "exec",
                    "-T",
                    "redis",
                    "redis-cli",
                ]
                steps.append(
                    run_step_expect_stdout(
                        "R5 seed Redis persistence marker",
                        "redis_recovery",
                        [*redis_cli, "SET", marker_key, str(marker_value)],
                        args.step_timeout_seconds,
                        "OK",
                    )
                )

            if steps[-1]["status"] == "passed" and args.verify_redis_alert_transition:
                alert_verifier_command = [
                    sys.executable,
                    str(REPO_ROOT / "scripts/verify_prometheus_alert_states.py"),
                    "--configuration",
                    args.configuration,
                    "--prometheus-url",
                    "http://127.0.0.1:9090",
                    "--api",
                    "alerts",
                    "--alert-sequence",
                    "BoostGatewayRedisUnavailable=inactive,pending,firing,resolved",
                    "--state-timeout-seconds",
                    str(max(float(args.step_timeout_seconds), 480.0)),
                    "--overall-timeout-seconds",
                    str(max(float(args.step_timeout_seconds) * 2.0, 720.0)),
                    "--summary-path",
                    str(redis_alert_summary),
                ]
                alert_verifier, alert_start_step = start_background_step(
                    "R5 start Prometheus Redis alert verifier",
                    "prometheus_alert_runtime",
                    alert_verifier_command,
                )
                steps.append(alert_start_step)

            if steps[-1]["status"] == "passed" and args.include_redis_recovery:
                failure_started_at = datetime.now(UTC)
                failure_ended_at = None
                measured_rto_seconds = None
                fault_started_monotonic = time.monotonic()
                redis_fault_active = True
                redis_fault_services = ["redis"]
                redis_stop_step = run_step(
                    "R5 stop Redis during SDK traffic",
                    "redis_recovery",
                    [
                        *compose_command,
                        "-f",
                        str(compose_file),
                        "stop",
                        *redis_fault_services,
                    ],
                    args.step_timeout_seconds,
                )
                steps.append(redis_stop_step)

            if steps[-1]["status"] == "passed" and args.include_redis_recovery:
                steps.append(
                    run_expected_failure_step(
                        "R5 SDK leaderboard probe degrades while Redis is unavailable",
                        "redis_recovery",
                        [
                            sys.executable,
                            str(REPO_ROOT / "scripts/verify_preprod_recovery_drill.py"),
                            "--sdk-leaderboard-probe",
                            "--sdk-library",
                            str(sdk_library),
                            "--gateway-host",
                            "127.0.0.1",
                            "--gateway-port",
                            "9201",
                        ],
                        args.step_timeout_seconds,
                        ("leaderboard",),
                    )
                )

            if steps[-1]["status"] == "passed" and args.verify_redis_alert_transition:
                assert alert_verifier is not None
                steps.append(
                    wait_for_prometheus_alert_firing(
                        alert_verifier,
                        "BoostGatewayRedisUnavailable",
                        args.redis_alert_firing_timeout_seconds,
                    )
                )

            if steps[-1]["status"] == "passed" and args.include_redis_recovery:
                recovery_started = time.monotonic()
                redis_recovery_services = ["redis"]
                steps.append(
                    run_step(
                        "R5 start Redis after fault injection",
                        "redis_recovery",
                        [
                            *compose_command,
                            "-f",
                            str(compose_file),
                            "start",
                            *redis_recovery_services,
                        ],
                        args.step_timeout_seconds,
                    )
                )
                if steps[-1]["status"] == "passed":
                    try:
                        redis_doc = wait_for_compose_redis(
                            compose_command,
                            compose_file,
                            min(float(args.step_timeout_seconds), 120.0),
                        )
                        failure_ended_at = datetime.now(UTC)
                        measured_rto_seconds = time.monotonic() - (
                            fault_started_monotonic or recovery_started
                        )
                        redis_fault_active = False
                        steps.append(
                            {
                                "name": "R5 Redis responds after recovery",
                                "category": "redis_recovery",
                                "command": [*redis_cli, "PING"],
                                "status": "passed",
                                "duration_seconds": round(
                                    time.monotonic() - recovery_started, 3
                                ),
                                "stdout_tail": json.dumps(redis_doc, sort_keys=True),
                                "stderr_tail": "",
                                "rto_seconds": round(
                                    time.monotonic() - recovery_started, 3
                                ),
                            }
                        )
                    except (
                        Exception
                    ) as exc:  # noqa: BLE001 - captured in validation summary
                        steps.append(
                            {
                                "name": "R5 Redis responds after recovery",
                                "category": "redis_recovery",
                                "command": [*redis_cli, "PING"],
                                "status": "failed",
                                "duration_seconds": round(
                                    time.monotonic() - recovery_started, 3
                                ),
                                "stdout_tail": "",
                                "stderr_tail": str(exc),
                            }
                        )

            if steps[-1]["status"] == "passed" and args.verify_redis_alert_transition:
                steps.append(
                    run_step(
                        "R5 restart Redis exporter after dependency recovery",
                        "prometheus_alert_runtime",
                        [
                            *compose_command,
                            "-f",
                            str(compose_file),
                            "restart",
                            "redis-exporter",
                        ],
                        args.step_timeout_seconds,
                    )
                )

            if steps[-1]["status"] == "passed" and args.verify_redis_alert_transition:
                assert alert_verifier is not None
                steps.append(
                    wait_background_step(
                        "R5 verify Prometheus Redis alert inactive/pending/firing/resolved",
                        "prometheus_alert_runtime",
                        alert_verifier_command,
                        alert_verifier,
                        max(args.step_timeout_seconds * 2, 720) + 30,
                    )
                )
                alert_verifier = None

            if steps[-1]["status"] == "passed" and args.include_redis_recovery:
                steps.append(
                    run_step_expect_stdout(
                        "R5 verify Redis persistence marker after recovery",
                        "redis_recovery",
                        [*redis_cli, "--raw", "GET", marker_key],
                        args.step_timeout_seconds,
                        str(marker_value),
                    )
                )

            if steps[-1]["status"] == "passed" and args.include_redis_recovery:
                redis_post_step = run_step(
                    "R5 SDK full-flow after Redis recovery",
                    "redis_recovery",
                    [str(client), "127.0.0.1", "9201"],
                    args.step_timeout_seconds,
                )
                steps.append(redis_post_step)
                write_command_summary(
                    redis_sdk_summary,
                    "R5 SDK full-flow after Redis recovery",
                    redis_post_step,
                )

            if steps[-1]["status"] == "passed":
                prometheus_started = time.monotonic()
                try:
                    prometheus_doc = wait_for_prometheus_targets_up(
                        compose_command,
                        compose_file,
                        min(float(args.step_timeout_seconds), 90.0),
                    )
                    steps.append(
                        {
                            "name": "R5 Prometheus targets healthy before snapshot",
                            "category": "docker_snapshot",
                            "command": [
                                "GET",
                                "http://127.0.0.1:9090/api/v1/targets?state=active",
                            ],
                            "status": "passed",
                            "duration_seconds": round(
                                time.monotonic() - prometheus_started, 3
                            ),
                            "stdout_tail": json.dumps(
                                prometheus_doc, ensure_ascii=False, sort_keys=True
                            )[-6000:],
                            "stderr_tail": "",
                        }
                    )
                except (
                    Exception
                ) as exc:  # noqa: BLE001 - captured in validation summary
                    steps.append(
                        {
                            "name": "R5 Prometheus targets healthy before snapshot",
                            "category": "docker_snapshot",
                            "command": [
                                "GET",
                                "http://127.0.0.1:9090/api/v1/targets?state=active",
                            ],
                            "status": "failed",
                            "duration_seconds": round(
                                time.monotonic() - prometheus_started, 3
                            ),
                            "stdout_tail": "",
                            "stderr_tail": str(exc),
                        }
                    )

            if steps[-1]["status"] == "passed":
                steps.append(
                    run_step(
                        "R5 Docker production snapshot after recovery",
                        "docker_snapshot",
                        [
                            sys.executable,
                            str(
                                REPO_ROOT
                                / "scripts/producers/collect_docker_production_perf_snapshot.py"
                            ),
                            "--output-dir",
                            str(REPO_ROOT / "runtime/perf/docker-production-snapshot"),
                        ],
                        args.step_timeout_seconds,
                    )
                )
        else:
            native_process = mode == "native-process"
            sdk_step = run_step(
                (
                    "R5 native gateway restart and SDK full-flow"
                    if native_process
                    else "R5 bounded-local SDK full-flow"
                ),
                "sdk_full_flow",
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts/gates/sdk/verify_sdk_full_flow_client.py"),
                    "--build-dir",
                    str(build_dir),
                    "--skip-build",
                    "--summary-path",
                    str(sdk_summary),
                    *(["--restart-gateway"] if native_process else []),
                ],
                args.step_timeout_seconds,
            )
            steps.append(sdk_step)
            if native_process and sdk_summary.is_file():
                try:
                    sdk_document = json.loads(sdk_summary.read_text(encoding="utf-8"))
                    measured_rto_seconds = sdk_document.get(
                        "gateway_restart_rto_seconds"
                    )
                except (OSError, json.JSONDecodeError):
                    measured_rto_seconds = None

        drill_passed = all(step.get("status") == "passed" for step in steps)
        write_drill_record(
            record_path,
            recovery_summary,
            sdk_summary,
            redis_alert_summary,
            docker_snapshot_summary,
            monitoring_summary,
            drill_passed,
            include_redis_recovery=args.include_redis_recovery,
            verify_redis_alert_transition=args.verify_redis_alert_transition,
            failure_started_at=failure_started_at,
            failure_ended_at=failure_ended_at,
            measured_rto_seconds=measured_rto_seconds,
            mode=mode,
        )
        steps.append(
            run_step(
                "R5 recovery drill record validation",
                "recovery_drill_record",
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts/gates/production/check_recovery_drill_record.py"),
                    "--record",
                    str(record_path),
                    "--summary-path",
                    str(record_check_summary),
                ],
                60,
            )
        )
    finally:
        if redis_fault_active:
            redis_recovery_services = ["redis"]
            restore_step = run_step(
                "R5 restore Redis after interrupted fault drill",
                "cleanup",
                [
                    *compose_command,
                    "-f",
                    str(compose_file),
                    "start",
                    *redis_recovery_services,
                ],
                args.step_timeout_seconds,
            )
            steps.append(restore_step)
            if restore_step["status"] == "passed":
                cleanup_started = time.monotonic()
                try:
                    redis_doc = wait_for_compose_redis(
                        compose_command,
                        compose_file,
                        min(float(args.step_timeout_seconds), 120.0),
                    )
                    redis_fault_active = False
                    steps.append(
                        {
                            "name": "R5 verify Redis ready after interrupted fault drill",
                            "category": "cleanup",
                            "command": ["redis-cli", "PING"],
                            "status": "passed",
                            "duration_seconds": round(
                                time.monotonic() - cleanup_started, 3
                            ),
                            "stdout_tail": json.dumps(redis_doc, sort_keys=True),
                            "stderr_tail": "",
                        }
                    )
                except (
                    Exception
                ) as exc:  # noqa: BLE001 - cleanup failure belongs in summary
                    steps.append(
                        {
                            "name": "R5 verify Redis ready after interrupted fault drill",
                            "category": "cleanup",
                            "command": ["redis-cli", "PING"],
                            "status": "failed",
                            "duration_seconds": round(
                                time.monotonic() - cleanup_started, 3
                            ),
                            "stdout_tail": "",
                            "stderr_tail": str(exc),
                        }
                    )
        if alert_verifier is not None:
            steps.append(terminate_background_process(alert_verifier))
        if cleanup_needed and not args.leave_running:
            steps.append(
                run_step(
                    "R5 docker compose cleanup",
                    "cleanup",
                    [*compose_command, "-f", str(compose_file), "down"],
                    args.step_timeout_seconds,
                )
            )

    checks.append(
        {
            "name": "r5-real-platform-recovery-drill",
            "category": "preprod_recovery",
            "passed": mode in {"docker-compose", "native-process"}
            and all(step.get("status") == "passed" for step in steps),
            "mode": mode,
            "detail": (
                "Docker Compose gateway restart drill executed"
                if mode == "docker-compose"
                else (
                    "native gateway process restart drill executed"
                    if mode == "native-process"
                    else "bounded local mode used"
                )
            ),
        }
    )
    failed = next((step for step in steps if step.get("status") != "passed"), None)
    failed_check = next(
        (check for check in checks if check.get("passed") is not True), None
    )
    passed = failed is None and failed_check is None
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "provenance": build_evidence_provenance(
            REPO_ROOT,
            build_configuration=args.configuration,
        ),
        "overall_pass": passed,
        "passed": passed,
        "failed_category": (
            str(failed.get("category", ""))
            if failed
            else ("preprod_recovery" if failed_check else "")
        ),
        "failed_step": (
            str(failed.get("name", ""))
            if failed
            else (str(failed_check.get("name", "")) if failed_check else "")
        ),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "scope": {
            "mode": mode,
            "real_docker_compose_drill": mode == "docker-compose",
            "real_native_process_drill": mode == "native-process",
            "scenario": (
                "redis_recovery" if args.include_redis_recovery else "gateway_restart"
            ),
            "include_redis_recovery": args.include_redis_recovery,
            "verify_redis_alert_transition": args.verify_redis_alert_transition,
            "redis_alert_firing_timeout_seconds": args.redis_alert_firing_timeout_seconds,
            "docker_pull_policy": args.docker_pull_policy,
            "docker_target_platform": (
                args.docker_target_platform if mode == "docker-compose" else ""
            ),
        },
        "docker_image_preflight": image_preflight,
        "checks": checks,
        "steps": steps,
        "artifacts": {
            "summary_path": str(summary_path),
            "production_recovery_summary": str(recovery_summary),
            "sdk_full_flow_summary": str(sdk_summary),
            "redis_recovery_sdk_full_flow_summary": (
                str(redis_sdk_summary) if args.include_redis_recovery else ""
            ),
            "redis_alert_runtime_summary": (
                str(redis_alert_summary) if args.verify_redis_alert_transition else ""
            ),
            "docker_snapshot_summary": str(docker_snapshot_summary),
            "drill_record_path": str(record_path),
            "drill_record_check_summary": str(record_check_summary),
            "docker_image_preflight_summary": str(image_preflight_summary),
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"preprod recovery drill: {'PASS' if passed else 'FAIL'}")
    print(f"summary: {summary_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
