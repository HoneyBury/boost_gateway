#!/usr/bin/env python3
"""Verify a native macOS ARM64 install tree and SDK consumer."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.evidence_provenance import build_evidence_provenance


def run(command: list[str], *, cwd: Path | None = None) -> str:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_consumer(install_root: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="boost-gateway-macos-consumer-") as raw:
        root = Path(raw)
        (root / "CMakeLists.txt").write_text(
            """cmake_minimum_required(VERSION 3.21)
project(boost_gateway_macos_consumer LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(boost_gateway_sdk CONFIG REQUIRED)
add_executable(consumer main.cpp)
target_link_libraries(consumer PRIVATE boost_gateway::sdk)
""",
            encoding="utf-8",
        )
        (root / "main.cpp").write_text(
            """#include <boost_gateway/sdk/client.h>
#include <boost_gateway/sdk/version.h>
#include <iostream>
int main() { boost_gateway::sdk::SdkClient client; std::cout << BOOST_GATEWAY_SDK_VERSION; }
""",
            encoding="utf-8",
        )
        build = root / "build"
        run([
            "cmake", "-S", str(root), "-B", str(build), "-G", "Ninja",
            f"-DCMAKE_PREFIX_PATH={install_root}", "-DCMAKE_OSX_ARCHITECTURES=arm64",
        ])
        run(["cmake", "--build", str(build), "--parallel"])
        output = run([str(build / "consumer")])
        if not output.startswith("4."):
            raise RuntimeError(f"unexpected C++ SDK version output: {output!r}")
        return {"cmake_find_package": True, "sdk_version": output}


def verify(install_root: Path, lockfile: Path | None, candidate_revision: str) -> dict[str, object]:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("macOS ARM64 verification requires a native Darwin arm64 process")
    if not install_root.is_dir():
        raise RuntimeError(f"install root does not exist: {install_root}")

    binaries = sorted((install_root / "bin").glob("*"))
    dylibs = sorted((install_root / "lib").glob("*.dylib"))
    if not binaries or not dylibs:
        raise RuntimeError("install tree is missing executables or dylibs")

    inspected: list[dict[str, object]] = []
    for path in [*binaries, *dylibs]:
        if not path.is_file():
            continue
        identity = run(["file", str(path)])
        if "Mach-O 64-bit" not in identity or "arm64" not in identity or "x86_64" in identity:
            raise RuntimeError(f"non-native Mach-O artifact: {identity}")
        dependencies = run(["otool", "-L", str(path)])
        dependency_lines = dependencies.splitlines()[1:]
        if any(str(Path.cwd()) in line or "/build/" in line for line in dependency_lines):
            raise RuntimeError(f"build path leaked into Mach-O dependencies: {path}")
        inspected.append({
            "path": str(path.relative_to(install_root)),
            "sha256": sha256(path),
            "file": identity,
            "otool": dependency_lines,
        })

    sdk_dylib = install_root / "lib" / "libboost_gateway_sdk.dylib"
    library = ctypes.CDLL(str(sdk_dylib))
    library.gsdk_version.restype = ctypes.c_char_p
    native_version = library.gsdk_version().decode("ascii")
    if not native_version.startswith("4."):
        raise RuntimeError(f"unexpected C ABI version: {native_version}")

    hello = install_root / "bin" / "example_hello_world"
    hello_output = run([str(hello)])
    consumer = verify_consumer(install_root)
    return {
        "summary_version": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": True,
        "production_platform": "macos-arm64",
        "candidate_revision": candidate_revision,
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "macos_version": platform.mac_ver()[0],
            "clang": run(["xcrun", "clang", "--version"]).splitlines()[0],
            "sdk": run(["xcrun", "--show-sdk-version"]),
            "capacity_claimed": False,
            "notarized": False,
        },
        "lockfile": None if lockfile is None else {
            "path": str(lockfile),
            "sha256": sha256(lockfile),
        },
        "artifacts": inspected,
        "c_abi": {"loaded": True, "version": native_version},
        "cpp_consumer": consumer,
        "hello_world": {"exit_code": 0, "output": hello_output[-500:]},
        "skipped_linux_capabilities": ["cpu_affinity", "procfs", "docker_kind", "capacity"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--lockfile", type=Path)
    parser.add_argument("--candidate-revision", required=True)
    parser.add_argument("--configuration", default="Release")
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=Path("runtime/validation/macos-arm64-summary.json"),
    )
    args = parser.parse_args()
    try:
        summary = verify(
            args.install_root.resolve(),
            args.lockfile.resolve() if args.lockfile else None,
            args.candidate_revision,
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"macOS ARM64 package verification: FAIL ({error})")
        return 1
    summary["provenance"] = build_evidence_provenance(
        ROOT,
        build_configuration=args.configuration,
        conan_lockfile=args.lockfile.resolve() if args.lockfile else None,
        candidate_revision=args.candidate_revision,
    )
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("macOS ARM64 package verification: PASS")
    print(f"summary: {args.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
