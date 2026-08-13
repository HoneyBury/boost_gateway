from __future__ import annotations

import argparse
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import run_tests


class RunTestsTest(unittest.TestCase):
    def test_changed_paths_includes_untracked_files(self) -> None:
        completed = [
            mock.Mock(returncode=0, stdout="src/v2/existing.cpp\n"),
            mock.Mock(returncode=0, stdout=""),
            mock.Mock(returncode=0, stdout="sdk/new_client.cpp\n"),
        ]
        with mock.patch.object(run_tests.subprocess, "run", side_effect=completed):
            paths = run_tests.changed_paths("origin/main")

        self.assertEqual(["sdk/new_client.cpp", "src/v2/existing.cpp"], paths)

    def test_document_only_change_recommends_governance_without_ctest_layer(self) -> None:
        layers, reasons = run_tests.recommend_layers(["docs/ONBOARDING.md", "scripts/dev.py"])

        self.assertEqual([], layers)
        self.assertEqual(2, len(reasons))

    def test_sdk_and_gateway_changes_recommend_focused_layers(self) -> None:
        layers, _ = run_tests.recommend_layers(
            ["sdk/src/client.cpp", "src/v2/gateway/gateway_service.cpp"]
        )

        self.assertEqual(["e2e", "integration", "sdk", "unit"], layers)

    def test_unknown_change_fails_safe_to_all(self) -> None:
        layers, _ = run_tests.recommend_layers(["third_party/new-layout.txt"])

        self.assertEqual(["all"], layers)
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
