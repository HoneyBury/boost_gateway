#!/usr/bin/env python3
"""Install the SDK and verify a downstream CMake consumer can use it."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SDK_VERSION = "4.1.0"


def run_step(name: str, command: list[str], cwd: Path, checks: list[dict[str, Any]]) -> bool:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    passed = result.returncode == 0
    checks.append(
        {
            "name": name,
            "passed": passed,
            "command": command,
            "stdout": stdout[-4000:],
            "stderr": stderr[-4000:],
        }
    )
    return passed


def write_consumer_project(root: Path, prefix: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    cmake_prefix = prefix.as_posix()
    (root / "CMakeLists.txt").write_text(
        f"""cmake_minimum_required(VERSION 3.21)
project(sdk_consumer_smoke LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(boost_gateway_sdk {SDK_VERSION} CONFIG REQUIRED
    PATHS "{cmake_prefix}"
    NO_DEFAULT_PATH)
add_executable(sdk_consumer_smoke main.cpp)
target_link_libraries(sdk_consumer_smoke PRIVATE boost_gateway::sdk)
""",
        encoding="utf-8",
    )
    (root / "main.cpp").write_text(
        f"""#include <boost_gateway/sdk/client.h>
#include <boost_gateway/sdk/version.h>
#include <cstring>

int main() {{
    static_assert(BOOST_GATEWAY_SDK_VERSION_MAJOR == 4);
    static_assert(BOOST_GATEWAY_SDK_VERSION_MINOR == 1);
    boost_gateway::sdk::SdkClient client;
    if (client.is_connected()) {{
        return 2;
    }}
    return std::strcmp(BOOST_GATEWAY_SDK_VERSION, "{SDK_VERSION}") == 0 ? 0 : 1;
}}
""",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=REPO_ROOT / "build/default")
    parser.add_argument("--prefix", type=Path, default=REPO_ROOT / "runtime/sdk-package-prefix")
    parser.add_argument("--work-dir", type=Path, default=REPO_ROOT / "runtime/sdk-package-consumer")
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/sdk-package-consumer-summary.json",
    )
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    prefix = args.prefix.resolve()
    work_dir = args.work_dir.resolve()
    consumer_src = work_dir / "src"
    consumer_build = work_dir / "build"

    if prefix.exists():
        shutil.rmtree(prefix)
    if work_dir.exists():
        shutil.rmtree(work_dir)

    install_ok = run_step(
        "build-sdk-install-artifacts",
        [
            "cmake",
            "--build",
            str(args.build_dir),
            "--config",
            "Release",
            "--target",
            "boost_gateway_sdk",
            "boost_gateway_sdk_dll",
        ],
        REPO_ROOT,
        checks,
    )
    if install_ok:
        install_ok = run_step(
            "install-sdk",
            ["cmake", "--install", str(args.build_dir / "sdk"), "--config", "Release", "--prefix", str(prefix)],
            REPO_ROOT,
            checks,
        )
    if install_ok:
        write_consumer_project(consumer_src, prefix)
        configure_ok = run_step(
            "configure-consumer",
            ["cmake", "-S", str(consumer_src), "-B", str(consumer_build), "-DCMAKE_BUILD_TYPE=Release"],
            REPO_ROOT,
            checks,
        )
        build_ok = configure_ok and run_step(
            "build-consumer",
            ["cmake", "--build", str(consumer_build), "--config", "Release", "--parallel"],
            REPO_ROOT,
            checks,
        )
        if build_ok:
            exe = consumer_build / "sdk_consumer_smoke"
            if os.name == "nt":
                exe = consumer_build / "Release" / "sdk_consumer_smoke.exe"
            run_step("run-consumer", [str(exe)], REPO_ROOT, checks)

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "passed": not failed,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "prefix": str(prefix),
        "work_dir": str(work_dir),
        "checks": checks,
    }
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"sdk package consumer: {'PASS' if summary['passed'] else 'FAIL'} "
        f"({len(checks) - len(failed)}/{len(checks)} checks)"
    )
    if failed:
        for check in failed:
            print(f"  - {check['name']}")
            if check.get("stderr"):
                print(check["stderr"])
        return 1
    print(f"summary: {args.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
