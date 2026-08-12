from __future__ import annotations

import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from scripts.tools.verify_release_package_consumer import (
    extract_archive,
    inspect_installed_binaries,
    validate_elf_identity,
    validate_image_identity,
)


class VerifyReleasePackageConsumerTest(unittest.TestCase):
    def test_extract_archive_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "unsafe.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                payload = b"bad"
                member = tarfile.TarInfo("../outside")
                member.size = len(payload)
                bundle.addfile(member, io.BytesIO(payload))
            with self.assertRaisesRegex(RuntimeError, "unsafe archive member"):
                extract_archive(archive, root / "output")

    def test_inspect_installed_binaries_requires_all_elf_executables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            binary = bin_dir / "v2_gateway_demo"
            binary.write_bytes(b"not-elf")
            binary.chmod(0o755)
            with self.assertRaisesRegex(RuntimeError, "expected an ELF executable"):
                inspect_installed_binaries(root, "linux-x64")

    def test_elf_identity_is_bound_to_requested_platform(self) -> None:
        validate_elf_identity("ELF 64-bit LSB pie executable, x86-64", "linux-x64")
        validate_elf_identity(
            "ELF 64-bit LSB pie executable, ARM aarch64", "linux-arm64"
        )
        with self.assertRaisesRegex(RuntimeError, "expected linux-arm64"):
            validate_elf_identity(
                "ELF 64-bit LSB pie executable, x86-64", "linux-arm64"
            )

    def test_container_image_is_bound_to_requested_platform(self) -> None:
        validate_image_identity(["sha256:test", "arm64"], "linux-arm64")
        with self.assertRaisesRegex(RuntimeError, "does not match linux-x64"):
            validate_image_identity(["sha256:test", "arm64"], "linux-x64")


if __name__ == "__main__":
    unittest.main()
