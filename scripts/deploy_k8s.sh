#!/usr/bin/env bash
# Deploy Boost Gateway to Kubernetes.
# Usage:
#   ./scripts/deploy_k8s.sh              # deploy to default context
#   ./scripts/deploy_k8s.sh --dry-run    # validate only (no apply)
#   ./scripts/deploy_k8s.sh --delete     # tear down everything

set -euo pipefail

cd "$(dirname "$0")/.."

K8S_DIR="env/k8s"
DRY_RUN=""
ACTION="apply"

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN="--dry-run=client" ;;
        --delete)  ACTION="delete" ;;
        *) echo "Unknown arg: $arg"; exit 1 ;;
    esac
done

KUBECTL="kubectl $ACTION"

if [ -n "$DRY_RUN" ]; then
    KUBECTL="$KUBECTL $DRY_RUN"
fi

echo "=== Boost Gateway K8s: ${ACTION} ==="

# 1. Namespace (create first, ignore if exists)
if [ "$ACTION" = "apply" ]; then
    kubectl apply -f "$K8S_DIR/namespace.yaml"
    echo "[✓] namespace"
fi

if [ "$ACTION" = "delete" ]; then
    echo "Tearing down all resources..."
    for f in "$K8S_DIR"/*-deployment.yaml "$K8S_DIR"/redis-deployment.yaml; do
        [ -f "$f" ] && kubectl delete -f "$f" --ignore-not-found=true || true
    done
    kubectl delete -f "$K8S_DIR/namespace.yaml" --ignore-not-found=true || true
    echo "[✓] all resources deleted"
    exit 0
fi

# 2. Deploy in dependency order
DEPLOY_ORDER=(
    "redis-deployment.yaml"
    "login-backend-deployment.yaml"
    "room-backend-deployment.yaml"
    "battle-backend-deployment.yaml"
    "matchmaking-backend-deployment.yaml"
    "leaderboard-backend-deployment.yaml"
    "gateway-deployment.yaml"
)

for f in "${DEPLOY_ORDER[@]}"; do
    path="$K8S_DIR/$f"
    if [ -f "$path" ]; then
        $KUBECTL -f "$path"
        echo "[✓] $(basename "$f")"
    else
        echo "[!] missing: $path"
    fi
done

echo ""
echo "=== Deploy complete ==="
echo ""
echo "  Check status:   kubectl -n boost-gateway get pods"
echo "  Gateway health: kubectl -n boost-gateway port-forward svc/gateway 9080:9080"
echo "                  curl http://localhost:9080/health"
echo "  Redis:          kubectl -n boost-gateway port-forward svc/redis 6379:6379"
echo ""
echo "  Full stack with monitoring: docker compose up -d"
