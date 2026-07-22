#!/usr/bin/env python3
"""Validate Conan nosqlite lockfile workflow wiring for mainline and fixed-runner flows."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
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
    "grpc_experimental": ".github/workflows/grpc-experimental.yml",
    "release": ".github/workflows/release.yml",
    "long_soak_capacity": ".github/workflows/long-soak-capacity.yml",
    "nightly_stability": ".github/workflows/nightly-stability.yml",
    "perf_regression": ".github/workflows/perf-regression.yml",
    "production_candidate_evidence": ".github/workflows/production-candidate-evidence.yml",
    "production_gates": ".github/workflows/production-gates.yml",
    "specialized_e2e": ".github/workflows/specialized-e2e.yml",
    "preprod_evidence": ".github/workflows/preprod-evidence.yml",
    "macos_arm64": ".github/workflows/macos-arm64.yml",
}
RUNNER_CACHE_RESOLVER = "scripts/tools/resolve_runner_cache.py"
COMPOSITE_CONAN_ACTION = ".github/actions/setup-cpp-conan/action.yml"
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
        and (re.search(r"\bpython(?:3(?:\.\d+)?)?\s+", line) is not None or "args=(" in line)
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
        "actions/upload-artifact@v4" in content,
        f"{path} uploads Conan/fixed-runner validation artifacts",
    )
    if "actions/cache@v4" in content:
        add(
            checks,
            f"workflow:{name}:cache-key-includes-conan-inputs",
            all(token in content for token in CACHE_INPUTS),
            f"{path} cache key is bound to Conan graph inputs",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/conan-lockfile-workflows-summary.json",
    )
    args = parser.parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path

    checks: list[dict[str, Any]] = []
    add(checks, "lockfile:linux-nosqlite-exists", exists(LOCKFILE), f"{LOCKFILE} exists")
    add(checks, "lockfile:linux-grpc-nosqlite-exists", exists(GRPC_LOCKFILE), f"{GRPC_LOCKFILE} exists")
    add(checks, "profile:linux-gcc-x64-exists", exists(PROFILE), f"{PROFILE} exists")
    add(checks, "lockfile:linux-arm64-exists", exists(LINUX_ARM64_LOCKFILE), f"{LINUX_ARM64_LOCKFILE} exists")
    add(checks, "lockfile:linux-arm64-debug-exists", exists(LINUX_ARM64_DEBUG_LOCKFILE), f"{LINUX_ARM64_DEBUG_LOCKFILE} exists")
    add(checks, "lockfile:linux-arm64-grpc-exists", exists(LINUX_ARM64_GRPC_LOCKFILE), f"{LINUX_ARM64_GRPC_LOCKFILE} exists")
    add(checks, "profile:linux-gcc-arm64-exists", exists(LINUX_ARM64_PROFILE), f"{LINUX_ARM64_PROFILE} exists")
    add(checks, "lockfile:macos-arm64-exists", exists(MACOS_LOCKFILE), f"{MACOS_LOCKFILE} exists")
    add(checks, "profile:macos-arm64-exists", exists(MACOS_PROFILE), f"{MACOS_PROFILE} exists")
    add(checks, "conanfile:grpc-default-off", '"&:with_grpc": False' in read("conanfile.py"), "conanfile default disables gRPC")
    add(
        checks,
        "conanfile:raft-protobuf-default-on",
        '"&:with_raft_protobuf": True' in read("conanfile.py"),
        "conanfile enables the internal Raft protobuf runtime by default",
    )
    add(checks, "conanfile:sqlite-default-off", '"&:with_sqlite": False' in read("conanfile.py"), "conanfile default disables sqlite")
    root_cmake = read("CMakeLists.txt")
    dependencies_cmake = read("cmake/Dependencies.cmake")
    sdk_cmake = read("sdk/CMakeLists.txt")
    provider_contract = root_cmake + dependencies_cmake + sdk_cmake + read("conanfile.py")
    add(
        checks,
        "provider:default-strict-conan",
        'set(BOOST_DEPENDENCY_PROVIDER "conan" CACHE STRING' in root_cmake,
        "the default CMake dependency provider is strict Conan",
    )
    add(
        checks,
        "provider:explicit-fetchcontent-development-only",
        'if(BOOST_DEPENDENCY_PROVIDER STREQUAL "fetchcontent")' in dependencies_cmake
        and "explicit FetchContent development mode" in dependencies_cmake,
        "FetchContent is reachable only through the explicit development provider",
    )
    add(
        checks,
        "provider:conan-packages-required",
        all(
            f"find_package({package} CONFIG REQUIRED)" in dependencies_cmake
            for package in ("fmt", "spdlog", "nlohmann_json", "OpenSSL", "hiredis", "Boost", "GTest")
        ),
        "strict Conan configuration fails immediately when a generated package config is absent",
    )
    add(
        checks,
        "provider:raft-protobuf-required",
        "find_package(Protobuf CONFIG REQUIRED)" in dependencies_cmake
        and "BOOST_BUILD_RAFT_PROTOBUF" in root_cmake,
        "the default Raft protobuf codec requires protobuf without enabling gRPC",
    )
    default_lock = read(LOCKFILE)
    add(
        checks,
        "lockfile:linux-nosqlite-contains-raft-protobuf-runtime",
        "protobuf/5.27.0#" in default_lock and "abseil/20250127.0#" in default_lock,
        "the default Linux lockfile pins protobuf and its runtime dependency graph",
    )
    add(
        checks,
        "provider:no-legacy-toggle-or-fallback",
        "BOOST_USE_CONAN_DEPS" not in provider_contract and "falling back to FetchContent" not in dependencies_cmake,
        "the dependency contract contains no legacy boolean or implicit fallback",
    )
    offline_installer = read(RAFT_OFFLINE_INSTALLER) if exists(RAFT_OFFLINE_INSTALLER) else ""
    add(
        checks,
        "raft-offline-installer:strict-contract",
        all(
            token in offline_installer
            for token in (
                '"install"',
                '"&:with_grpc=False"',
                '"&:with_raft_protobuf=True"',
                '"&:with_sqlite=False"',
                '"--build=never"',
                '"--no-remote"',
                "build_evidence_provenance",
            )
        ),
        "the shared Raft Conan producer fixes offline options and records provenance",
    )

    contents = {name: read(path) if exists(path) else "" for name, path in WORKFLOWS.items()}
    for name, path in WORKFLOWS.items():
        workflow_checks(checks, name, path, contents[name])

    long_soak = contents["long_soak_capacity"]
    release = contents["release"]
    production_gates = contents["production_gates"]
    add(
        checks,
        "workflow:release:conan-preflight",
        "conan-preflight" in release and "enable_conan_validation" not in release,
        "release workflow always runs lockfile-based Conan preflight before release baseline collection",
    )
    add(
        checks,
        "workflow:long-soak:real-conan-validation-build",
        "build/conan-long-soak-capacity-cmake" in long_soak and "--target project_v2" in long_soak,
        "long-soak-capacity performs a lockfile-based Conan configure/build preflight",
    )
    add(
        checks,
        "workflow:production-gates:real-conan-validation-build",
        "build/conan-production-gates-cmake" in production_gates and "--target project_v2" in production_gates,
        "production-gates performs a lockfile-based Conan configure/build preflight",
    )

    composite_conan = read(COMPOSITE_CONAN_ACTION) if exists(COMPOSITE_CONAN_ACTION) else ""
    add(
        checks,
        "composite-conan:isolated-pinned-venv",
        composite_uses_pinned_venv(composite_conan),
        "the shared Conan action creates or verifies the pinned isolated virtual environment",
    )
    add(
        checks,
        "composite-conan:no-global-or-floating-conan",
        FLOATING_CONAN_REQUIREMENT not in composite_conan and "command -v conan" not in composite_conan,
        "the shared Conan action never accepts a global or floating Conan executable",
    )
    add(
        checks,
        "composite-conan:explicit-persistent-home",
        bootstrap_uses_resolved_home(composite_conan),
        "the shared Conan action passes the resolved CONAN_HOME to bootstrap explicitly",
    )
    for name, path in FIXED_RUNNER_CONAN_WORKFLOWS.items():
        content = read(path) if exists(path) else ""
        uses_composite = uses_composite_conan_action(content)
        resolver_available = RUNNER_CACHE_RESOLVER in content
        if uses_composite and exists(COMPOSITE_CONAN_ACTION):
            resolver_available = resolver_available or RUNNER_CACHE_RESOLVER in read(COMPOSITE_CONAN_ACTION)
        add(
            checks,
            f"workflow:{name}:os-safe-conan-cache",
            resolver_available,
            f"{path} resolves Conan Home through {RUNNER_CACHE_RESOLVER}",
        )
        add(
            checks,
            f"workflow:{name}:no-static-fixed-conan-home",
            "../.conan2-local" not in content,
            f"{path} does not reuse a shared checkout-sibling Conan Home",
        )
        venv_available = all(
            token in content
            for token in (CONAN_VENV_HELPER, f"--conan-version {PINNED_CONAN_VERSION}", '--github-path "$GITHUB_PATH"')
        ) or (uses_composite and composite_uses_pinned_venv(composite_conan))
        add(
            checks,
            f"workflow:{name}:isolated-pinned-conan-venv",
            venv_available,
            f"{path} obtains Conan {PINNED_CONAN_VERSION} through the isolated venv helper or the audited composite action",
        )
        add(
            checks,
            f"workflow:{name}:no-global-or-floating-conan",
            FLOATING_CONAN_REQUIREMENT not in content and "command -v conan" not in content,
            f"{path} does not accept a global Conan executable or a floating Conan version range",
        )
        direct_bootstrap = "scripts/bootstrap_conan.py" in content
        add(
            checks,
            f"workflow:{name}:explicit-persistent-conan-home",
            not direct_bootstrap or bootstrap_uses_resolved_home(content),
            f"{path} passes $CONAN_HOME explicitly on every direct bootstrap call",
        )
        add(
            checks,
            f"workflow:{name}:offline-build-never",
            "--no-remote" not in content
            or "--build=never" in content
            or uses_raft_offline_installer(content),
            f"{path} rejects missing packages instead of building or downloading sources when remotes are disabled",
        )

    grpc_workflow = read(FIXED_RUNNER_CONAN_WORKFLOWS["grpc_experimental"])
    grpc_install = named_workflow_step(grpc_workflow, "Conan install")
    add(
        checks,
        "workflow:grpc-experimental:pinned-grpc-lockfile",
        GRPC_LOCKFILE in grpc_workflow and "scripts/generate_conan_lock.py" not in grpc_workflow,
        "gRPC validation consumes the repository lockfile instead of resolving a graph on the runner",
    )
    add(
        checks,
        "workflow:grpc-experimental:strict-offline-install",
        all(token in grpc_install for token in ("--no-remote", "--build=never", 'with_grpc=True')),
        "gRPC validation only consumes an admitted runner-local binary cache",
    )
    add(
        checks,
        "workflow:grpc-experimental:no-public-path",
        "--allow-public" not in grpc_workflow and "--build=missing" not in grpc_workflow,
        "gRPC validation cannot download or build dependencies as part of the evidence job",
    )

    ci = read(".github/workflows/ci.yml")
    add(
        checks,
        "workflow:ci:isolated-pinned-conan-venv",
        all(
            token in ci
            for token in (CONAN_VENV_HELPER, f"--conan-version {PINNED_CONAN_VERSION}", '--github-path "$GITHUB_PATH"')
        ),
        "ci uses the same pinned isolated Conan virtual environment as development and runners",
    )
    add(
        checks,
        "workflow:ci:no-global-or-floating-conan",
        FLOATING_CONAN_REQUIREMENT not in ci and "command -v conan" not in ci,
        "ci does not accept a global Conan executable or a floating Conan version range",
    )
    add(
        checks,
        "workflow:ci:cache-key-pinned-conan-version",
        "conan-2.8.1" in ci,
        "ci cache keys include the fixed Conan version",
    )
    add(
        checks,
        "workflow:ci:no-broad-conan-cache-restore",
        "restore-keys:" not in named_workflow_step(ci, "Restore Conan cache"),
        "ci never restores a Conan cache from a less-specific version or dependency graph key",
    )

    preprod = read(".github/workflows/preprod-evidence.yml")
    add(
        checks,
        "workflow:preprod-evidence:offline-conan",
        'scripts/bootstrap_conan.py --conan-home "$CONAN_HOME" --no-remote' in preprod
        and "--build=never" in preprod,
        "preprod evidence uses the resolved persistent cache without remote access or package builds",
    )
    add(
        checks,
        "workflow:preprod-evidence:isolated-pinned-conan",
        all(
            token in preprod
            for token in (
                "scripts/tools/ensure_conan_venv.py",
                "--conan-version 2.8.1",
                "--offline",
                '--github-path "$GITHUB_PATH"',
            )
        ),
        "preprod evidence requires the pinned isolated Conan virtual environment without network installation",
    )
    add(
        checks,
        "workflow:preprod-evidence:bounded-build-parallelism",
        'build_parallelism:' in preprod
        and 'default: "2"' in preprod
        and "--parallel ${{ inputs.build_parallelism || '2' }}" in preprod,
        "preprod evidence caps C++ build concurrency for memory-constrained fixed runners",
    )

    macos = read(FIXED_RUNNER_CONAN_WORKFLOWS["macos_arm64"])
    add(
        checks,
        "workflow:macos-arm64:persistent-tool-cache",
        'cache_root="$RUNNER_TOOL_CACHE/boost-gateway"' in macos
        and "BOOST_GATEWAY_RUNNER_CACHE_ROOT" in macos
        and "BOOST_GATEWAY_CONAN_VENV" in macos
        and 'CONAN_HOME: ${{ github.workspace }}' not in macos,
        "macOS runner keeps Conan tools and packages outside the checkout workspace",
    )
    add(
        checks,
        "workflow:macos-arm64:strict-offline-cache",
        MACOS_PROFILE in macos
        and MACOS_LOCKFILE in macos
        and "scripts/tools/resolve_runner_cache.py" in macos
        and "scripts/tools/verify_conan_offline_install.py" in macos
        and "--offline" in macos
        and "--no-remote" in macos
        and "--allow-public" not in macos
        and "--build=missing" not in macos,
        "macOS candidate only consumes its admitted persistent Conan namespace",
    )

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "conan_lockfile_workflows" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {"summary_path": str(summary_path)},
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(f"conan lockfile workflows: {'PASS' if summary['passed'] else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)} checks)")
    print(f"summary: {summary_path}")
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
