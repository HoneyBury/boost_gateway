from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from scripts.tools.verify_release_package_consumer import (
    extract_archive,
    inspect_installed_binaries,
    validate_elf_identity,
    validate_image_identity,
)


def test_extract_archive_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        payload = b"bad"
        member = tarfile.TarInfo("../outside")
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))
    with pytest.raises(RuntimeError, match="unsafe archive member"):
        extract_archive(archive, tmp_path / "output")


def test_inspect_installed_binaries_requires_all_elf_executables(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    binary = bin_dir / "v2_gateway_demo"
    binary.write_bytes(b"not-elf")
    binary.chmod(0o755)
    with pytest.raises(RuntimeError, match="expected an ELF executable"):
        inspect_installed_binaries(tmp_path, "linux-x64")


def test_elf_identity_is_bound_to_requested_platform() -> None:
    validate_elf_identity("ELF 64-bit LSB pie executable, x86-64", "linux-x64")
    validate_elf_identity("ELF 64-bit LSB pie executable, ARM aarch64", "linux-arm64")

    with pytest.raises(RuntimeError, match="expected linux-arm64"):
        validate_elf_identity("ELF 64-bit LSB pie executable, x86-64", "linux-arm64")


def test_container_image_is_bound_to_requested_platform() -> None:
    validate_image_identity(["sha256:test", "arm64"], "linux-arm64")

    with pytest.raises(RuntimeError, match="does not match linux-x64"):
        validate_image_identity(["sha256:test", "arm64"], "linux-x64")
