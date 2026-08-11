"""Shared release archive and runtime dependency validation primitives."""

from __future__ import annotations

import tarfile
from pathlib import Path, PurePosixPath


REQUIRED_ROOT_FILES = {"README.md", "CHANGELOG.md", "LICENSE"}
ALLOWED_RUNTIME_LIBRARIES = {
    "libc.so.6",
    "libgcc_s.so.1",
    "libm.so.6",
    "libstdc++.so.6",
}
ALLOWED_RUNTIME_LOADERS = {
    "/lib/ld-linux-aarch64.so.1",
    "/lib64/ld-linux-x86-64.so.2",
}


def verify_archive(archive: Path, expected_root: str) -> list[str]:
    if not expected_root or "/" in expected_root or expected_root in {".", ".."}:
        return [f"unsafe expected root: {expected_root!r}"]

    failures: list[str] = []
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            paths = [
                PurePosixPath(member.name)
                for member in bundle.getmembers()
                if member.name
            ]
    except (OSError, tarfile.TarError) as exc:
        return [f"archive is not a readable gzip-compressed tarball: {exc}"]

    if not paths:
        return ["archive is empty"]

    roots = {path.parts[0] for path in paths if path.parts}
    if roots != {expected_root}:
        failures.append(
            f"expected one top-level directory {expected_root!r}, found {sorted(roots)!r}"
        )
    if "dist" in roots:
        failures.append("archive must not expose the build workspace dist directory")

    names = {path.as_posix().rstrip("/") for path in paths}
    for filename in sorted(REQUIRED_ROOT_FILES):
        expected = f"{expected_root}/{filename}"
        if expected not in names:
            failures.append(f"missing required release metadata: {expected}")
    return failures


def extract_archive(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination):
                raise RuntimeError(f"unsafe archive member: {member.name}")
        bundle.extractall(destination, filter="data")


def validate_runtime_dependencies(binary: Path, ldd_output: str) -> list[str]:
    if "not found" in ldd_output:
        raise RuntimeError(f"{binary}: unresolved runtime dependency\n{ldd_output}")
    libraries: list[str] = []
    for raw_line in ldd_output.splitlines():
        line = raw_line.strip()
        loader = line.split(maxsplit=1)[0] if line else ""
        if (
            not line
            or line.startswith("linux-vdso.so")
            or loader in ALLOWED_RUNTIME_LOADERS
        ):
            continue
        name = line.split(" => ", 1)[0].split()[0]
        libraries.append(name)
    unexpected = sorted(set(libraries) - ALLOWED_RUNTIME_LIBRARIES)
    if unexpected:
        names = ", ".join(unexpected)
        raise RuntimeError(
            f"{binary}: non-system runtime libraries remain ({names}); "
            "the Conan release graph must link third-party dependencies statically"
        )
    return sorted(set(libraries))
