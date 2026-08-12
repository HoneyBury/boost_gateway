from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.gates.governance import check_workflow_catalog as catalog


class WorkflowSupplyChainGovernanceTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write_action(self, reference: str) -> Path:
        path = self.root / "workflow.yml"
        path.write_text(
            "steps:\n  - uses: " + reference + "\n",
            encoding="utf-8",
        )
        return path

    def failures(self, path: Path) -> set[str]:
        return {
            str(check["name"])
            for check in catalog.action_reference_checks([path])
            if not check["passed"]
        }

    def test_reviewed_sha_and_release_comment_pass(self) -> None:
        sha, tag = catalog.REVIEWED_ACTIONS["actions/checkout"]
        path = self.write_action(f"actions/checkout@{sha} # {tag}")

        self.assertEqual(set(), self.failures(path))

    def test_floating_tag_fails_pin_check(self) -> None:
        path = self.write_action("actions/checkout@v4 # v4.4.0")

        self.assertIn("action-pin:workflow.yml:2", self.failures(path))

    def test_unknown_action_fails_allowlist(self) -> None:
        path = self.write_action(
            "unreviewed/example@0123456789abcdef0123456789abcdef01234567 # v1.0.0"
        )

        self.assertIn("action-allowlist:workflow.yml:2", self.failures(path))

    def test_external_action_without_revision_fails(self) -> None:
        path = self.write_action("actions/checkout")

        self.assertIn("action-reference:workflow.yml:2", self.failures(path))

    def test_missing_release_comment_fails(self) -> None:
        sha, _ = catalog.REVIEWED_ACTIONS["actions/checkout"]
        path = self.write_action(f"actions/checkout@{sha}")

        self.assertIn("action-release-comment:workflow.yml:2", self.failures(path))

    def test_top_level_permissions_are_parsed_without_job_permissions(self) -> None:
        text = """permissions:
  contents: read

jobs:
  test:
    permissions:
      contents: write
"""

        self.assertEqual({"contents": "read"}, catalog.top_level_permissions(text))

    def test_machine_catalog_covers_every_workflow(self) -> None:
        payload = catalog.load_catalog()
        rules = catalog.workflow_rules(payload)
        actual = {path.stem for path in catalog.WORKFLOWS_ROOT.glob("*.yml")}

        self.assertEqual(actual, set(rules))
        self.assertTrue(
            all("workflow_dispatch" in rule["triggers"] for rule in rules.values())
        )

    def test_runner_class_is_derived_from_operational_expression(self) -> None:
        self.assertEqual("github-hosted", catalog.expected_runner_class('"ubuntu-latest"'))
        self.assertEqual(
            "native-macos",
            catalog.expected_runner_class('["self-hosted","macOS","ARM64"]'),
        )
        self.assertEqual(
            "self-hosted",
            catalog.expected_runner_class('["self-hosted","Linux","X64"]'),
        )

    def test_shared_summary_action_is_local_and_reused(self) -> None:
        action = catalog.ROOT / ".github/actions/render-validation-summary/action.yml"
        self.assertTrue(action.is_file())
        self.assertIn("compgen -G", action.read_text(encoding="utf-8"))
        references = sum(
            "uses: ./.github/actions/render-validation-summary"
            in path.read_text(encoding="utf-8")
            for path in catalog.WORKFLOWS_ROOT.glob("*.yml")
        )
        self.assertGreaterEqual(references, 6)

    def test_offline_composite_allows_deferred_governed_bootstrap(self) -> None:
        text = """uses: ./.github/actions/setup-cpp-conan
with:
  conan-venv-offline: "true"
  run-bootstrap: "false"
run: python3 scripts/bootstrap_conan.py --conan-home "$CONAN_HOME" --no-remote
"""

        self.assertTrue(catalog.offline_composite_action_is_safe(text))

    def test_offline_composite_rejects_deferred_remote_bootstrap(self) -> None:
        text = """uses: ./.github/actions/setup-cpp-conan
with:
  conan-venv-offline: "true"
  run-bootstrap: "false"
run: python3 scripts/bootstrap_conan.py --conan-home "$CONAN_HOME" --allow-public
"""

        self.assertFalse(catalog.offline_composite_action_is_safe(text))

    def test_fixed_runner_workflows_reuse_governed_conan_setup(self) -> None:
        migrated = {
            "long-soak-capacity.yml": 0,
            "nightly-stability.yml": 0,
            "preprod-evidence.yml": 0,
            "production-candidate-evidence.yml": 1,
            "release.yml": 0,
        }
        for filename, remaining_cache_resolvers in migrated.items():
            text = (catalog.WORKFLOWS_ROOT / filename).read_text(encoding="utf-8")
            with self.subTest(workflow=filename):
                self.assertIn("uses: ./.github/actions/setup-cpp-conan", text)
                self.assertTrue(catalog.offline_composite_action_is_safe(text))
                self.assertNotIn("scripts/tools/ensure_conan_venv.py", text)
                self.assertEqual(
                    remaining_cache_resolvers,
                    text.count("scripts/tools/resolve_runner_cache.py"),
                )


if __name__ == "__main__":
    unittest.main()
