#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


def require_cmd(name: str) -> None:
    if not shutil.which(name):
        raise SystemExit(f"missing required command: {name}")


def run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def capture(cmd: list[str], cwd: Path) -> str:
    return subprocess.check_output(cmd, cwd=cwd, text=True)


def ensure_namespace(namespace: str, cwd: Path) -> None:
    result = subprocess.run(
        ["kubectl", "get", "namespace", namespace],
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        run(["kubectl", "create", "namespace", namespace], cwd)


def delete_existing_sample(namespace: str, cwd: Path) -> None:
    result = subprocess.run(
        ["kubectl", "-n", namespace, "get", "boostgatewaycluster/demo-cluster"],
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        run(["kubectl", "-n", namespace, "delete", "boostgatewaycluster/demo-cluster"], cwd)
        run([
            "kubectl", "-n", namespace, "wait", "--for=delete",
            "boostgatewaycluster/demo-cluster", "--timeout=120s",
        ], cwd)


def free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_kind_config(path: Path) -> None:
    http_port = free_tcp_port()
    https_port = free_tcp_port()
    path.write_text(
        "\n".join([
            "kind: Cluster",
            "apiVersion: kind.x-k8s.io/v1alpha4",
            "nodes:",
            "  - role: control-plane",
            "    extraPortMappings:",
            "      - containerPort: 30080",
            f"        hostPort: {http_port}",
            "        listenAddress: \"127.0.0.1\"",
            "        protocol: TCP",
            "      - containerPort: 30443",
            f"        hostPort: {https_port}",
            "        listenAddress: \"127.0.0.1\"",
            "        protocol: TCP",
            "",
        ]),
        encoding="utf-8",
    )


def write_smoke_sample(path: Path, namespace: str, workload_image: str) -> None:
    component_names = ["gateway", "login", "room", "battle", "match", "leaderboard"]
    component_blocks = []
    for name in component_names:
        component_blocks.extend([
            f"  {name}:",
            "    replicas: 1",
            f"    image: {workload_image}",
            "    port: 80",
        ])
    path.write_text(
        "\n".join([
            "apiVersion: gateway.boost.io/v1alpha1",
            "kind: BoostGatewayCluster",
            "metadata:",
            "  name: demo-cluster",
            f"  namespace: {namespace}",
            "spec:",
            "  pullPolicy: IfNotPresent",
            *component_blocks,
            "  tls:",
            "    enabled: false",
            "",
        ]),
        encoding="utf-8",
    )


def build_operator_image(operator_dir: Path, image: str, runtime_image: str) -> None:
    with tempfile.TemporaryDirectory(prefix="boost-operator-image-") as temp_dir:
        context = Path(temp_dir)
        run(["go", "build", "-trimpath", "-o", str(context / "manager"), "./main.go"], operator_dir)
        (context / "Dockerfile").write_text(
            "\n".join([
                f"FROM {runtime_image}",
                "COPY --chmod=0555 manager /manager",
                "USER 65532:65532",
                'ENTRYPOINT ["/manager"]',
                "",
            ]),
            encoding="utf-8",
        )
        run(["docker", "build", "--pull=false", "-t", image, "."], context)


def delete_cluster(cluster_name: str) -> None:
    subprocess.run(["kind", "delete", "cluster", "--name", cluster_name], check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run operator kind smoke test.")
    default_cluster = os.environ.get("KIND_CLUSTER_NAME") or f"boostgateway-operator-smoke-{os.environ.get('GITHUB_RUN_ID', 'local')}"
    parser.add_argument("--cluster-name", default=default_cluster)
    parser.add_argument("--namespace", default="boost-gateway")
    parser.add_argument("--kind-config", type=Path)
    parser.add_argument("--operator-image", default="ghcr.io/honeybury/boostgateway-operator:v0.1.0")
    parser.add_argument("--workload-image", default="nginx:1.27-alpine")
    parser.add_argument("--operator-runtime-image", default="ubuntu:24.04")
    parser.add_argument(
        "--node-image",
        default="kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5",
    )
    parser.add_argument("--keep-cluster", action="store_true")
    parser.add_argument("--sample-manifest", type=Path)
    args = parser.parse_args()

    for cmd in ["docker", "go", "kind", "kubectl", "make"]:
        require_cmd(cmd)

    root = Path(__file__).resolve().parents[2]
    operator_dir = root / "operator" / "boostgateway-operator"

    clusters = subprocess.check_output(["kind", "get", "clusters"], text=True)
    created_cluster = False
    if args.cluster_name not in {line.strip() for line in clusters.splitlines()}:
        with tempfile.TemporaryDirectory(prefix="boost-kind-") as temp_dir:
            kind_config = args.kind_config
            if kind_config is None:
                kind_config = Path(temp_dir) / "kind-config.yaml"
                write_kind_config(kind_config)
            run([
                "kind", "create", "cluster",
                "--name", args.cluster_name,
                "--config", str(kind_config),
                "--image", args.node_image,
            ], operator_dir)
        created_cluster = True
        if not args.keep_cluster:
            atexit.register(delete_cluster, args.cluster_name)

    print(f"kind node image: {args.node_image}")
    build_operator_image(operator_dir, args.operator_image, args.operator_runtime_image)
    run(["kind", "load", "docker-image", args.operator_image, "--name", args.cluster_name], operator_dir)
    run(["docker", "pull", args.workload_image], operator_dir)
    run(["kind", "load", "docker-image", args.workload_image, "--name", args.cluster_name], operator_dir)
    run(["make", "install"], operator_dir)
    run([
        "kubectl", "-n", "boost-gateway-system", "set", "image",
        "deployment/boostgateway-operator-controller-manager", f"manager={args.operator_image}",
    ], operator_dir)
    run(["kubectl", "wait", "--for=condition=Established",
         "crd/boostgatewayclusters.gateway.boost.io", "--timeout=120s"], operator_dir)
    with tempfile.TemporaryDirectory(prefix="boost-sample-") as temp_dir:
        sample_manifest = args.sample_manifest
        if sample_manifest is None:
            sample_manifest = Path(temp_dir) / "boostgatewaycluster.yaml"
            write_smoke_sample(sample_manifest, args.namespace, args.workload_image)
        ensure_namespace(args.namespace, operator_dir)
        delete_existing_sample(args.namespace, operator_dir)
        run(["kubectl", "apply", "-f", str(sample_manifest)], operator_dir)
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

    gateway_deployment = "deployment/demo-cluster-gateway"
    run(["kubectl", "-n", args.namespace, "patch", "boostgatewaycluster/demo-cluster",
         "--type=merge", "-p", '{"spec":{"gateway":{"replicas":2}}}'], operator_dir)
    run(["kubectl", "-n", args.namespace, "rollout", "status", gateway_deployment, "--timeout=180s"], operator_dir)
    run(["kubectl", "-n", args.namespace, "rollout", "restart", gateway_deployment], operator_dir)
    run(["kubectl", "-n", args.namespace, "rollout", "status", gateway_deployment, "--timeout=180s"], operator_dir)
    run(["kubectl", "-n", args.namespace, "rollout", "undo", gateway_deployment], operator_dir)
    run(["kubectl", "-n", args.namespace, "rollout", "status", gateway_deployment, "--timeout=180s"], operator_dir)
    run(["kubectl", "-n", args.namespace, "patch", "boostgatewaycluster/demo-cluster",
         "--type=merge", "-p", '{"spec":{"gateway":{"replicas":1}}}'], operator_dir)
    run(["kubectl", "-n", args.namespace, "rollout", "status", gateway_deployment, "--timeout=180s"], operator_dir)
    run(["kubectl", "-n", "boost-gateway-system", "rollout", "restart",
         "deployment/boostgateway-operator-controller-manager"], operator_dir)
    run(["kubectl", "-n", "boost-gateway-system", "rollout", "status",
         "deployment/boostgateway-operator-controller-manager", "--timeout=180s"], operator_dir)
    run(["kubectl", "-n", args.namespace, "wait",
         "--for=jsonpath={.status.conditions[?(@.type==\"Ready\")].status}=True",
         "boostgatewaycluster/demo-cluster", "--timeout=180s"], operator_dir)

    run(["kubectl", "-n", args.namespace, "get", "deploy,statefulset,svc"], operator_dir)
    run(["kubectl", "-n", args.namespace, "delete", "boostgatewaycluster/demo-cluster"], operator_dir)
    run(["kubectl", "-n", args.namespace, "wait", "--for=delete",
         "boostgatewaycluster/demo-cluster", "--timeout=120s"], operator_dir)
    if created_cluster and not args.keep_cluster:
        delete_cluster(args.cluster_name)
        atexit.unregister(delete_cluster)
    return 0


if __name__ == "__main__":
    sys.exit(main())
