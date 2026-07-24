#!/usr/bin/env python3
"""Run bounded release-candidate gates with structured summaries."""

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
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.lib.subprocess_utils import run_gate_step
from scripts.lib.summary_utils import write_json_summary


run_step = run_gate_step


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build/contributor-debug"))
    parser.add_argument("--configuration", default="Debug")
    parser.add_argument("--baseline-profile", choices=["debug", "release"], default="debug")
    parser.add_argument("--soak-profile", choices=["smoke", "short", "medium"], default="smoke")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-release-baseline", action="store_true")
    parser.add_argument("--include-tank-demo", action="store_true",
                        help="Include tank battle demo verification and perf smoke test")
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/release-candidate-summary.json"))
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
        "baseline_profile": args.baseline_profile,
        "soak_profile": args.soak_profile,
        "overall_pass": False,
        "passed": False,
        "failed_category": "",
        "failed_step": "",
        "steps": [],
        "artifacts": {"summary_path": str(summary_path)},
    }

    steps: list[dict[str, object]] = []
    steps.append(run_step(
        "current docs install gate",
        "docs",
        [
            sys.executable,
            str(root / "scripts/gates/governance/check_current_docs_install.py"),
            "--summary-path",
            str(root / "runtime" / "validation" / "rc-current-docs-install-summary.json"),
        ],
        root,
        30,
    ))
    steps.append(run_step(
        "reliability matrix evidence",
        "docs",
        [sys.executable, str(root / "scripts/gates/governance/check_reliability_matrix.py")],
        root,
        30,
    ))
    steps.append(run_step(
        "mainline readiness gate",
        "mainline",
        [
            sys.executable,
            str(root / "scripts" / "check_mainline_readiness.py"),
            "--summary-path",
            str(root / "runtime" / "validation" / "rc-mainline-readiness-summary.json"),
        ],
        root,
        30,
    ))
    steps.append(run_step(
        "P3/P4 release readiness gate",
        "release_readiness",
        [
            sys.executable,
            str(root / "scripts/gates/release/check_p3_p4_release_readiness.py"),
            "--summary-path",
            str(root / "runtime" / "validation" / "rc-p3-p4-release-readiness-summary.json"),
        ],
        root,
        30,
    ))
    steps.append(run_step(
        "R4 contract gate",
        "contract",
        [
            sys.executable,
            str(root / "scripts/gates/release/verify_r4_contract.py"),
            "--build-dir",
            str(args.build_dir),
            "--configuration",
            args.configuration,
            "--skip-arch-baseline",
            "--baseline-profile",
            args.baseline_profile,
            "--summary-path",
            str(root / "runtime" / "validation" / "rc-r4-contract-summary.json"),
            *(["--skip-build"] if args.skip_build else []),
        ],
        root,
        args.timeout_seconds,
    ))
    steps.append(run_step(
        "security release gate",
        "security",
        [sys.executable, str(root / "scripts/gates/release/check_security_release_gate.py")],
        root,
        30,
    ))
    steps.append(run_step(
        "monitoring operability gate",
        "monitoring",
        [
            sys.executable,
            str(root / "scripts/gates/production/check_monitoring_operability.py"),
            "--summary-path",
            str(root / "runtime" / "validation" / "rc-monitoring-operability-summary.json"),
        ],
        root,
        30,
    ))
    steps.append(run_step(
        "data recovery gate",
        "data_recovery",
        [
            sys.executable,
            str(root / "scripts/gates/production/verify_data_recovery_gate.py"),
            "--build-dir",
            str(args.build_dir),
            "--configuration",
            args.configuration,
            "--summary-path",
            str(root / "runtime" / "validation" / "rc-data-recovery-summary.json"),
            *(["--skip-build"] if args.skip_build else []),
        ],
        root,
        args.timeout_seconds,
    ))
    steps.append(run_step(
        "observability release gate",
        "observability",
        [
            sys.executable,
            str(root / "scripts/gates/production/verify_observability_gate.py"),
            "--build-dir",
            str(args.build_dir),
            "--configuration",
            args.configuration,
            "--summary-path",
            str(root / "runtime" / "validation" / "rc-observability-gate-summary.json"),
            *(["--skip-build"] if args.skip_build else []),
        ],
        root,
        args.timeout_seconds,
    ))
    steps.append(run_step(
        "control-plane operator gate",
        "control_plane",
        [
            sys.executable,
            str(root / "scripts/gates/production/verify_control_plane_gate.py"),
            "--summary-path",
            str(root / "runtime" / "validation" / "rc-control-plane-gate-summary.json"),
        ],
        root,
        args.timeout_seconds,
    ))
    steps.append(run_step(
        "stability soak gate",
        "soak",
        [
            sys.executable,
            str(root / "scripts/gates/release/verify_stability_soak.py"),
            "--build-dir",
            str(args.build_dir),
            "--configuration",
            args.configuration,
            "--baseline-profile",
            args.baseline_profile,
            "--soak-profile",
            args.soak_profile,
            "--summary-path",
            str(root / "runtime" / "validation" / "rc-stability-soak-summary.json"),
            *(["--skip-build"] if args.skip_build else []),
        ],
        root,
        args.timeout_seconds,
    ))
    if not args.skip_release_baseline:
        steps.append(run_step(
            "release baseline entry",
            "baseline",
            [
                sys.executable,
                str(root / "scripts/producers/collect_release_baseline.py"),
                "--build-dir",
                str(args.build_dir),
                "--configuration",
                args.configuration,
                "--perf-timeout-seconds",
                str(args.timeout_seconds + 300),
                *(["--skip-build"] if args.skip_build else []),
            ],
            root,
            args.timeout_seconds + 420,
        ))

    if args.include_tank_demo:
        steps.append(run_step(
            "tank demo checkpoint verification",
            "tank_demo",
            [
                sys.executable,
                str(root / "demo" / "games" / "tank_battle" / "scripts" / "verify_tank_battle_demo.py"),
                "--build-dir",
                str(args.build_dir),
            ],
            root,
            args.timeout_seconds,
        ))
        steps.append(run_step(
            "tank demo perf smoke",
            "tank_demo",
            [
                sys.executable,
                str(root / "demo" / "games" / "tank_battle" / "scripts" / "perf_smoke_test.py"),
                "--build-dir",
                str(args.build_dir),
            ],
            root,
            args.timeout_seconds + 60,
        ))

    summary["steps"] = steps
    failed = next((step for step in steps if step["status"] != "passed"), None)
    if failed:
        summary["failed_category"] = str(failed["category"])
        summary["failed_step"] = str(failed["name"])
    else:
        summary["overall_pass"] = True
        summary["passed"] = True

    write_json_summary(summary_path, summary)
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
