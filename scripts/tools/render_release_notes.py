#!/usr/bin/env python3
"""Extract one released version section from CHANGELOG.md."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def extract_release_notes(changelog: str, version: str) -> str:
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"invalid release version: {version!r}")

    heading = re.compile(rf"^## v{re.escape(version)}(?:\s|$)", re.MULTILINE)
    match = heading.search(changelog)
    if match is None:
        raise ValueError(f"CHANGELOG.md has no v{version} section")

    next_heading = re.search(r"^## v\d+\.\d+\.\d+(?:\s|$)", changelog[match.end() :], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(changelog)
    notes = changelog[match.start() : end].strip()
    if len(notes.splitlines()) < 4:
        raise ValueError(f"v{version} release notes are incomplete")
    return notes + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    notes = extract_release_notes(args.changelog.read_text(encoding="utf-8"), args.version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(notes, encoding="utf-8")
    print(f"release notes: v{args.version} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
