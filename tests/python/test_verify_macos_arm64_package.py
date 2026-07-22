#!/usr/bin/env python3
"""Contract coverage for the native macOS ARM64 package summary."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "tools" / "verify_macos_arm64_package.py"
SPEC = importlib.util.spec_from_file_location("verify_macos_arm64_package", SCRIPT)
assert SPEC and SPEC.loader
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class VerifyMacosArm64PackageTests(unittest.TestCase):
    def test_success_summary_uses_standard_v2_pass_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            install_root = Path(raw)
            (install_root / "bin").mkdir()
            (install_root / "lib").mkdir()
            (install_root / "bin" / "example_hello_world").write_bytes(b"binary")
            (install_root / "lib" / "libboost_gateway_sdk.dylib").write_bytes(b"library")

            library = mock.Mock()
            library.gsdk_version.return_value = b"4.2.0"

            def run(command: list[str], **_: object) -> str:
                if command[0] == "file":
                    return f"{command[1]}: Mach-O 64-bit executable arm64"
                if command[:2] == ["otool", "-L"]:
                    return f"{command[2]}:\n\t/usr/lib/libSystem.B.dylib"
                if command[:3] == ["xcrun", "clang", "--version"]:
                    return "Apple clang version 21.0.0"
                if command[:2] == ["xcrun", "--show-sdk-version"]:
                    return "26.5"
                return "hello"

            with mock.patch.object(VERIFY.platform, "system", return_value="Darwin"), \
                 mock.patch.object(VERIFY.platform, "machine", return_value="arm64"), \
                 mock.patch.object(VERIFY.platform, "mac_ver", return_value=("26.5.2", (), "")), \
                 mock.patch.object(VERIFY, "run", side_effect=run), \
                 mock.patch.object(VERIFY, "verify_consumer", return_value={"cmake_find_package": True}), \
                 mock.patch.object(VERIFY.ctypes, "CDLL", return_value=library):
                summary = VERIFY.verify(install_root, None, "a" * 40)

            self.assertEqual(summary["summary_version"], 2)
            self.assertTrue(summary["overall_pass"])
            self.assertTrue(summary["passed"])
            self.assertEqual(summary["failed_category"], "")
            self.assertEqual(summary["failed_step"], "")
            self.assertEqual(summary["production_platform"], "macos-arm64")


if __name__ == "__main__":
    unittest.main()
