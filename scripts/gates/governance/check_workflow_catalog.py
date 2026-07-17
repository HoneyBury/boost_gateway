#!/usr/bin/env python3
"""Validate the workflow catalog, runner matrix, and documented workflow inventory."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS_ROOT = ROOT / ".github" / "workflows"
EXPECTED_NAMES = {
    "ci": "Mainline / Build, Test & Governance",
    "conan-validate": "Dependencies / Conan Graph Validation",
    "grpc-experimental": "Experimental / gRPC",
    "long-soak-capacity": "Stability / Fixed-Runner Soak & Capacity",
    "nightly-stability": "Stability / Bounded Soak",
    "perf-regression": "Performance / Baseline & Regression",
    "preprod-evidence": "Production / Preproduction Evidence",
    "production-candidate-evidence": "Production / Candidate Evidence",
    "production-gates": "Production / Gate Diagnostics",
    "production-readiness": "Production / Readiness Decision",
    "release": "Release / Package & Publish",
    "specialized-e2e": "Infrastructure / Redis, Raft & Operator E2E",
}
TAG_WORKFLOWS = {"release"}
STRICT_OFFLINE_CONAN_WORKFLOWS = {
    "grpc-experimental",
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

    add(checks, "workflow-count", len(actual) == 12, f"actual={len(actual)}")
    add(checks, "workflow-set:expected", actual == expected, f"actual={sorted(actual)} expected={sorted(expected)}")

    matrix_path = ROOT / ".github" / "runner-matrix.json"
    matrix = json.loads(read(matrix_path)) if matrix_path.exists() else {}
    matrix_workflows = set(matrix.get("workflows", {}))
    add(checks, "runner-matrix:exact-workflow-set", matrix_workflows == actual, f"matrix={sorted(matrix_workflows)} actual={sorted(actual)}")

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
    specialized_workflow = read(WORKFLOWS_ROOT / "specialized-e2e.yml")
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
        "release:isolated-asset-directory",
        'release_asset_dir="$RUNNER_TEMP/boost-gateway-release-${{ github.run_id }}"' in release_workflow
        and '>> "$GITHUB_ENV"' in release_workflow,
        "release publish job uses a run-local asset directory instead of a persistent workspace path",
    )
    add(
        checks,
        "release:checksum-only-published-archive",
        "find \"$RELEASE_ASSET_DIR\" -type f -name '*.tar.gz'" in release_workflow
        and "test \"${#archives[@]}\" -eq 1" in release_workflow,
        "release checksum is derived from exactly one packaged tarball",
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
        '(cd dist && cmake -E tar czfv "../${archive_basename}.tar.gz"' in release_workflow,
        "release archive is gzip-compressed and starts at the version directory instead of persistent dist",
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
        "release:sbom-and-attestations",
        "uses: anchore/sbom-action@v0" in release_workflow
        and release_workflow.count("uses: actions/attest@v4") == 2
        and "sbom-path:" in release_workflow
        and "attestations: write" in release_workflow
        and "id-token: write" in release_workflow,
        "tag release publishes SPDX SBOM plus build-provenance and SBOM attestations",
    )
    add(
        checks,
        "release:sbom-published-and-checksummed",
        "*-sbom.spdx.json" in release_workflow
        and 'for asset in "$archive" "$sbom"' in release_workflow,
        "release publishes and checksums both the tarball and SPDX SBOM",
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
