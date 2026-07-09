#!/usr/bin/env python3
"""Validate default production mainline boundaries and P2 evidence readiness."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def exists(relative: str) -> bool:
    return (ROOT / relative).exists()


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def validate_p0_docs(checks: list[dict[str, Any]]) -> None:
    readme = read("README.md")
    docs = read("docs/README.md")
    current = read("docs/current-state.md")
    root_cmake = read("CMakeLists.txt")
    add(checks, "p0:docs-index-current-state", "current-state.md" in docs, "docs index points to current-state")
    add(checks, "p0:docs-index-archive-policy", "docs/archive/" in docs and "current-state.md" in docs, "docs index documents archive policy")
    add(checks, "p0:current-state-default-chain", "默认生产主链仍是 SDK + TCP gateway" in current, "current-state states the default production chain")
    add(checks, "p0:readme-boostgateway-title", "# BoostGateway" in readme, "README uses BoostGateway title")
    add(checks, "p0:cmake-framework-description", 'DESCRIPTION "Enterprise-grade C++20 realtime service framework"' in root_cmake, "CMake description matches framework positioning")
    add(checks, "p0:legacy-helper-doc-listed", "legacy-helper-inventory.md" in docs, "docs index lists legacy/helper inventory")


def validate_p1_mainline(checks: list[dict[str, Any]]) -> None:
    demo = read("src/v2/gateway/demo_server.cpp")
    runtime = read("src/v2/gateway/runtime.cpp")
    arch = read("docs/architecture-overview.md")
    current = read("docs/current-state.md")

    add(
        checks,
        "p1:demo-server-queues-business-packets",
        "enqueue_packet(session_id, std::move(message));" in demo,
        "DemoServer sends non-fast-path packets into SessionAdapter/GatewayActor",
    )
    for token in ("parse_login_body", "parse_match_body", "parse_leaderboard_submit_body", "send_backend_request("):
        add(checks, f"p1:demo-server-no-{token}", token not in demo, f"DemoServer no longer owns {token}")
    add(
        checks,
        "p1:runtime-owns-bridge-routing",
        "bridge_->route(v2::service::ServiceId::kLogin" in runtime
        and "bridge_->route(v2::service::ServiceId::kRoom" in runtime
        and "bridge_->route(v2::service::ServiceId::kBattle" in runtime
        and "bridge_->route(v2::service::ServiceId::kLeaderboard" in runtime,
        "Runtime owns backend bridge routing for core services",
    )
    add(
        checks,
        "p1:architecture-data-flow",
        "Client -> Gateway Session -> GatewayActor -> GatewayServiceBridge" in arch,
        "architecture overview documents the main request path",
    )
    add(
        checks,
        "p1:grpc-stays-experimental",
        "gRPC" in current and "不进入默认生产链路" in current,
        "current-state keeps gRPC out of the default production chain",
    )
    add(
        checks,
        "p1:tank-plugin-stays-demo",
        "TankBattlePlugin" in current and "不属于默认生产 battle 主链" in current,
        "current-state keeps TankBattlePlugin outside the default battle mainline",
    )


def validate_p2_evidence(checks: list[dict[str, Any]]) -> None:
    manifest_raw = read("docs/production-candidate-evidence-manifest.json") if exists("docs/production-candidate-evidence-manifest.json") else "{}"
    manifest = json.loads(manifest_raw)
    ids = {entry.get("id") for entry in manifest.get("evidence", []) if isinstance(entry, dict)}
    for evidence_id in (
        "long_soak_capacity",
        "fixed_runner_release_capacity",
        "preprod_recovery_drill",
        "tls_preprod_multi_run",
    ):
        add(checks, f"p2:manifest:{evidence_id}", evidence_id in ids, f"manifest declares {evidence_id}")

    for script in (
        "scripts/check_script_inventory.py",
        "scripts/check_validation_summary_contract.py",
        "scripts/check_config_source_layout.py",
        "scripts/check_fixed_runner_evidence_plan.py",
        "scripts/verify_fixed_runner_release_capacity.py",
        "scripts/verify_preprod_recovery_drill.py",
        "scripts/verify_tls_preprod_multi_run.py",
        "scripts/check_production_evidence_manifest.py",
        "scripts/render_production_readiness_report.py",
    ):
        add(checks, f"p2:script:{script}", exists(script), f"{script} exists")

    workflow = read(".github/workflows/production-evidence.yml")
    for token in (
        "include_redis_live",
        "include_operator_kind",
        "include_capacity_baseline",
        "include_observability_runtime",
        "actions/upload-artifact@v4",
        "scripts/render_validation_summary.py",
    ):
        add(checks, f"p2:workflow:{token}", token in workflow, f"production evidence workflow includes {token}")

    fixed_runner = read("docs/fixed-runner-playbook.md")
    add(checks, "p2:fixed-runner-r4", "verify_fixed_runner_release_capacity.py" in fixed_runner, "fixed runner playbook documents R4")
    add(checks, "p2:fixed-runner-r5", "verify_preprod_recovery_drill.py" in fixed_runner, "fixed runner playbook documents R5")
    add(checks, "p2:fixed-runner-r6", "verify_tls_preprod_multi_run.py" in fixed_runner, "fixed runner playbook documents R6")


def validate_p3_governance(checks: list[dict[str, Any]]) -> None:
    inventory = json.loads(read("docs/script-inventory.json"))
    public = set(inventory.get("public_entrypoints", []))
    scripts = inventory.get("scripts", {})
    add(checks, "p3:script-inventory-present", bool(scripts), "script inventory declares scripts")
    for entrypoint in (
        "scripts/verify_release_candidate.py",
        "scripts/check_mainline_readiness.py",
        "scripts/verify_production_candidate_evidence.py",
        "scripts/check_production_evidence_manifest.py",
        "scripts/run_long_soak_capacity.py",
        "scripts/verify_sdk_enterprise_delivery.py",
    ):
        add(checks, f"p3:public-entrypoint:{entrypoint}", entrypoint in public, f"{entrypoint} is public")

    env_readme = read("env/README.md")
    add(checks, "p3:env-source-of-truth", "`env/` is the maintained production configuration source of truth" in env_readme, "env README declares config source of truth")
    add(checks, "p3:legacy-config-boundary", "legacy/reference surfaces" in env_readme, "env README documents legacy config boundary")
    add(checks, "p3:legacy-helper-gate-exists", exists("scripts/check_legacy_helper_inventory.py"), "legacy/helper governance gate exists")
    add(checks, "p4:legacy-helper-gate-guards-raw-json", "no-new-raw-json-marker" in read("scripts/gates/governance/check_legacy_helper_inventory.py"), "legacy/helper gate guards against new raw JSON-only business markers")
    add(checks, "p3:conan-governance-readme", exists("conan/README.md"), "repository documents Conan governance entrypoints")
    add(checks, "p3:conan-managed-profile", exists("conan/profiles/windows-msvc-x64"), "repository ships a managed Conan profile")
    add(checks, "p3:conan-linux-profile", exists("conan/profiles/linux-gcc-x64"), "repository ships a Linux fixed-runner Conan profile")
    add(checks, "p3:conan-lock-generator", exists("scripts/tools/generate_conan_lock.py"), "repository ships a Conan lockfile generator")
    linux_profile = read("conan/profiles/linux-gcc-x64")
    add(checks, "p3:conan-linux-profile-pins-compiler-version", "compiler.version=" in linux_profile, "Linux fixed-runner Conan profile pins compiler.version")
    conan_validate_workflow = read(".github/workflows/conan-validate.yml")
    long_soak_workflow = read(".github/workflows/long-soak-capacity.yml")
    production_evidence_workflow = read(".github/workflows/production-evidence.yml")
    linux_lockfile = "conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock"
    add(checks, "p3:conan-linux-lockfile-default", linux_lockfile in conan_validate_workflow and linux_lockfile in long_soak_workflow and linux_lockfile in production_evidence_workflow, "fixed-runner workflows default to the Linux nosqlite lockfile path")
    add(checks, "p3:conan-lockfile-workflow-gate", exists("scripts/check_conan_lockfile_workflows.py"), "Conan lockfile workflow governance gate exists")
    add(checks, "p3:fixed-runner-evidence-plan-gate", exists("scripts/check_fixed_runner_evidence_plan.py"), "fixed-runner evidence plan governance gate exists")
    add(checks, "p3:long-soak-consumes-conan-lockfile", "build/conan-long-soak-capacity-cmake" in long_soak_workflow and "--lockfile" in long_soak_workflow, "long-soak-capacity workflow performs lockfile-based Conan configure/build preflight")
    add(checks, "p3:production-evidence-consumes-conan-lockfile", "build/conan-production-evidence-cmake" in production_evidence_workflow and "--lockfile" in production_evidence_workflow, "production-evidence workflow performs lockfile-based Conan configure/build preflight")
    add(
        checks,
        "p3:fixed-runner-summaries-uploaded",
        "runtime/validation/long-soak-capacity-summary.json" in long_soak_workflow
        and "runtime/validation/fixed-runner-release-capacity-summary.json" in long_soak_workflow
        and "runtime/validation/production-evidence-summary.json" in production_evidence_workflow,
        "fixed-runner workflows upload the required long-soak/capacity/production evidence summaries",
    )

    root_cmake = read("CMakeLists.txt")
    add(checks, "p3:sqlite-default-off", 'option(BOOST_BUILD_SQLITE "Build SQLite-backed storage (requires sqlite3)" OFF)' in root_cmake, "default mainline keeps SQLite opt-in")

    deps_cmake = read("cmake/Dependencies.cmake")
    third_party_readme = read("third_party/README.md")
    add(checks, "p3:openssl-central-resolver", "function(project_ensure_openssl)" in deps_cmake, "OpenSSL resolution is centralized")
    add(checks, "p3:openssl-conan-system-local", "find_package(OpenSSL CONFIG QUIET)" in deps_cmake and "find_package(OpenSSL QUIET)" in deps_cmake and '"${THIRD_PARTY_DIR}/openssl"' in deps_cmake, "OpenSSL supports Conan, system and local install fallback")
    add(checks, "p3:openssl-third-party-doc", "OpenSSL 是例外" in third_party_readme and "Conan config package" in third_party_readme, "third_party docs explain OpenSSL fallback policy")
    add(checks, "p3:conan-boost-target-compatible", "PROJECT_CONAN_BOOST_TARGET" in deps_cmake and "boost::boost" in deps_cmake and "boost::headers" in deps_cmake and "Boost::headers" in deps_cmake, "Conan Boost target accepts both boost::boost and boost::headers")
    v3_cmake = read("src/v3/CMakeLists.txt")
    redis_client = read("src/v3/persistence/redis_client.cpp")
    add(checks, "p3:conan-hiredis-target-compatible", "hiredis::hiredis" in v3_cmake and "TARGET_EXISTS:hiredis::hiredis" in v3_cmake, "project_v3 links Conan hiredis target when available")
    add(checks, "p3:conan-hiredis-include-compatible", "__has_include(<hiredis/hiredis.h>)" in redis_client and "#include <hiredis.h>" in redis_client, "Redis client accepts both Conan and legacy hiredis include layouts")
    sdk_cmake = read("sdk/CMakeLists.txt")
    sdk_tests_cmake = read("sdk/tests/CMakeLists.txt")
    add(checks, "p3:sdk-uses-project-boost-asio", "target_link_libraries(boost_gateway_sdk PRIVATE $<BUILD_INTERFACE:project_boost_asio>)" in sdk_cmake and "target_link_libraries(boost_gateway_sdk_dll PRIVATE project_boost_asio)" in sdk_cmake, "SDK reuses project_boost_asio privately for Conan/fallback Boost compatibility")
    add(checks, "p3:sdk-tests-use-project-boost-asio", "target_link_libraries(sdk_tests PRIVATE project_boost_asio)" in sdk_tests_cmake and "target_link_libraries(sdk_business_flow_tests PRIVATE project_boost_asio)" in sdk_tests_cmake, "SDK tests reuse project_boost_asio for Conan/fallback Boost compatibility")

    src_cmake = read("src/CMakeLists.txt")
    add(
        checks,
        "p3:legacy-v1-surface-removed",
        "BOOST_BUILD_V1_LEGACY_CORE" not in root_cmake
        and "project_game" not in src_cmake
        and not (ROOT / "include/game").exists(),
        "v1 legacy build surface (BOOST_BUILD_V1_LEGACY_*) and include/game no longer exist",
    )



def validate_p4_integration_stability(checks: list[dict[str, Any]]) -> None:
    supervisor = read("src/app/process_supervisor.cpp")
    demo_server = read("src/v2/gateway/demo_server.cpp")
    backend_routing_test = read("tests/v2/integration/backend_routing_test.cpp")
    demo_smoke_test = read("tests/v2/integration/demo_server_smoke_test.cpp")
    matchmaking_test = read("tests/v2/integration/matchmaking_e2e_test.cpp")
    reliability = read("docs/release-governance.md")

    add(
        checks,
        "p4:process-supervisor-kills-process-group",
        "const pid_t process_group = -state.pid" in supervisor
        and "::kill(process_group, SIGTERM)" in supervisor
        and "::kill(process_group, SIGKILL)" in supervisor,
        "POSIX ProcessSupervisor terminates child process groups, not only the direct child PID",
    )
    add(
        checks,
        "p4:demo-server-destructor-stops-runtime",
        "DemoServer::~DemoServer()" in demo_server and "stop();" in demo_server,
        "DemoServer destructor calls stop() so smoke fixtures cannot leak background threads",
    )
    add(
        checks,
        "p4:fake-otlp-collector-wakes-acceptor",
        "wake_socket.connect" in backend_routing_test and "thread.join()" in backend_routing_test,
        "Fake OTLP collector wakes its blocking accept loop before joining",
    )
    add(
        checks,
        "p4:demo-smoke-read-timeout-diagnostics",
        "SO_RCVTIMEO" in demo_smoke_test
        and "timed out waiting for message id" in demo_smoke_test
        and "observed=" in demo_smoke_test,
        "demo server smoke client has bounded reads with observed-message diagnostics",
    )
    add(
        checks,
        "p4:matchmaking-leave-order-regression",
        "Bob leaves before another compatible player joins" in matchmaking_test
        and "Bob must not be matched retroactively" in matchmaking_test,
        "matchmaking E2E covers leave-before-compatible-join ordering",
    )
    add(
        checks,
        "p4:reliability-docs-integration-teardown",
        "integration_teardown_no_hang" in reliability,
        "reliability matrix documents integration teardown no-hang evidence",
    )

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/mainline-readiness-summary.json",
    )
    args = parser.parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path

    checks: list[dict[str, Any]] = []
    validate_p0_docs(checks)
    validate_p1_mainline(checks)
    validate_p2_evidence(checks)
    validate_p3_governance(checks)
    validate_p4_integration_stability(checks)

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "mainline_readiness" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {"summary_path": str(summary_path)},
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(f"mainline readiness gate: {'PASS' if summary['passed'] else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)} checks)")
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check['detail']}")
        return 1
    print(f"summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
