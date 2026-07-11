#!/usr/bin/env python3
"""Validate Conan nosqlite lockfile workflow wiring for mainline and fixed-runner flows."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
LOCKFILE = "conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock"
PROFILE = "conan/profiles/linux-gcc-x64"
CACHE_INPUTS = ("conanfile.py", "conan/profiles/**", "conan/remotes*.json", "conan/locks/*.lock")


WORKFLOWS = {
    "ci": ".github/workflows/ci.yml",
    "conan_validate": ".github/workflows/conan-validate.yml",
    "release": ".github/workflows/release.yml",
    "long_soak_capacity": ".github/workflows/long-soak-capacity.yml",
    "production_evidence": ".github/workflows/production-evidence.yml",
    "production_candidate_evidence": ".github/workflows/production-candidate-evidence.yml",
}

# Fixed runners keep the Conan home outside the checkout so a fresh checkout
# does not invalidate the dependency cache.  ``ci.yml`` is intentionally
# different: it targets GitHub-hosted runners and restores a checkout-local
# home through actions/cache.
FIXED_RUNNER_CONAN_WORKFLOWS = {
    "conan_validate": ".github/workflows/conan-validate.yml",
    "release": ".github/workflows/release.yml",
    "long_soak_capacity": ".github/workflows/long-soak-capacity.yml",
    "nightly_stability": ".github/workflows/nightly-stability.yml",
    "perf_commit_check": ".github/workflows/perf-commit-check.yml",
    "perf_regression": ".github/workflows/perf-regression.yml",
    "production_candidate_evidence": ".github/workflows/production-candidate-evidence.yml",
    "production_evidence": ".github/workflows/production-evidence.yml",
    "production_resilience": ".github/workflows/production-resilience.yml",
    "specialized_e2e": ".github/workflows/specialized-e2e.yml",
}
FIXED_RUNNER_CONAN_HOME = "CONAN_HOME: ${{ github.workspace }}/../.conan2-local"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def exists(relative: str) -> bool:
    return (ROOT / relative).exists()


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def workflow_checks(checks: list[dict[str, Any]], name: str, path: str, content: str) -> None:
    add(checks, f"workflow:{name}:exists", exists(path), f"{path} exists")
    add(checks, f"workflow:{name}:linux-lockfile-default", LOCKFILE in content, f"{path} references {LOCKFILE}")
    add(checks, f"workflow:{name}:linux-profile-default", PROFILE in content, f"{path} references {PROFILE}")
    add(checks, f"workflow:{name}:grpc-disabled", '-o "&:with_grpc=False"' in content, f"{path} disables gRPC in default Conan graph")
    sqlite_disabled = (
        '-o "&:with_sqlite=False"' in content
        or (
            "with_sqlite:" in content
            and "default: false" in content
            and '&:with_sqlite=${{' in content
        )
    )
    add(checks, f"workflow:{name}:sqlite-disabled", sqlite_disabled, f"{path} keeps sqlite disabled by default for nosqlite mainline")
    add(checks, f"workflow:{name}:lockfile-consumed", "--lockfile" in content and "conan install" in content, f"{path} consumes lockfile during conan install")
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
    add(checks, "profile:linux-gcc-x64-exists", exists(PROFILE), f"{PROFILE} exists")
    add(checks, "conanfile:grpc-default-off", '"&:with_grpc": False' in read("conanfile.py"), "conanfile default disables gRPC")
    add(checks, "conanfile:sqlite-default-off", '"&:with_sqlite": False' in read("conanfile.py"), "conanfile default disables sqlite")

    contents = {name: read(path) if exists(path) else "" for name, path in WORKFLOWS.items()}
    for name, path in WORKFLOWS.items():
        workflow_checks(checks, name, path, contents[name])

    long_soak = contents["long_soak_capacity"]
    release = contents["release"]
    production_evidence = contents["production_evidence"]
    add(
        checks,
        "workflow:release:conan-preflight-toggle",
        "enable_conan_validation" in release and "conan-preflight" in release,
        "release workflow exposes lockfile-based Conan preflight before release baseline collection",
    )
    add(
        checks,
        "workflow:long-soak:real-conan-validation-build",
        "build/conan-long-soak-capacity-cmake" in long_soak and "--target project_v2" in long_soak,
        "long-soak-capacity performs a lockfile-based Conan configure/build preflight",
    )
    add(
        checks,
        "workflow:production-evidence:real-conan-validation-build",
        "build/conan-production-evidence-cmake" in production_evidence and "--target project_v2" in production_evidence,
        "production-evidence performs a lockfile-based Conan configure/build preflight",
    )
    for name, path in FIXED_RUNNER_CONAN_WORKFLOWS.items():
        content = read(path) if exists(path) else ""
        add(
            checks,
            f"workflow:{name}:fixed-conan-home",
            FIXED_RUNNER_CONAN_HOME in content,
            f"{path} reuses {FIXED_RUNNER_CONAN_HOME} outside the checkout",
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
