from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import dev


class DeveloperCliTest(unittest.TestCase):
    def write_command_inventory(self, root: Path) -> Path:
        inventory = root / "script-inventory.json"
        release_command = (Path("scripts") / "release.py").as_posix()
        inventory.write_text(
            json.dumps(
                {
                    "public_entrypoints": ["scripts/dev.py", release_command],
                    "public_entrypoint_lifecycle": {
                        "scripts/dev.py": {
                            "domain": "contributor",
                            "summary": "Run contributor checks.",
                            "execution_environment": "developer-or-ci",
                            "typical_duration": "minutes",
                            "side_effects": ["runtime-artifacts"],
                            "support_level": "stable",
                            "documentation": ["docs/ONBOARDING.md"],
                        },
                        release_command: {
                            "domain": "release",
                            "summary": "Verify a release candidate.",
                            "execution_environment": "fixed-runner",
                            "typical_duration": "hours",
                            "side_effects": ["runtime-artifacts", "network-access"],
                            "support_level": "controlled",
                            "documentation": ["docs/release-governance.md"],
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        return inventory

    def test_cache_value_reads_configured_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            build_dir = Path(temporary)
            (build_dir / "CMakeCache.txt").write_text(
                "CMAKE_COMMAND:INTERNAL=/opt/cmake/bin/cmake\n",
                encoding="utf-8",
            )

            self.assertEqual(
                "/opt/cmake/bin/cmake", dev.cache_value(build_dir, "CMAKE_COMMAND")
            )

    def test_fresh_clone_build_tree_is_an_informational_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "not-configured"

            diagnostics = dev.doctor_diagnostics(missing)

            build_tree = next(item for item in diagnostics if item.name == "build-tree")
            self.assertTrue(build_tree.passed)
            self.assertIn("fresh clone", build_tree.detail)

    def test_check_uses_current_interpreter_and_includes_contract_tests(self) -> None:
        with mock.patch.object(dev, "run_commands", return_value=0) as run_commands:
            result = dev.run_check()

        self.assertEqual(0, result)
        commands = run_commands.call_args.args[0]
        self.assertIn(("scripts/gates/governance/check_script_inventory.py",), commands)
        self.assertIn(dev.PYTHON_TEST_COMMAND, commands)

    def test_command_catalog_filters_maintainer_domain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inventory = self.write_command_inventory(Path(temporary))
            output = io.StringIO()
            with mock.patch.object(dev, "INVENTORY_PATH", inventory), redirect_stdout(
                output
            ):
                result = dev.run_command_catalog("release", False)

        self.assertEqual(0, result)
        self.assertIn((Path("scripts") / "release.py").as_posix(), output.getvalue())
        self.assertNotIn("scripts/dev.py", output.getvalue())
        self.assertIn("docs/release-governance.md", output.getvalue())
        self.assertIn("side-effects=runtime-artifacts,network-access", output.getvalue())

    def test_command_catalog_json_is_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inventory = self.write_command_inventory(Path(temporary))
            output = io.StringIO()
            with mock.patch.object(dev, "INVENTORY_PATH", inventory), redirect_stdout(
                output
            ):
                result = dev.run_command_catalog(None, True)

        self.assertEqual(0, result)
        document = json.loads(output.getvalue())
        self.assertEqual(1, document["schema_version"])
        self.assertEqual(
            ["scripts/dev.py", (Path("scripts") / "release.py").as_posix()],
            [item["command"] for item in document["commands"]],
        )

    def test_command_catalog_rejects_unknown_domain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inventory = self.write_command_inventory(Path(temporary))
            errors = io.StringIO()
            with mock.patch.object(dev, "INVENTORY_PATH", inventory), redirect_stderr(
                errors
            ):
                result = dev.run_command_catalog("unknown", False)

        self.assertEqual(2, result)
        self.assertIn("choose from: contributor, release", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
