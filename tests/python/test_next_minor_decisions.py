from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check_next_minor_decisions.py"


class NextMinorDecisionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.manifest_path = (
            self.root / "docs/decisions/v3.6-decision-manifest.json"
        )
        self.summary_path = self.root / "summary.json"
        self.manifest = self.make_manifest()
        self.write_fixture(self.manifest)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def make_manifest() -> dict[str, Any]:
        decisions: list[dict[str, Any]] = []
        decision_ids = (
            "v36-raft-wire-schema",
            "v36-identity-jwks",
            "v36-macos-arm64",
            "v36-sdk-distribution",
            "v36-debug-symbols",
        )
        for index, decision_id in enumerate(decision_ids):
            decisions.append(
                {
                    "id": decision_id,
                    "status": "accepted_for_implementation",
                    "target_minor": "v3.6.0",
                    "document": f"docs/decisions/{index + 1:02d}-{decision_id}.md",
                    "implementation_order": index + 1,
                    "compatibility_window": f"{decision_id}-compatibility-window",
                    "migration_window": f"{decision_id}-migration-window",
                    "release_assets": [f"{decision_id}-release-asset"],
                    "validation_gates": [f"{decision_id}-validation-gate"],
                    "default_activation": "blocked_until_gates_pass",
                    "dependencies": [] if index == 0 else [decision_ids[index - 1]],
                }
            )
        return {
            "schema_version": 1,
            "target_minor": "v3.6.0",
            "default_activation_policy": "blocked_until_gates_pass",
            "decisions": decisions,
        }

    @staticmethod
    def adr_text(decision: dict[str, Any]) -> str:
        values = [
            decision["id"],
            decision["status"],
            decision["target_minor"],
            decision["compatibility_window"],
            decision["migration_window"],
            *decision["release_assets"],
            *decision["validation_gates"],
            decision["default_activation"],
            *decision["dependencies"],
        ]
        return "# Decision\n\n" + "\n\n".join(str(value) for value in values) + "\n"

    def write_fixture(self, manifest: dict[str, Any], *, write_docs: bool = True) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        if write_docs:
            for decision in manifest.get("decisions", []):
                if not isinstance(decision, dict) or not isinstance(decision.get("document"), str):
                    continue
                doc = self.root / decision["document"]
                doc.parent.mkdir(parents=True, exist_ok=True)
                doc.write_text(self.adr_text(decision), encoding="utf-8")

    def run_gate(self) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(self.root),
                "--summary-path",
                str(self.summary_path),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        summary = json.loads(self.summary_path.read_text(encoding="utf-8"))
        return completed, summary

    def test_valid_manifest_and_five_adrs_pass(self) -> None:
        completed, summary = self.run_gate()

        self.assertEqual(0, completed.returncode, completed.stdout)
        self.assertTrue(summary["overall_pass"])
        self.assertEqual(2, summary["summary_version"])
        self.assertEqual("next_minor_decisions", summary["gate"])
        self.assertEqual("v3.6.0", summary["target_minor"])
        self.assertEqual(5, summary["decision_count"])
        self.assertEqual(5, len(summary["decisions"]))
        self.assertTrue(all(item["passed"] for item in summary["decisions"]))
        self.assertEqual([], [check for check in summary["checks"] if not check["passed"]])

    def test_missing_required_decision_fields_fail_closed(self) -> None:
        for field in (
            "compatibility_window",
            "migration_window",
            "release_assets",
            "validation_gates",
            "default_activation",
            "dependencies",
        ):
            with self.subTest(field=field):
                manifest = copy.deepcopy(self.manifest)
                del manifest["decisions"][0][field]
                self.write_fixture(manifest, write_docs=False)
                completed, summary = self.run_gate()
                self.assertNotEqual(0, completed.returncode)
                self.assertFalse(summary["overall_pass"])
                self.assertIn(
                    f"decision:v36-raft-wire-schema:{field.replace('_', '-')}",
                    {check["name"] for check in summary["checks"] if not check["passed"]},
                )

    def test_status_target_policy_and_count_drift_are_rejected(self) -> None:
        mutations = (
            ("status", lambda manifest: manifest["decisions"][0].update(status="draft")),
            ("target", lambda manifest: manifest.update(target_minor="v3.7.0")),
            (
                "policy",
                lambda manifest: manifest.update(default_activation_policy="enabled"),
            ),
            ("count", lambda manifest: manifest["decisions"].pop()),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                manifest = copy.deepcopy(self.manifest)
                mutate(manifest)
                self.write_fixture(manifest)
                completed, summary = self.run_gate()
                self.assertNotEqual(0, completed.returncode)
                self.assertFalse(summary["passed"])
                self.assertEqual("next_minor_decisions", summary["failed_category"])
                self.assertTrue(summary["failed_step"])

    def test_one_decision_cannot_enable_default_activation(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["decisions"][2]["default_activation"] = "enabled"
        self.write_fixture(manifest)

        completed, summary = self.run_gate()

        self.assertNotEqual(0, completed.returncode)
        self.assertFalse(summary["overall_pass"])
        failed = {check["name"] for check in summary["checks"] if not check["passed"]}
        self.assertIn("decision:v36-macos-arm64:default-activation", failed)

    def test_one_decision_cannot_be_replaced_outside_accepted_scope(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["decisions"][1]["id"] = "v36-unplanned-feature"
        self.write_fixture(manifest)

        completed, summary = self.run_gate()

        self.assertNotEqual(0, completed.returncode)
        self.assertFalse(summary["overall_pass"])
        failed = {check["name"] for check in summary["checks"] if not check["passed"]}
        self.assertIn("manifest:decision-scope-and-order", failed)

    def test_implementation_order_must_be_exact_and_dependencies_must_precede(self) -> None:
        mutations = (
            (
                "duplicate-order",
                lambda manifest: manifest["decisions"][2].update(implementation_order=2),
            ),
            (
                "boolean-order",
                lambda manifest: manifest["decisions"][0].update(implementation_order=True),
            ),
            (
                "forward-dependency",
                lambda manifest: manifest["decisions"][1].update(
                    dependencies=["v36-debug-symbols"]
                ),
            ),
            (
                "unknown-dependency",
                lambda manifest: manifest["decisions"][4].update(
                    dependencies=["not-a-decision"]
                ),
            ),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                manifest = copy.deepcopy(self.manifest)
                mutate(manifest)
                self.write_fixture(manifest)
                completed, summary = self.run_gate()
                self.assertNotEqual(0, completed.returncode)
                self.assertFalse(summary["overall_pass"])
                self.assertTrue(
                    any(
                        check["name"] == "manifest:implementation-order"
                        or check["name"].endswith(":dependency-order")
                        for check in summary["checks"]
                        if not check["passed"]
                    )
                )

    def test_missing_or_incomplete_adr_is_rejected(self) -> None:
        missing_doc = self.root / self.manifest["decisions"][0]["document"]
        missing_doc.unlink()
        completed, summary = self.run_gate()
        self.assertNotEqual(0, completed.returncode)
        self.assertIn(
            "decision:v36-raft-wire-schema:doc-exists",
            {check["name"] for check in summary["checks"] if not check["passed"]},
        )

        self.write_fixture(self.manifest)
        missing_doc.write_text("# identity\n\nv3.6.0\n", encoding="utf-8")
        completed, summary = self.run_gate()
        self.assertNotEqual(0, completed.returncode)
        self.assertIn(
            "decision:v36-raft-wire-schema:doc-contract",
            {check["name"] for check in summary["checks"] if not check["passed"]},
        )

    def test_doc_path_cannot_escape_repository_root(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["decisions"][0]["document"] = "../outside.md"
        self.write_fixture(manifest, write_docs=False)

        completed, summary = self.run_gate()

        self.assertNotEqual(0, completed.returncode)
        failed = {check["name"] for check in summary["checks"] if not check["passed"]}
        self.assertIn("decision:v36-raft-wire-schema:doc-path", failed)

    def test_malformed_manifest_still_writes_failure_summary(self) -> None:
        self.manifest_path.write_text("{not-json", encoding="utf-8")

        completed, summary = self.run_gate()

        self.assertNotEqual(0, completed.returncode)
        self.assertFalse(summary["overall_pass"])
        self.assertEqual("manifest:readable-json-object", summary["failed_step"])
        self.assertEqual(0, summary["decision_count"])


if __name__ == "__main__":
    unittest.main()
