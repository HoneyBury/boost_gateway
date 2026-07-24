#!/usr/bin/env python3
"""Run the P6 production evidence aggregation gate."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        parent for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "__init__.py").is_file()
    )
    sys.path.insert(0, str(repo_import_root))

import argparse
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from functools import partial

from scripts.lib.subprocess_utils import run_gate_step
from scripts.lib.summary_utils import write_json_summary


run_step = partial(run_gate_step, include_execution_context=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build/default"))
    parser.add_argument("--configuration", default="Debug")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--include-redis-live", action="store_true")
    parser.add_argument("--include-operator-kind", action="store_true")
    parser.add_argument("--include-settlement-replay", action="store_true")
    parser.add_argument("--include-release-baseline", action="store_true")
    parser.add_argument("--include-capacity-baseline", action="store_true")
    parser.add_argument("--soak-profile", choices=["smoke", "short", "medium"], default="smoke")
    parser.add_argument("--baseline-profile", choices=["debug", "release"], default="debug")
    parser.add_argument("--perf-repetitions", type=int, default=1)
    parser.add_argument("--step-timeout-seconds", type=int, default=900)
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/production-evidence-summary.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[3]
    summary_path = args.summary_path if args.summary_path.is_absolute() else root / args.summary_path
    summary: dict[str, object] = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "build_dir": str(args.build_dir.resolve()),
        "configuration": args.configuration,
        "soak_profile": args.soak_profile,
        "baseline_profile": args.baseline_profile,
        "include_redis_live": args.include_redis_live,
        "include_operator_kind": args.include_operator_kind,
        "include_settlement_replay": args.include_settlement_replay,
        "include_release_baseline": args.include_release_baseline,
        "include_capacity_baseline": args.include_capacity_baseline,
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "overall_pass": False,
        "passed": False,
        "failed_category": "",
        "failed_step": "",
        "steps": [],
        "artifacts": {
            "summary_path": str(summary_path),
            "stability_summary_path": str(root / "runtime" / "validation" / "p6-stability-soak-summary.json"),
            "data_recovery_summary_path": str(root / "runtime" / "validation" / "p6-data-recovery-summary.json"),
            "specialized_summary_path": str(root / "runtime" / "validation" / "p6-specialized-e2e-summary.json"),
            "candidate_audit_summary_path": str(root / "runtime" / "validation" / "p6-candidate-audit-summary.json"),
            "release_baseline_summary_path": str(root / "runtime" / "validation" / "p6-release-baseline-summary.json"),
        },
    }

    steps: list[dict[str, object]] = []

    stability_cmd = [
        sys.executable,
        str(root / "scripts/gates/release/verify_stability_soak.py"),
        "--build-dir",
        str(args.build_dir),
        "--configuration",
        args.configuration,
        "--soak-profile",
        args.soak_profile,
        "--baseline-profile",
        args.baseline_profile,
        "--summary-path",
        str(root / "runtime" / "validation" / "p6-stability-soak-summary.json"),
    ]
    if args.skip_build:
        stability_cmd.append("--skip-build")
    steps.append(run_step(
        "P6 bounded stability evidence",
        "stability",
        stability_cmd,
        root,
        args.step_timeout_seconds,
    ))

    data_cmd = [
        sys.executable,
        str(root / "scripts/gates/production/verify_data_recovery_gate.py"),
        "--build-dir",
        str(args.build_dir),
        "--configuration",
        args.configuration,
        "--summary-path",
        str(root / "runtime" / "validation" / "p6-data-recovery-summary.json"),
    ]
    if args.skip_build:
        data_cmd.append("--skip-build")
    if args.include_redis_live:
        data_cmd.append("--include-redis-live")
    if args.include_settlement_replay:
        data_cmd.append("--include-settlement-replay")
    steps.append(run_step(
        "P6 data recovery evidence",
        "data_recovery",
        data_cmd,
        root,
        args.step_timeout_seconds,
    ))

    specialized_cmd = [
        sys.executable,
        str(root / "scripts/gates/e2e/verify_specialized_e2e.py"),
        "--build-dir",
        str(args.build_dir),
        "--configuration",
        args.configuration,
        "--summary-path",
        str(root / "runtime" / "validation" / "p6-specialized-e2e-summary.json"),
    ]
    if args.skip_build:
        specialized_cmd.append("--skip-build")
    if args.include_redis_live:
        specialized_cmd.append("--include-redis-live")
    if args.include_operator_kind:
        specialized_cmd.append("--include-operator-kind")
    steps.append(run_step(
        "P6 specialized Redis/Raft/Operator evidence",
        "specialized",
        specialized_cmd,
        root,
        args.step_timeout_seconds,
    ))

    candidate_cmd = [
        sys.executable,
        str(root / "scripts/gates/production/check_production_candidate_audit.py"),
        "--summary-path",
        str(root / "runtime" / "validation" / "p6-candidate-audit-summary.json"),
    ]
    steps.append(run_step(
        "P6 production candidate audit",
        "candidate_audit",
        candidate_cmd,
        root,
        60,
    ))

    if args.include_release_baseline or args.include_capacity_baseline:
        perf_preset = "capacity" if args.include_capacity_baseline else "baseline"
        release_cmd = [
            sys.executable,
            str(root / "scripts/producers/collect_release_baseline.py"),
            "--build-dir",
            str(args.build_dir),
            "--configuration",
            args.configuration,
            "--perf-preset",
            perf_preset,
            "--perf-repetitions",
            str(args.perf_repetitions),
            "--summary-path",
            str(root / "runtime" / "validation" / "p6-release-baseline-summary.json"),
        ]
        if args.skip_build:
            release_cmd.append("--skip-build")
        steps.append(run_step(
            "P6 release performance evidence",
            "release_baseline",
            release_cmd,
            root,
            args.step_timeout_seconds,
        ))

    summary["steps"] = steps
    summary["duration_seconds"] = round(sum(float(step.get("duration_seconds", 0.0)) for step in steps), 3)
    failed = next((step for step in steps if step.get("status") != "passed"), None)
    if failed:
        summary["failed_category"] = str(failed.get("category", "unknown"))
        summary["failed_step"] = str(failed.get("name", "unknown"))
    else:
        summary["overall_pass"] = True
        summary["passed"] = True

    write_json_summary(summary_path, summary)
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
