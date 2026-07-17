#!/usr/bin/env python3
"""Verify the portable layout and required metadata of a release tarball."""

from __future__ import annotations

import argparse
import tarfile
from pathlib import Path, PurePosixPath


REQUIRED_ROOT_FILES = {"README.md", "CHANGELOG.md", "LICENSE"}


def verify_archive(archive: Path, expected_root: str) -> list[str]:
    if not expected_root or "/" in expected_root or expected_root in {".", ".."}:
        return [f"unsafe expected root: {expected_root!r}"]

    failures: list[str] = []
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            paths = [PurePosixPath(member.name) for member in bundle.getmembers() if member.name]
    except (OSError, tarfile.TarError) as exc:
        return [f"archive is not a readable gzip-compressed tarball: {exc}"]

    if not paths:
        return ["archive is empty"]

    roots = {path.parts[0] for path in paths if path.parts}
    if roots != {expected_root}:
        failures.append(f"expected one top-level directory {expected_root!r}, found {sorted(roots)!r}")
    if "dist" in roots:
        failures.append("archive must not expose the build workspace dist directory")

    names = {path.as_posix().rstrip("/") for path in paths}
    for filename in sorted(REQUIRED_ROOT_FILES):
        expected = f"{expected_root}/{filename}"
        if expected not in names:
            failures.append(f"missing required release metadata: {expected}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-root", required=True)
    args = parser.parse_args()

    failures = verify_archive(args.archive, args.expected_root)
    if failures:
        print("release archive layout: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"release archive layout: PASS ({args.archive})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
