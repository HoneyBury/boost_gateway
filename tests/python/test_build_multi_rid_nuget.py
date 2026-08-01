import tempfile
import unittest
from pathlib import Path

from scripts.tools import build_multi_rid_nuget


class BuildMultiRidNugetTest(unittest.TestCase):
    def test_parse_native_requires_release_supported_rid_and_format(self):
        with tempfile.TemporaryDirectory() as temp_text:
            root = Path(temp_text)
            files = {"linux-x64": root / "lib-x64.so"}
            files["linux-x64"].write_bytes(b"\x7fELF-x64")

            parsed = build_multi_rid_nuget.parse_native(
                [f"linux-x64={files['linux-x64']}"]
            )

            self.assertEqual(set(build_multi_rid_nuget.RID_PROPERTIES), set(parsed))

    def test_parse_native_rejects_incomplete_set(self):
        with self.assertRaisesRegex(ValueError, "missing native RIDs"):
            build_multi_rid_nuget.parse_native([])


if __name__ == "__main__":
    unittest.main()
