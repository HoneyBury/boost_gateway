#!/usr/bin/env python3
"""Validate SDK packaging, ABI, and wrapper distribution facts."""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
SDK_VERSION = "4.2.0"

REQUIRED_C_API_SYMBOLS = {
    "gsdk_version",
    "gsdk_create",
    "gsdk_destroy",
    "gsdk_connect",
    "gsdk_disconnect",
    "gsdk_is_connected",
    "gsdk_on_push",
    "gsdk_on_disconnect",
    "gsdk_start_heartbeat",
    "gsdk_stop_heartbeat",
    "gsdk_login",
    "gsdk_create_room",
    "gsdk_join_room",
    "gsdk_leave_room",
    "gsdk_set_ready",
    "gsdk_start_battle",
    "gsdk_send_battle_input",
    "gsdk_echo",
}

WRAPPER_METHODS = {
    "connect",
    "disconnect",
    "start_heartbeat",
    "stop_heartbeat",
    "login",
    "create_room",
    "join_room",
    "leave_room",
    "set_ready",
    "start_battle",
    "send_battle_input",
    "echo",
}


def read_text(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def add_check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def validate_versions(checks: list[dict[str, Any]]) -> None:
    cmake = read_text("sdk/CMakeLists.txt")
    version_header = read_text("sdk/include/boost_gateway/sdk/version.h.in")
    c_api = read_text("sdk/include/boost_gateway/sdk/c_api.h")
    docs = read_text("sdk/docs/README.md")
    python_setup = read_text("sdk/python/setup.py")
    python_project = read_text("sdk/python/pyproject.toml")
    csharp_project = read_text("sdk/csharp/SdkClient.csproj")
    compatibility = read_text("sdk/docs/compatibility.md")

    add_check(checks, "sdk-version:cmake", f'"{SDK_VERSION}"' in cmake, "CMake SDK version is 4.2.0")
    add_check(
        checks,
        "sdk-version:minor",
        "set(BOOST_GATEWAY_SDK_VERSION_MINOR 2)" in cmake,
        "CMake SDK minor version is 2",
    )
    add_check(
        checks,
        "sdk-version:generated-header",
        "BOOST_GATEWAY_SDK_VERSION" in version_header,
        "generated version header exposes SDK version macros",
    )
    add_check(checks, "sdk-version:c-api-doc", "SDK v4.2.0" in c_api, "C API header version is current")
    add_check(checks, "sdk-version:docs", "v4.2.0" in docs, "SDK docs mention current version")
    add_check(
        checks,
        "sdk-version:python-package",
        f'version="{SDK_VERSION}"' in python_setup,
        "Python package version matches the SDK version",
    )
    add_check(
        checks,
        "sdk-version:python-project-metadata",
        f'version = "{SDK_VERSION}"' in python_project,
        "pyproject.toml version matches the native SDK version",
    )
    add_check(
        checks,
        "sdk-python:pep621-dynamic-metadata",
        'dynamic = ["readme", "authors", "classifiers"]' in python_project,
        "setup.py-owned metadata is declared dynamic for current setuptools",
    )
    add_check(
        checks,
        "sdk-version:csharp-project-metadata",
        f"<Version>{SDK_VERSION}</Version>" in csharp_project,
        "C# package version matches the native SDK version",
    )
    add_check(
        checks,
        "sdk-version:gateway-v35-compatibility",
        "`v3.5.x`" in compatibility and "`v4.2.0`" in compatibility,
        "compatibility matrix records Gateway v3.5.x with SDK v4.2.0",
    )


def validate_cmake_distribution(checks: list[dict[str, Any]]) -> None:
    cmake = read_text("sdk/CMakeLists.txt")
    config = read_text("sdk/cmake/boost_gateway_sdk-config.cmake.in")

    add_check(
        checks,
        "sdk-cmake:dll-target",
        "add_library(boost_gateway_sdk_dll SHARED" in cmake,
        "C API shared library target exists",
    )
    add_check(
        checks,
        "sdk-cmake:dll-install",
        "install(TARGETS boost_gateway_sdk_dll" in cmake,
        "C API shared library is installed",
    )
    add_check(
        checks,
        "sdk-cmake:static-export",
        "install(TARGETS boost_gateway_sdk EXPORT boost_gateway_sdk-targets" in cmake,
        "static C++ SDK target is exported",
    )
    add_check(
        checks,
        "sdk-cmake:config-version-vars",
        "BoostGatewaySdk_VERSION" in config and "@BOOST_GATEWAY_SDK_VERSION@" in config,
        "package config exposes version variables",
    )
    add_check(
        checks,
        "sdk-cmake:grpc-target",
        "add_library(boost_gateway_sdk_grpc STATIC" in cmake,
        "experimental gRPC SDK target exists",
    )
    add_check(
        checks,
        "sdk-cmake:grpc-export",
        "project_proto" in cmake
        and "boost_gateway_sdk_grpc" in cmake
        and "EXPORT boost_gateway_sdk-targets" in cmake,
        "gRPC SDK target and generated proto target are exported for install consumers",
    )
    add_check(
        checks,
        "sdk-cmake:grpc-headers-install",
        "include/boost_gateway/sdk/grpc_client.h" in cmake and "gateway.grpc.pb.h" in cmake,
        "gRPC SDK header and generated proto headers are installed",
    )
    add_check(
        checks,
        "sdk-cmake:grpc-config-deps",
        "BOOST_GATEWAY_SDK_WITH_GRPC" in config
        and "find_dependency(Protobuf CONFIG REQUIRED)" in config
        and "find_dependency(gRPC CONFIG REQUIRED)" in config
        and "boost_gateway::sdk_grpc" in config,
        "package config exposes gRPC capability flag, dependencies, and alias",
    )


def validate_c_api(checks: list[dict[str, Any]]) -> None:
    header = read_text("sdk/include/boost_gateway/sdk/c_api.h")
    source = read_text("sdk/src/c_api.cpp")

    for symbol in sorted(REQUIRED_C_API_SYMBOLS):
        add_check(
            checks,
            f"sdk-c-api:{symbol}:declared",
            re.search(rf"\b{re.escape(symbol)}\s*\(", header) is not None,
            f"{symbol} is declared in public C API",
        )
        add_check(
            checks,
            f"sdk-c-api:{symbol}:defined",
            re.search(rf"\b{re.escape(symbol)}\s*\(", source) is not None,
            f"{symbol} is defined in C API implementation",
        )

    add_check(
        checks,
        "sdk-c-api:null-guards",
        "c == nullptr" in source and "invalid_argument" in source,
        "C API protects opaque handles and invalid arguments",
    )
    add_check(
        checks,
        "sdk-c-api:exception-boundary",
        "catch (const std::exception" in source,
        "C API catches C++ exceptions at ABI boundary",
    )


def validate_wrappers(checks: list[dict[str, Any]]) -> None:
    python = read_text("sdk/python/__init__.py")
    csharp = read_text("sdk/csharp/SdkClient.cs")

    add_check(
        checks,
        "sdk-python:version-binding",
        "gsdk_version" in python and "assert_compatible_version" in python,
        "Python wrapper exposes and validates native SDK version",
    )
    add_check(
        checks,
        "sdk-csharp:version-binding",
        "gsdk_version" in csharp and "AssertCompatibleNativeVersion" in csharp,
        "C# wrapper exposes and validates native SDK version",
    )
    add_check(
        checks,
        "sdk-python:load-diagnostics",
        "BOOST_GATEWAY_SDK_LIBRARY" in python and "_load_errors" in python,
        "Python wrapper reports native library load diagnostics",
    )
    add_check(
        checks,
        "sdk-csharp:allocation-guard",
        "native client allocation failed" in csharp,
        "C# wrapper reports native allocation failure",
    )
    for method in sorted(WRAPPER_METHODS):
        add_check(
            checks,
            f"sdk-python:{method}",
            f"def {method}" in python,
            f"Python wrapper exposes {method}",
        )
    for symbol in ("gsdk_start_battle", "gsdk_send_battle_input", "gsdk_echo", "gsdk_start_heartbeat", "gsdk_stop_heartbeat"):
        add_check(
            checks,
            f"sdk-csharp:{symbol}",
            symbol in csharp,
            f"C# wrapper imports {symbol}",
        )


def validate_docs(checks: list[dict[str, Any]]) -> None:
    docs = read_text("sdk/docs/README.md")
    compatibility = read_text("sdk/docs/compatibility.md")
    roadmap = read_text("sdk/docs/roadmap.md")
    add_check(
        checks,
        "sdk-docs:distribution",
        "find_package(boost_gateway_sdk" in docs and "boost_gateway::sdk" in docs,
        "SDK docs include installed package consumption path",
    )
    add_check(
        checks,
        "sdk-docs:grpc-distribution",
        "boost_gateway::sdk_grpc" in docs and "--with-grpc" in docs,
        "SDK docs describe installed gRPC package consumption and smoke validation",
    )
    add_check(
        checks,
        "sdk-docs:c-api",
        "C API" in docs and "Python" in docs and "C#" in docs,
        "SDK docs cover cross-language wrappers",
    )
    add_check(
        checks,
        "sdk-docs:tls-profile",
        "--backend-tls" in docs and "--backend-tls" in compatibility,
        "SDK docs describe the backend TLS full-flow profile",
    )
    add_check(
        checks,
        "sdk-roadmap:p3",
        "P3" in roadmap or "分发" in roadmap,
        "SDK roadmap names current distribution priority",
    )


def validate_tests_and_tools(checks: list[dict[str, Any]]) -> None:
    tests_cmake = read_text("sdk/tests/CMakeLists.txt")
    c_api_test = read_text("sdk/tests/unit/c_api_test.cpp")
    package_builder = read_text("scripts/tools/build_sdk_packages.py")
    package_verifier = read_text("scripts/tools/verify_sdk_distribution_packages.py")
    package_workflow = read_text(".github/workflows/sdk-distribution.yml")
    add_check(
        checks,
        "sdk-tests:c-api-test-registered",
        "unit/c_api_test.cpp" in tests_cmake,
        "C ABI boundary tests are compiled into sdk_tests",
    )
    add_check(
        checks,
        "sdk-tests:c-api-version",
        "gsdk_version()" in c_api_test and "BOOST_GATEWAY_SDK_VERSION" in c_api_test,
        "C ABI tests verify native version",
    )
    add_check(
        checks,
        "sdk-tests:c-api-null-guards",
        "NullHandleOperationsAreSafe" in c_api_test and "InvalidArgumentsReturnErrors" in c_api_test,
        "C ABI tests cover null handles and invalid arguments",
    )
    add_check(
        checks,
        "sdk-tools:consumer-smoke",
        (REPO_ROOT / "scripts/gates/sdk/verify_sdk_package_consumer.py").exists(),
        "installed package consumer verification script exists",
    )
    add_check(
        checks,
        "sdk-tools:nuget-pack-build",
        '"-p:GeneratePackageOnBuild=false"' in package_builder,
        "explicit dotnet pack keeps its managed build dependency enabled",
    )
    add_check(
        checks,
        "sdk-tools:nuget-consumer-system-namespace",
        "using System; using BoostGateway.Sdk;" in package_verifier,
        "clean NuGet consumer imports System without relying on implicit usings",
    )
    add_check(
        checks,
        "sdk-workflow:dedicated-package-python",
        'package_python="$PACKAGE_PYTHON"' in package_workflow
        and 'package_python="$(command -v python)"' not in package_workflow
        and 'BOOST_GATEWAY_PACKAGE_PYTHON=%s' in package_workflow
        and package_workflow.count('"$BOOST_GATEWAY_PACKAGE_PYTHON"') >= 4,
        "wheel/NuGet tools use the admitted package venv by absolute path after managed Python setup",
    )
    add_check(
        checks,
        "sdk-workflow:package-toolchain-maintenance",
        "prepare_package_toolchain:" in package_workflow
        and "if: inputs.prepare_package_toolchain" in package_workflow
        and '"setuptools==83.0.0"' in package_workflow
        and '"wheel==0.47.0"' in package_workflow
        and '"auditwheel==6.7.0"' in package_workflow
        and "backup-${GITHUB_RUN_ID}" in package_workflow
        and "restore_backup" in package_workflow,
        "manual SDK dispatch can restore the pinned package venv with rollback while normal evidence remains offline",
    )
    add_check(
        checks,
        "sdk-tools:grpc-consumer-smoke",
        "--with-grpc" in read_text("scripts/gates/sdk/verify_sdk_package_consumer.py"),
        "installed package consumer verification supports the gRPC SDK path",
    )
    add_check(
        checks,
        "sdk-docs:consumer-smoke",
        "verify_sdk_package_consumer.py" in read_text("sdk/docs/README.md"),
        "SDK docs mention installed package consumer verification",
    )
    add_check(
        checks,
        "sdk-tests:business-flow-target",
        "sdk_business_flow_tests" in tests_cmake and "sdk_integration_test.cpp" in tests_cmake,
        "SDK business flow integration target is registered",
    )
    client_test = read_text("sdk/tests/unit/client_test.cpp")
    integration_test = read_text("sdk/tests/sdk_integration_test.cpp")
    add_check(
        checks,
        "sdk-tests:heartbeat-unit",
        "HeartbeatLifecycleSafeWhenDisconnected" in client_test,
        "SDK unit tests cover heartbeat lifecycle without a connection",
    )
    add_check(
        checks,
        "sdk-tests:heartbeat-integration",
        "SdkHeartbeatKeepsConnectionAlive" in integration_test,
        "SDK integration tests cover heartbeat against a real gateway",
    )
    add_check(
        checks,
        "sdk-tests:disconnect-callback-integration",
        "SdkDisconnectCallbackFiresAfterHeartbeatFailure" in integration_test,
        "SDK integration tests cover disconnect callback on heartbeat failure",
    )
    add_check(
        checks,
        "sdk-tools:business-flow",
        (REPO_ROOT / "scripts/gates/sdk/verify_sdk_business_flow.py").exists(),
        "SDK business flow verification script exists",
    )
    add_check(
        checks,
        "sdk-tools:full-flow-client",
        (REPO_ROOT / "scripts/gates/sdk/verify_sdk_full_flow_client.py").exists()
        and "--backend-tls" in read_text("scripts/gates/sdk/verify_sdk_full_flow_client.py"),
        "SDK full-flow example verification script exists and supports backend TLS profile",
    )


def validate_build_artifacts(build_dir: Path | None, checks: list[dict[str, Any]]) -> None:
    if build_dir is None:
        return
    release_dir = build_dir / "Release"
    expected = {
        "static-library": ("libboost_gateway_sdk.a", "boost_gateway_sdk.lib"),
        "shared-library": (
            "libboost_gateway_sdk.dylib",
            "libboost_gateway_sdk.so",
            "boost_gateway_sdk.dll",
        ),
        "unit-test": ("sdk_tests", "sdk_tests.exe"),
    }
    for name, candidates in expected.items():
        search_roots = [build_dir]
        if release_dir.exists():
            search_roots.append(release_dir)
        found = any(
            any(root.rglob(candidate) for root in search_roots)
            for candidate in candidates
        )
        add_check(
            checks,
            f"sdk-artifact:{name}",
            found,
            f"{name} found under {build_dir}" if found else f"{name} missing under {build_dir}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, help="Optional build tree to validate SDK artifacts")
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/sdk-distribution-summary.json",
        help="Path for JSON summary output",
    )
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    validate_versions(checks)
    validate_cmake_distribution(checks)
    validate_c_api(checks)
    validate_wrappers(checks)
    validate_docs(checks)
    validate_tests_and_tools(checks)
    validate_build_artifacts(args.build_dir, checks)

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "sdk_distribution" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {
            "summary_path": str(args.summary_path),
            "sdk_readme": str(REPO_ROOT / "sdk/docs/README.md"),
            "sdk_compatibility": str(REPO_ROOT / "sdk/docs/compatibility.md"),
            "sdk_cmake": str(REPO_ROOT / "sdk/CMakeLists.txt"),
        },
    }
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(
        f"sdk distribution: {'PASS' if summary['overall_pass'] else 'FAIL'} "
        f"({len(checks) - len(failed)}/{len(checks)} checks)"
    )
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check['detail']}")
        return 1
    print(f"summary: {args.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
