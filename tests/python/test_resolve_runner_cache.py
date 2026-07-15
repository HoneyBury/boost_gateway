#!/usr/bin/env python3
"""Unit coverage for fixed-runner compiler cache identity."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts/tools/resolve_runner_cache.py"
SPEC = importlib.util.spec_from_file_location("resolve_runner_cache", SCRIPT_PATH)
assert SPEC and SPEC.loader
CACHE_TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CACHE_TOOL)


class ResolveRunnerCacheTests(unittest.TestCase):
    def write_profile(self, directory: str, compiler_version: str) -> Path:
        profile = Path(directory) / "profile"
        profile.write_text(
            "[settings]\n"
            f"compiler.version={compiler_version}\n"
            "[conf]\n"
            'tools.build:compiler_executables={"c":"/usr/bin/gcc-13","cpp":"/usr/bin/g++-13"}\n',
            encoding="utf-8",
        )
        return profile

    def test_compiler_identity_uses_profile_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = self.write_profile(temp_dir, "13")
            with mock.patch.object(CACHE_TOOL, "required_file", return_value=profile), mock.patch.object(
                CACHE_TOOL, "command_output", return_value="13.4.0"
            ) as command:
                self.assertEqual(CACHE_TOOL.compiler_identity("profile"), ("/usr/bin/gcc-13", "13.4.0"))
                command.assert_called_once_with(["/usr/bin/gcc-13", "-dumpfullversion", "-dumpversion"])

    def test_compiler_identity_rejects_profile_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = self.write_profile(temp_dir, "13")
            with mock.patch.object(CACHE_TOOL, "required_file", return_value=profile), mock.patch.object(
                CACHE_TOOL, "command_output", return_value="11.4.0"
            ):
                with self.assertRaisesRegex(ValueError, "profile pins GCC 13"):
                    CACHE_TOOL.compiler_identity("profile")


if __name__ == "__main__":
    unittest.main()
