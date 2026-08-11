from __future__ import annotations

import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.tools import bootstrap_kind_tools


def metadata(payload: bytes) -> dict[str, str]:
    return {
        "version": "test",
        "url": "https://example.invalid/tool",
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


class BootstrapKindToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_install_tool_reuses_checksum_verified_binary(self) -> None:
        payload = b"verified tool"
        destination = self.root / "kind"
        destination.write_bytes(payload)
        with mock.patch.object(
            bootstrap_kind_tools.urllib.request,
            "urlopen",
            side_effect=AssertionError("unexpected download"),
        ):
            installed = bootstrap_kind_tools.install_tool(
                "kind", metadata(payload), self.root
            )

        self.assertEqual(destination, installed)
        self.assertTrue(destination.stat().st_mode & 0o111)

    def test_install_tool_rejects_download_with_wrong_checksum(self) -> None:
        with mock.patch.object(
            bootstrap_kind_tools.urllib.request,
            "urlopen",
            return_value=io.BytesIO(b"tampered"),
        ):
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                bootstrap_kind_tools.install_tool(
                    "kubectl", metadata(b"expected"), self.root
                )

        self.assertFalse((self.root / ".kubectl.download").exists())


if __name__ == "__main__":
    unittest.main()
