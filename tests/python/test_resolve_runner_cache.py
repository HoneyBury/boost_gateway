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
    def write_profile(
        self,
        directory: str,
        compiler_version: str,
        *,
        compiler: str = "gcc",
        executable: str = "/usr/bin/gcc-13",
    ) -> Path:
        profile = Path(directory) / "profile"
        profile.write_text(
            "[settings]\n"
            f"compiler={compiler}\n"
            f"compiler.version={compiler_version}\n"
            "[conf]\n"
            f'tools.build:compiler_executables={{"c":"{executable}","cpp":"{executable}++"}}\n',
            encoding="utf-8",
        )
        return profile

    def test_compiler_identity_uses_profile_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = self.write_profile(temp_dir, "13")
            with mock.patch.object(
                CACHE_TOOL, "required_file", return_value=profile
            ), mock.patch.object(
                CACHE_TOOL, "command_output", return_value="13.4.0"
            ) as command:
                self.assertEqual(
                    CACHE_TOOL.compiler_identity("profile"),
                    CACHE_TOOL.CompilerIdentity(
                        "gcc", "13", "/usr/bin/gcc-13", "13.4.0"
                    ),
                )
                command.assert_called_once_with(
                    ["/usr/bin/gcc-13", "-dumpfullversion", "-dumpversion"]
                )

    def test_compiler_identity_rejects_profile_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = self.write_profile(temp_dir, "13")
            with mock.patch.object(
                CACHE_TOOL, "required_file", return_value=profile
            ), mock.patch.object(CACHE_TOOL, "command_output", return_value="11.4.0"):
                with self.assertRaisesRegex(ValueError, "profile pins GCC 13"):
                    CACHE_TOOL.compiler_identity("profile")

    def test_compiler_identity_supports_apple_clang_profile_compatibility_version(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = self.write_profile(
                temp_dir,
                "17",
                compiler="apple-clang",
                executable="/usr/bin/clang",
            )
            output = "Apple clang version 21.0.0 (clang-2100.1.1.101)\nTarget: arm64-apple-darwin25.5.0"
            with mock.patch.object(
                CACHE_TOOL, "required_file", return_value=profile
            ), mock.patch.object(
                CACHE_TOOL, "command_output", return_value=output
            ) as command:
                self.assertEqual(
                    CACHE_TOOL.compiler_identity("profile"),
                    CACHE_TOOL.CompilerIdentity(
                        "apple-clang", "17", "/usr/bin/clang", "21.0.0"
                    ),
                )
                command.assert_called_once_with(["/usr/bin/clang", "--version"])

    def test_read_os_identity_supports_macos(self) -> None:
        with mock.patch.object(
            CACHE_TOOL.platform, "system", return_value="Darwin"
        ), mock.patch.object(
            CACHE_TOOL.platform, "mac_ver", return_value=("26.5.2", ("", "", ""), "")
        ):
            self.assertEqual(CACHE_TOOL.read_os_identity(), ("macos", "26.5.2"))

    def test_macos_platform_namespace_uses_actual_apple_clang_version(self) -> None:
        compiler = CACHE_TOOL.CompilerIdentity(
            "apple-clang", "17", "/usr/bin/clang", "21.0.0"
        )
        self.assertEqual(
            CACHE_TOOL.build_platform_namespace(
                "macos", "26.5.2", compiler, "arm64", "Release"
            ),
            "macos-26.5.2-apple-clang21.0.0-arm64-release",
        )

    def test_linux_platform_namespace_remains_backward_compatible(self) -> None:
        compiler = CACHE_TOOL.CompilerIdentity("gcc", "13", "/usr/bin/gcc-13", "13.4.0")
        self.assertEqual(
            CACHE_TOOL.build_platform_namespace(
                "ubuntu", "22.04", compiler, "x64", "Release"
            ),
            "ubuntu-22.04-gcc13.4.0-x64-release",
        )

    def test_linux_aarch64_normalizes_to_arm64(self) -> None:
        with mock.patch.object(CACHE_TOOL.platform, "machine", return_value="aarch64"):
            self.assertEqual(CACHE_TOOL.normalized_architecture(), "arm64")


if __name__ == "__main__":
    unittest.main()
