from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.gates.governance import (
    verify_release_source_authorization as authorization,
)


class ReleaseSourceAuthorizationTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Release Test")
        self.git("config", "user.email", "release-test@example.com")
        (self.root / "tracked.txt").write_text("main\n", encoding="utf-8")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "initial")
        self.candidate = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", self.candidate)
        self.git("tag", "-a", "v1.2.3", "-m", "v1.2.3", self.candidate)
        self.evidence = self.root / "evidence.json"
        self.write_evidence()

    def git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.root,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return completed.stdout.strip()

    def write_evidence(
        self, *, passed: bool = True, revision: str | None = None
    ) -> None:
        candidate = revision or self.candidate
        payload = {
            "summary_version": 2,
            "overall_pass": passed,
            "passed": passed,
            "provenance": {
                "candidate_revision": candidate,
                "git_commit": candidate,
                "git_ref": "v1.2.3",
                "workflow": "Release / Package & Publish",
                "run_id": "123",
                "runner": "test-runner",
                "build_configuration": "Release",
                "conan_lockfile": "conan/locks/test.lock",
                "conan_lockfile_sha256": "a" * 64,
                "revision_matches_checkout": True,
            },
        }
        self.evidence.write_text(json.dumps(payload), encoding="utf-8")

    def evaluate(
        self,
        *,
        event_name: str = "push",
        github_ref: str = "refs/tags/v1.2.3",
        github_ref_name: str = "v1.2.3",
        evidence_paths: list[Path] | None = None,
    ) -> tuple[set[str], dict[str, object]]:
        checks, resolved = authorization.evaluate_authorization(
            self.root,
            event_name=event_name,
            github_ref=github_ref,
            github_ref_name=github_ref_name,
            candidate_revision=self.candidate,
            governed_ref="refs/remotes/origin/main",
            evidence_paths=(
                [self.evidence] if evidence_paths is None else evidence_paths
            ),
        )
        failures = {str(check["name"]) for check in checks if not check["passed"]}
        return failures, resolved

    def test_annotated_main_tag_with_same_revision_evidence_passes(self) -> None:
        failures, resolved = self.evaluate()

        self.assertEqual(set(), failures)
        self.assertEqual(self.candidate, resolved["candidate_revision"])
        self.assertEqual("v1.2.3", resolved["tag"])
        self.assertRegex(resolved["evidence"][0]["sha256"], r"^[0-9a-f]{64}$")

    def test_lightweight_tag_fails_closed(self) -> None:
        self.git("tag", "v1.2.4", self.candidate)

        failures, _ = self.evaluate(
            github_ref="refs/tags/v1.2.4", github_ref_name="v1.2.4"
        )

        self.assertIn("tag:annotated", failures)

    def test_candidate_outside_governed_main_fails(self) -> None:
        self.git("checkout", "-b", "unreviewed")
        (self.root / "tracked.txt").write_text("unreviewed\n", encoding="utf-8")
        self.git("commit", "-am", "unreviewed")
        self.candidate = self.git("rev-parse", "HEAD")
        self.git("tag", "-a", "v2.0.0", "-m", "v2.0.0", self.candidate)
        self.write_evidence()

        failures, _ = self.evaluate(
            github_ref="refs/tags/v2.0.0", github_ref_name="v2.0.0"
        )

        self.assertIn("main:candidate-is-ancestor", failures)

    def test_missing_governed_main_ref_fails_closed(self) -> None:
        self.git("update-ref", "-d", "refs/remotes/origin/main")

        failures, _ = self.evaluate()

        self.assertIn("main:ref-present", failures)
        self.assertIn("main:candidate-is-ancestor", failures)

    def test_missing_or_failed_evidence_fails(self) -> None:
        failures, _ = self.evaluate(evidence_paths=[])
        self.assertIn("evidence:required", failures)

        self.write_evidence(passed=False)
        failures, _ = self.evaluate()
        self.assertIn("evidence:1:evidence.json:passed", failures)

    def test_cross_revision_evidence_fails(self) -> None:
        self.write_evidence(revision="b" * 40)

        failures, _ = self.evaluate()

        self.assertIn("evidence:1:evidence.json:provenance", failures)

    def test_manual_dispatch_requires_current_main(self) -> None:
        failures, _ = self.evaluate(
            event_name="workflow_dispatch",
            github_ref="refs/heads/main",
            github_ref_name="main",
        )
        self.assertEqual(set(), failures)

        self.git("checkout", "main")
        (self.root / "tracked.txt").write_text("new main\n", encoding="utf-8")
        self.git("commit", "-am", "new main")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("checkout", "--detach", self.candidate)
        failures, _ = self.evaluate(
            event_name="workflow_dispatch",
            github_ref="refs/heads/main",
            github_ref_name="main",
        )

        self.assertIn("dispatch:current-main", failures)

    def test_push_from_branch_fails_event_contract(self) -> None:
        failures, _ = self.evaluate(
            event_name="push",
            github_ref="refs/heads/main",
            github_ref_name="main",
        )

        self.assertIn("ref:event-contract", failures)

    def test_cli_writes_passing_audit_summary(self) -> None:
        summary = self.root / "authorization-summary.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(authorization.__file__)),
                "--root",
                str(self.root),
                "--event-name",
                "push",
                "--github-ref",
                "refs/tags/v1.2.3",
                "--github-ref-name",
                "v1.2.3",
                "--candidate-revision",
                self.candidate,
                "--governed-ref",
                "refs/remotes/origin/main",
                "--evidence-summary",
                str(self.evidence),
                "--summary-path",
                str(summary),
            ],
            cwd=self.root,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        payload = json.loads(summary.read_text(encoding="utf-8"))
        self.assertTrue(payload["overall_pass"])
        self.assertEqual("release_source_authorization", payload["gate"])


if __name__ == "__main__":
    unittest.main()
