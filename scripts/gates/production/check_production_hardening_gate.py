#!/usr/bin/env python3
"""Validate H0-H5 production-candidate hardening evidence."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]


def read_text(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def exists(relative: str) -> bool:
    return (REPO_ROOT / relative).exists()


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def contains(relative: str, token: str) -> bool:
    return exists(relative) and token in read_text(relative)


def validate_h0(checks: list[dict[str, Any]]) -> None:
    workflow = ".github/workflows/production-gates.yml"
    text = read_text(workflow)
    add(checks, f"h0:{workflow}:manual-dispatch", "workflow_dispatch:" in text and "gate:" in text, "manual fixed-runner diagnostic workflow exists")
    add(checks, f"h0:{workflow}:runner-override", "vars.PRODUCTION_GATES_RUNNER" in text and '["self-hosted","Linux","X64"]' in text, "workflow keeps fixed-runner override")
    add(
        checks,
        f"h0:{workflow}:strict-conan-configure",
        "conan install ." in text
        and "--build=never" in text
        and "build/conan-production-gates-cmake" in text,
        "workflow uses the admitted Release Conan graph without a source fallback",
    )
    add(checks, f"h0:{workflow}:concurrency", "group: production-gates-" in text, "workflow has stable concurrency group")
    add(checks, f"h0:{workflow}:p5", "scripts/gates/production/verify_production_resilience_gate.py" in text, "workflow exposes P5 resilience gate")
    add(checks, f"h0:{workflow}:p6", "scripts/gates/production/verify_production_evidence_gate.py" in text, "workflow exposes P6 evidence gate")
    add(checks, f"h0:{workflow}:summary-render", "scripts/tools/render_validation_summary.py" in text, "workflow renders GitHub Step Summary")
    add(checks, f"h0:{workflow}:artifact", "actions/upload-artifact@v4" in text, "workflow archives evidence artifacts")


def validate_h1(checks: list[dict[str, Any]]) -> None:
    soak = read_text("scripts/gates/release/verify_stability_soak.py")
    plan = read_text("docs/mainline-execution-plan.md")
    add(checks, "h1:soak-profiles", all(item in soak for item in ['"smoke"', '"short"', '"medium"']), "bounded soak profiles exist")
    add(
        checks,
        "h1:long-soak-plan",
        "72 小时" in plan and "30 天不可变验证" in plan and "2,592,000s" in plan,
        "current long-run operations plan defines shakedown and immutable duration",
    )
    add(checks, "h1:bounded-stability-workflow", contains(".github/workflows/nightly-stability.yml", "workflow_dispatch:"), "bounded stability workflow exists")


def validate_h2(checks: list[dict[str, Any]]) -> None:
    release = read_text("scripts/producers/collect_release_baseline.py")
    perf = read_text("scripts/producers/collect_v2_perf_baseline.py")
    add(checks, "h2:capacity-preset", '"capacity"' in release and "perf_preset" in release, "release baseline supports capacity preset")
    add(checks, "h2:capacity-cases", "echo-10000" in perf and "battle-500" in perf, "capacity profile covers 10K echo and battle-500")
    add(checks, "h2:release-gates", "release_gates" in perf and "overall_pass" in perf, "performance summary has release gates")


def validate_h3(checks: list[dict[str, Any]]) -> None:
    manifests = [
        "env/k8s/gateway-deployment.yaml",
        "env/k8s/login-backend-deployment.yaml",
        "env/k8s/room-backend-deployment.yaml",
        "env/k8s/battle-backend-deployment.yaml",
        "env/k8s/matchmaking-backend-deployment.yaml",
        "env/k8s/leaderboard-backend-deployment.yaml",
    ]
    for manifest in manifests:
        text = read_text(manifest)
        add(checks, f"h3:{manifest}:resources", "resources:" in text and "requests:" in text and "limits:" in text, "manifest has resource requests/limits")
        add(checks, f"h3:{manifest}:hpa-pdb", "HorizontalPodAutoscaler" in text and "PodDisruptionBudget" in text, "manifest has HPA and PDB")
    add(checks, "h3:operator-kind", contains("scripts/tools/operator_kind_smoke.py", "rollout") and contains("scripts/tools/operator_kind_smoke.py", "conditions"), "operator kind smoke covers rollout/status")


def validate_h4(checks: list[dict[str, Any]]) -> None:
    add(checks, "h4:runtime-http-gate", contains("scripts/gates/production/verify_observability_gate.py", "--include-runtime-http"), "runtime HTTP observability gate exists")
    add(checks, "h4:otel-collector-gate", contains("scripts/gates/production/verify_observability_gate.py", "--include-otel-collector"), "OTel collector gate exists")
    add(checks, "h4:gateway-red-dashboard", contains("env/monitoring/grafana-dashboard.json", "gateway_backend_.*_requests_total"), "dashboard has backend RED panels")
    add(checks, "h4:p99-boundary-doc", contains("docs/performance-baseline.md", "P99"), "current P99 observability boundary is documented")


def validate_h5(checks: list[dict[str, Any]]) -> None:
    add(checks, "h5:python-example", exists("sdk/examples/python_full_flow.py"), "Python full-flow SDK example exists")
    add(checks, "h5:csharp-example", exists("sdk/examples/csharp_full_flow/Program.cs"), "C# full-flow SDK example exists")
    add(checks, "h5:csharp-heartbeat-disconnect", contains("sdk/examples/csharp_full_flow/Program.cs", "StartHeartbeat") and contains("sdk/examples/csharp_full_flow/Program.cs", "Disconnect"), "C# example covers heartbeat and disconnect")
    add(checks, "h5:python-business-flow", contains("sdk/examples/python_full_flow.py", "start_battle") and contains("sdk/examples/python_full_flow.py", "disconnect"), "Python example covers battle flow and disconnect")
    add(checks, "h5:compatibility-matrix", exists("sdk/docs/compatibility.md") and contains("sdk/docs/compatibility.md", "v3.6.x") and contains("sdk/docs/compatibility.md", "v4.2.0"), "current SDK compatibility matrix exists")
    sdk_docs = read_text("sdk/docs/README.md")
    add(checks, "h5:heartbeat-doc", "start_heartbeat" in sdk_docs and "on_disconnect" in sdk_docs, "SDK docs cover heartbeat/disconnect")
    add(checks, "h5:version-diagnostics", "BOOST_GATEWAY_SDK_LIBRARY" in sdk_docs and "gsdk_version()" in sdk_docs, "SDK docs cover native version/load diagnostics")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary-path", type=Path, default=REPO_ROOT / "runtime/validation/production-hardening-summary.json")
    args = parser.parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else REPO_ROOT / args.summary_path

    checks: list[dict[str, Any]] = []
    validate_h0(checks)
    validate_h1(checks)
    validate_h2(checks)
    validate_h3(checks)
    validate_h4(checks)
    validate_h5(checks)

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "production_hardening" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {"summary_path": str(summary_path)},
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"production hardening gate: {'PASS' if summary['passed'] else 'FAIL'} ({len(checks)-len(failed)}/{len(checks)} checks)")
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check['detail']}")
        return 1
    print(f"summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
