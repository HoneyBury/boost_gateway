#!/usr/bin/env python3
"""Verify that an installed release tarball runs on a clean Ubuntu base image."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.tools.prepare_docker_runtime_context import validate_runtime_dependencies
from scripts.tools.verify_release_archive import verify_archive
from scripts.lib.evidence_provenance import build_evidence_provenance


REQUIRED_BINARIES = (
    "v2_gateway_demo",
    "v2_login_backend",
    "v2_room_backend",
    "v2_battle_backend",
    "v2_match_backend",
    "v2_leaderboard_backend",
    "raft_state_tool",
    "example_hello_world",
)

PLATFORM_CONTRACTS = {
    "linux-x64": {
        "file_architecture": "x86-64",
        "docker_platform": "linux/amd64",
        "image_architecture": "amd64",
    },
    "linux-arm64": {
        "file_architecture": "aarch64",
        "docker_platform": "linux/arm64",
        "image_architecture": "arm64",
    },
}


def validate_elf_identity(identity: str, expected_platform: str) -> None:
    expected_architecture = PLATFORM_CONTRACTS[expected_platform]["file_architecture"]
    if expected_architecture not in identity:
        raise RuntimeError(
            f"expected {expected_platform} ELF architecture, observed {identity}"
        )


def validate_image_identity(image_identity: list[str], expected_platform: str) -> None:
    expected_architecture = PLATFORM_CONTRACTS[expected_platform]["image_architecture"]
    if len(image_identity) != 2 or image_identity[1] != expected_architecture:
        raise RuntimeError(
            f"container image does not match {expected_platform}: {' '.join(image_identity)}"
        )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_archive(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination):
                raise RuntimeError(f"unsafe archive member: {member.name}")
        bundle.extractall(destination, filter="data")


def inspect_installed_binaries(package_root: Path, expected_platform: str) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for name in REQUIRED_BINARIES:
        binary = package_root / "bin" / name
        if not binary.is_file():
            raise RuntimeError(f"missing installed executable: bin/{name}")
        with binary.open("rb") as stream:
            magic = stream.read(4)
        if magic != b"\x7fELF":
            raise RuntimeError(f"bin/{name}: expected an ELF executable")
        identity = subprocess.run(
            ["file", str(binary)], check=True, text=True, capture_output=True
        ).stdout.strip()
        try:
            validate_elf_identity(identity, expected_platform)
        except RuntimeError as exc:
            raise RuntimeError(f"bin/{name}: {exc}") from exc
        if not binary.stat().st_mode & 0o111:
            raise RuntimeError(f"bin/{name}: executable bit is not set")
        entries.append({"name": name, "sha256": sha256(binary), "file": identity})
    return entries


def run_clean_ubuntu_validation(
    package_root: Path, image: str, expected_platform: str
) -> dict[str, object]:
    if shutil.which("docker") is None:
        raise RuntimeError("docker is required for clean-environment package validation")
    image_identity = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}} {{.Architecture}}", image],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip().split()
    validate_image_identity(image_identity, expected_platform)
    image_id = image_identity[0]
    docker_platform = PLATFORM_CONTRACTS[expected_platform]["docker_platform"]
    mount = f"{package_root.resolve()}:/opt/boost-gateway:ro"
    libraries: dict[str, list[str]] = {}
    for name in REQUIRED_BINARIES:
        binary = f"/opt/boost-gateway/bin/{name}"
        result = subprocess.run(
            [
                "docker", "run", "--rm", "--pull=never", "--network=none",
                "--platform", docker_platform, "-v", mount, image, "ldd", binary,
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        libraries[name] = validate_runtime_dependencies(Path(binary), result.stdout)
    hello = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--platform",
            docker_platform,
            "-v",
            mount,
            image,
            "/opt/boost-gateway/bin/example_hello_world",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    return {
        "image": image,
        "image_id": image_id,
        "target_platform": docker_platform,
        "network": "none",
        "pull_policy": "never",
        "runtime_libraries": libraries,
        "hello_world_exit_code": hello.returncode,
    }


def verify_package(
    archive: Path, expected_root: str, image: str, expected_platform: str
) -> dict[str, object]:
    failures = verify_archive(archive, expected_root)
    if failures:
        raise RuntimeError("; ".join(failures))
    with tempfile.TemporaryDirectory(prefix="boost-gateway-package-") as directory:
        extraction_root = Path(directory)
        extract_archive(archive, extraction_root)
        package_root = extraction_root / expected_root
        binaries = inspect_installed_binaries(package_root, expected_platform)
        container = run_clean_ubuntu_validation(package_root, image, expected_platform)
    return {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": True,
        "passed": True,
        "production_platform": expected_platform,
        "archive": {"name": archive.name, "sha256": sha256(archive), "root": expected_root},
        "binaries": binaries,
        "clean_environment": container,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-root", required=True)
    parser.add_argument("--image", default="ubuntu:24.04")
    parser.add_argument(
        "--expected-platform", choices=sorted(PLATFORM_CONTRACTS), default="linux-x64"
    )
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--candidate-revision")
    parser.add_argument("--lockfile", type=Path)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=Path("runtime/validation/release-package-consumer-summary.json"),
    )
    args = parser.parse_args()
    try:
        summary = verify_package(
            args.archive.resolve(), args.expected_root, args.image, args.expected_platform
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"release package clean-environment validation: FAIL ({exc})")
        return 1
    summary["provenance"] = build_evidence_provenance(
        ROOT,
        build_configuration=args.configuration,
        conan_lockfile=args.lockfile,
        candidate_revision=args.candidate_revision,
    )
    summary["artifacts"] = {
        "summary_path": str(args.summary_path),
        "archive_path": str(args.archive),
    }
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"release package clean-environment validation: PASS ({args.image}, network=none, pull=never)")
    print(f"summary: {args.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
