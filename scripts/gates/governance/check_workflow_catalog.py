#!/usr/bin/env python3
"""Validate the workflow catalog, runner matrix, and documented workflow inventory."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS_ROOT = ROOT / ".github" / "workflows"
EXPECTED_NAMES = {
    "ci": "Mainline / Build, Test & Governance",
    "conan-validate": "Dependencies / Conan Graph Validation",
    "debug-symbols": "Release / Linux Debug Symbols Candidate",
    "grpc-experimental": "Experimental / gRPC",
    "jwks-rotation": "Security / JWKS Rotation Drill",
    "long-soak-capacity": "Stability / Fixed-Runner Soak & Capacity",
    "macos-arm64": "Platform / macOS ARM64 Production Candidate",
    "nightly-stability": "Stability / Bounded Soak",
    "perf-regression": "Performance / Baseline & Regression",
    "preprod-evidence": "Production / Preproduction Evidence",
    "production-candidate-evidence": "Production / Candidate Evidence",
    "production-gates": "Production / Gate Diagnostics",
    "production-readiness": "Production / Readiness Decision",
    "release": "Release / Package & Publish",
    "release-asset-verification": "Release / Published Asset Verification",
    "sdk-distribution": "SDK / Wheel & NuGet Candidate",
    "specialized-e2e": "Infrastructure / Redis, Raft & Operator E2E",
}
TAG_WORKFLOWS = {"release"}
STRICT_OFFLINE_CONAN_WORKFLOWS = {
    "grpc-experimental",
    "jwks-rotation",
    "long-soak-capacity",
    "nightly-stability",
    "perf-regression",
    "preprod-evidence",
    "production-candidate-evidence",
    "production-gates",
    "release",
    "specialized-e2e",
}


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def workflow_name(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def workflow_dispatch_inputs(text: str) -> list[str]:
    """Return top-level workflow_dispatch input names without a YAML dependency."""
    in_dispatch = False
    in_inputs = False
    names: list[str] = []
    for line in text.splitlines():
        if line == "  workflow_dispatch:":
            in_dispatch = True
            continue
        if not in_dispatch:
            continue
        if line and not line.startswith("    "):
            break
        if line == "    inputs:":
            in_inputs = True
            continue
        if not in_inputs:
            continue
        match = re.fullmatch(r"      ([A-Za-z_][A-Za-z0-9_-]*):", line)
        if match:
            names.append(match.group(1))
        elif line and not line.startswith("      "):
            break
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/workflow-catalog-summary.json",
    )
    args = parser.parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path

    checks: list[dict[str, Any]] = []
    workflow_paths = sorted(WORKFLOWS_ROOT.glob("*.yml"))
    actual = {path.stem for path in workflow_paths}
    expected = set(EXPECTED_NAMES)

    add(checks, "workflow-count", len(actual) == len(EXPECTED_NAMES), f"actual={len(actual)}")
    add(checks, "workflow-set:expected", actual == expected, f"actual={sorted(actual)} expected={sorted(expected)}")

    matrix_path = ROOT / ".github" / "runner-matrix.json"
    matrix = json.loads(read(matrix_path)) if matrix_path.exists() else {}
    matrix_workflows = set(matrix.get("workflows", {}))
    add(checks, "runner-matrix:exact-workflow-set", matrix_workflows == actual, f"matrix={sorted(matrix_workflows)} actual={sorted(actual)}")

    boundary_path = ROOT / "docs" / "platform-production-boundaries.json"
    boundary = json.loads(read(boundary_path)) if boundary_path.exists() else {}
    boundary_workflows = set(boundary.get("workflows", {}))
    production_platforms = boundary.get("policy", {}).get("production_platforms", [])
    add(
        checks,
        "platform-boundary:production-platforms",
        production_platforms == ["linux-x64", "linux-arm64", "macos-arm64"],
        f"production_platforms={production_platforms}",
    )
    add(
        checks,
        "platform-boundary:exact-workflow-set",
        boundary_workflows == actual,
        f"boundary={sorted(boundary_workflows)} actual={sorted(actual)}",
    )
    for stem in (
        "release",
        "release-asset-verification",
        "perf-regression",
        "production-gates",
        "production-candidate-evidence",
        "long-soak-capacity",
        "preprod-evidence",
    ):
        platform_runners = matrix.get("workflows", {}).get(stem, {}).get("platforms", {})
        add(
            checks,
            f"runner-matrix:{stem}:production-platforms",
            set(platform_runners) == set(production_platforms),
            f"{stem} platform runners={sorted(platform_runners)}",
        )
    readiness_targets = (
        matrix.get("workflows", {}).get("production-readiness", {}).get("target_platforms", [])
    )
    add(
        checks,
        "runner-matrix:production-readiness:target-platforms",
        readiness_targets == production_platforms,
        f"readiness target platforms={readiness_targets}",
    )
    for stem, states in sorted(boundary.get("workflows", {}).items()):
        add(
            checks,
            f"platform-boundary:{stem}:complete-platform-set",
            isinstance(states, dict) and set(states) == set(production_platforms),
            f"{stem} platforms={sorted(states) if isinstance(states, dict) else states}",
        )

    readme_path = ROOT / ".github" / "CI-CD.md"
    readme = read(readme_path) if readme_path.exists() else ""
    for stem in sorted(actual):
        filename = f"{stem}.yml"
        add(checks, f"readme:lists:{filename}", f"`{filename}`" in readme, f".github/CI-CD.md lists {filename}")

    add(
        checks,
        "readme:root-not-shadowed",
        not (ROOT / ".github" / "README.md").exists(),
        ".github/README.md does not shadow the repository root README on GitHub",
    )

    for path in workflow_paths:
        stem = path.stem
        text = read(path)
        add(checks, f"name:{stem}", workflow_name(text) == EXPECTED_NAMES.get(stem), f"{path.name} name={workflow_name(text)!r}")
        add(checks, f"trigger:{stem}:dispatch", "workflow_dispatch:" in text, f"{path.name} supports workflow_dispatch")
        dispatch_inputs = workflow_dispatch_inputs(text)
        add(
            checks,
            f"trigger:{stem}:dispatch-input-limit",
            len(dispatch_inputs) <= 25,
            f"{path.name} declares {len(dispatch_inputs)}/25 workflow_dispatch inputs",
        )
        has_tag_push = "push:" in text and "tags:" in text and "v*" in text
        add(checks, f"trigger:{stem}:tag-policy", has_tag_push == (stem in TAG_WORKFLOWS), f"{path.name} tag_push={has_tag_push}")
        add(checks, f"trigger:{stem}:no-schedule", "schedule:" not in text and "cron:" not in text, f"{path.name} has no scheduled trigger")
        add(checks, f"trigger:{stem}:no-pr", "pull_request:" not in text, f"{path.name} has no pull_request trigger")
        if "uses: actions/setup-go@" in text:
            add(
                checks,
                f"go:{stem}:cache-dependency-path",
                "cache-dependency-path: operator/boostgateway-operator/go.sum" in text,
                f"{path.name} keys setup-go cache from the operator go.sum file",
            )
        if stem in STRICT_OFFLINE_CONAN_WORKFLOWS:
            add(
                checks,
                f"conan:{stem}:no-public-remote",
                "--allow-public" not in text,
                f"{path.name} does not enable a public Conan remote",
            )
            add(
                checks,
                f"conan:{stem}:no-build-missing",
                "--build=missing" not in text,
                f"{path.name} does not build missing Conan dependencies",
            )
            if "uses: ./.github/actions/setup-cpp-conan" in text:
                offline_action = 'bootstrap-args: "--no-remote"' in text and 'conan-venv-offline: "true"' in text
                add(
                    checks,
                    f"conan:{stem}:offline-composite-action",
                    offline_action,
                    f"{path.name} configures setup-cpp-conan for runner-local offline use",
                )

    release_workflow = read(WORKFLOWS_ROOT / "release.yml")
    ci_workflow = read(WORKFLOWS_ROOT / "ci.yml")
    release_asset_verification = read(WORKFLOWS_ROOT / "release-asset-verification.yml")
    specialized_workflow = read(WORKFLOWS_ROOT / "specialized-e2e.yml")
    candidate_workflow = read(WORKFLOWS_ROOT / "production-candidate-evidence.yml")
    long_soak_workflow = read(WORKFLOWS_ROOT / "long-soak-capacity.yml")
    jwks_workflow = read(WORKFLOWS_ROOT / "jwks-rotation.yml")
    macos_workflow = read(WORKFLOWS_ROOT / "macos-arm64.yml")
    sdk_distribution_workflow = read(WORKFLOWS_ROOT / "sdk-distribution.yml")
    perf_workflow = read(WORKFLOWS_ROOT / "perf-regression.yml")
    production_gates_workflow = read(WORKFLOWS_ROOT / "production-gates.yml")
    production_readiness_workflow = read(WORKFLOWS_ROOT / "production-readiness.yml")
    preprod_workflow = read(WORKFLOWS_ROOT / "preprod-evidence.yml")
    production_platform_action = read(ROOT / ".github" / "actions" / "resolve-production-platform" / "action.yml")
    add(
        checks,
        "ci:next-minor-decision-gate",
        "- name: Next minor decision gate" in ci_workflow
        and "python3 scripts/gates/governance/check_next_minor_decisions.py" in ci_workflow,
        "mainline CI enforces the accepted next-minor decision manifest",
    )
    add(
        checks,
        "long-soak-capacity:leaderboard-redis-comparison-input-forwarding",
        "leaderboard_redis_comparison:" in long_soak_workflow
        and "if [ \"${{ inputs.leaderboard_redis_comparison }}\" = \"true\" ]" in long_soak_workflow
        and "--leaderboard-redis-comparison" in long_soak_workflow
        and "--leaderboard-redis-host" in long_soak_workflow
        and "--leaderboard-redis-port" in long_soak_workflow
        and "--require-leaderboard-redis-comparison" in long_soak_workflow
        and "if: always() && inputs.run_capacity && inputs.run_business_capacity" in long_soak_workflow,
        "long-soak capacity explicitly provisions and forwards the Redis persistence comparison to collection and R4",
    )
    add(
        checks,
        "long-soak-capacity:leaderboard-redis-image-provenance",
        "docker run -d --pull never" in long_soak_workflow
        and "docker image inspect redis:7-alpine" in long_soak_workflow
        and "runtime/validation/leaderboard-redis-image.json" in long_soak_workflow,
        "leaderboard comparison consumes the prewarmed Redis image offline and archives its image identity",
    )
    add(
        checks,
        "long-soak-capacity:independent-saturation-evidence",
        "saturation_plan:" in long_soak_workflow
        and 'saturation_plan="${{ inputs.saturation_plan }}"' in long_soak_workflow
        and 'if [ -n "$saturation_plan" ]; then' in long_soak_workflow
        and 'if [ "$saturation_plan" != "default" ]; then' in long_soak_workflow
        and "--run-saturation" in long_soak_workflow
        and "--saturation-case" in long_soak_workflow
        and "run_saturation:" not in long_soak_workflow
        and "saturation_cases:" not in long_soak_workflow
        and "saturation_cpu_threshold_percent:" not in long_soak_workflow
        and "saturation_loadgen_headroom_percent:" not in long_soak_workflow
        and "--saturation-cpu-threshold-percent" not in long_soak_workflow
        and "--saturation-loadgen-headroom-percent" not in long_soak_workflow
        and "runtime/validation/saturation-baseline-summary.json" in long_soak_workflow
        and "runtime/perf/fixed-runner-saturation/**" in long_soak_workflow
        and "if: always() && inputs.run_capacity && inputs.run_business_capacity"
        in long_soak_workflow,
        "long-soak capacity runs and archives saturation independently without widening the R4 condition",
    )
    pid_marker = 'pid_marker="runtime/validation/long-soak-capacity.pid"'
    background_launch = 'python3 "${args[@]}" &'
    pid_capture = "long_soak_pid=$!"
    atomic_pid_publish = 'mv -f "$pid_marker_tmp" "$pid_marker"'
    process_wait = 'wait "$long_soak_pid" || long_soak_status=$?'
    pid_cleanup = 'rm -f "$pid_marker"'
    add(
        checks,
        "long-soak-capacity:cancellation-pid-bridge",
        long_soak_workflow.count(pid_marker) == 2
        and background_launch in long_soak_workflow
        and pid_capture in long_soak_workflow
        and 'mktemp "${pid_marker}.tmp.XXXXXX"' in long_soak_workflow
        and atomic_pid_publish in long_soak_workflow
        and process_wait in long_soak_workflow
        and 'exit "$long_soak_status"' in long_soak_workflow
        and long_soak_workflow.index(background_launch)
        < long_soak_workflow.index(pid_capture)
        < long_soak_workflow.index(atomic_pid_publish)
        < long_soak_workflow.index(process_wait)
        < long_soak_workflow.rindex(pid_cleanup),
        "long-soak capacity publishes its background orchestrator PID atomically and preserves wait status",
    )
    render_step = "- name: Render long-soak capacity summary"
    render_pid_check = 'if [ -f "$pid_marker" ]; then'
    render_pid_validation = '[[ "$long_soak_pid" =~ ^[1-9][0-9]*$ ]]'
    render_process_wait = 'while kill -0 "$long_soak_pid" 2>/dev/null; do'
    render_timeout = "wait_deadline=$((SECONDS + 20))"
    render_summaries = "summaries=()"
    add(
        checks,
        "long-soak-capacity:render-waits-for-cancelled-process",
        render_step in long_soak_workflow
        and render_pid_check in long_soak_workflow
        and render_pid_validation in long_soak_workflow
        and render_process_wait in long_soak_workflow
        and render_timeout in long_soak_workflow
        and "still running after 20 seconds" in long_soak_workflow
        and long_soak_workflow.index(render_step)
        < long_soak_workflow.index(render_pid_check)
        < long_soak_workflow.index(render_process_wait)
        < long_soak_workflow.index(render_summaries),
        "always-render waits up to 20 seconds for a valid recorded PID before reading fail-closed evidence",
    )
    redis_cleanup_step = "- name: Cleanup long-soak Redis"
    render_summary_step = "- name: Render long-soak capacity summary"
    upload_evidence_step = "- name: Upload long-soak capacity evidence"
    add(
        checks,
        "long-soak-capacity:always-cleans-redis",
        redis_cleanup_step in long_soak_workflow
        and "if: always() && inputs.leaderboard_redis_comparison"
        in long_soak_workflow
        and 'docker rm -f "boost-capacity-redis-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"'
        in long_soak_workflow
        and long_soak_workflow.index(redis_cleanup_step)
        < long_soak_workflow.index(render_summary_step)
        < long_soak_workflow.index(upload_evidence_step),
        "an independent always step removes the deterministic Redis container before evidence rendering and upload",
    )
    add(
        checks,
        "specialized-e2e:pinned-kind-bootstrap",
        "scripts/tools/bootstrap_kind_tools.py" in specialized_workflow
        and "if: inputs.include_operator_kind" in specialized_workflow
        and '--github-path "$GITHUB_PATH"' in specialized_workflow,
        "Operator kind E2E installs checksum-pinned kind and kubectl before fixed-runner preflight",
    )
    add(
        checks,
        "jwks-rotation:real-https-fixed-runner-evidence",
        "scripts/verify_jwks_rotation.py" in jwks_workflow
        and "jwks_rotation_probe" in jwks_workflow
        and "runtime/validation/jwks-rotation-summary.json" in jwks_workflow
        and "runtime/validation/jwks-conan-offline-summary.json" in jwks_workflow
        and 'bootstrap-args: "--no-remote"' in jwks_workflow
        and 'conan-venv-offline: "true"' in jwks_workflow
        and "- name: Validate JWKS evidence summary contract\n        if: always()"
        in jwks_workflow,
        "JWKS workflow builds the real HTTPS probe and archives same-SHA strict-offline evidence",
    )
    add(
        checks,
        "jwks-rotation:native-production-platform-routing",
        "platform:" in jwks_workflow
        and "linux-x64" in jwks_workflow
        and "linux-arm64" in jwks_workflow
        and "macos-arm64" in jwks_workflow
        and "conan/profiles/linux-gcc-x64" in jwks_workflow
        and "conan/profiles/linux-gcc-arm64" in jwks_workflow
        and "conan/profiles/macos-apple-clang-arm64" in jwks_workflow
        and "conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock" in jwks_workflow
        and "conan/locks/linux-gcc-arm64-release-nogrpc-nosqlite.lock" in jwks_workflow
        and "conan/locks/macos-apple-clang-arm64-release-nogrpc-nosqlite.lock" in jwks_workflow
        and "Setup native macOS Conan toolchain" in jwks_workflow
        and "Setup native Linux ARM64 Conan toolchain" in jwks_workflow
        and 'cache_root="$RUNNER_TOOL_CACHE/boost-gateway"' in jwks_workflow
        and "-DCMAKE_OSX_ARCHITECTURES=arm64" in jwks_workflow,
        "JWKS routes strict-offline builds and native host checks independently for all production platforms",
    )
    add(
        checks,
        "sdk-distribution:three-platform-native-assets",
        all(
            token in sdk_distribution_workflow
            for token in (
                "linux-x64",
                "linux-arm64",
                "macos-arm64",
                "manylinux_2_35_x86_64",
                "manylinux_2_39_aarch64",
                "macosx_26_0_arm64",
                "--rid \"$SDK_RID\"",
                "sdk-package-py3.12",
                "setuptools.__version__ == \"83.0.0\"",
                "wheel.__version__ == \"0.47.0\"",
                "auditwheel --version | grep -Eq '^auditwheel 6\\.7\\.0([[:space:]]|$)'",
                "boost_gateway_sdk_dll sdk_tests",
                "v2_gateway_demo v2_login_backend v2_room_backend v2_battle_backend",
                "sdk-distribution-${{ inputs.platform }}-${{ github.sha }}",
            )
        ),
        "SDK workflow emits platform-labelled wheel/NuGet evidence for Linux x64, Linux ARM64 and macOS ARM64",
    )
    add(
        checks,
        "macos-arm64:bounded-native-platform-evidence",
        "run_platform_baseline:" in macos_workflow
        and "scripts/producers/collect_v2_perf_baseline.py" in macos_workflow
        and "scripts/gates/release/verify_stability_soak.py" in macos_workflow
        and "runtime/perf/macos-arm64" in macos_workflow
        and "runtime/validation/macos-arm64-stability-summary.json" in macos_workflow
        and "--baseline-profile release" in macos_workflow
        and "--skip-build" in macos_workflow,
        "macOS candidate can produce bounded native performance and stability evidence from its Mach-O build",
    )
    add(
        checks,
        "macos-arm64:native-dsym-pair",
        "run_debug_symbols:" in macos_workflow
        and "scripts/tools/create_macos_dsym_package.py" in macos_workflow
        and "scripts/tools/verify_macos_dsym_package.py" in macos_workflow
        and "-DCMAKE_CXX_FLAGS_RELEASE='-O2 -g -DNDEBUG'" in macos_workflow
        and "runtime/macos-arm64-dsym-assets" in macos_workflow
        and "runtime/validation/macos-dsym-verify-summary.json" in macos_workflow,
        "macOS candidate creates and independently verifies an exact UUID-bound dSYM/runtime pair",
    )
    debug_symbols_workflow = read(WORKFLOWS_ROOT / "debug-symbols.yml")
    add(
        checks,
        "debug-symbols:release-conan-debug-flags",
        "-DCMAKE_BUILD_TYPE=Release" in debug_symbols_workflow
        and "-DCMAKE_CXX_FLAGS_RELEASE='-O2 -g -DNDEBUG'" in debug_symbols_workflow
        and "-DCMAKE_BUILD_TYPE=RelWithDebInfo" not in debug_symbols_workflow,
        "Linux symbols keep the admitted Release Conan graph while compiling project DWARF with release-compatible flags",
    )
    add(
        checks,
        "debug-symbols:checksums-only-published-assets",
        "sha256sum *.tar.gz *.spdx.json > SHA256SUMS-debug-symbols.txt" in debug_symbols_workflow,
        "Linux symbol checksums exclude the materialized packaging work directories",
    )
    add(
        checks,
        "debug-symbols:native-linux-architecture-routing",
        "linux-x64" in debug_symbols_workflow
        and "linux-arm64" in debug_symbols_workflow
        and "conan/profiles/linux-gcc-arm64" in debug_symbols_workflow
        and "--expected-platform" in debug_symbols_workflow
        and "linux-debug-symbols-${{ inputs.platform }}-${{ github.sha }}" in debug_symbols_workflow,
        "Linux debug-symbol workflow binds profile, archive and artifact identity to the native architecture",
    )
    add(
        checks,
        "debug-symbols:uploads-only-published-assets",
        "runtime/debug-symbol-assets/*.tar.gz" in debug_symbols_workflow
        and "runtime/debug-symbol-assets/*.spdx.json" in debug_symbols_workflow
        and "runtime/debug-symbol-assets/SHA256SUMS-debug-symbols.txt" in debug_symbols_workflow
        and "runtime/debug-symbol-assets/*\n" not in debug_symbols_workflow,
        "Linux symbol artifacts exclude unchecksummed materialized packaging work directories",
    )
    add(
        checks,
        "debug-symbols:tolerates-slow-fixed-runner-upload",
        'ACTIONS_ARTIFACT_UPLOAD_CONCURRENCY: "3"' in debug_symbols_workflow
        and 'ACTIONS_ARTIFACT_UPLOAD_TIMEOUT_MS: "1200000"' in debug_symbols_workflow,
        "large pre-compressed symbol assets use bounded upload concurrency and a 20-minute chunk timeout",
    )
    add(
        checks,
        "specialized-e2e:raft-phase-b-evidence",
        "scripts/tools/verify_conan_offline_install.py" in specialized_workflow
        and "runtime/validation/raft-conan-offline-summary.json" in specialized_workflow
        and "scripts/gates/production/verify_data_recovery_gate.py" in specialized_workflow
        and "runtime/validation/raft-data-recovery-summary.json" in specialized_workflow,
        "specialized E2E archives strict offline Conan and Raft recovery evidence",
    )
    add(
        checks,
        "candidate-evidence:pinned-kind-bootstrap",
        "scripts/tools/bootstrap_kind_tools.py" in candidate_workflow
        and "if: inputs.include_kind" in candidate_workflow
        and '--github-path "$GITHUB_PATH"' in candidate_workflow,
        "enhanced R0 installs checksum-pinned kind and kubectl when kind evidence is required",
    )
    add(
        checks,
        "release:isolated-asset-directory",
        'release_asset_dir="$RUNNER_TEMP/boost-gateway-release-${{ github.run_id }}"' in release_workflow
        and '>> "$GITHUB_ENV"' in release_workflow,
        "release publish job uses a run-local asset directory instead of a persistent workspace path",
    )
    add(
        checks,
        "release:checksum-only-published-archive",
        "find \"$RELEASE_ASSET_DIR\" -type f -name '*.tar.gz'" in release_workflow
        and "test \"${#archives[@]}\" -eq 6" in release_workflow
        and "for platform in linux-x64 linux-arm64 macos-arm64" in release_workflow,
        "release checksums one runtime and one symbol tarball for every production platform",
    )
    add(
        checks,
        "release:checksum-portable-basename",
        '"$(basename "$asset")"' in release_workflow,
        "release checksum records the downloadable asset basename",
    )
    add(
        checks,
        "release:archive-without-dist-prefix",
        "scripts/tools/create_debug_symbol_package.py" in release_workflow
        and "scripts/tools/create_macos_dsym_package.py" in release_workflow
        and "--standard-runtime-name" in release_workflow,
        "release creates stripped archives rooted at the version directory while retaining exact symbols",
    )
    add(
        checks,
        "release:archive-layout-gate",
        "scripts/tools/verify_release_archive.py" in release_workflow
        and "--expected-root" in release_workflow,
        "release workflow validates archive layout and required metadata",
    )
    add(
        checks,
        "release:clean-ubuntu-package-consumer",
        "scripts/tools/verify_release_package_consumer.py" in release_workflow
        and "--image ubuntu:24.04" in release_workflow,
        "release workflow consumes the installed archive in the pinned clean Ubuntu environment",
    )
    add(
        checks,
        "release:clean-cmake-consumer",
        "scripts/tools/verify_release_cmake_consumer.py" in release_workflow
        and "boost-gateway/release-cmake-consumer:ubuntu24.04-gcc13" in release_workflow
        and "runtime/validation/release-cmake-consumer-summary.json" in release_workflow,
        "release builds and runs a downstream CMake consumer in the admitted compiler image",
    )
    add(
        checks,
        "release:clean-cmake-consumer-maintenance",
        "prepare_cmake_consumer_image:" in release_workflow
        and "github.event_name == 'workflow_dispatch' && inputs.prepare_cmake_consumer_image"
        in release_workflow
        and "docker build --pull=false" in release_workflow
        and "env/docker/release-cmake-consumer.Dockerfile" in release_workflow,
        "manual release dispatch can restore the pinned compiler image without enabling tag-time preparation",
    )
    add(
        checks,
        "release:legacy-raft-linux-x64-maintenance",
        "prepare_legacy_raft_linux_x64_binary:" in release_workflow
        and "github.event_name == 'workflow_dispatch' && inputs.prepare_legacy_raft_linux_x64_binary"
        in release_workflow
        and "boost-gateway-v3.5.3-linux-x64.tar.gz" in release_workflow
        and "4ad6945b08b4f7bfceac7e0b8e41a1d61c9ae12c0075d85e4268b209cc4ab4d8"
        in release_workflow
        and "sha256sum --check --strict" in release_workflow
        and "test \"$PRODUCTION_PLATFORM\" = linux-x64" in release_workflow,
        "manual release dispatch can restore the immutable verified v3.5.3 x64 binary without changing tag behavior",
    )
    add(
        checks,
        "release:legacy-raft-linux-x64-writable-fallback",
        'destination="$PRODUCTION_CACHE_ROOT/tools/releases/v3.5.3/bin/v2_leaderboard_backend"'
        in release_workflow
        and '[ ! -f "$default_legacy_binary" ]' in release_workflow
        and 'default_legacy_binary="$PRODUCTION_CACHE_ROOT/tools/releases/v3.5.3/bin/v2_leaderboard_backend"'
        in release_workflow,
        "Linux x64 legacy asset recovery uses the admitted writable tools namespace and remains reusable",
    )
    add(
        checks,
        "release:sbom-and-attestations",
        "uses: anchore/sbom-action@v0" in release_workflow
        and "scripts/tools/harden_release_sbom.py enrich" in release_workflow
        and release_workflow.index("scripts/tools/harden_release_sbom.py enrich")
        < release_workflow.index("Attest release archive SBOM")
        and release_workflow.count("uses: actions/attest@v4") >= 8
        and "Attest symbol archive SBOM" in release_workflow
        and "Attest wheel SBOM" in release_workflow
        and "Attest NuGet SBOM" in release_workflow
        and "sbom-path:" in release_workflow
        and "attestations: write" in release_workflow
        and "id-token: write" in release_workflow,
        "tag release publishes SPDX SBOM plus build-provenance and SBOM attestations",
    )
    add(
        checks,
        "release:wheel-sbom-exact-attestation-path",
        "Resolve wheel attestation inputs" in release_workflow
        and 'test "${#wheels[@]}" -eq 1' in release_workflow
        and 'echo "SDK_WHEEL_PATH=$wheel"' in release_workflow
        and 'echo "SDK_WHEEL_SBOM_PATH=$wheel_sbom"' in release_workflow
        and "subject-path: ${{ env.SDK_WHEEL_PATH }}" in release_workflow
        and "sbom-path: ${{ env.SDK_WHEEL_SBOM_PATH }}" in release_workflow
        and "sbom-path: runtime/release-sdk-assets/*.whl.spdx.json"
        not in release_workflow,
        "release resolves the single wheel SBOM before passing an exact path to actions/attest",
    )
    raft_release_gate = "scripts/gates/release/verify_raft_release_evidence.py"
    add(
        checks,
        "release:raft-phase-b-same-run-evidence",
        "scripts/tools/verify_conan_offline_install.py" in release_workflow
        and "scripts/gates/e2e/verify_specialized_e2e.py" in release_workflow
        and "--profile raft-ha" in release_workflow
        and "scripts/gates/production/verify_data_recovery_gate.py" in release_workflow
        and raft_release_gate in release_workflow
        and release_workflow.index("scripts/tools/harden_release_sbom.py enrich")
        < release_workflow.index(raft_release_gate)
        < release_workflow.index("Attest release archive provenance")
        and "runtime/validation/raft-release-evidence-summary.json" in release_workflow,
        "release binds Raft mixed-version, recovery, offline Conan, package and SBOM evidence before attestation",
    )
    add(
        checks,
        "release:published-sbom-semantics",
        "scripts/tools/harden_release_sbom.py verify" in release_asset_verification
        and "published-release-sbom-semantics-summary.json" in release_asset_verification
        and '"sbom_semantics": sbom_semantics' in release_asset_verification,
        "published asset verification rechecks SBOM file digests and Conan dependency semantics",
    )
    binding_command = "scripts/tools/harden_release_sbom.py verify-attestation"
    binding_summary = "published-${label}-sbom-attestation-binding-summary.json"
    add(
        checks,
        "release:published-sbom-attestation-binding",
        binding_command in release_asset_verification
        and binding_summary in release_asset_verification
        and "published-${label}-sbom-verification.json" in release_asset_verification
        and "Render published asset summary" in release_asset_verification
        and release_asset_verification.index("Verify provenance and SBOM attestations")
        < release_asset_verification.index(binding_command)
        < release_asset_verification.index("Render published asset summary")
        and 'all(item.get("overall_pass") is True for item in bindings.values())'
        in release_asset_verification
        and 'item.get("predicate_matches_published_sbom") is True for item in bindings.values()'
        in release_asset_verification
        and '"predicate_matches_published_sbom": predicate_matches_published_sbom'
        in release_asset_verification
        and '"sbom_attestation_bindings": bindings'
        in release_asset_verification
        and "runtime/validation/published-release-*.json" in release_asset_verification,
        "published asset verification binds the standalone SBOM to its verified SPDX predicate",
    )
    add(
        checks,
        "release:sbom-published-and-checksummed",
        "*.spdx.json" in release_workflow
        and '"${nugets[@]}" "${sboms[@]}" "${provenance[@]}"' in release_workflow
        and "test \"${#sboms[@]}\" -eq 10" in release_workflow,
        "release publishes and checksums runtime, symbol, wheel and NuGet SPDX SBOMs",
    )
    add(
        checks,
        "release-asset-verification:macos-gh-archive-layout",
        'executable="gh_${gh_version}_macOS_arm64/bin/gh"'
        in release_asset_verification,
        "published asset verification resolves the versioned macOS GitHub CLI archive layout",
    )
    production_platform_workflows = {
        "release": release_workflow,
        "production-candidate-evidence": candidate_workflow,
        "perf-regression": perf_workflow,
        "long-soak-capacity": long_soak_workflow,
        "production-gates": production_gates_workflow,
        "production-readiness": production_readiness_workflow,
        "release-asset-verification": release_asset_verification,
    }
    for stem, workflow in production_platform_workflows.items():
        add(
            checks,
            f"production-platform:{stem}:explicit-platform-input",
            all(token in workflow for token in ("platform:", "linux-x64", "linux-arm64", "macos-arm64")),
            f"{stem} exposes all production platforms explicitly",
        )
    for stem in (
        "release",
        "production-candidate-evidence",
        "perf-regression",
        "long-soak-capacity",
        "production-gates",
        "release-asset-verification",
    ):
        add(
            checks,
            f"production-platform:{stem}:native-resolution",
            "uses: ./.github/actions/resolve-production-platform"
            in production_platform_workflows[stem],
            f"{stem} validates the native host through the shared platform contract",
        )
        add(
            checks,
            f"production-platform:{stem}:native-runner-defaults",
            all(
                token in production_platform_workflows[stem]
                for token in (
                    "node-aoi-omen-gaming-laptop-16-am0xxx",
                    "node-honeybury-m4-linux-arm64",
                    "macos-arm64-candidate",
                )
            ),
            f"{stem} selects a native runner when no explicit runner override is supplied",
        )
    add(
        checks,
        "production-platform:shared-contract",
        all(
            token in production_platform_action
            for token in (
                "conan/profiles/linux-gcc-x64",
                "conan/profiles/linux-gcc-arm64",
                "conan/profiles/macos-apple-clang-arm64",
                "linux/amd64",
                "linux/arm64",
                "PRODUCTION_ARCHIVE_PLATFORM",
                "production-platform-summary.json",
            )
        ),
        "shared production platform resolution binds host, Conan graph, Docker target and artifact identity",
    )
    add(
        checks,
        "production-platform:system-python-compatibility",
        "from datetime import datetime, timezone" in production_platform_action
        and "datetime.now(timezone.utc)" in production_platform_action
        and "from datetime import UTC" not in production_platform_action,
        "shared platform resolution remains compatible with runner system Python before managed Python setup",
    )
    add(
        checks,
        "production-platform:readiness-artifact-isolation",
        all(
            token in production_readiness_workflow
            for token in (
                "production-candidate-evidence-${PRODUCTION_PLATFORM}",
                "long-soak-capacity-${PRODUCTION_PLATFORM}",
                'data.get("platform") != expected',
                "readiness-platform-evidence-summary.json",
                "production-readiness-${{ inputs.platform }}",
            )
        ),
        "readiness imports and validates only the selected platform evidence set",
    )
    add(
        checks,
        "production-platform:preprod-native-linux-routing",
        all(
            token in preprod_workflow
            for token in (
                "linux-x64",
                "linux-arm64",
                "uses: ./.github/actions/resolve-production-platform",
                'DOCKER_DEFAULT_PLATFORM="$PRODUCTION_DOCKER_PLATFORM"',
                "preprod-evidence-${{ inputs.platform }}-${{ github.run_id }}",
                "production-platform-summary.json",
            )
        ),
        "preprod R5/R6 binds the native Linux runner, Conan graph, OCI platform and artifact identity",
    )
    add(
        checks,
        "production-platform:macos-native-r5-r6-readiness",
        "uses: ./.github/actions/resolve-production-platform" in macos_workflow
        and "runtime/validation/preprod-recovery-drill-summary.json" in macos_workflow
        and "scripts/verify_tls_preprod_multi_run.py" in macos_workflow
        and "runtime/validation/tls-preprod-multi-run-summary.json" in macos_workflow
        and "production-platform-summary.json" in macos_workflow,
        "macOS candidate publishes native R5/R6 aliases and platform identity for readiness",
    )
    add(
        checks,
        "release:version-from-project-metadata",
        "scripts/tools/resolve_release_version.py" in release_workflow
        and '--github-ref "$GITHUB_REF"' in release_workflow
        and '--github-ref-name "$GITHUB_REF_NAME"' in release_workflow
        and '--github-env "$GITHUB_ENV"' in release_workflow,
        "release names derive from CMake project version and tag pushes must match it",
    )
    add(
        checks,
        "release:changelog-notes-rendered",
        "scripts/tools/render_release_notes.py" in release_workflow
        and '--version "$RELEASE_VERSION"' in release_workflow,
        "tag releases render their matching CHANGELOG section",
    )
    add(
        checks,
        "release:changelog-notes-published",
        "body_path: ${{ env.RELEASE_ASSET_DIR }}/RELEASE_NOTES.md" in release_workflow
        and "generate_release_notes: true" not in release_workflow,
        "GitHub Release body uses deterministic CHANGELOG notes",
    )

    retired = ("perf-commit-check.yml", "production-resilience.yml", "production-evidence.yml")
    for filename in retired:
        add(checks, f"retired:{filename}", not (WORKFLOWS_ROOT / filename).exists(), f"{filename} is retired")

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "workflow_catalog" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {"summary_path": str(summary_path)},
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(f"workflow catalog: {'PASS' if not failed else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)} checks)")
    print(f"summary: {summary_path}")
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
