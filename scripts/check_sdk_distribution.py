#!/usr/bin/env python3
"""Validate SDK packaging, ABI, and wrapper distribution facts."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SDK_VERSION = "4.1.0"

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

    add_check(checks, "sdk-version:cmake", f'"{SDK_VERSION}"' in cmake, "CMake SDK version is 4.1.0")
    add_check(
        checks,
        "sdk-version:minor",
        "set(BOOST_GATEWAY_SDK_VERSION_MINOR 1)" in cmake,
        "CMake SDK minor version is 1",
    )
    add_check(
        checks,
        "sdk-version:generated-header",
        "BOOST_GATEWAY_SDK_VERSION" in version_header,
        "generated version header exposes SDK version macros",
    )
    add_check(checks, "sdk-version:c-api-doc", "SDK v4.1.0" in c_api, "C API header version is current")
    add_check(checks, "sdk-version:docs", "v4.1.0" in docs, "SDK docs mention current version")


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
        (REPO_ROOT / "scripts/verify_sdk_package_consumer.py").exists(),
        "installed package consumer verification script exists",
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
        (REPO_ROOT / "scripts/verify_sdk_business_flow.py").exists(),
        "SDK business flow verification script exists",
    )
    add_check(
        checks,
        "sdk-tools:full-flow-client",
        (REPO_ROOT / "scripts/verify_sdk_full_flow_client.py").exists()
        and "--backend-tls" in read_text("scripts/verify_sdk_full_flow_client.py"),
        "SDK full-flow example verification script exists and supports backend TLS profile",
    )


def validate_build_artifacts(build_dir: Path | None, checks: list[dict[str, Any]]) -> None:
    if build_dir is None:
        return
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
        found = any(list(build_dir.rglob(candidate)) for candidate in candidates)
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
        "passed": not failed,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
    }
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(
        f"sdk distribution: {'PASS' if summary['passed'] else 'FAIL'} "
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
