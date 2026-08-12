#!/usr/bin/env python3
"""Manage immutable single-node release deployment lifecycle transactions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.release_deployment_core import *  # noqa: E402,F403
from scripts.lib.release_deployment_executor import (  # noqa: E402
    SystemLifecycleExecutor,
    lifecycle_lock,
)
from scripts.lib.release_deployment_recovery import RecoveryMixin  # noqa: E402
from scripts.lib.release_deployment_install import InstallMixin  # noqa: E402
from scripts.lib.release_deployment_transaction import TransactionMixin  # noqa: E402
from scripts.lib.release_deployment_activation import ActivationMixin  # noqa: E402
from scripts.lib.release_deployment_commands import CommandsMixin  # noqa: E402


class ReleaseDeploymentManager(
    RecoveryMixin,
    InstallMixin,
    TransactionMixin,
    ActivationMixin,
    CommandsMixin,
):
    """Coordinate governed install, activation, recovery, and status operations."""

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    install = subparsers.add_parser("install")
    install.add_argument("--release-dir", type=Path, required=True)
    install.add_argument("--image-env", type=Path, required=True)
    install.add_argument("--release-summary", type=Path, required=True)
    install.add_argument("--image-summary", type=Path, required=True)
    install.add_argument("--config-dir", type=Path)
    for command in ("deploy", "upgrade"):
        child = subparsers.add_parser(command)
        child.add_argument("--deployment-id", required=True)
    reconcile = subparsers.add_parser(
        "reconcile-recovery",
        help="resolve one blocking recovery_failed transaction from governed evidence",
    )
    reconcile.add_argument("--transaction-id", required=True)
    reconcile.add_argument("--resolution-summary", type=Path, required=True)
    reconcile.add_argument(
        "--allow-legacy-redis-hardening-bridge",
        action="store_true",
        help="accept only the exact pre-hardening RDB Redis contract for this reconciliation",
    )
    subparsers.add_parser("rollback")
    subparsers.add_parser("status")
    subparsers.add_parser("verify")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        guard_target_host()
        layout = Layout()
        manager = ReleaseDeploymentManager(layout, SystemLifecycleExecutor(layout))
        if args.command == "install":
            result = manager.install(
                args.release_dir,
                args.image_env,
                args.release_summary,
                args.image_summary,
                args.config_dir,
            )
        elif args.command == "deploy":
            result = manager.deploy(args.deployment_id)
        elif args.command == "upgrade":
            result = manager.upgrade(args.deployment_id)
        elif args.command == "rollback":
            result = manager.rollback()
        elif args.command == "reconcile-recovery":
            result = manager.reconcile_recovery(
                args.transaction_id,
                args.resolution_summary,
                allow_legacy_redis_hardening_bridge=(
                    args.allow_legacy_redis_hardening_bridge
                ),
            )
        elif args.command == "verify":
            result = manager.verify_current()
        else:
            result = manager.status()
    except (OSError, LifecycleError, subprocess.SubprocessError) as exc:
        print(f"release lifecycle: FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
