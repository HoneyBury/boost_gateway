#!/usr/bin/env python3
"""Build and run a release SDK consumer in an offline compiler container."""

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
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.tools.verify_release_archive import verify_archive  # noqa: E402
from scripts.tools.verify_release_package_consumer import extract_archive  # noqa: E402

SDK_VERSION = "4.2.0"
DEFAULT_IMAGE = "boost-gateway/release-cmake-consumer:ubuntu24.04-gcc13"
TARGET_ARCHITECTURES = {"linux/amd64": "amd64", "linux/arm64": "arm64"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_consumer_project(source: Path) -> None:
    source.mkdir(parents=True, exist_ok=True)
    (source / "CMakeLists.txt").write_text(
        f"""cmake_minimum_required(VERSION 3.21)
project(release_sdk_consumer LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(boost_gateway_sdk {SDK_VERSION} CONFIG REQUIRED)
add_executable(release_sdk_consumer main.cpp)
target_link_libraries(release_sdk_consumer PRIVATE boost_gateway::sdk)
""",
        encoding="utf-8",
    )
    (source / "main.cpp").write_text(
        f"""#include <boost_gateway/sdk/client.h>
#include <boost_gateway/sdk/version.h>
#include <cstring>

int main() {{
    static_assert(BOOST_GATEWAY_SDK_VERSION_MAJOR == 4);
    boost_gateway::sdk::SdkClient client;
    if (client.is_connected()) {{
        return 2;
    }}
    return std::strcmp(BOOST_GATEWAY_SDK_VERSION, "{SDK_VERSION}") == 0 ? 0 : 1;
}}
""",
        encoding="utf-8",
    )


def docker_command(
    image: str,
    package_root: Path,
    source: Path,
    build: Path,
    command: list[str],
    target_platform: str = "linux/amd64",
) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--platform",
        target_platform,
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-e",
        "HOME=/tmp",
        "-v",
        f"{package_root.resolve()}:/opt/boost-gateway:ro",
        "-v",
        f"{source.resolve()}:/work/src:ro",
        "-v",
        f"{build.resolve()}:/work/build",
        image,
        *command,
    ]


def run_check(name: str, command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    return {
        "name": name,
        "passed": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }


def verify_consumer(
    archive: Path,
    expected_root: str,
    image: str,
    candidate_revision: str,
    target_platform: str = "linux/amd64",
) -> dict[str, Any]:
    if shutil.which("docker") is None:
        raise RuntimeError("docker is required for clean CMake consumer validation")
    archive_failures = verify_archive(archive, expected_root)
    if archive_failures:
        raise RuntimeError("; ".join(archive_failures))

    image_result = (
        subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}} {{.Architecture}}",
                image,
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        .stdout.strip()
        .split()
    )
    expected_architecture = TARGET_ARCHITECTURES[target_platform]
    if len(image_result) != 2 or image_result[1] != expected_architecture:
        raise RuntimeError(
            f"compiler image must be {target_platform}: {' '.join(image_result)}"
        )

    with tempfile.TemporaryDirectory(
        prefix="boost-gateway-cmake-consumer-"
    ) as directory:
        root = Path(directory)
        extract_archive(archive, root / "package")
        package_root = root / "package" / expected_root
        source = root / "source"
        build = root / "build"
        build.mkdir()
        write_consumer_project(source)
        checks = [
            run_check(
                "toolchain-versions",
                docker_command(
                    image,
                    package_root,
                    source,
                    build,
                    [
                        "bash",
                        "-c",
                        "set -euo pipefail; gcc-13 --version | head -1; cmake --version | head -1; ninja --version",
                    ],
                    target_platform,
                ),
            ),
            run_check(
                "configure-consumer",
                docker_command(
                    image,
                    package_root,
                    source,
                    build,
                    [
                        "cmake",
                        "-S",
                        "/work/src",
                        "-B",
                        "/work/build",
                        "-G",
                        "Ninja",
                        "-DCMAKE_BUILD_TYPE=Release",
                        "-DCMAKE_PREFIX_PATH=/opt/boost-gateway",
                    ],
                    target_platform,
                ),
            ),
        ]
        if checks[-1]["passed"]:
            checks.append(
                run_check(
                    "build-consumer",
                    docker_command(
                        image,
                        package_root,
                        source,
                        build,
                        ["cmake", "--build", "/work/build", "--parallel", "2"],
                        target_platform,
                    ),
                )
            )
        if checks[-1]["passed"]:
            checks.append(
                run_check(
                    "run-consumer",
                    docker_command(
                        image,
                        package_root,
                        source,
                        build,
                        ["/work/build/release_sdk_consumer"],
                        target_platform,
                    ),
                )
            )

    failed = [check for check in checks if not check["passed"]]
    versions = checks[0]["stdout"].splitlines() if checks else []
    return {
        "summary_version": 2,
        "generated_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "candidate_revision": candidate_revision,
        "archive": {
            "name": archive.name,
            "sha256": sha256(archive),
            "root": expected_root,
        },
        "clean_environment": {
            "image": image,
            "image_id": image_result[0],
            "architecture": image_result[1],
            "target_platform": target_platform,
            "network": "none",
            "pull_policy": "never",
            "compiler": versions[0] if len(versions) > 0 else "",
            "cmake": versions[1] if len(versions) > 1 else "",
            "ninja": versions[2] if len(versions) > 2 else "",
        },
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-root", required=True)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--candidate-revision", required=True)
    parser.add_argument(
        "--target-platform",
        choices=sorted(TARGET_ARCHITECTURES),
        default="linux/amd64",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=Path("runtime/validation/release-cmake-consumer-summary.json"),
    )
    args = parser.parse_args()
    try:
        summary = verify_consumer(
            args.archive.resolve(),
            args.expected_root,
            args.image,
            args.candidate_revision,
            args.target_platform,
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"release clean CMake consumer: FAIL ({exc})")
        return 1
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"release clean CMake consumer: {'PASS' if summary['passed'] else 'FAIL'} "
        f"({len(summary['checks']) - summary['failed_checks']}/{len(summary['checks'])} checks)"
    )
    print(f"summary: {args.summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
