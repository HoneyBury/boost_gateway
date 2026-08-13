#!/usr/bin/env python3
"""Restore a Mac-exported Redis bundle into a new isolated Docker volume."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        parent for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "__init__.py").is_file()
    )
    sys.path.insert(0, str(repo_import_root))

import argparse
import json
import sys
from pathlib import Path

from scripts.lib.isolated_restore import *  # noqa: E402,F401,F403

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore-id", required=True)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--redis-profile", type=Path, required=True)
    parser.add_argument("--target-volume", required=True)
    parser.add_argument("--baseline-container", required=True)
    parser.add_argument("--target-container", required=True)
    parser.add_argument("--active-volume", default=DEFAULT_ACTIVE_VOLUME)
    parser.add_argument("--redis-image", required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    parser.add_argument("--required-seed-key", action="append", default=[])
    parser.add_argument(
        "--allow-local-bundle-without-transport-receipt",
        action="store_true",
        help="allow a directly controlled local bundle; governed remote drills must not use this",
    )
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--docker", default="/usr/bin/docker")
    parser.add_argument("--rto-seconds", type=float, default=600.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run_isolated_restore(
            restore_id=args.restore_id,
            bundle_dir=args.bundle_dir,
            policy_path=args.policy,
            redis_profile_path=args.redis_profile,
            target_volume=args.target_volume,
            baseline_container=args.baseline_container,
            target_container=args.target_container,
            active_volume=args.active_volume,
            redis_image=args.redis_image,
            summary_path=args.summary_path,
            required_seed_keys=args.required_seed_key,
            require_transport_receipt=not args.allow_local_bundle_without_transport_receipt,
            lock_path=args.lock_path,
            docker=args.docker,
            rto_seconds=args.rto_seconds,
        )
    except RestoreError as exc:
        print(f"isolated backup restore: FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
