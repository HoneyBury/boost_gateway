#!/usr/bin/env python3
"""Verify the portable layout and required metadata of a release tarball."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "__init__.py").is_file()
    )
    sys.path.insert(0, str(repo_import_root))

import argparse
from pathlib import Path

from scripts.lib.release_package import verify_archive



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
