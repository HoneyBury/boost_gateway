# v3.x 环境依赖与生产就绪状态

> 状态：持续收口
> 版本基线：v3.4.0

## 1. 当前环境组件状态

| 组件 | 配置/脚本 | 代码集成 | 当前状态 |
|---|---|---|---|
| Redis | `docker-compose` / `env/redis/redis.conf` | `RedisClient` / `RedisConnectionPool` / `RedisEventStore` / `RedisLeaderboard` | 已接入 |
| Docker | `Dockerfile*` / `env/docker/` / `scripts/build_docker.sh` | 6 服务 + Redis + 监控 | 已接入 |
| K8s 部署 | `env/k8s/*.yaml` / `scripts/deploy_k8s.sh` | 6 服务 Deployment/StatefulSet | 已接入 |
| K8s Operator | `operator/boostgateway-operator/` | controller-runtime controller | 已接入 |
| TLS/mTLS | `scripts/gen_certs.sh` / `config/gateway.json` | `SecurityPolicy` + FeatureFlags | 已接入 |
| OTLP | env `OTEL_EXPORT_ENDPOINT` | `OtlpExporter` | 已接入 |
| Proto generation | `scripts/generate_proto_cpp.ps1` | `generate_v3_proto_cpp` helper target | 已提供入口 |
| Typed envelope helper | `include/v3/proto/envelope_codec.h` | `login/room/battle/match/leaderboard` 后端兼容 | 已接入 |
| CI/CD | `env/cicd/github-actions.yml` | build/test/operator/kind smoke | 已接入但仍需收口平台差异 |

## 2. Operator 当前状态

当前 Operator 已不再只是“文档级骨架”，而是具备真实 controller 行为：

- reconcile `Deployment`
- reconcile `StatefulSet`
- reconcile `Service`
- reconcile `ConfigMap`
- reconcile `Secret`
- reconcile `cert-manager.io/v1 Certificate`
- 写入：
  - `status.phase`
  - `status.readyReplicas`
  - `status.desiredReplicas`
  - `status.components[]`
  - `Ready / Progressing / Degraded / TLSReady`

当前仍待继续强化：

- 更真实的依赖健康判定
- smoke 对 `components[]` / `Degraded` 的更强约束
- Helm/Operator 关系收口

## 3. Proto / transport 当前状态

当前主线不是“纯 generated gRPC”，而是分两层：

1. proto 定义：
   - `proto/v3/common.proto`
   - `proto/v3/login.proto`
   - `proto/v3/room.proto`
   - `proto/v3/battle.proto`
   - `proto/v3/match.proto`
   - `proto/v3/leaderboard.proto`
2. typed helper 过渡层：
   - `include/v3/proto/envelope_codec.h`

当前 helper 已支持：

- `EnvelopeDomain`
- `EnvelopeMessageKind`
- `TypedEnvelope`
- `encode_typed_envelope()`
- `decode_typed_envelope()`
- generated-style helper：
  - `encode_match_join_request()`
  - `encode_leaderboard_submit_request()`

当前后端兼容状态：

- `login`
- `room`
- `battle`
- `match`
- `leaderboard`

都可接受：

- legacy raw JSON payload
- wrapped typed envelope payload

## 4. 当前环境收口重点

1. 修复 CI / 平台差异问题
2. 将 typed helper 推进到真正 generated protobuf/gRPC
3. 继续细化 Operator rollout/dependency health
4. 在真实或近似真实环境中补故障注入与恢复演练

## 5. 当前不应误读的点

- “有 proto 定义” 不等于 “已经完成 generated gRPC transport”
- “有 Operator controller” 不等于 “已经达到生产级 platform control plane”
- “Redis live tests skipped” 在无 Redis 的 CI 环境中是预期行为，不代表功能未接入
