#!/usr/bin/env python3
"""Resolve release naming from CMake metadata and validate tag identity."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


PROJECT_VERSION_RE = re.compile(r"project\(boost_gateway\s+VERSION\s+(\d+\.\d+\.\d+)")


def resolve_project_version(cmake_text: str) -> str:
    match = PROJECT_VERSION_RE.search(cmake_text)
    if match is None:
        raise ValueError("unable to resolve project version from CMakeLists.txt")
    return match.group(1)


def validate_tag(version: str, github_ref: str, github_ref_name: str) -> None:
    expected = f"v{version}"
    if github_ref.startswith("refs/tags/v") and github_ref_name != expected:
        raise ValueError(f"tag {github_ref_name} does not match project version {expected}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cmake", type=Path, default=Path("CMakeLists.txt"))
    parser.add_argument("--github-ref", default="")
    parser.add_argument("--github-ref-name", default="")
    parser.add_argument("--github-env", type=Path, required=True)
    args = parser.parse_args()

    version = resolve_project_version(args.cmake.read_text(encoding="utf-8"))
    validate_tag(version, args.github_ref, args.github_ref_name)
    label = f"v{version}"
    with args.github_env.open("a", encoding="utf-8") as stream:
        stream.write(f"RELEASE_VERSION={version}\nRELEASE_LABEL={label}\n")
    print(f"release version: {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
