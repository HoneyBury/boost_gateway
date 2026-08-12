from __future__ import annotations

import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from scripts.lib.release_package import (
    extract_archive,
    validate_runtime_dependencies,
    verify_archive,
)


class ReleasePackageLibraryTest(unittest.TestCase):
    def make_archive(self, names: list[str]) -> Path:
        directory = Path(tempfile.mkdtemp())
        archive = directory / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            for name in names:
                payload = b"content\n"
                member = tarfile.TarInfo(name)
                member.size = len(payload)
                bundle.addfile(member, io.BytesIO(payload))
        return archive

    def test_archive_layout_is_available_without_importing_a_cli(self) -> None:
        root = "boost-gateway-v3.6.6-linux-x64"
        archive = self.make_archive(
            [f"{root}/{name}" for name in ("README.md", "CHANGELOG.md", "LICENSE")]
        )

        self.assertEqual([], verify_archive(archive, root))

    def test_safe_extraction_rejects_parent_traversal(self) -> None:
        archive = self.make_archive(["../outside"])
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, "unsafe archive member"):
                extract_archive(archive, Path(temporary))

    def test_runtime_dependency_validation_is_shared_without_cli_coupling(self) -> None:
        output = """
            linux-vdso.so.1 (0x1)
            libstdc++.so.6 => /lib/libstdc++.so.6 (0x2)
            libc.so.6 => /lib/libc.so.6 (0x3)
            /lib64/ld-linux-x86-64.so.2 (0x4)
        """

        self.assertEqual(
            ["libc.so.6", "libstdc++.so.6"],
            validate_runtime_dependencies(Path("service"), output),
        )


if __name__ == "__main__":
    unittest.main()
