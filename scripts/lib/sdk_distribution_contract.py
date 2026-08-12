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



"""Shared implementation extracted from check_sdk_distribution.py."""

REPO_ROOT = Path(__file__).resolve().parents[2]
SDK_VERSION = "4.2.1"

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

    add_check(checks, "sdk-version:cmake", f'"{SDK_VERSION}"' in cmake, "CMake SDK version is 4.2.1")
    add_check(
        checks,
        "sdk-version:minor",
        "set(BOOST_GATEWAY_SDK_VERSION_MINOR 2)" in cmake,
        "CMake SDK minor version is 2",
    )
    add_check(
        checks,
        "sdk-version:patch",
        "set(BOOST_GATEWAY_SDK_VERSION_PATCH 1)" in cmake,
        "CMake SDK patch version is 1",
    )
    add_check(
        checks,
        "sdk-version:generated-header",
        "BOOST_GATEWAY_SDK_VERSION" in version_header,
        "generated version header exposes SDK version macros",
    )
    add_check(checks, "sdk-version:c-api-doc", "SDK v4.2.1" in c_api, "C API header version is current")
    add_check(checks, "sdk-version:docs", "v4.2.1" in docs, "SDK docs mention current version")
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
        "`v3.5.x`" in compatibility and "`v4.2.1`" in compatibility,
        "compatibility matrix records Gateway v3.5.x with SDK v4.2.1",
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

