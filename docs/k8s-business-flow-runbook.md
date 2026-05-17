# Kubernetes Business Flow Runbook

更新时间：2026-05-18

本文档对应生产业务闭环 P7。Docker Compose 是当前本机生产演练主链；Kubernetes / Operator 是可选发布面和固定 runner 证据路径。

## Manifest 发布

基础部署：

```bash
kubectl apply -f env/k8s/namespace.yaml
kubectl apply -f env/k8s/redis-deployment.yaml
kubectl apply -f env/k8s/login-backend-deployment.yaml
kubectl apply -f env/k8s/room-backend-deployment.yaml
kubectl apply -f env/k8s/battle-backend-deployment.yaml
kubectl apply -f env/k8s/matchmaking-backend-deployment.yaml
kubectl apply -f env/k8s/leaderboard-backend-deployment.yaml
kubectl apply -f env/k8s/gateway-deployment.yaml
```

等待：

```bash
kubectl -n boost-gateway rollout status deploy/gateway
kubectl -n boost-gateway rollout status deploy/login-backend
kubectl -n boost-gateway rollout status deploy/room-backend
kubectl -n boost-gateway rollout status deploy/battle-backend
kubectl -n boost-gateway rollout status deploy/matchmaking-backend
kubectl -n boost-gateway rollout status deploy/leaderboard-backend
```

## SDK Full-flow

对已部署的 gateway Service 跑真实 SDK full-flow：

```bash
python3 scripts/verify_k8s_full_flow.py \
  --build-dir build/default \
  --skip-build \
  --namespace boost-gateway \
  --service gateway
```

脚本会：

- 等待 `deployment/gateway` rollout。
- `kubectl port-forward svc/gateway <local>:9201 <local>:9080`。
- 运行 `sdk_full_flow_client`。
- 查询 `/health` 和 `/metrics/diagnostics/json`。
- 确认 login、room、battle、matchmaking、leaderboard backend metrics 均有请求。

产物：

- `runtime/validation/k8s-full-flow-summary.json`

## Operator Kind

Operator 仍通过控制面 gate 验证：

```bash
python3 scripts/verify_control_plane_gate.py --include-kind
```

该路径验证 CRD、RBAC、fake-client/unit tests、kind smoke、`Ready/Progressing/Degraded/TLSReady` 条件、六组件 `status.components[]` 和 sample CR 删除。

P5-P8 聚合入口可显式启用：

```bash
python3 scripts/verify_p5_p8_business_closure.py \
  --build-dir build/default \
  --skip-build \
  --include-operator-kind \
  --include-k8s-full-flow
```

需要注意：`--include-k8s-full-flow` 要求已有可访问的 Kubernetes 集群和已部署镜像；默认本地 smoke 不强制启用。
