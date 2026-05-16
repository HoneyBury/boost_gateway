#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def require_cmd(name: str) -> None:
    if not shutil.which(name):
        raise SystemExit(f"missing required command: {name}")


def run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def capture(cmd: list[str], cwd: Path) -> str:
    return subprocess.check_output(cmd, cwd=cwd, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run operator kind smoke test.")
    parser.add_argument("--cluster-name", default=os.environ.get("KIND_CLUSTER_NAME", "boostgateway-operator-smoke"))
    parser.add_argument("--namespace", default="boost-gateway")
    args = parser.parse_args()

    for cmd in ["kind", "kubectl", "make"]:
        require_cmd(cmd)

    root = Path(__file__).resolve().parent.parent
    operator_dir = root / "operator" / "boostgateway-operator"

    clusters = subprocess.check_output(["kind", "get", "clusters"], text=True)
    if args.cluster_name not in {line.strip() for line in clusters.splitlines()}:
        run([
            "kind", "create", "cluster",
            "--name", args.cluster_name,
            "--config", str(operator_dir / "hack" / "kind-config.yaml"),
        ], operator_dir)

    run(["make", "install"], operator_dir)
    run(["make", "install-sample"], operator_dir)
    run(["kubectl", "wait", "--for=condition=Established",
         "crd/boostgatewayclusters.gateway.boost.io", "--timeout=120s"], operator_dir)
    run(["kubectl", "-n", "boost-gateway-system", "rollout", "status",
         "deployment/boostgateway-operator-controller-manager", "--timeout=180s"], operator_dir)
    run(["kubectl", "-n", args.namespace, "wait",
         "--for=jsonpath={.status.phase}=Running", "boostgatewaycluster/demo-cluster", "--timeout=180s"], operator_dir)
    run(["kubectl", "-n", args.namespace, "wait",
         "--for=jsonpath={.status.conditions[?(@.type==\"Ready\")].status}=True",
         "boostgatewaycluster/demo-cluster", "--timeout=180s"], operator_dir)
    cluster_json = capture(
        ["kubectl", "-n", args.namespace, "get", "boostgatewaycluster/demo-cluster", "-o", "json"],
        operator_dir,
    )
    status = json.loads(cluster_json).get("status", {})
    conditions = {item.get("type"): item.get("status") for item in status.get("conditions", [])}
    required_conditions = {
        "Ready": "True",
        "Progressing": "False",
        "Degraded": "False",
        "TLSReady": "False",
    }
    for cond_type, expected in required_conditions.items():
        observed = conditions.get(cond_type)
        if observed != expected:
            raise SystemExit(
                f"unexpected {cond_type} condition: expected {expected}, observed {observed}"
            )

    components = {item.get("name"): item for item in status.get("components", [])}
    required_components = ["gateway", "login", "room", "battle", "match", "leaderboard"]
    missing = [name for name in required_components if name not in components]
    if missing:
        raise SystemExit(f"missing status.components entries: {', '.join(missing)}")
    for name in required_components:
        desired = int(components[name].get("desiredReplicas", 0))
        available = int(components[name].get("availableReplicas", 0))
        if desired < 1:
            raise SystemExit(f"component {name} desiredReplicas must be >= 1, got {desired}")
        if available < 1:
            raise SystemExit(f"component {name} availableReplicas must be >= 1, got {available}")

    run(["kubectl", "-n", args.namespace, "get", "deploy,statefulset,svc"], operator_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
