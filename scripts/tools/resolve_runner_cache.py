#!/usr/bin/env python3
"""Resolve an OS-safe persistent Conan and sccache namespace for a runner.

Conan packages can contain dynamically linked native binaries.  A cache made on
Ubuntu 24.04 is therefore not a valid binary package cache for an Ubuntu 22.04
runner, even when both runners are Linux/x86_64.  Docker images are deliberately
handled elsewhere because their userland travels with the image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_ROOT = Path("/opt/boost-gateway")


def read_os_release() -> tuple[str, str]:
    values: dict[str, str] = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    distro = values.get("ID", "unknown").lower()
    release = values.get("VERSION_ID", "unknown")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", distro):
        raise ValueError(f"unsafe operating system ID: {distro!r}")
    if not re.fullmatch(r"[0-9][0-9a-z._-]*", release):
        raise ValueError(f"unsafe operating system release: {release!r}")
    return distro, release


def command_output(command: list[str]) -> str:
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT).strip()


def compiler_version() -> str:
    version = command_output(["gcc", "-dumpfullversion", "-dumpversion"]).splitlines()[0]
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
        raise ValueError(f"unsafe GCC version: {version!r}")
    return version


def conan_version() -> str:
    output = command_output(["conan", "--version"])
    match = re.search(r"([0-9]+(?:\.[0-9]+)+)", output)
    if not match:
        raise ValueError(f"unable to parse Conan version from: {output!r}")
    return match.group(1)


def normalized_architecture() -> str:
    value = platform.machine().lower()
    aliases = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}
    arch = aliases.get(value, value)
    if not re.fullmatch(r"[a-z0-9_+-]+", arch):
        raise ValueError(f"unsafe architecture: {arch!r}")
    return arch


def required_file(path: str) -> Path:
    candidate = Path(path)
    candidate = candidate if candidate.is_absolute() else ROOT / candidate
    if not candidate.is_file():
        raise FileNotFoundError(f"cache identity input does not exist: {candidate}")
    return candidate


def conan_graph_digest(profile: str, lockfile: str) -> tuple[str, dict[str, str]]:
    paths = [required_file("conanfile.py"), required_file(profile), required_file(lockfile)]
    paths.extend(sorted((ROOT / "conan").glob("remotes*.json")))
    hashes: dict[str, str] = {}
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[relative] = file_digest
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest(), hashes


def remote_environment_digest() -> str:
    """Hash bootstrap remote overrides without writing their values to evidence."""
    digest = hashlib.sha256()
    for name in ("CONAN_REMOTE_URL", "CONAN_REMOTE_NAME", "CONAN_REMOTE_VERIFY_SSL"):
        digest.update(name.encode("ascii"))
        digest.update(b"\0")
        digest.update(os.environ.get(name, "").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-type", required=True, choices=("Debug", "Release"))
    parser.add_argument("--profile", default="conan/profiles/linux-gcc-x64")
    parser.add_argument("--lockfile", required=True)
    parser.add_argument("--graph-variant", default="default", help="Dependency option-set label included in the Conan cache identity.")
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path(os.environ.get("BOOST_GATEWAY_RUNNER_CACHE_ROOT", DEFAULT_CACHE_ROOT)),
        help="Persistent runner root; default: /opt/boost-gateway or BOOST_GATEWAY_RUNNER_CACHE_ROOT.",
    )
    parser.add_argument("--github-env", type=Path, help="Append resolved variables to this GitHub Actions environment file.")
    parser.add_argument("--summary-path", type=Path, help="Optional JSON cache identity artifact path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    distro, release = read_os_release()
    gcc = compiler_version()
    conan = conan_version()
    arch = normalized_architecture()
    build_type = args.build_type.lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.graph_variant):
        raise ValueError(f"unsafe graph variant: {args.graph_variant!r}")
    graph_digest, input_hashes = conan_graph_digest(args.profile, args.lockfile)
    remote_env_digest = remote_environment_digest()
    platform_namespace = f"{distro}-{release}-gcc{gcc}-{arch}-{build_type}"
    conan_key_material = json.dumps(
        {
            "conan_version": conan,
            "gcc_version": gcc,
            "platform": platform_namespace,
            "graph_digest": graph_digest,
            "graph_variant": args.graph_variant,
            "remote_environment_digest": remote_env_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    conan_key = hashlib.sha256(conan_key_material.encode("utf-8")).hexdigest()
    cache_root = args.cache_root.expanduser().resolve()
    conan_home = cache_root / "conan" / platform_namespace / f"conan-{conan}-graph-{conan_key[:20]}"
    sccache_dir = cache_root / "sccache" / platform_namespace
    try:
        conan_home.mkdir(parents=True, exist_ok=True)
        sccache_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise SystemExit(
            f"cannot create persistent runner cache under {cache_root}; prepare it with: "
            f"sudo install -d -o $USER -g $(id -gn) {cache_root}/conan {cache_root}/sccache"
        ) from exc

    identity = {
        "summary_version": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "cache_root": str(cache_root),
        "platform_namespace": platform_namespace,
        "os": {"id": distro, "release": release},
        "architecture": arch,
        "gcc_version": gcc,
        "build_type": args.build_type,
        "conan_version": conan,
        "conan_graph_key": conan_key,
        "conan_graph_variant": args.graph_variant,
        "conan_graph_input_sha256": input_hashes,
        "conan_remote_environment_sha256": remote_env_digest,
        "conan_home": str(conan_home),
        "sccache_dir": str(sccache_dir),
    }
    if args.github_env:
        args.github_env.parent.mkdir(parents=True, exist_ok=True)
        with args.github_env.open("a", encoding="utf-8") as handle:
            for key, value in {
                "CONAN_HOME": str(conan_home),
                "SCCACHE_DIR": str(sccache_dir),
                "BOOST_GATEWAY_CONAN_CACHE_KEY": conan_key,
                "BOOST_GATEWAY_RUNNER_CACHE_PLATFORM": platform_namespace,
            }.items():
                handle.write(f"{key}={value}\n")
    if args.summary_path:
        summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(identity, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
