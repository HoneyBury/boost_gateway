import tempfile
import unittest
from pathlib import Path

from scripts.tools import build_multi_rid_nuget


class BuildMultiRidNugetTest(unittest.TestCase):
    def test_parse_native_requires_all_supported_rids_and_formats(self):
        with tempfile.TemporaryDirectory() as temp_text:
            root = Path(temp_text)
            files = {
                "linux-x64": root / "lib-x64.so",
                "linux-arm64": root / "lib-arm64.so",
                "osx-arm64": root / "lib-osx.dylib",
            }
            files["linux-x64"].write_bytes(b"\x7fELF-x64")
            files["linux-arm64"].write_bytes(b"\x7fELF-arm64")
            files["osx-arm64"].write_bytes(b"\xcf\xfa\xed\xfe-osx")

            parsed = build_multi_rid_nuget.parse_native(
                [f"{rid}={path}" for rid, path in files.items()]
            )

            self.assertEqual(set(build_multi_rid_nuget.RID_PROPERTIES), set(parsed))

    def test_parse_native_rejects_incomplete_set(self):
        with tempfile.TemporaryDirectory() as temp_text:
            path = Path(temp_text) / "lib.so"
            path.write_bytes(b"\x7fELF")
            with self.assertRaisesRegex(ValueError, "missing native RIDs"):
                build_multi_rid_nuget.parse_native([f"linux-x64={path}"])


if __name__ == "__main__":
    unittest.main()
