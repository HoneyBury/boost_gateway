"""Pre-production recovery responsibility module: recovery_drill_images."""

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
from scripts.lib.recovery_drill_contract import *  # noqa: F401,F403
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
