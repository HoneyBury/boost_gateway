#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEPLOY_ORDER = [
    "redis-deployment.yaml",
    "login-backend-deployment.yaml",
    "room-backend-deployment.yaml",
    "battle-backend-deployment.yaml",
    "matchmaking-backend-deployment.yaml",
    "leaderboard-backend-deployment.yaml",
    "gateway-deployment.yaml",
]


def run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def kubectl_action(action: str, file_path: Path, root: Path, dry_run: bool) -> None:
    cmd = ["kubectl", action]
    if dry_run and action == "apply":
        cmd = ["kubectl", "create", "--dry-run=client", "--validate=false"]
    cmd.extend(["-f", str(file_path)])
    run(cmd, root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy Boost Gateway to Kubernetes.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delete", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    k8s_dir = root / "env" / "k8s"
    action = "delete" if args.delete else "apply"
    display_action = "dry-run" if args.dry_run and action == "apply" else action

    print(f"=== Boost Gateway K8s: {display_action} ===")

    if action == "apply":
        kubectl_action(action, k8s_dir / "namespace.yaml", root, args.dry_run)
        print("[OK] namespace")
    else:
        print("Tearing down all resources...")
        for path in DEPLOY_ORDER:
            file_path = k8s_dir / path
            if file_path.exists():
                subprocess.run(["kubectl", "delete", "-f", str(file_path), "--ignore-not-found=true"], cwd=root, check=False)
        subprocess.run(["kubectl", "delete", "-f", str(k8s_dir / "namespace.yaml"), "--ignore-not-found=true"], cwd=root, check=False)
        print("[OK] all resources deleted")
        return 0

    for path in DEPLOY_ORDER:
        file_path = k8s_dir / path
        if not file_path.exists():
            print(f"[!] missing: {file_path}")
            continue
        kubectl_action(action, file_path, root, args.dry_run)
        print(f"[OK] {path}")

    print("\n=== Deploy complete ===\n")
    print("  Check status:   kubectl -n boost-gateway get pods")
    print("  Gateway health: kubectl -n boost-gateway port-forward svc/gateway 9080:9080")
    print("                  curl http://localhost:9080/health")
    print("  Redis:          kubectl -n boost-gateway port-forward svc/redis 6379:6379")
    print("  Full stack with monitoring: docker compose up -d")
    return 0


if __name__ == "__main__":
    sys.exit(main())
