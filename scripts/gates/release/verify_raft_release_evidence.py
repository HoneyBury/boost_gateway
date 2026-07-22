#!/usr/bin/env python3
"""Aggregate same-revision Raft Phase B release evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.lib.evidence_provenance import validate_evidence_provenance


ROOT = Path(__file__).resolve().parents[3]
MIXED_VERSION_TEST = (
    "RaftE2ETest."
    "E2E_MixedVersionRollingUpgradeAndRollbackKeepsLegacyWriterAndCommittedLog"
)
MIXED_BINARY_STAGES = (
    "all-legacy",
    "upgrade-follower-1",
    "upgrade-follower-2",
    "all-candidate",
    "rollback-follower-1",
    "rollback-follower-2",
    "all-legacy-after-rollback",
    "retry-upgrade-follower-1",
    "retry-upgrade-follower-2",
    "all-candidate-after-retry",
    "retry-rollback-follower-1",
    "retry-rollback-follower-2",
    "all-legacy-after-second-rollback",
)
MIXED_BINARY_SCHEMAS = (
    [0, 0, 0],
    [0, 0, 1],
    [0, 1, 1],
    [1, 1, 1],
    [0, 1, 1],
    [0, 0, 1],
    [0, 0, 0],
    [0, 0, 1],
    [0, 1, 1],
    [1, 1, 1],
    [0, 1, 1],
    [0, 0, 1],
    [0, 0, 0],
)


def load_summary(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read summary {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"summary must be a JSON object: {path}")
    return value


def resolved_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.stdout.strip()


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specialized-summary", type=Path, required=True)
    parser.add_argument("--data-recovery-summary", type=Path, required=True)
    parser.add_argument("--conan-offline-summary", type=Path, required=True)
    parser.add_argument("--sbom-summary", type=Path, required=True)
    parser.add_argument("--package-consumer-summary", type=Path, required=True)
    parser.add_argument("--mixed-binary-summary", type=Path, required=True)
    parser.add_argument("--candidate-revision")
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=Path("runtime/validation/raft-release-evidence-summary.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    expected_revision = args.candidate_revision or resolved_head()
    paths = {
        "specialized": args.specialized_summary,
        "data_recovery": args.data_recovery_summary,
        "conan_offline": args.conan_offline_summary,
        "sbom": args.sbom_summary,
        "package_consumer": args.package_consumer_summary,
        "mixed_binary": args.mixed_binary_summary,
    }
    summaries: dict[str, dict[str, Any]] = {}
    checks: list[dict[str, Any]] = []
    for name, raw_path in paths.items():
        path = raw_path if raw_path.is_absolute() else ROOT / raw_path
        try:
            summaries[name] = load_summary(path)
            add(checks, f"{name}:json", True, str(path))
        except ValueError as exc:
            add(checks, f"{name}:json", False, str(exc))

    if len(summaries) == len(paths):
        for name, summary in summaries.items():
            add(
                checks,
                f"{name}:passed",
                summary.get("overall_pass") is True and summary.get("passed") is True,
                "summary must pass",
            )
            errors = validate_evidence_provenance(
                summary.get("provenance"),
                expected_candidate_revision=expected_revision,
            )
            add(
                checks,
                f"{name}:provenance",
                not errors,
                "; ".join(errors) if errors else "candidate and lockfile provenance valid",
            )

        specialized_steps = summaries["specialized"].get("steps", [])
        matched_tests = {
            test
            for step in specialized_steps
            if isinstance(step, dict) and step.get("category") == "raft-ha"
            for test in step.get("matched_tests", [])
            if isinstance(test, str)
        }
        add(
            checks,
            "specialized:mixed-version-test",
            MIXED_VERSION_TEST in matched_tests,
            MIXED_VERSION_TEST,
        )
        recovery_steps = summaries["data_recovery"].get("steps", [])
        add(
            checks,
            "data-recovery:raft-step",
            any(
                isinstance(step, dict)
                and step.get("category") == "raft"
                and step.get("status") == "passed"
                for step in recovery_steps
            ),
            "passed Raft recovery step is required",
        )

        offline = summaries["conan_offline"].get("offline_contract", {})
        add(
            checks,
            "conan:strict-offline",
            isinstance(offline, dict)
            and offline.get("no_remote") is True
            and offline.get("build_policy") == "never"
            and offline.get("with_grpc") is False
            and offline.get("with_raft_protobuf") is True,
            "requires --no-remote --build=never, protobuf enabled, gRPC disabled",
        )
        lock_packages = set(offline.get("lock_packages", [])) if isinstance(offline, dict) else set()
        add(
            checks,
            "conan:raft-runtime-packages",
            {"protobuf", "abseil"} <= lock_packages and "grpc" not in lock_packages,
            "protobuf and abseil present; grpc absent",
        )

        sbom_dependencies = {
            dependency.get("name")
            for dependency in summaries["sbom"].get("conan", {}).get("runtime_dependencies", [])
            if isinstance(dependency, dict)
        }
        add(
            checks,
            "sbom:raft-runtime-packages",
            {"protobuf", "abseil"} <= sbom_dependencies and "grpc" not in sbom_dependencies,
            "SBOM contains protobuf and abseil but not grpc",
        )
        package_consumer = summaries["package_consumer"]
        production_platform = package_consumer.get("production_platform", "linux-x64")
        clean = package_consumer.get("clean_environment", {})
        native_platform = package_consumer.get("platform", {})
        c_abi = package_consumer.get("c_abi", {})
        cpp_consumer = package_consumer.get("cpp_consumer", {})
        linux_isolated = (
            production_platform in {"linux-x64", "linux-arm64"}
            and isinstance(clean, dict)
            and clean.get("network") == "none"
            and clean.get("pull_policy") == "never"
        )
        macos_isolated = (
            production_platform == "macos-arm64"
            and isinstance(native_platform, dict)
            and native_platform.get("system") == "Darwin"
            and native_platform.get("machine") == "arm64"
            and isinstance(c_abi, dict)
            and c_abi.get("loaded") is True
            and isinstance(cpp_consumer, dict)
            and cpp_consumer.get("cmake_find_package") is True
        )
        add(
            checks,
            "package-consumer:platform-isolation",
            linux_isolated or macos_isolated,
            "Linux uses an offline native OCI consumer; macOS uses native Mach-O/C ABI/CMake consumers",
        )

        mixed = summaries["mixed_binary"]
        binaries = mixed.get("binaries", {})
        legacy = binaries.get("legacy", {}) if isinstance(binaries, dict) else {}
        candidate = binaries.get("candidate", {}) if isinstance(binaries, dict) else {}
        legacy_hash = legacy.get("sha256") if isinstance(legacy, dict) else ""
        candidate_hash = candidate.get("sha256") if isinstance(candidate, dict) else ""
        add(
            checks,
            "mixed-binary:distinct-artifacts",
            isinstance(legacy_hash, str)
            and len(legacy_hash) == 64
            and isinstance(candidate_hash, str)
            and len(candidate_hash) == 64
            and legacy_hash != candidate_hash
            and legacy.get("expected_sha256") == legacy_hash,
            "legacy and candidate hashes must differ and legacy hash must match its expected digest",
        )
        add(
            checks,
            "mixed-binary:legacy-revision",
            isinstance(legacy, dict)
            and isinstance(legacy.get("revision"), str)
            and bool(legacy["revision"])
            and legacy["revision"] != expected_revision,
            "legacy revision must be explicit and differ from the candidate",
        )
        stages = mixed.get("stages", [])
        stage_names = [stage.get("name") for stage in stages if isinstance(stage, dict)]
        add(
            checks,
            "mixed-binary:stage-order",
            mixed.get("cycle_count") == 2
            and mixed.get("expected_stage_count") == 13
            and mixed.get("stage_count") == 13
            and stage_names == list(MIXED_BINARY_STAGES),
            f"stages={stage_names}",
        )
        stage_contract_ok = len(stages) == len(MIXED_BINARY_SCHEMAS)
        previous_commit = 0
        if stage_contract_ok:
            for index, (stage, expected_schemas) in enumerate(
                zip(stages, MIXED_BINARY_SCHEMAS, strict=True), start=1
            ):
                nodes = stage.get("nodes", {}) if isinstance(stage, dict) else {}
                node_values = (
                    [node for node in nodes.values() if isinstance(node, dict)]
                    if isinstance(nodes, dict)
                    else []
                )
                raw_schemas = [node.get("schema_version") for node in node_values]
                schemas = (
                    sorted(raw_schemas)
                    if all(isinstance(value, int) for value in raw_schemas)
                    else []
                )
                raw_commits = [node.get("commit_index") for node in node_values]
                commits = (
                    set(raw_commits)
                    if all(isinstance(value, int) for value in raw_commits)
                    else set()
                )
                reads = stage.get("read_responses", {}) if isinstance(stage, dict) else {}
                command = stage.get("command", {}) if isinstance(stage, dict) else {}
                stage_contract_ok = (
                    stage_contract_ok
                    and schemas == expected_schemas
                    and len(nodes) == 3
                    and len(node_values) == 3
                    and len(commits) == 1
                    and all(isinstance(value, int) and value >= index for value in commits)
                    and min(commits) > previous_commit
                    and isinstance(reads, dict)
                    and len(reads) == 3
                    and isinstance(command, dict)
                    and isinstance(command.get("user_id"), str)
                    and isinstance(command.get("score"), int)
                )
                if commits:
                    previous_commit = min(commits)
        add(
            checks,
            "mixed-binary:data-and-schema-contract",
            stage_contract_ok,
            "each stage requires three matching reads, equal advancing commit indexes and the governed schema trajectory",
        )
        downgrade_stages = [
            stage for stage in stages
            if isinstance(stage, dict) and isinstance(stage.get("downgrade"), dict)
        ]
        add(
            checks,
            "mixed-binary:downgrade-records",
            len(downgrade_stages) == 6
            and all(
                stage["downgrade"].get("overall_pass") is True
                and stage["downgrade"].get("operation") == "raft_state_v1_to_v0"
                and bool(stage["downgrade"].get("v1_backup_path"))
                and bool(stage["downgrade"].get("downgrade_record_path"))
                for stage in downgrade_stages
            ),
            "all six rollback replacements require successful v1-to-v0 backup and audit records",
        )
        downgrade_record_paths = [
            stage["downgrade"].get("downgrade_record_path")
            for stage in downgrade_stages
        ]
        backup_paths = [
            stage["downgrade"].get("v1_backup_path")
            for stage in downgrade_stages
        ]
        add(
            checks,
            "mixed-binary:multi-cycle-history",
            all(isinstance(path, str) for path in downgrade_record_paths)
            and all(isinstance(path, str) for path in backup_paths)
            and len(set(downgrade_record_paths)) == 6
            and len(set(backup_paths)) == 6
            and all(".history." in str(path) for path in downgrade_record_paths[3:])
            and all(".history." in str(path) for path in backup_paths[3:]),
            "second rollback cycle must use distinct content-addressed history sidecars",
        )
        add(
            checks,
            "mixed-binary:transition-generations",
            [stage["downgrade"].get("transition_generation") for stage in downgrade_stages[:3]]
            == [0, 0, 0]
            and all(
                type(stage["downgrade"].get("transition_generation")) is int
                and stage["downgrade"]["transition_generation"] > 0
                for stage in downgrade_stages[3:]
            ),
            "first rollback uses generation zero and the retry uses positive generations",
        )

        provenances = [summary.get("provenance", {}) for summary in summaries.values()]
        for key in ("candidate_revision", "git_commit", "workflow", "run_id", "runner", "conan_lockfile_sha256"):
            values = {provenance.get(key) for provenance in provenances if isinstance(provenance, dict)}
            add(
                checks,
                f"binding:{key}",
                len(values) == 1 and None not in values and "" not in values,
                f"values={sorted(str(value) for value in values)}",
            )

    failed = [check for check in checks if not check["passed"]]
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "raft_release_evidence" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "candidate_revision": expected_revision,
        "checks": checks,
        "artifacts": {
            "summary_path": str(summary_path),
            **{name: str(path) for name, path in paths.items()},
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"Raft release evidence: {'PASS' if summary['passed'] else 'FAIL'} "
        f"({len(checks) - len(failed)}/{len(checks)} checks)"
    )
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
