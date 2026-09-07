#!/usr/bin/env python3
"""Report one external dead-man status to the fixed Healthchecks.io provider."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        (
            parent
            for parent in Path(__file__).resolve().parents
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
    from scripts.lib.deadman_evidence import stamp
    from scripts.lib.external_deadman import (
        DEFAULT_TIMEOUT_SECONDS,
        DeadmanError,
        report_deadman_signal,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - installed flat layout
    if exc.name not in {"scripts", "scripts.lib", "scripts.lib.external_deadman", "scripts.lib.deadman_evidence"}:
        raise
    from deadman_evidence import stamp
    from external_deadman import (  # type: ignore[no-redef]
        DEFAULT_TIMEOUT_SECONDS,
        DeadmanError,
        report_deadman_signal,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", choices=("success", "failure"), required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--receipt-path", type=Path)
    destination.add_argument("--receipt-directory", type=Path)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="direct HTTPS request timeout; maximum 15 seconds",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = args.receipt_path
        if args.receipt_directory:
            name = stamp().replace("-", "").replace(":", "")
            receipt = args.receipt_directory / f"{name}-{args.status}-{os.urandom(8).hex()}.json"
        report_deadman_signal(
            status=args.status,
            credential_path=args.credential_file,
            receipt_path=receipt,
            timeout_seconds=args.timeout_seconds,
        )
    except (DeadmanError, OSError, ValueError):
        print("external dead-man reporter: FAIL; inspect sanitized event receipt", file=sys.stderr)
        return 1
    print(f"external dead-man reporter: PASS status={args.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
