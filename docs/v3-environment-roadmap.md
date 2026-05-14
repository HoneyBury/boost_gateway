# v3.x 环境依赖与生产就绪规划

> 状态: 执行中 | 版本: v3.1.0

## 1. 当前状态

| 组件 | 配置 | 代码集成 | 生产就绪 |
|------|------|---------|---------|
| Redis | docker-compose + K8s + redis.conf | ✅ hiredis + RedisClient + RedisEventStore | 部分 |
| K8s CRD | gameserver-crd.yaml | ✅ k8s_operator_test.cpp 基础框架 | 否 |
| K8s Deploy | gateway/5×backend Deployment | ✅ 6 个独立 Deployment + HPA + PDB | 否 |
| Helm | Chart.yaml + values.yaml | ❌ 未部署验证 | 否 |
| Prometheus | prometheus.yml | ✅ /metrics | 是 |
| Grafana | dashboard.json | ✅ 端点可用 | 是 |
| Docker | Dockerfile × 2 + compose | ✅ 9 服务栈 + build_docker.sh | 是 |
| TLS | tls_config.h | ✅ SecurityPolicy + FeatureFlags 已接入 GatewayServiceBridge | 部分 |
| CI/CD | github-actions.yml | ✅ 基础流水线 | 部分 |

## 2. Phase E1: Redis 集成 ✅ (2026-05-14)

### 目标
Leaderboard 和 EventStore 从内存切换到 Redis 持久化存储。

### 完成内容
- hiredis C 客户端通过 CMake FetchContent 接入
- `RedisClient` PIMPL/RAII C++ 包装（GET/SET/DEL/LPUSH/LRANGE/ZADD/ZRANGE 等）
- `RedisEventStore` 实现 `IEventStore` 接口
- 16 项测试，Redis 不可用时优雅降级（GTEST_SKIP）

### 待完成
- `RedisLeaderboard` 实现（排行榜 Sorted Set 存储）
- `RedisConnectionPool` 连接池

## 3. Phase E2: Docker 生产构建 ✅ (2026-05-14)

### 目标
Docker 镜像可构建、可运行、可通过 compose 编排。

### 完成内容
- Dockerfile.gateway + Dockerfile.backend 多阶段构建（ubuntu:24.04）
- 修复 ENTRYPOINT bug（曾硬编码 login 二进制，现通过 SERVICE_BINARY build-arg 动态选择）
- docker-compose.yml 9 服务栈（gateway + 5 backends + redis + prometheus + grafana）
- scripts/build_docker.sh 全量 + per-service 构建

### 验收
- 所有服务 healthcheck + depends_on service_healthy
- `/health` 端点标准化

## 4. Phase E3: K8s 部署验证 ✅ (2026-05-14)

### 目标
全部 K8s 配置可用，生产级 Deployment。

### 完成内容
- 5 个独立 backend Deployment（login/room/battle/matchmaking/leaderboard）+ gateway Deployment
- 每个 Deployment: ConfigMap + RollingUpdate（maxUnavailable: 0, maxSurge: 1）+ podAntiAffinity + HPA + PDB
- Gateway 完善：matchmaking/leaderboard 后端参数、livenessProbe + readinessProbe
- scripts/deploy_k8s.sh 一键部署

## 5. Phase E4: TLS/mTLS 安全传输 ✅ (2026-05-14)

### 目标
服务间通信加密，支持 mTLS，FeatureFlag 灰度控制。

### 完成内容
- `FeatureFlags` 扩展：`load_from_json()` + `apply_env_overrides()`（env > JSON > default）
- `GatewayServiceBridge` 接入 `SecurityPolicy` + `FeatureFlags`
- `make_options()` 条件填充 `tls_config`
- TLS FeatureFlag 门控（`v3_tls_enabled`）
- `scripts/gen_certs.sh` 自签证书生成
- `config/gateway.json` 新增 `feature_flags`/`tls`/`security_policy` 三段配置
- 安全默认值：全部关闭，需显式开启

### 验收
- 751 tests 通过
- 证书生成脚本可用

## 6. Phase E5: K8s Operator 实现

### 目标
GameServer CRD 的 Controller 实现，自动化运维。

### 技术选型
- Go + controller-runtime 或 Python + kopf
- 打包为 Docker 镜像，部署在集群内

### 功能
- 监听 GameServer CR 变更
- 自动创建/更新 Deployment + Service
- 滚动更新（先排干再更新）
- 水平扩缩容（基于 metrics）

## 7. 版本规划

```
v3.0.0: 分布式运行时核心 ✅ (2026-05-13, 655 tests)
v3.1.0: E1 Redis + E2 Docker + E3 K8s + E4 TLS/mTLS + FeatureFlags ✅ (2026-05-14, 751 tests)
v3.2.0: E5 K8s Operator + RedisLeaderboard + Raft 集群验证
v3.3.0: gRPC 服务端 + 生产部署压测
```
