#!/usr/bin/env python3
"""Forced-command receiver for create-only restore bundles."""

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
import os
import sys
from pathlib import Path

try:
    from scripts.lib.restore_bundle_transport import *  # noqa: E402,F401,F403
except ModuleNotFoundError as exc:  # pragma: no cover - installed flat layout
    if exc.name != "scripts":
        raise
    from restore_bundle_transport import *  # type: ignore[no-redef]  # noqa: E402,F401,F403

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--receiver-identity-file", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        operation, values = parse_original_command(
            os.environ.get("SSH_ORIGINAL_COMMAND", "")
        )
        result = (
            store_bundle(
                args.staging_root, args.receiver_identity_file, sys.stdin.buffer
            )
            if operation == "store"
            else read_receipt(args.staging_root, args.receiver_identity_file, values[0])
        )
        sys.stdout.buffer.write(canonical_json(result))
        return 0
    except (RestoreTransportError, OSError, ValueError) as exc:
        print(f"restore bundle receiver: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
