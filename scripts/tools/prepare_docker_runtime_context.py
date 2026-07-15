#!/usr/bin/env python3
"""Stage Conan-built release binaries for network-free Docker image builds."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCKFILE = ROOT / "conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock"
BINARIES = {
    "v2_gateway_demo": Path("examples/v2_gateway_demo/v2_gateway_demo"),
    "v2_login_backend": Path("examples/v2_login_backend/v2_login_backend"),
    "v2_room_backend": Path("examples/v2_room_backend/v2_room_backend"),
    "v2_battle_backend": Path("examples/v2_battle_backend/v2_battle_backend"),
    "v2_match_backend": Path("examples/v2_match_backend/v2_match_backend"),
    "v2_leaderboard_backend": Path("examples/v2_leaderboard_backend/v2_leaderboard_backend"),
}
ALLOWED_RUNTIME_LIBRARIES = {
    "libc.so.6",
    "libgcc_s.so.1",
    "libm.so.6",
    "libstdc++.so.6",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_binary(build_dir: Path, relative: Path, configuration: str | None) -> Path:
    candidates = [build_dir / relative]
    if configuration:
        candidates.insert(0, build_dir / relative.parent / configuration / relative.name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"missing built binary {relative.name}; searched: {searched}")


def validate_runtime_dependencies(binary: Path, ldd_output: str) -> list[str]:
    if "not found" in ldd_output:
        raise RuntimeError(f"{binary}: unresolved runtime dependency\n{ldd_output}")
    libraries: list[str] = []
    for raw_line in ldd_output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("linux-vdso.so") or line.startswith("/lib64/ld-linux"):
            continue
        name = line.split(" => ", 1)[0].split()[0]
        libraries.append(name)
    unexpected = sorted(set(libraries) - ALLOWED_RUNTIME_LIBRARIES)
    if unexpected:
        names = ", ".join(unexpected)
        raise RuntimeError(
            f"{binary}: non-system runtime libraries remain ({names}); "
            "the Conan release graph must link third-party dependencies statically"
        )
    return sorted(set(libraries))


def git_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True, capture_output=True
    )
    return result.stdout.strip()


def prepare(build_dir: Path, output_dir: Path, lockfile: Path, configuration: str | None) -> Path:
    build_dir = build_dir.resolve()
    output_dir = output_dir.resolve()
    lockfile = lockfile.resolve()
    if not lockfile.is_file():
        raise FileNotFoundError(f"Conan lockfile does not exist: {lockfile}")

    bin_dir = output_dir / "bin"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    bin_dir.mkdir(parents=True)

    entries = []
    for name, relative in BINARIES.items():
        source = resolve_binary(build_dir, relative, configuration)
        with source.open("rb") as stream:
            magic = stream.read(4)
        if magic != b"\x7fELF":
            raise RuntimeError(f"{source}: expected an ELF executable")
        ldd = subprocess.run(["ldd", str(source)], check=True, text=True, capture_output=True)
        libraries = validate_runtime_dependencies(source, ldd.stdout)
        destination = bin_dir / name
        shutil.copy2(source, destination)
        destination.chmod(0o755)
        entries.append(
            {
                "name": name,
                "source": str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else str(source),
                "sha256": sha256(destination),
                "runtime_libraries": libraries,
            }
        )

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": git_revision(),
        "dependency_provider": "conan",
        "conan_lockfile": str(lockfile.relative_to(ROOT)) if lockfile.is_relative_to(ROOT) else str(lockfile),
        "conan_lockfile_sha256": sha256(lockfile),
        "binaries": entries,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/release")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "runtime/docker-rootfs")
    parser.add_argument("--lockfile", type=Path, default=DEFAULT_LOCKFILE)
    parser.add_argument("--configuration", help="Multi-config build configuration, for example Release")
    args = parser.parse_args()
    manifest = prepare(args.build_dir, args.output_dir, args.lockfile, args.configuration)
    print(f"Docker runtime context prepared: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
