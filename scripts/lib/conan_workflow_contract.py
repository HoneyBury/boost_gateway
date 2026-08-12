#!/usr/bin/env python3
"""Validate Conan nosqlite lockfile workflow wiring for mainline and fixed-runner flows."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any



"""Shared implementation extracted from check_conan_lockfile_workflows.py."""

ROOT = Path(__file__).resolve().parents[2]
LOCKFILE = "conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock"
GRPC_LOCKFILE = "conan/locks/linux-gcc-x64-release-grpc-nosqlite.lock"
PROFILE = "conan/profiles/linux-gcc-x64"
MACOS_LOCKFILE = "conan/locks/macos-apple-clang-arm64-release-nogrpc-nosqlite.lock"
MACOS_PROFILE = "conan/profiles/macos-apple-clang-arm64"
LINUX_ARM64_LOCKFILE = "conan/locks/linux-gcc-arm64-release-nogrpc-nosqlite.lock"
LINUX_ARM64_DEBUG_LOCKFILE = "conan/locks/linux-gcc-arm64-debug-nogrpc-nosqlite.lock"
LINUX_ARM64_GRPC_LOCKFILE = "conan/locks/linux-gcc-arm64-release-grpc-nosqlite.lock"
LINUX_ARM64_PROFILE = "conan/profiles/linux-gcc-arm64"
CACHE_INPUTS = ("conanfile.py", "conan/profiles/**", "conan/remotes*.json", "conan/locks/*.lock")


WORKFLOWS = {
    "ci": ".github/workflows/ci.yml",
    "conan_validate": ".github/workflows/conan-validate.yml",
    "release": ".github/workflows/release.yml",
    "long_soak_capacity": ".github/workflows/long-soak-capacity.yml",
    "production_gates": ".github/workflows/production-gates.yml",
    "production_candidate_evidence": ".github/workflows/production-candidate-evidence.yml",
}

# Fixed runners derive a persistent cache namespace from host ABI inputs and
# the Conan graph. ``ci.yml`` is intentionally different: it targets
# GitHub-hosted runners and restores a checkout-local home through actions/cache.
FIXED_RUNNER_CONAN_WORKFLOWS = {
    "conan_validate": ".github/workflows/conan-validate.yml",
    "debug_symbols": ".github/workflows/debug-symbols.yml",
    "grpc_experimental": ".github/workflows/grpc-experimental.yml",
    "jwks_rotation": ".github/workflows/jwks-rotation.yml",
    "release": ".github/workflows/release.yml",
    "long_soak_capacity": ".github/workflows/long-soak-capacity.yml",
    "nightly_stability": ".github/workflows/nightly-stability.yml",
    "perf_regression": ".github/workflows/perf-regression.yml",
    "production_candidate_evidence": ".github/workflows/production-candidate-evidence.yml",
    "production_gates": ".github/workflows/production-gates.yml",
    "sdk_distribution": ".github/workflows/sdk-distribution.yml",
    "specialized_e2e": ".github/workflows/specialized-e2e.yml",
    "preprod_evidence": ".github/workflows/preprod-evidence.yml",
    "macos_arm64": ".github/workflows/macos-arm64.yml",
    "sdk_distribution": ".github/workflows/sdk-distribution.yml",
    "debug_symbols": ".github/workflows/debug-symbols.yml",
}
RUNNER_CACHE_RESOLVER = "scripts/tools/resolve_runner_cache.py"
COMPOSITE_CONAN_ACTION = ".github/actions/setup-cpp-conan/action.yml"
PRODUCTION_PLATFORM_ACTION = ".github/actions/resolve-production-platform/action.yml"
CONAN_VENV_HELPER = "scripts/tools/ensure_conan_venv.py"
RAFT_OFFLINE_INSTALLER = "scripts/tools/verify_conan_offline_install.py"
PINNED_CONAN_VERSION = "2.8.1"
FLOATING_CONAN_REQUIREMENT = "conan>=2.0,<2.9"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def exists(relative: str) -> bool:
    return (ROOT / relative).exists()


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def bootstrap_uses_resolved_home(content: str) -> bool:
    """Reject bootstrap calls that can silently create a checkout-local Conan home."""
    calls = [
        line.strip()
        for line in content.splitlines()
        if "scripts/bootstrap_conan.py" in line
        and (
            re.search(r"\bpython(?:3(?:\.\d+)?)?\s+", line) is not None
            or "EVIDENCE_PYTHON" in line
            or "args=(" in line
        )
    ]
    return bool(calls) and all("--conan-home" in call and "CONAN_HOME" in call for call in calls)


def uses_composite_conan_action(content: str) -> bool:
    return "uses: ./.github/actions/setup-cpp-conan" in content


def uses_raft_offline_installer(content: str) -> bool:
    return RAFT_OFFLINE_INSTALLER in content


def composite_uses_pinned_venv(content: str) -> bool:
    return all(
        token in content
        for token in (
            CONAN_VENV_HELPER,
            'default: "2.8.1"',
            '--conan-version "${{ inputs.conan-version }}"',
            '--github-path "$GITHUB_PATH"',
        )
    )


def named_workflow_step(content: str, name: str) -> str:
    marker = f"- name: {name}"
    if marker not in content:
        return ""
    step = content.split(marker, 1)[1]
    return step.split("\n      - name:", 1)[0]


def workflow_checks(checks: list[dict[str, Any]], name: str, path: str, content: str) -> None:
    uses_offline_installer = uses_raft_offline_installer(content)
    add(checks, f"workflow:{name}:exists", exists(path), f"{path} exists")
    add(checks, f"workflow:{name}:linux-lockfile-default", LOCKFILE in content, f"{path} references {LOCKFILE}")
    add(checks, f"workflow:{name}:linux-profile-default", PROFILE in content, f"{path} references {PROFILE}")
    add(checks, f"workflow:{name}:grpc-disabled", '-o "&:with_grpc=False"' in content or uses_offline_installer, f"{path} disables gRPC in default Conan graph")
    add(
        checks,
        f"workflow:{name}:raft-protobuf-enabled",
        '-o "&:with_raft_protobuf=True"' in content or uses_offline_installer,
        f"{path} explicitly enables the default internal Raft protobuf runtime",
    )
    sqlite_disabled = (
        '-o "&:with_sqlite=False"' in content
        or uses_offline_installer
        or (
            "with_sqlite:" in content
            and "default: false" in content
            and '&:with_sqlite=${{' in content
        )
    )
    add(checks, f"workflow:{name}:sqlite-disabled", sqlite_disabled, f"{path} keeps sqlite disabled by default for nosqlite mainline")
    add(checks, f"workflow:{name}:lockfile-consumed", "--lockfile" in content and ("conan install" in content or uses_offline_installer), f"{path} consumes lockfile during conan install")
    if "cmake " in content:
        add(
            checks,
            f"workflow:{name}:strict-conan-provider",
            "-DBOOST_DEPENDENCY_PROVIDER=conan" in content,
            f"{path} explicitly configures the strict Conan dependency provider",
        )
    add(
        checks,
        f"workflow:{name}:artifact-upload",
        "actions/upload-artifact@" in content,
        f"{path} uploads Conan/fixed-runner validation artifacts",
    )
    if "uses: actions/cache/" in content:
        add(
            checks,
            f"workflow:{name}:cache-key-includes-conan-inputs",
            all(token in content for token in CACHE_INPUTS),
            f"{path} cache key is bound to Conan graph inputs",
        )

