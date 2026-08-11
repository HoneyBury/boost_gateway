from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import dev


class DeveloperCliTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
