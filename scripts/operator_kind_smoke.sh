#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OPERATOR_DIR="$ROOT_DIR/operator/boostgateway-operator"
CLUSTER_NAME="${KIND_CLUSTER_NAME:-boostgateway-operator-smoke}"
NAMESPACE="boost-gateway"

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "missing required command: $1" >&2
        exit 1
    fi
}

require_cmd kind
require_cmd kubectl
require_cmd make

if ! kind get clusters | grep -qx "$CLUSTER_NAME"; then
    kind create cluster \
        --name "$CLUSTER_NAME" \
        --config "$OPERATOR_DIR/hack/kind-config.yaml"
fi

cd "$OPERATOR_DIR"

make install
make install-sample

kubectl wait --for=condition=Established \
    crd/boostgatewayclusters.gateway.boost.io \
    --timeout=120s

kubectl -n boost-gateway-system rollout status \
    deployment/boostgateway-operator-controller-manager \
    --timeout=180s

kubectl -n "$NAMESPACE" wait \
    --for=jsonpath='{.status.phase}'=Running \
    boostgatewaycluster/demo-cluster \
    --timeout=180s

kubectl -n "$NAMESPACE" wait \
    --for=jsonpath='{.status.conditions[?(@.type=="Ready")].status}'=True \
    boostgatewaycluster/demo-cluster \
    --timeout=180s

kubectl -n "$NAMESPACE" get boostgatewaycluster/demo-cluster -o jsonpath='{.status.conditions}'
echo

kubectl -n "$NAMESPACE" get deploy,statefulset,svc
