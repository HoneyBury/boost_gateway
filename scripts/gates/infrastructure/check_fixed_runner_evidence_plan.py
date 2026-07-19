#!/usr/bin/env python3
"""Validate fixed-runner workflow evidence wiring, runner policy, and required summary paths."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
LINUX_LOCKFILE = "conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock"
LINUX_GRPC_LOCKFILE = "conan/locks/linux-gcc-x64-release-grpc-nosqlite.lock"
LINUX_PROFILE = "conan/profiles/linux-gcc-x64"
SELF_HOSTED_LINUX_RUNNER = '["self-hosted","Linux","X64"]'
PREPROD_R5_RUNNER = '["self-hosted","Linux","X64","preprod-r5"]'
GITHUB_HOSTED_UBUNTU_RUNNER = '"ubuntu-latest"'
CONAN_VENV_HELPER = "scripts/tools/ensure_conan_venv.py"
PINNED_CONAN_VERSION = "2.8.1"
FLOATING_CONAN_REQUIREMENT = "conan>=2.0,<2.9"

WORKFLOW_REQUIREMENTS = {
    "conan_validate": {
        "path": ".github/workflows/conan-validate.yml",
        "tokens": (
            LINUX_LOCKFILE,
            LINUX_PROFILE,
            "conan install",
            "--lockfile",
            "BOOST_DEPENDENCY_PROVIDER=conan",
            "--target \"$target\"",
            "actions/upload-artifact@v4",
        ),
        "summaries": (),
    },
    "release": {
        "path": ".github/workflows/release.yml",
        "tokens": (
            LINUX_LOCKFILE,
            LINUX_PROFILE,
            "conan install",
            "--lockfile",
            "conan-preflight",
        ),
        "summaries": ("runtime/validation/release-baseline-summary.json",),
    },
    "grpc_experimental": {
        "path": ".github/workflows/grpc-experimental.yml",
        "tokens": (
            LINUX_PROFILE,
            LINUX_GRPC_LOCKFILE,
            "scripts/tools/resolve_runner_cache.py",
            "vars.GRPC_EXPERIMENTAL_RUNNER",
            'with_grpc=True',
            "--build=never",
            "--no-remote",
            "BOOST_BUILD_GRPC=ON",
            "scripts/verify_sdk_package_consumer.py",
            "scripts/check_v3_grpc_poc_decision.py",
            "actions/upload-artifact@v4",
        ),
        "summaries": (
            "runtime/validation/grpc-fixed-runner-preflight-summary.json",
            "runtime/validation/grpc-sdk-package-consumer-summary.json",
            "runtime/validation/grpc-fixed-runner-decision-summary.json",
        ),
    },
    "long_soak_capacity": {
        "path": ".github/workflows/long-soak-capacity.yml",
        "tokens": (
            LINUX_LOCKFILE,
            LINUX_PROFILE,
            "build/conan-long-soak-capacity-cmake",
            "runtime/validation/long-soak-capacity-summary.json",
            "runtime/validation/fixed-runner-release-capacity-summary.json",
            "runtime/perf/fixed-runner-capacity/**",
            "runtime/perf/fixed-runner-business-capacity/**",
            "actions/upload-artifact@v4",
        ),
        "summaries": (
            "runtime/validation/long-soak-capacity-summary.json",
            "runtime/validation/long-soak-2h-summary.json",
            "runtime/validation/fixed-runner-release-capacity-summary.json",
        ),
    },
    "production_gates": {
        "path": ".github/workflows/production-gates.yml",
        "tokens": (
            LINUX_LOCKFILE,
            LINUX_PROFILE,
            "build/conan-production-gates-cmake",
            "runtime/validation/production-evidence-summary.json",
            "runtime/validation/production-resilience-summary.json",
            "actions/upload-artifact@v4",
        ),
        "summaries": (
            "runtime/validation/production-evidence-summary.json",
            "runtime/validation/production-resilience-summary.json",
        ),
    },
    "production_candidate_evidence": {
        "path": ".github/workflows/production-candidate-evidence.yml",
        "tokens": (
            LINUX_LOCKFILE,
            LINUX_PROFILE,
            "build/conan-production-candidate-cmake",
            "cmake --build",
            "scripts/verify_production_candidate_evidence.py",
            "runtime/validation/r0-production-candidate-evidence-summary.json",
            "actions/upload-artifact@v4",
        ),
        "summaries": (
            "runtime/validation/r0-production-candidate-evidence-summary.json",
        ),
    },
    "preprod_evidence": {
        "path": ".github/workflows/preprod-evidence.yml",
        "tokens": (
            LINUX_LOCKFILE,
            LINUX_PROFILE,
            "scripts/verify_preprod_recovery_drill.py",
            "docker_pull_policy",
            "--docker-pull-policy",
            "runtime/validation/r5-docker-image-preflight-summary.json",
            "scripts/verify_tls_preprod_multi_run.py",
            "runtime/validation/preprod-recovery-drill-summary.json",
            "runtime/validation/r5-preprod-recovery-drill-record.json",
            "runtime/validation/monitoring-operability-summary.json",
            "runtime/validation/tls-preprod-multi-run-summary.json",
            "preprod-evidence-${{ github.run_id }}",
            "actions/upload-artifact@v4",
        ),
        "summaries": (
            "runtime/validation/preprod-recovery-drill-summary.json",
            "runtime/validation/tls-preprod-multi-run-summary.json",
        ),
    },
    "production_readiness": {
        "path": ".github/workflows/production-readiness.yml",
        "tokens": (
            "production_candidate_run_id",
            "long_soak_run_id",
            "capacity_run_id",
            "preprod_evidence_run_id",
            "preprod-evidence-$PREPROD_EVIDENCE_RUN_ID",
            "gh run download",
            "--require-fixed-runner",
            "runtime/validation/r2-production-evidence-manifest-summary.json",
            "runtime/validation/r2-production-evidence-manifest-fixed-runner-summary.json",
            "runtime/validation/r3-production-readiness-report-summary.json",
            "actions/upload-artifact@v4",
        ),
        "summaries": (
            "runtime/validation/r2-production-evidence-manifest-fixed-runner-summary.json",
            "runtime/validation/r3-production-readiness-report-summary.json",
        ),
    },
}

DOC_TOKENS = (
    "Ubuntu Fixed-Runner 第一批执行矩阵",
    "不能用本机 smoke 或 `--allow-missing` 结果替代",
    "python3 scripts/check_fixed_runner_evidence_plan.py",
    "Linux `nosqlite` lockfile",
    "overall_pass=true",
    "docs/runner-inventory.md",
    "ensure_conan_venv.py",
    "conan-2.8.1-py3.12",
)

README_TOKENS = (
    "GitHub-hosted `ubuntu-latest`",
    "fixed-runner 证据",
    "runner-matrix.json",
)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def exists(relative: str) -> bool:
    return (ROOT / relative).exists()


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def bootstrap_uses_resolved_home(content: str) -> bool:
    calls = [
        line.strip()
        for line in content.splitlines()
        if "scripts/bootstrap_conan.py" in line and ("python " in line or "args=(" in line)
    ]
    return bool(calls) and all("--conan-home" in call and "CONAN_HOME" in call for call in calls)


def uses_composite_conan_action(content: str) -> bool:
    return "uses: ./.github/actions/setup-cpp-conan" in content


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/fixed-runner-evidence-plan-summary.json",
    )
    args = parser.parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path

    checks: list[dict[str, Any]] = []
    add(checks, "lockfile:linux-nosqlite-exists", exists(LINUX_LOCKFILE), f"{LINUX_LOCKFILE} exists")
    add(checks, "profile:linux-gcc-x64-exists", exists(LINUX_PROFILE), f"{LINUX_PROFILE} exists")

    runner_matrix_path = ".github/runner-matrix.json"
    add(checks, "runner-matrix:exists", exists(runner_matrix_path), f"{runner_matrix_path} exists")
    runner_matrix = json.loads(read(runner_matrix_path)) if exists(runner_matrix_path) else {}
    matrix_workflows = runner_matrix.get("workflows", {})
    add(
        checks,
        "runner-matrix:default-runner:self-hosted-linux",
        runner_matrix.get("default_runner") == SELF_HOSTED_LINUX_RUNNER,
        f"{runner_matrix_path} default_runner stays on {SELF_HOSTED_LINUX_RUNNER} for fixed-runner evidence",
    )
    add(
        checks,
        "runner-matrix:ci:github-hosted-default",
        matrix_workflows.get("ci", {}).get("runner") == GITHUB_HOSTED_UBUNTU_RUNNER,
        f"{runner_matrix_path} pins ci runner fallback to {GITHUB_HOSTED_UBUNTU_RUNNER}",
    )
    add(
        checks,
        "runner-matrix:ci:vars-hint",
        matrix_workflows.get("ci", {}).get("vars_hint") == "CI_RUNNER",
        f"{runner_matrix_path} exposes CI_RUNNER override for ci",
    )
    add(
        checks,
        "runner-matrix:ci:github-hosted-capability",
        matrix_workflows.get("ci", {}).get("capabilities", {}).get("github_hosted_fallback") is True,
        f"{runner_matrix_path} records ci github-hosted fallback capability",
    )
    for workflow_name, expected_var in (
        ("release", "RELEASE_RUNNER"),
        ("conan-validate", "CONAN_VALIDATE_RUNNER"),
        ("grpc-experimental", "GRPC_EXPERIMENTAL_RUNNER"),
        ("specialized-e2e", "SPECIALIZED_E2E_RUNNER"),
    ):
        entry = matrix_workflows.get(workflow_name, {})
        add(
            checks,
            f"runner-matrix:{workflow_name}:self-hosted-default",
            entry.get("runner") == SELF_HOSTED_LINUX_RUNNER,
            f"{runner_matrix_path} keeps {workflow_name} on {SELF_HOSTED_LINUX_RUNNER}",
        )
        add(
            checks,
            f"runner-matrix:{workflow_name}:vars-hint",
            entry.get("vars_hint") == expected_var,
            f"{runner_matrix_path} exposes {expected_var} override for {workflow_name}",
        )

    expected_summaries: list[str] = []
    composite_action_path = ".github/actions/setup-cpp-conan/action.yml"
    composite_action = read(composite_action_path) if exists(composite_action_path) else ""
    composite_venv_ok = composite_uses_pinned_venv(composite_action)
    for name, requirement in WORKFLOW_REQUIREMENTS.items():
        path = str(requirement["path"])
        add(checks, f"workflow:{name}:exists", exists(path), f"{path} exists")
        content = read(path) if exists(path) else ""
        for token in requirement["tokens"]:
            add(checks, f"workflow:{name}:token:{token}", token in content, f"{path} includes {token}")
        for summary in requirement["summaries"]:
            expected_summaries.append(summary)
            add(checks, f"workflow:{name}:uploads:{summary}", summary in content, f"{path} uploads {summary}")
        if name != "production_readiness":
            add(
                checks,
                f"workflow:{name}:pinned-conan-venv",
                composite_venv_ok if uses_composite_conan_action(content) else all(
                    token in content
                    for token in (CONAN_VENV_HELPER, f"--conan-version {PINNED_CONAN_VERSION}", '--github-path "$GITHUB_PATH"')
                ),
                f"{path} uses the audited Conan {PINNED_CONAN_VERSION} virtual environment",
            )
            add(
                checks,
                f"workflow:{name}:no-floating-or-global-conan",
                FLOATING_CONAN_REQUIREMENT not in content and "command -v conan" not in content,
                f"{path} rejects floating Conan versions and global Conan discovery",
            )
            direct_bootstrap = "scripts/bootstrap_conan.py" in content
            add(
                checks,
                f"workflow:{name}:bootstrap-persistent-conan-home",
                not direct_bootstrap or bootstrap_uses_resolved_home(content),
                f"{path} explicitly passes the resolved CONAN_HOME to direct bootstrap calls",
            )

    add(
        checks,
        "composite-conan:pinned-venv",
        composite_venv_ok,
        "the shared Conan action uses the pinned virtual environment helper",
    )
    add(
        checks,
        "composite-conan:no-global-or-floating-conan",
        FLOATING_CONAN_REQUIREMENT not in composite_action and "command -v conan" not in composite_action,
        "the shared Conan action never falls back to global or floating Conan",
    )
    add(
        checks,
        "composite-conan:bootstrap-persistent-conan-home",
        bootstrap_uses_resolved_home(composite_action),
        "the shared Conan action explicitly passes the resolved CONAN_HOME to bootstrap",
    )

    ci_workflow = read(".github/workflows/ci.yml")
    add(
        checks,
        "workflow:ci:github-hosted-default",
        "default: '\"ubuntu-latest\"'" in ci_workflow and "vars.CI_RUNNER" in ci_workflow,
        ".github/workflows/ci.yml defaults to ubuntu-latest and keeps CI_RUNNER override",
    )
    add(
        checks,
        "workflow:ci:cache-key-includes-conan-inputs",
        all(token in ci_workflow for token in ("conanfile.py", "conan/profiles/**", "conan/remotes*.json", "conan/locks/*.lock")),
        ".github/workflows/ci.yml cache key is bound to Conan graph inputs",
    )
    add(
        checks,
        "workflow:ci:pinned-conan-venv",
        all(
            token in ci_workflow
            for token in (CONAN_VENV_HELPER, f"--conan-version {PINNED_CONAN_VERSION}", '--github-path "$GITHUB_PATH"')
        ),
        ".github/workflows/ci.yml uses the pinned Conan virtual environment helper",
    )
    add(
        checks,
        "workflow:ci:no-floating-or-global-conan",
        FLOATING_CONAN_REQUIREMENT not in ci_workflow and "command -v conan" not in ci_workflow,
        ".github/workflows/ci.yml rejects floating Conan versions and global Conan discovery",
    )
    add(
        checks,
        "workflow:ci:cache-key-pinned-conan-version",
        "conan-2.8.1" in ci_workflow and "restore-keys:" not in named_workflow_step(ci_workflow, "Restore Conan cache"),
        ".github/workflows/ci.yml cache key includes Conan 2.8.1 and has no broad fallback restore key",
    )

    release_workflow = read(".github/workflows/release.yml")
    add(
        checks,
        "workflow:release:runner-input-declared",
        "workflow_dispatch:" in release_workflow and "inputs:" in release_workflow and "\n      runner:\n" in release_workflow,
        ".github/workflows/release.yml declares workflow_dispatch runner input",
    )
    add(
        checks,
        "workflow:release:release-runner-override",
        "vars.RELEASE_RUNNER" in release_workflow,
        ".github/workflows/release.yml keeps RELEASE_RUNNER override",
    )

    conan_validate_workflow = read(".github/workflows/conan-validate.yml")
    add(
        checks,
        "workflow:conan-validate:runner-override",
        "vars.CONAN_VALIDATE_RUNNER" in conan_validate_workflow,
        ".github/workflows/conan-validate.yml keeps CONAN_VALIDATE_RUNNER override",
    )

    specialized_e2e_workflow = read(".github/workflows/specialized-e2e.yml")
    add(
        checks,
        "workflow:specialized-e2e:runner-override",
        "vars.SPECIALIZED_E2E_RUNNER" in specialized_e2e_workflow,
        ".github/workflows/specialized-e2e.yml keeps SPECIALIZED_E2E_RUNNER override",
    )
    add(
        checks,
        "workflow:specialized-e2e:fresh-checkout-default",
        "default: false" in specialized_e2e_workflow and "test \"$(git rev-parse HEAD)\" = \"$GITHUB_SHA\"" in specialized_e2e_workflow and "mkdir -p runtime/validation" in specialized_e2e_workflow,
        ".github/workflows/specialized-e2e.yml defaults to a checked-out workflow commit",
    )
    for workflow_name in (
        "production-candidate-evidence",
        "preprod-evidence",
        "production-gates",
        "long-soak-capacity",
        "production-readiness",
        "release",
        "release-asset-verification",
        "specialized-e2e",
        "nightly-stability",
        "perf-regression",
    ):
        workflow_path = f".github/workflows/{workflow_name}.yml"
        workflow_content = read(workflow_path)
        add(
            checks,
            f"workflow:{workflow_name}:repair-fixed-workspace",
            all(
                token in workflow_content
                for token in (
                    "Normalize stale Go cache permissions",
                    'cache="$GITHUB_WORKSPACE/runtime/go-cache"',
                    "ubuntu:24.04 chmod -R a+rwX /cache",
                    "uses: actions/checkout@v4",
                )
            ),
            f"{workflow_path} repairs immutable Go cache output before a strict checkout clean",
        )

    control_plane_gate = read("scripts/gates/production/verify_control_plane_gate.py")
    add(
        checks,
        "workflow:control-plane:external-go-state",
        all(
            token in control_plane_gate
            for token in (
                '"--go-state-root"',
                'os.environ.get("BOOST_GATEWAY_GO_STATE_ROOT"',
                'os.environ.get("RUNNER_TEMP"',
                'state_root / "cache" / "build"',
            )
        )
        and 'root / "runtime" / "go-cache"' not in control_plane_gate,
        "control-plane Go caches and home default outside the persistent checkout",
    )

    for workflow_name, workflow_path in (
        ("production-gates", ".github/workflows/production-gates.yml"),
    ):
        workflow_content = read(workflow_path)
        add(
            checks,
            f"workflow:{workflow_name}:bounded-soak-choices",
            "          - long\n" not in workflow_content and "          - overnight\n" not in workflow_content,
            f"{workflow_path} exposes only profiles supported by its bounded gate",
        )

    long_soak_workflow = read(".github/workflows/long-soak-capacity.yml")
    add(
        checks,
        "workflow:long-soak-capacity:extended-job-timeout",
        "timeout-minutes: 1440" in long_soak_workflow,
        ".github/workflows/long-soak-capacity.yml allows the advertised soak/capacity combination to finish",
    )
    boolean_inputs = ("run_2h_soak", "run_8h_soak", "run_capacity", "run_business_capacity")
    add(
        checks,
        "workflow:long-soak-capacity:preserves-explicit-boolean-inputs",
        all(
            f'if [ "${{{{ inputs.{name} }}}}" = "true" ]; then' in long_soak_workflow
            and f"inputs.{name} ||" not in long_soak_workflow
            for name in boolean_inputs
        ),
        ".github/workflows/long-soak-capacity.yml does not turn a dispatched false input into its default",
    )
    stability_soak = read("scripts/gates/release/verify_stability_soak.py")
    add(
        checks,
        "workflow:long-soak-capacity:profile-timeouts",
        all(token in stability_soak for token in ("\"long\": {\"build\": 1800, \"test\": 300, \"baseline\": 10800}", "\"overnight\": {\"build\": 1800, \"test\": 300, \"baseline\": 32400}")),
        "verify_stability_soak.py carries explicit long/overnight timeout profiles",
    )
    add(
        checks,
        "workflow:long-soak-capacity:failed-run-archive",
        all(
            token in stability_soak
            for token in (
                "archive_failed_arch_run",
                'output_root / "failures"',
                "shutil.rmtree(failure_archive_root)",
                "host_resource_snapshot",
                "SUSTAINED_GATE_CONFIRMATION_RUNS = 2",
                '"confirmation_recovered"',
            )
        )
        and "runtime/perf/v2-stability-soak/**" in long_soak_workflow,
        "long soak preserves failed benchmark outputs, host diagnostics, and confirmation evidence",
    )
    fixed_runner_environment = read("scripts/gates/infrastructure/check_fixed_runner_environment.py")
    add(
        checks,
        "workflow:long-soak-capacity:preflight-profile",
        '"long-soak-capacity"' in fixed_runner_environment and '--profile long-soak-capacity' in long_soak_workflow,
        "long-soak-capacity.yml uses a profile accepted by fixed-runner preflight",
    )
    long_soak_gate = read("scripts/gates/production/run_long_soak_capacity.py")
    add(
        checks,
        "workflow:long-soak-capacity:canonical-root",
        "Path(__file__).resolve().parents[3]" in long_soak_gate,
        "run_long_soak_capacity.py resolves paths from the repository root",
    )
    release_baseline = read("scripts/producers/collect_release_baseline.py")
    add(
        checks,
        "capacity:explicit-battle-route-concurrency",
        'parser.add_argument("--backend-pool-size", type=int, default=8)' in long_soak_gate
        and 'parser.add_argument("--battle-route-workers", type=int, default=8)' in long_soak_gate
        and 'args.backend_pool_size = 8' in release_baseline
        and 'args.battle_route_workers = 8' in release_baseline
        and '"--backend-pool-size"' in release_baseline
        and '"--battle-route-workers"' in release_baseline,
        "capacity profiles explicitly configure battle backend connections and route workers",
    )
    readiness_report = read("scripts/gates/production/render_production_readiness_report.py")
    add(
        checks,
        "workflow:readiness-report:canonical-root",
        "Path(__file__).resolve().parents[3]" in readiness_report,
        "render_production_readiness_report.py resolves paths from the repository root",
    )
    add(
        checks,
        "workflow:readiness-report:requires-bounded-and-fixed",
        "decision_passed = final_ready if args.require_fixed_runner else bounded_ready" in readiness_report
        and "--require-fixed-runner" in read(".github/workflows/production-readiness.yml"),
        "final readiness requires both bounded and fixed-runner R2 summaries",
    )
    evidence_manifest_gate = read("scripts/gates/production/check_production_evidence_manifest.py")
    add(
        checks,
        "workflow:readiness-report:provenance-coherence",
        "provenance-mismatch" in evidence_manifest_gate
        and "expected-candidate-revision" in evidence_manifest_gate
        and "provenance_required" in read("docs/production/production-candidate-evidence-manifest.json"),
        "R2 validates provenance-bearing evidence against one candidate revision",
    )
    preprod_recovery_gate = read("scripts/gates/production/verify_preprod_recovery_drill.py")
    add(
        checks,
        "workflow:preprod:r5-docker-image-policies",
        all(
            token in preprod_recovery_gate
            for token in (
                'choices=["always", "missing", "never"]',
                "resolve_compose_image_requirements",
                "inspect_required_images",
                "missing_build_images",
                "r5-docker-image-preflight-summary.json",
            )
        ),
        "R5 supports cached, offline, and forced-refresh image policies with image evidence",
    )
    add(
        checks,
        "workflow:preprod:r5-monitoring-record-evidence",
        "R5 monitoring operability static gate" in preprod_recovery_gate
        and "scripts/check_monitoring_operability.py" in preprod_recovery_gate
        and "monitoring-operability-summary.json" in preprod_recovery_gate,
        "R5 generates the monitoring summary required by its recovery drill record",
    )
    preprod_workflow = read(".github/workflows/preprod-evidence.yml")
    add(
        checks,
        "workflow:preprod:r5-record-artifacts",
        "runtime/validation/r5-preprod-recovery-drill-record.json" in preprod_workflow
        and "runtime/validation/monitoring-operability-summary.json" in preprod_workflow,
        "R5 uploads both the recovery drill record and its referenced monitoring summary",
    )
    add(
        checks,
        "workflow:preprod:offline-defaults",
        'default: "never"' in preprod_workflow
        and 'scripts/bootstrap_conan.py --conan-home "$CONAN_HOME" --no-remote' in preprod_workflow
        and "--build=never" in preprod_workflow
        and "scripts/tools/ensure_conan_venv.py" in preprod_workflow
        and "--offline" in preprod_workflow
        and "inputs.docker_pull_policy || 'never'" in preprod_workflow,
        "R5 workflow defaults to offline Docker and a pre-warmed local Conan cache",
    )
    add(
        checks,
        "workflow:preprod:bounded-build-parallelism",
        'build_parallelism:' in preprod_workflow
        and 'default: "2"' in preprod_workflow
        and "--parallel ${{ inputs.build_parallelism || '2' }}" in preprod_workflow,
        "R5 build concurrency defaults to two jobs for 8GiB/no-swap runner admission",
    )
    add(
        checks,
        "workflow:preprod:r5-eligible-runner-default",
        f"default: '{PREPROD_R5_RUNNER}'" in preprod_workflow,
        "R5 workflow defaults to the preprod-r5 eligible runner pool rather than all Linux runners",
    )
    specialized_gate = read("scripts/gates/e2e/verify_specialized_e2e.py")
    add(
        checks,
        "workflow:specialized-e2e:canonical-root",
        "Path(__file__).resolve().parents[3]" in specialized_gate,
        "verify_specialized_e2e.py resolves paths from the repository root",
    )

    fixed_runner_doc = read("docs/fixed-runner-playbook.md")
    for token in DOC_TOKENS:
        add(checks, f"docs:fixed-runner:{token}", token in fixed_runner_doc, f"fixed-runner playbook mentions {token}")

    github_readme = read(".github/CI-CD.md")
    for token in README_TOKENS:
        add(checks, f"docs:github-readme:{token}", token in github_readme, f".github/CI-CD.md mentions {token}")

    runner_inventory_path = "docs/runner-inventory.md"
    add(checks, "docs:runner-inventory:exists", exists(runner_inventory_path), f"{runner_inventory_path} exists")
    runner_inventory = read(runner_inventory_path) if exists(runner_inventory_path) else ""
    for token in (
        "MyDesktop-Win",
        '["self-hosted","Linux","X64"]',
        "gh api repos/HoneyBury/boost_gateway/actions/runners",
        "单一事实源",
    ):
        add(
            checks,
            f"docs:runner-inventory:{token}",
            token in runner_inventory,
            f"{runner_inventory_path} mentions {token}",
        )

    runner_standard_path = "docs/runner-gate-standard.md"
    add(
        checks,
        "docs:runner-gate-standard:exists",
        exists(runner_standard_path),
        f"{runner_standard_path} exists",
    )
    runner_standard = read(runner_standard_path) if exists(runner_standard_path) else ""
    for token in (
        "Runner Gate Standard",
        "preprod-r5",
        "G2 Conan",
        "G3 Docker",
        "G4 R5 Drill",
        "G5 GitHub Evidence",
        "Conan Virtual Environment Contract",
        "ensure_conan_venv.py",
        "conan-2.8.1-py3.12",
        "Conan and Docker Separation",
    ):
        add(
            checks,
            f"docs:runner-gate-standard:{token}",
            token in runner_standard,
            f"{runner_standard_path} defines {token}",
        )

    current_state = read("docs/current-state.md")
    add(
        checks,
        "docs:current-state:references-runner-inventory",
        "docs/runner-inventory.md" in current_state,
        "docs/current-state.md references docs/runner-inventory.md",
    )

    manifest_path = "docs/production/production-candidate-evidence-manifest.json"
    if not exists(manifest_path):
        manifest_path = "docs/production-candidate-evidence-manifest.json"
    if exists(manifest_path):
        manifest = json.loads(read(manifest_path))
        manifest_text = json.dumps(manifest, ensure_ascii=False)
        for summary in (
            "runtime/validation/long-soak-2h-summary.json",
            "runtime/validation/fixed-runner-release-capacity-summary.json",
            "runtime/validation/preprod-recovery-drill-summary.json",
            "runtime/validation/tls-preprod-multi-run-summary.json",
        ):
            add(checks, f"manifest:requires:{summary}", summary in manifest_text, f"manifest references {summary}")
    else:
        for summary in (
            "runtime/validation/long-soak-2h-summary.json",
            "runtime/validation/fixed-runner-release-capacity-summary.json",
            "runtime/validation/preprod-recovery-drill-summary.json",
            "runtime/validation/tls-preprod-multi-run-summary.json",
        ):
            add(checks, f"manifest:requires:{summary}", False, f"manifest missing, cannot check {summary}")

    validation_contract = read("scripts/gates/governance/check_validation_summary_contract.py")
    for summary in expected_summaries:
        add(
            checks,
            f"summary-contract:knows:{summary}",
            summary in validation_contract or summary.endswith("/summary.json"),
            f"validation summary contract can audit {summary}",
        )

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "fixed_runner_evidence_plan" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "expected_fixed_runner_summaries": sorted(set(expected_summaries)),
        "checks": checks,
        "artifacts": {"summary_path": str(summary_path)},
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(f"fixed-runner evidence plan: {'PASS' if summary['passed'] else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)} checks)")
    print(f"summary: {summary_path}")
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
