#!/usr/bin/env python3
"""Verify one encrypted off-host backup without materializing its plaintext tar."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        (
            parent for parent in Path(__file__).resolve().parents
            if (parent / "scripts" / "__init__.py").is_file()
        ),
        None,
    )
    if repo_import_root is not None:
        sys.path.insert(0, str(repo_import_root))

import argparse
import subprocess
import sys
from pathlib import Path

try:
    from scripts.lib.backup_vault import *  # noqa: E402,F401,F403
except ModuleNotFoundError as exc:  # pragma: no cover - installed flat layout
    if exc.name != "scripts":
        raise
    from backup_vault import *  # type: ignore[no-redef]  # noqa: E402,F401,F403

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault-root", type=Path, required=True)
    parser.add_argument("--backup-id", required=True)
    parser.add_argument("--age-identity", type=Path, required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    parser.add_argument("--age", default="age")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--redis-image", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = verify_backup(
            vault_root=args.vault_root,
            backup_id=args.backup_id,
            age_identity=args.age_identity,
            age=args.age,
            docker=args.docker,
            redis_image=args.redis_image,
        )
        write_new(args.summary_path, canonical_json(summary), 0o600)
    except (BackupError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"backup vault verification: FAIL: {exc}", file=sys.stderr)
        return 1
    print("backup vault verification: PASS")
    print(f"summary: {args.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
