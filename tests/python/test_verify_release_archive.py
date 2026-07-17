from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.tools.verify_release_archive import verify_archive


class VerifyReleaseArchiveTest(unittest.TestCase):
    def make_archive(self, names: list[str]) -> Path:
        directory = Path(tempfile.mkdtemp())
        archive = directory / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            for name in names:
                payload = b"content\n"
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                bundle.addfile(info, io.BytesIO(payload))
        return archive

    def test_accepts_single_version_root_with_metadata(self) -> None:
        root = "boost-gateway-v3.5.1-linux-x64"
        archive = self.make_archive([f"{root}/{name}" for name in ("README.md", "CHANGELOG.md", "LICENSE")])
        self.assertEqual(verify_archive(archive, root), [])

    def test_rejects_dist_prefix_and_missing_license(self) -> None:
        root = "boost-gateway-v3.5.1-linux-x64"
        archive = self.make_archive([f"dist/{root}/README.md", f"dist/{root}/CHANGELOG.md"])
        failures = verify_archive(archive, root)
        self.assertTrue(any("top-level" in failure for failure in failures))
        self.assertTrue(any("dist" in failure for failure in failures))
        self.assertTrue(any("LICENSE" in failure for failure in failures))

    def test_rejects_uncompressed_tar_with_gzip_suffix(self) -> None:
        directory = Path(tempfile.mkdtemp())
        archive = directory / "release.tar.gz"
        with tarfile.open(archive, "w") as bundle:
            payload = b"content\n"
            info = tarfile.TarInfo("boost-gateway-v3.5.1-linux-x64/README.md")
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))
        failures = verify_archive(archive, "boost-gateway-v3.5.1-linux-x64")
        self.assertTrue(any("gzip-compressed" in failure for failure in failures))


if __name__ == "__main__":
    unittest.main()
