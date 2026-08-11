from __future__ import annotations

import argparse
from pathlib import Path
import tempfile
import unittest

from scripts import run_tests


class RunTestsTest(unittest.TestCase):
    def test_configured_tool_uses_tool_from_cmake_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ctest = root / "configured-ctest"
            ctest.touch()
            (root / "CMakeCache.txt").write_text(
                f"CMAKE_CTEST_COMMAND:INTERNAL={ctest}\n",
                encoding="utf-8",
            )

            self.assertEqual(
                str(ctest), run_tests.configured_tool(root, "CMAKE_CTEST_COMMAND")
            )

    def test_layer_command_uses_configured_ctest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ctest = root / "configured-ctest"
            ctest.touch()
            (root / "CMakeCache.txt").write_text(
                f"CMAKE_CTEST_COMMAND:INTERNAL={ctest}\n",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                preset="default",
                build_dir=str(root),
                layer="unit",
                timeout=300,
                parallel=None,
                verbose=True,
            )

            command = run_tests.build_ctest_command(args, run_tests.LAYERS["unit"])

            self.assertEqual(str(ctest), command[0])
            self.assertEqual(["--test-dir", str(root)], command[1:3])
            self.assertIn("unit", command)

    def test_layer_command_falls_back_to_named_preset(self) -> None:
        args = argparse.Namespace(
            preset="release",
            build_dir=None,
            layer="unit",
            timeout=300,
            parallel=None,
            verbose=False,
        )
        original = run_tests.find_build_dir
        self.addCleanup(setattr, run_tests, "find_build_dir", original)
        run_tests.find_build_dir = lambda _preset: None

        command = run_tests.build_ctest_command(args, run_tests.LAYERS["unit"])

        self.assertEqual(["ctest", "--preset", "release"], command[:3])


if __name__ == "__main__":
    unittest.main()
