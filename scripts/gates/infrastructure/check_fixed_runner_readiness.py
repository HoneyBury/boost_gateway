#!/usr/bin/env python3
"""Combined fixed-runner readiness gate.

Runs both the environment preflight check and the evidence plan validation
in sequence. Use --environment-only or --evidence-plan-only to run just one.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Fixed-runner readiness gate")
    parser.add_argument("--environment-only", action="store_true",
                        help="Only run environment preflight check")
    parser.add_argument("--evidence-plan-only", action="store_true",
                        help="Only run evidence plan validation")
    parser.add_argument("--build-dir", default="build/default")
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    gates_dir = script_dir

    checks = []
    if not args.evidence_plan_only:
        checks.append(("Environment preflight",
                       gates_dir / "check_fixed_runner_environment.py"))
    if not args.environment_only:
        checks.append(("Evidence plan",
                       gates_dir / "check_fixed_runner_evidence_plan.py"))

    overall_pass = True
    for label, script in checks:
        print(f"\n{'=' * 60}")
        print(f"  {label}")
        print(f"{'=' * 60}\n")
        cmd = [sys.executable, str(script)]
        if args.allow_missing:
            cmd.append("--allow-missing")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            overall_pass = False
            print(f"\n❌ {label}: FAILED")
        else:
            print(f"\n✅ {label}: PASSED")

    print(f"\n{'=' * 60}")
    if overall_pass:
        print("  Fixed-runner readiness: PASS")
    else:
        print("  Fixed-runner readiness: FAIL")
    print(f"{'=' * 60}\n")

    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
