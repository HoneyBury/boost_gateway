from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from scripts.tools.verify_release_package_consumer import extract_archive, inspect_installed_binaries


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
        inspect_installed_binaries(tmp_path)
