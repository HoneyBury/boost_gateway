from __future__ import annotations

import sys
import unittest

from scripts.tools import create_debug_symbol_package, verify_debug_symbol_package


class VerifyDebugSymbolPackageTest(unittest.TestCase):
    def test_native_linux_platform_normalizes_supported_architectures(self) -> None:
        self.assertEqual(
            create_debug_symbol_package.native_linux_platform("Linux", "x86_64"),
            "linux-x64",
        )
        self.assertEqual(
            create_debug_symbol_package.native_linux_platform("Linux", "aarch64"),
            "linux-arm64",
        )

    def test_native_linux_platform_rejects_non_linux(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "requires native Linux"):
            create_debug_symbol_package.native_linux_platform("Darwin", "arm64")

    def test_output_replaces_non_utf8_tool_bytes(self) -> None:
        result = verify_debug_symbol_package.output(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(b'valid\\xfftail')",
            ]
        )

        self.assertEqual(result, "valid\ufffdtail")


if __name__ == "__main__":
    unittest.main()
