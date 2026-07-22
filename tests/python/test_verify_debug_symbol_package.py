from __future__ import annotations

import sys
import unittest

from scripts.tools import verify_debug_symbol_package


class VerifyDebugSymbolPackageTest(unittest.TestCase):
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
