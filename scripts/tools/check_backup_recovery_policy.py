#!/usr/bin/env python3
"""Validate the repository-only TODO-0012 backup and Redis candidate contract."""

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
from pathlib import Path

from scripts.lib.backup_recovery_policy import *  # noqa: E402,F401,F403

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--redis-profile", type=Path)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()

    policy_path = args.policy if args.policy.is_absolute() else ROOT / args.policy
    profile_path = args.redis_profile
    if profile_path is not None and not profile_path.is_absolute():
        profile_path = ROOT / profile_path
    summary_path = (
        args.summary_path
        if args.summary_path.is_absolute()
        else ROOT / args.summary_path
    )
    summary = validate_policy(policy_path, profile_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        "backup/recovery policy: "
        f"{'PASS' if summary['passed'] else 'FAIL'} "
        f"({summary['total_checks'] - summary['failed_checks']}/{summary['total_checks']} checks)"
    )
    print("activation_ready: false")
    print(f"summary: {summary_path.resolve()}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
