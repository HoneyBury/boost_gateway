"""Pre-production recovery responsibility module: recovery_drill_contract."""

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



from scripts.lib.recovery_drill_runtime import *  # noqa: F401,F403
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
    library_name = "libboost_gateway_sdk.so"
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
