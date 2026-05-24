#!/usr/bin/env python3
"""Validate default production mainline boundaries and P2 evidence readiness."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def exists(relative: str) -> bool:
    return (ROOT / relative).exists()


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def validate_p0_docs(checks: list[dict[str, Any]]) -> None:
    docs = read("docs/README.md")
    current = read("docs/current-state.md")
    add(checks, "p0:docs-index-current-state", "current-state.md" in docs, "docs index points to current-state")
    add(checks, "p0:docs-index-archive-policy", "docs/archive/" in docs and "current-state.md" in docs, "docs index documents archive policy")
    add(checks, "p0:current-state-default-chain", "默认生产主链仍是 SDK + TCP gateway" in current, "current-state states the default production chain")


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
    manifest = json.loads(read("docs/production-candidate-evidence-manifest.json"))
    ids = {entry.get("id") for entry in manifest.get("evidence", []) if isinstance(entry, dict)}
    for evidence_id in (
        "fixed_runner_release_capacity",
        "preprod_recovery_drill",
        "tls_preprod_multi_run",
    ):
        add(checks, f"p2:manifest:{evidence_id}", evidence_id in ids, f"manifest declares {evidence_id}")

    for script in (
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
    evidence_runner = read("docs/production-evidence-runner.md")
    add(checks, "p2:fixed-runner-r4", "verify_fixed_runner_release_capacity.py" in fixed_runner, "fixed runner playbook documents R4")
    add(checks, "p2:fixed-runner-r5", "verify_preprod_recovery_drill.py" in fixed_runner, "fixed runner playbook documents R5")
    add(checks, "p2:fixed-runner-r6", "verify_tls_preprod_multi_run.py" in fixed_runner, "fixed runner playbook documents R6")
    add(checks, "p2:evidence-manifest-require-fixed-runner", "--require-fixed-runner" in evidence_runner, "evidence runner documents fixed-runner blocking mode")


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

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 1,
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
