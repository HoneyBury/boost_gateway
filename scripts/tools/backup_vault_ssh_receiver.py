#!/usr/bin/env python3
"""Forced-command SSH receiver for the off-host backup vault."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from scripts.tools.manage_backup_recovery import (  # noqa: E402
        BACKUP_ID_RE,
        BackupError,
        canonical_json,
        remote_receipt,
        remote_store,
    )
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    tools = Path(__file__).resolve().parent
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    from manage_backup_recovery import (  # noqa: E402
        BACKUP_ID_RE,
        BackupError,
        canonical_json,
        remote_receipt,
        remote_store,
    )


def parse_original_command(value: str) -> tuple[str, list[str]]:
    try:
        arguments = shlex.split(value, posix=True)
    except ValueError as exc:
        raise BackupError(f"invalid SSH original command: {exc}") from exc
    if arguments[:1] != ["boost-gateway-vault"]:
        raise BackupError("SSH command is outside the backup vault surface")
    if arguments == ["boost-gateway-vault", "store"]:
        return "store", []
    if (
        len(arguments) == 3
        and arguments[1] == "receipt"
        and BACKUP_ID_RE.fullmatch(arguments[2])
    ):
        return "receipt", arguments[2:]
    raise BackupError("SSH command is outside the backup vault surface")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault-root", type=Path, required=True)
    parser.add_argument("--vault-identity-file", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        operation, values = parse_original_command(
            os.environ.get("SSH_ORIGINAL_COMMAND", "")
        )
        if operation == "store":
            result = remote_store(
                args.vault_root, args.vault_identity_file, sys.stdin.buffer
            )
        elif operation == "receipt":
            result = remote_receipt(args.vault_root, values[0])
        sys.stdout.buffer.write(canonical_json(result))
        return 0
    except (BackupError, OSError, ValueError) as exc:
        print(f"backup vault receiver: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
