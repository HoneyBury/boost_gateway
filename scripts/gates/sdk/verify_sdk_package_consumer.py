#!/usr/bin/env python3
"""Install the SDK and verify a downstream CMake consumer can use it."""

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
import json
import os
import platform
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.lib.subprocess_utils import run_step

REPO_ROOT = Path(__file__).resolve().parents[3]
SDK_VERSION = "4.2.0"


def read_cache_path(build_dir: Path, key: str) -> str:
    cache = build_dir / "CMakeCache.txt"
    if not cache.exists():
        return ""
    pattern = re.compile(rf"^{re.escape(key)}(?::[A-Z_]+)?=(.*)$")
    for line in cache.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1).strip()
    return ""


def write_consumer_project(root: Path, prefix: Path, with_grpc: bool) -> None:
    root.mkdir(parents=True, exist_ok=True)
    cmake_prefix = prefix.as_posix()
    grpc_block = ""
    if with_grpc:
        grpc_block = """
if(NOT BOOST_GATEWAY_SDK_WITH_GRPC)
    message(FATAL_ERROR "installed package did not expose gRPC support")
endif()
"""
    (root / "CMakeLists.txt").write_text(
        f"""cmake_minimum_required(VERSION 3.21)
project(sdk_consumer_smoke LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(boost_gateway_sdk {SDK_VERSION} CONFIG REQUIRED
    PATHS "{cmake_prefix}"
    NO_DEFAULT_PATH)
{grpc_block}
add_executable(sdk_consumer_smoke main.cpp)
target_link_libraries(sdk_consumer_smoke PRIVATE boost_gateway::sdk{" boost_gateway::sdk_grpc" if with_grpc else ""})
""",
        encoding="utf-8",
    )
    grpc_include = ""
    grpc_main = ""
    if with_grpc:
        grpc_include = "#include <boost_gateway/sdk/grpc_client.h>\n#include <gateway.grpc.pb.h>\n"
        grpc_main = """
    boost_gateway::sdk::GrpcClient grpc_client;
    boost::gateway::v3::LoginRequest request;
    request.set_user_id("smoke-user");
    if (grpc_client.is_connected()) {
        return 3;
    }
"""
    (root / "main.cpp").write_text(
        f"""#include <boost_gateway/sdk/client.h>
#include <boost_gateway/sdk/version.h>
#include <cstring>
{grpc_include}

int main() {{
    static_assert(BOOST_GATEWAY_SDK_VERSION_MAJOR == 4);
    static_assert(BOOST_GATEWAY_SDK_VERSION_MINOR == 2);
    boost_gateway::sdk::SdkClient client;
    if (client.is_connected()) {{
        return 2;
    }}
{grpc_main}
    return std::strcmp(BOOST_GATEWAY_SDK_VERSION, "{SDK_VERSION}") == 0 ? 0 : 1;
}}
""",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=REPO_ROOT / "build/default")
    parser.add_argument("--configuration", default="Debug")
    parser.add_argument("--with-grpc", action="store_true")
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
    sdk_install_script = args.build_dir / "sdk" / "cmake_install.cmake"

    if prefix.exists():
        shutil.rmtree(prefix)
    if work_dir.exists():
        shutil.rmtree(work_dir)

    result = run_step(
        name="build-sdk-install-artifacts",
        command=[
            "cmake",
            "--build",
            str(args.build_dir),
            "--config",
            args.configuration,
            "--target",
            "boost_gateway_sdk",
            "boost_gateway_sdk_dll",
            *(["project_proto", "boost_gateway_sdk_grpc"] if args.with_grpc else []),
        ],
        cwd=REPO_ROOT,
        timeout_seconds=300,
        tail_chars=4000,
    )
    checks.append(result)
    install_ok = bool(result["passed"])
    if install_ok:
        result = run_step(
            name="install-sdk",
            command=[
                "cmake",
                f"-DCMAKE_INSTALL_PREFIX={prefix}",
                f"-DCMAKE_INSTALL_CONFIG_NAME={args.configuration}",
                "-DCMAKE_INSTALL_LOCAL_ONLY=1",
                "-P",
                str(sdk_install_script),
            ],
            cwd=REPO_ROOT,
            timeout_seconds=300,
            tail_chars=4000,
        )
        checks.append(result)
        install_ok = bool(result["passed"])
    if install_ok:
        write_consumer_project(consumer_src, prefix, args.with_grpc)
        configure_command = [
            "cmake",
            "-S",
            str(consumer_src),
            "-B",
            str(consumer_build),
            f"-DCMAKE_BUILD_TYPE={args.configuration}",
        ]
        if args.with_grpc:
            protobuf_dir = read_cache_path(args.build_dir, "Protobuf_DIR")
            grpc_dir = read_cache_path(args.build_dir, "gRPC_DIR")
            prefix_paths = [prefix.as_posix()]
            if grpc_dir:
                prefix_paths.append(grpc_dir)
            elif protobuf_dir:
                prefix_paths.append(protobuf_dir)
            configure_command.append(f"-DCMAKE_PREFIX_PATH={';'.join(prefix_paths)}")
            if protobuf_dir:
                configure_command.append(f"-DProtobuf_DIR={protobuf_dir}")
            if grpc_dir:
                configure_command.append(f"-DgRPC_DIR={grpc_dir}")
        result = run_step(
            name="configure-consumer",
            command=configure_command,
            cwd=REPO_ROOT,
            timeout_seconds=300,
            tail_chars=4000,
        )
        checks.append(result)
        configure_ok = bool(result["passed"])
        build_ok = False
        if configure_ok:
            result = run_step(
                name="build-consumer",
                command=["cmake", "--build", str(consumer_build), "--config", args.configuration, "--parallel"],
                cwd=REPO_ROOT,
                timeout_seconds=300,
                tail_chars=4000,
            )
            checks.append(result)
            build_ok = bool(result["passed"])
        if build_ok:
            exe = consumer_build / "sdk_consumer_smoke"
            if os.name == "nt":
                exe = consumer_build / args.configuration / "sdk_consumer_smoke.exe"
            checks.append(run_step(
                name="run-consumer",
                command=[str(exe)],
                cwd=REPO_ROOT,
                timeout_seconds=300,
                tail_chars=4000,
            ))

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "sdk_package_consumer" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "configuration": args.configuration,
        "with_grpc": args.with_grpc,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "prefix": str(prefix),
        "work_dir": str(work_dir),
        "checks": checks,
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "artifacts": {
            "summary_path": str(args.summary_path),
            "prefix": str(prefix),
            "work_dir": str(work_dir),
        },
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
