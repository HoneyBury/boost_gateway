from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.gates.release import verify_raft_release_evidence
from scripts.tools import verify_release_package_consumer


SHA = "b" * 40
LOCK_SHA = "c" * 64
MIXED_TEST = verify_raft_release_evidence.MIXED_VERSION_TEST


def mixed_binary_stages() -> list[dict[str, object]]:
    stages: list[dict[str, object]] = []
    for index, (name, schemas) in enumerate(
        zip(
            verify_raft_release_evidence.MIXED_BINARY_STAGES,
            verify_raft_release_evidence.MIXED_BINARY_SCHEMAS,
            strict=True,
        ),
        start=1,
    ):
        nodes = {
            f"lb-{node_index}": {
                "schema_version": schema,
                "commit_index": index,
            }
            for node_index, schema in enumerate(schemas, start=1)
        }
        stage: dict[str, object] = {
            "name": name,
            "leader": "lb-1",
            "command": {"user_id": f"user-{index}", "score": 1000 + index},
            "read_responses": {node_id: {"kind": "response"} for node_id in nodes},
            "nodes": nodes,
        }
        if index in {5, 6, 7, 11, 12, 13}:
            history = ".history." if index >= 11 else "."
            stage["downgrade"] = {
                "overall_pass": True,
                "operation": "raft_state_v1_to_v0",
                "v1_backup_path": f"lb-{index}.v1{history}bak",
                "downgrade_record_path": f"lb-{index}.downgrade{history}json",
                "transition_generation": 1 if index >= 11 else 0,
            }
        stages.append(stage)
    return stages


def provenance() -> dict[str, object]:
    return {
        "candidate_revision": SHA,
        "git_commit": SHA,
        "git_ref": "refs/heads/candidate",
        "workflow": "Release / Package & Publish",
        "run_id": "12345",
        "runner": "fixed-linux",
        "build_configuration": "Release",
        "conan_lockfile": "conan/locks/linux.lock",
        "conan_lockfile_sha256": LOCK_SHA,
        "revision_matches_checkout": True,
    }


class VerifyRaftReleaseEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        common = {"summary_version": 2, "overall_pass": True, "passed": True, "provenance": provenance()}
        self.summaries: dict[str, dict[str, object]] = {
            "specialized": {
                **copy.deepcopy(common),
                "steps": [
                    {
                        "category": "raft-ha",
                        "status": "passed",
                        "matched_tests": [MIXED_TEST],
                    }
                ],
            },
            "data_recovery": {
                **copy.deepcopy(common),
                "steps": [{"category": "raft", "status": "passed"}],
            },
            "conan_offline": {
                **copy.deepcopy(common),
                "offline_contract": {
                    "no_remote": True,
                    "build_policy": "never",
                    "with_grpc": False,
                    "with_raft_protobuf": True,
                    "lock_packages": ["abseil", "fmt", "protobuf"],
                },
            },
            "sbom": {
                **copy.deepcopy(common),
                "conan": {
                    "runtime_dependencies": [
                        {"name": "abseil"},
                        {"name": "fmt"},
                        {"name": "protobuf"},
                    ]
                },
            },
            "package_consumer": {
                **copy.deepcopy(common),
                "clean_environment": {"network": "none", "pull_policy": "never"},
            },
            "mixed_binary": {
                **copy.deepcopy(common),
                "cycle_count": 2,
                "expected_stage_count": 13,
                "stage_count": 13,
                "binaries": {
                    "legacy": {
                        "revision": "a" * 40,
                        "sha256": "1" * 64,
                        "expected_sha256": "1" * 64,
                    },
                    "candidate": {"revision": SHA, "sha256": "2" * 64},
                },
                "stages": mixed_binary_stages(),
            },
        }

    def run_main(self) -> tuple[int, dict[str, object]]:
        paths: dict[str, Path] = {}
        for name, summary in self.summaries.items():
            path = self.directory / f"{name}.json"
            path.write_text(json.dumps(summary), encoding="utf-8")
            paths[name] = path
        output = self.directory / "aggregate.json"
        argv = [
            "verify_raft_release_evidence.py",
            "--specialized-summary",
            str(paths["specialized"]),
            "--data-recovery-summary",
            str(paths["data_recovery"]),
            "--conan-offline-summary",
            str(paths["conan_offline"]),
            "--sbom-summary",
            str(paths["sbom"]),
            "--package-consumer-summary",
            str(paths["package_consumer"]),
            "--mixed-binary-summary",
            str(paths["mixed_binary"]),
            "--candidate-revision",
            SHA,
            "--summary-path",
            str(output),
        ]
        with patch("sys.argv", argv):
            result = verify_raft_release_evidence.main()
        return result, json.loads(output.read_text(encoding="utf-8"))

    def test_accepts_complete_same_run_evidence(self) -> None:
        result, summary = self.run_main()
        self.assertEqual(result, 0)
        self.assertTrue(summary["overall_pass"])
        self.assertTrue(all(check["passed"] for check in summary["checks"]))

    def test_accepts_native_macos_package_consumer(self) -> None:
        self.summaries["package_consumer"] = {
            "summary_version": 2,
            "overall_pass": True,
            "passed": True,
            "production_platform": "macos-arm64",
            "platform": {"system": "Darwin", "machine": "arm64"},
            "c_abi": {"loaded": True, "version": "4.2.0"},
            "cpp_consumer": {"cmake_find_package": True, "sdk_version": "4.2.0"},
            "provenance": provenance(),
        }

        result, summary = self.run_main()

        self.assertEqual(result, 0)
        checks = {check["name"]: check["passed"] for check in summary["checks"]}
        self.assertTrue(checks["package-consumer:platform-isolation"])

    def test_rejects_cross_revision_and_missing_protobuf_sbom(self) -> None:
        self.summaries["data_recovery"]["provenance"]["candidate_revision"] = "d" * 40
        self.summaries["data_recovery"]["provenance"]["git_commit"] = "d" * 40
        self.summaries["sbom"]["conan"]["runtime_dependencies"] = [{"name": "abseil"}]

        result, summary = self.run_main()
        self.assertEqual(result, 1)
        failed = {check["name"] for check in summary["checks"] if not check["passed"]}
        self.assertIn("data_recovery:provenance", failed)
        self.assertIn("sbom:raft-runtime-packages", failed)
        self.assertIn("binding:candidate_revision", failed)

    def test_rejects_cross_run_and_lockfile_drift(self) -> None:
        self.summaries["package_consumer"]["provenance"]["run_id"] = "99999"
        self.summaries["sbom"]["provenance"]["conan_lockfile_sha256"] = "e" * 64

        result, summary = self.run_main()
        self.assertEqual(result, 1)
        failed = {check["name"] for check in summary["checks"] if not check["passed"]}
        self.assertIn("binding:run_id", failed)
        self.assertIn("binding:conan_lockfile_sha256", failed)

    def test_rejects_forged_revision_matches_checkout_flag(self) -> None:
        self.summaries["package_consumer"]["provenance"]["git_commit"] = "f" * 40
        self.summaries["package_consumer"]["provenance"]["revision_matches_checkout"] = True

        result, summary = self.run_main()
        self.assertEqual(result, 1)
        failed = {check["name"] for check in summary["checks"] if not check["passed"]}
        self.assertIn("package_consumer:provenance", failed)
        self.assertIn("binding:git_commit", failed)

    def test_rejects_forged_mixed_binary_trajectory(self) -> None:
        mixed = self.summaries["mixed_binary"]
        mixed["binaries"]["candidate"]["sha256"] = "1" * 64
        mixed["stages"][3]["nodes"]["lb-1"]["schema_version"] = 0
        mixed["stages"][10]["downgrade"]["transition_generation"] = 0
        del mixed["stages"][12]["downgrade"]

        result, summary = self.run_main()
        self.assertEqual(result, 1)
        failed = {check["name"] for check in summary["checks"] if not check["passed"]}
        self.assertIn("mixed-binary:distinct-artifacts", failed)
        self.assertIn("mixed-binary:data-and-schema-contract", failed)
        self.assertIn("mixed-binary:downgrade-records", failed)
        self.assertIn("mixed-binary:transition-generations", failed)

    def test_package_consumer_cli_attaches_release_provenance(self) -> None:
        output = self.directory / "package-summary.json"
        archive = self.directory / "release.tar.gz"
        archive.write_bytes(b"fixture")
        argv = [
            "verify_release_package_consumer.py",
            "--archive",
            str(archive),
            "--expected-root",
            "release",
            "--lockfile",
            "conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock",
            "--candidate-revision",
            SHA,
            "--summary-path",
            str(output),
        ]
        package_summary = {
            "summary_version": 2,
            "overall_pass": True,
            "passed": True,
            "clean_environment": {"network": "none", "pull_policy": "never"},
        }
        with (
            patch("sys.argv", argv),
            patch.object(
                verify_release_package_consumer,
                "verify_package",
                return_value=package_summary,
            ),
            patch.object(
                verify_release_package_consumer,
                "build_evidence_provenance",
                return_value=provenance(),
            ),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(verify_release_package_consumer.main(), 0)

        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["provenance"]["candidate_revision"], SHA)
        self.assertEqual(result["artifacts"]["summary_path"], str(output))


if __name__ == "__main__":
    unittest.main()
