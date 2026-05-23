# v3 发布检查清单

> 当前发布门禁入口：`scripts/verify_release_candidate.py`。完整 Release 性能基线入口：`scripts/collect_release_baseline.py`。可靠性证据以 `docs/reliability-matrix.md` 为准。

> 目标：在发布前确认 v3/R4 通信契约、typed envelope 迁移、恢复路径和短性能基线都有可重复验证入口。

## 1. 必跑门禁

| 门禁 | 命令 | 通过标准 |
| --- | --- | --- |
| R4 快速契约 | `python scripts/verify_r4_contract.py --build-dir build/windows-msvc-debug --configuration Debug` | proto contract、聚焦单测、聚合集成测试、短性能基线全部通过，并写出 `runtime/validation/r4-contract-summary.json` |
| CI 快速契约 | `python scripts/verify_r4_contract.py --build-dir <build-dir> --skip-build --skip-arch-baseline` | 不重编译、不跑性能基线，验证 schema 与 R4 聚焦测试；失败时 summary 标出 `failed_category` 和 `failed_step` |
| 安全发布门禁 | `python scripts/check_security_release_gate.py` | 生产模式 dev token fallback 有显式禁用路径，admin 审计最小键与 ACL 边界有证据 |
| P4 可观测性/限流门禁 | `python scripts/verify_observability_gate.py --build-dir <build-dir> --skip-build` | rate limit 全局消息类型/IP/user/login/connection、trace/OTel、backend RED metrics、gateway metrics 和 audit 聚合用例通过，并写出 `runtime/validation/observability-gate-summary.json` |
| P5 控制面门禁 | `python scripts/verify_control_plane_gate.py` | Operator manifest 静态契约与 fake-client Go 测试通过；固定 runner 可显式启用 envtest/kind，kind smoke 断言 status conditions、components 覆盖和样例 CR 删除路径 |
| P5 长稳故障回滚门禁 | `python scripts/verify_production_resilience_gate.py --build-dir <build-dir> --skip-build` | 固定 runner 预检、bounded soak、data recovery、Redis/Raft/Operator failure-path 聚合通过；固定 runner 可追加 Redis live、Operator kind、runtime HTTP、release/capacity baseline |
| 稳定性短 soak | `python scripts/verify_stability_soak.py --build-dir build/windows-msvc-debug --configuration Debug --skip-build --soak-profile short` | I/O accept 策略、WriteBehind drain/failure、backend timeout/recovery、短架构基线全部通过，并写出 `runtime/validation/stability-soak-summary.json` |
| P3 数据恢复门禁 | `python scripts/verify_data_recovery_gate.py --build-dir <build-dir> --skip-build` | replay/result/snapshot、WriteBehind flush/drain、Redis degraded、Raft committed restart replay 与持久化 round trip 全部通过，并写出 `runtime/validation/data-recovery-summary.json` |
| Proto schema | `cmake --build <build-dir> --target check_v3_proto_schema` | v3 proto 文件、包名、核心 message 存在 |
| Transport contract | `cmake --build <build-dir> --target check_v3_proto_transport_contract` | `ServiceEnvelope` 与各 domain oneof 字段覆盖生成传输实验所需字段 |
| Release baseline 聚合入口 | `python scripts/collect_release_baseline.py --build-dir build/windows-ninja-release --configuration Release --perf-preset baseline --perf-repetitions 3` | R4 release contract 与 v2 多进程 `echo/battle` baseline 均通过，并写出 `runtime/validation/release-baseline-summary.json` 与 `runtime/perf/release-baseline/summary.json` |
| 专项 E2E 聚合入口 | `python scripts/verify_specialized_e2e.py --build-dir <build-dir> --skip-build` | Raft 集群/恢复与 Redis 降级路径通过；Redis live 与 Operator kind 通过显式参数启用 |
| P6 生产证据聚合入口 | `python scripts/verify_production_evidence_gate.py --build-dir <build-dir> --skip-build` | 有界 stability、P3 data recovery、Redis/Raft/Operator 专项和生产候选完整性审核全部通过；固定 runner 可追加 Redis live、Operator kind、settlement replay、release/capacity baseline |
| P3 SDK 分发门禁 | `python scripts/check_sdk_distribution.py --build-dir <build-dir>` | SDK 版本、CMake package、C ABI 动态库、Python/C# wrapper、C ABI 测试和构建产物一致 |
| P3 SDK 安装消费 | `python scripts/verify_sdk_package_consumer.py --build-dir <build-dir>` | SDK 可安装到临时 prefix，外部 CMake 项目可 `find_package(boost_gateway_sdk)` 并链接 `boost_gateway::sdk` |
| P4 SDK 业务闭环 | `python scripts/verify_sdk_business_flow.py --build-dir <build-dir>` | SDK C++ 业务 API 跑通 login、echo、room、ready、battle、reconnect、heartbeat、push 与多客户端闭环 |
| P4 SDK 示例联调 | `python scripts/verify_sdk_full_flow_client.py --build-dir <build-dir>` | 启动真实 login/room/battle/matchmaking/leaderboard 后端、`v2_gateway_demo` 和 `sdk_full_flow_client`，验证 SDK full-flow 覆盖新增业务路径与 backend metrics |
| N4 传输安全/配置治理 | `python scripts/check_transport_config_governance.py` | TLS/mTLS profile 边界和 Docker/K8s/Helm 配置漂移检查通过；默认生产仍明确为 plain TCP |
| N5 SDK 企业交付 | `python scripts/verify_sdk_enterprise_delivery.py --build-dir <build-dir> --skip-build` | SDK distribution、package consumer、business-flow 和真实 gateway full-flow 全部通过 |
| N6 gRPC PoC 取舍 | `python scripts/check_v3_grpc_poc_decision.py --build-dir <build-dir>` | v3 proto/transport contract、CMake target、TCP baseline 对照和 ADR 取舍通过；generated gRPC 不进入默认主链 |

## 2. R4 契约状态

| 项目 | 当前状态 | 证据 |
| --- | --- | --- |
| typed envelope adapter | login/room/battle/match/leaderboard 后端主 handler 已接入统一 adapter | `include/v2/service/envelope_adapter.h`、`src/v2/service/envelope_adapter.cpp` |
| legacy raw JSON 兼容 | 保留兼容并标记 deprecation notice | `V2ServiceBoundaryTest.DecodeHandlerPayloadMarksLegacyRawJsonDeprecated` |
| trace/error 传播 | raw bridge 与 typed envelope 路径均有聚合测试 | `ServiceBusIntegrity.GatewayBridgeRoutePropagatesTraceAndErrorCode`、`ServiceBusIntegrity.GatewayBridgeTypedEnvelopePreservesTraceAndError` |
| proto round-trip | 五个业务后端已有 typed envelope 往返测试 | `ServiceBusIntegrity.ProtoEnvelopeRoundTripsThrough*Backend` |
| 连接恢复 | backend config 更新后可恢复路由，超时后旧连接关闭且可恢复 | `ServiceBusIntegrity.GatewayBridgeRecoversAfterBackendConfigUpdate`、`ServiceBusIntegrity.GatewayBridgeTimeoutClosesStaleConnectionAndRecovers` |
| 熔断恢复 | 连续失败后打开 circuit breaker，等待窗口后半开探测成功并闭合 | `ServiceBusIntegrity.GatewayBridgeCircuitBreakerHalfOpenProbeRecovers` |
| WriteBehind drain | flush/destructor 可排空队列，delegate 写失败会进入 failure 统计而不是卡住 | `V2WriteBehindStoreTest.WriteBehindFlushReportsDelegateFailures`、`V2WriteBehindStoreTest.WriteBehindDestructorDrainsLargePendingQueue` |

## 3. 性能基线

短架构基线由 `scripts/collect_v2_arch_baseline.py` 采集，输出到 `runtime/perf/v2-arch-baseline/summary.json`。退化阈值在 `config/perf/v2_arch_baseline_gates.json` 中按 `debug` / `release` profile 管理。当前 R4 门禁默认使用较小参数，目标是证明基线链路和退化 gate 可运行。

多进程业务性能基线由 `scripts/collect_v2_perf_baseline.py` 采集，`baseline` profile 覆盖 `echo-100/1000` 与 `battle-20/100`，`capacity` profile 覆盖 `echo-1000/5000/10000` 与 `battle-100/500`。发布候选入口 `scripts/collect_release_baseline.py` 会聚合 R4 release contract 与多进程业务性能基线。

推荐发布前命令：

```powershell
python scripts/collect_release_baseline.py --build-dir build/windows-ninja-release --configuration Release --perf-preset baseline --perf-repetitions 3
```

容量专项命令：

```powershell
python scripts/collect_release_baseline.py --build-dir build/windows-ninja-release --configuration Release --perf-preset capacity --perf-repetitions 3 --perf-timeout-seconds 1800
```

如果只想验证契约而不更新性能数据：

```powershell
python scripts/verify_r4_contract.py --build-dir build/windows-ninja-release --configuration Release --skip-build --skip-arch-baseline
```

## 4. CI/Release 集成

GitHub Actions 已在 `ci.yml` 与 `release.yml` 中接入 `scripts/verify_r4_contract.py --skip-build --skip-arch-baseline`。原因是完整 `ctest` 已覆盖全量测试，R4 gate 只负责把通信契约相关风险集中为短阻断项，避免 CI 时间和波动放大。夜间稳定性由 `nightly-stability.yml` 执行 `scripts/verify_stability_soak.py --soak-profile short`，并上传 `runtime/validation/nightly-stability-soak-summary.json` 与 `runtime/perf/v2-stability-soak/**`。固定机器 Release baseline 可通过 `release-baseline.yml` 手动触发，并上传 release summary、业务性能基线和 R4 短架构基线。P6 生产证据可通过 `production-evidence.yml` 手动触发，统一上传 `production-evidence-summary.json`、`p6-*-summary.json` 与相关 perf 产物。新 workflow 会通过 `scripts/render_validation_summary.py` 把 JSON summary 渲染到 GitHub Step Summary，便于直接查看失败步骤和 release gate 状态。

Redis/Raft/Operator 专项使用 `scripts/verify_specialized_e2e.py` 聚合，也可通过 `specialized-e2e.yml` 手动触发。默认入口不依赖 Redis/kind；固定 Redis 或 kind runner 可追加：

```powershell
.\scripts\verify_specialized_e2e.ps1 -BuildDir build/windows-ninja-debug -SkipBuild -IncludeRedisLive
python scripts/verify_specialized_e2e.py --build-dir build/windows-ninja-debug --skip-build --include-redis-live
python scripts/verify_specialized_e2e.py --build-dir build/default --skip-build --include-operator-kind
```

P3 数据恢复门禁使用 `scripts/verify_data_recovery_gate.py` 聚合。默认入口不依赖 Redis live 或多进程 fixture；固定 Redis 或真实多进程环境可追加：

```powershell
.\scripts\verify_data_recovery_gate.ps1 -BuildDir build/windows-ninja-debug -SkipBuild -IncludeRedisLive
python scripts/verify_data_recovery_gate.py --build-dir build/windows-ninja-debug --skip-build --include-settlement-replay
```

P4 可观测性/限流门禁使用 `scripts/verify_observability_gate.py` 聚合，并已接入 RC 总门禁。默认入口不依赖外部 OTel collector，也不启动真实 gateway HTTP 观测口；固定观测 runner 可追加：

```powershell
.\scripts\verify_observability_gate.ps1 -BuildDir build/windows-ninja-debug -SkipBuild -IncludeOtelCollector
python scripts/verify_observability_gate.py --build-dir build/default --skip-build --include-otel-collector
python scripts/verify_observability_gate.py --build-dir build/default --skip-build --include-runtime-http
```

P5 控制面门禁使用 `scripts/verify_control_plane_gate.py` 聚合，并已接入 RC 总门禁。默认入口不依赖 Docker/kind，覆盖 Operator manifest 静态契约与 fake-client Go 测试；固定 Kubernetes runner 可追加：

```powershell
.\scripts\verify_control_plane_gate.ps1 -IncludeEnvtest
python scripts/verify_control_plane_gate.py --include-kind
python scripts/verify_control_plane_gate.py --include-envtest --include-kind
```

P5 长稳、故障注入与回滚演练使用 `scripts/verify_production_resilience_gate.py` 聚合。默认入口不依赖 Redis live、kind 或长容量任务；固定 runner 上可逐步打开真实依赖和长项：

```powershell
.\scripts\verify_production_resilience_gate.ps1 -BuildDir build/windows-ninja-debug -SkipBuild
python scripts/verify_production_resilience_gate.py --build-dir build/default --skip-build --soak-profile short
python scripts/verify_production_resilience_gate.py --build-dir build/default --skip-build --include-redis-live --include-runtime-http
python scripts/verify_production_resilience_gate.py --build-dir build/default --skip-build --include-operator-kind --kind-timeout-seconds 1200
python scripts/verify_production_resilience_gate.py --build-dir build/release --configuration Release --skip-build --soak-profile short --baseline-profile release --include-release-baseline --perf-repetitions 3
```

固定 runner 可通过 `.github/workflows/production-resilience.yml` 手动触发，上传 `production-resilience-summary.json`、`p5-*-summary.json`、soak perf 产物和可选 release baseline 产物。

P6 生产证据聚合使用 `scripts/verify_production_evidence_gate.py`。默认入口保持有界，并额外执行 `scripts/check_production_candidate_audit.py` 审核生产候选证据链；固定 runner 上可逐步打开真实依赖和长项：

```powershell
.\scripts\verify_production_evidence_gate.ps1 -BuildDir build/windows-ninja-debug -SkipBuild
python scripts/verify_production_evidence_gate.py --build-dir build/default --skip-build --include-redis-live --include-operator-kind
python scripts/verify_production_evidence_gate.py --build-dir build/release --configuration Release --skip-build --soak-profile short --baseline-profile release --include-release-baseline --perf-repetitions 3
python scripts/verify_production_evidence_gate.py --build-dir build/release --configuration Release --skip-build --include-capacity-baseline --perf-repetitions 3 --step-timeout-seconds 1800
```

## 5. 发布阻断项

| 阻断项 | 处理要求 |
| --- | --- |
| R4 gate 失败 | 先修复失败测试或 schema contract，再继续发布 |
| 安全发布门禁失败 | 生产模式鉴权、admin 审计或 ACL 边界证据不完整时不得发布 |
| P4 可观测性/限流门禁失败 | 先修复 rate limit、trace/OTel、metrics 或 audit 聚合用例，再继续发布 |
| P5 控制面门禁失败 | 先修复 Operator reconcile/status/components/delete 证据；若为 kind/envtest 环境缺失，必须明确标记固定 runner 缺失 |
| P5 长稳故障回滚门禁失败 | 先查看 `runtime/validation/production-resilience-summary.json` 的 `failed_category` / `failed_step`，再修复 soak、data recovery、specialized、runtime observability 或 rollback/kind 子 summary |
| `runtime/perf/release-baseline/summary.json` 缺失或 `release_gates.overall_pass=false` | 重新采集多进程性能基线并确认退化原因 |
| R4 release summary 缺失或 `passed=false` | 先修复 R4 contract、schema、恢复路径或短架构基线 |
| typed envelope 新增业务字段未进 `common.proto` contract | 补 schema 与 `check_v3_proto_transport_contract` |
| legacy raw JSON 兼容路径新增主功能 | 必须同时补 typed envelope 测试，否则不发布 |
| 恢复路径缺测试 | 至少补充 backend reconnect/readiness/fault-injection 中对应一项 |
| 专项 E2E 失败 | 若变更涉及 Redis/Raft/Operator，对应专项必须先修复或明确标记为外部环境缺失 |
| 数据恢复门禁失败 | 先修复 replay/result/snapshot、WriteBehind flush/drain、Redis 降级或 Raft committed restart replay 中的失败项 |
| P6 生产证据聚合失败 | 先查看 `runtime/validation/production-evidence-summary.json` 的 `failed_category` / `failed_step`，再修复对应 stability、data recovery、specialized 或 release baseline 子 summary |
| N4 传输/配置治理失败 | 先查看 `runtime/validation/n4-transport-config-governance-summary.json`，修复 TLS 边界误判或 Docker/K8s/Helm 配置漂移 |
| N5 SDK 企业交付失败 | 先查看 `runtime/validation/n5-sdk-enterprise-delivery-summary.json`，区分 distribution、package consumer、business-flow 或真实 full-flow 失败 |
| N6 gRPC PoC 取舍失败 | 先查看 `runtime/validation/n6-v3-grpc-poc-decision-summary.json`；不得绕过 ADR 直接把 generated gRPC 接入默认生产链路 |

## 6. 发布记录模板

```text
版本：
提交：
构建预设：
R4 gate：
性能基线 summary：
已知风险：
回滚方式：
```

## 7. 产物流清单

### 7.1 二进制产物

| 产物 | 类型 | 安装路径 | 说明 |
|------|------|----------|------|
| `v2_gateway_demo` | 主入口 | `bin/v2_gateway_demo` | v2 Actor + IoEngine + 多进程 backend 路由，**推荐运行参考** |
| `v2_login_backend` | backend 服务 | `bin/v2_login_backend` | 独立登录后端进程 |
| `v2_room_backend` | backend 服务 | `bin/v2_room_backend` | 独立房间后端进程 |
| `v2_battle_backend` | backend 服务 | `bin/v2_battle_backend` | 独立战斗后端进程 |
| `v2_match_backend` | backend 服务 | `bin/v2_match_backend` | 独立匹配后端进程 |
| `v2_leaderboard_backend` | backend 服务 | `bin/v2_leaderboard_backend` | 排行榜后端（可选 Redis 持久化） |
| `v2_gateway_pressure` | 压测工具 | `bin/v2_gateway_pressure` | 9 种场景压测工具 |
| `v2_arch_benchmark` | 微基准 | `bin/v2_arch_benchmark` | 架构微基准测试 |
| `sdk_echo_client` | SDK 示例 | `bin/sdk_echo_client` | SDK Echo 客户端 |
| `sdk_full_flow_client` | SDK 示例 | `bin/sdk_full_flow_client` | SDK 全流程客户端 |
| `example_hello_world` | 示例 | `bin/example_hello_world` | Hello World 演示 |
| `echo_server` | v1 入口 | `bin/echo_server` | 完整 v1 网关装配 |
| `echo_client` | 工具 | `bin/echo_client` | 基础 Echo 客户端 |
| `gateway_pressure` | v1 压测 | `bin/gateway_pressure` | v1 压测工具（9 种场景） |
| `login_server` | v1 入口 | `bin/login_server` | 实验性独立登录服务 |
| `room_server` | v1 入口 | `bin/room_server` | 实验性独立房间服务 |
| `battle_server` | v1 入口 | `bin/battle_server` | 实验性独立战斗服务 |
| `login_demo` | showcase | `bin/login_demo` | 登录流程演示 |
| `room_demo` | showcase | `bin/room_demo` | 房间系统演示 |
| `battle_demo` | showcase | `bin/battle_demo` | 战斗系统演示 |
| `admin_demo` | showcase | `bin/admin_demo` | 管理工具演示（无权限校验，仅 demo） |

> 条件编译产物（需对应 CMake 选项开启）：`tank_battle_demo`（`BOOST_BUILD_TANK_DEMO=ON`）、`realtime_echo_plugin`（`BOOST_BUILD_ECHO_PLUGIN_DEMO=ON`）。

### 7.2 配置文件

| 文件 | 安装路径 | 说明 |
|------|----------|------|
| `config/gateway.json` | `share/boost_gateway/config/gateway.json` | 网关配置（v1/v2 共用） |
| `config/login_backend.json` | `share/boost_gateway/config/login_backend.json` | 登录后端配置 |
| `config/room_backend.json` | `share/boost_gateway/config/room_backend.json` | 房间后端配置 |
| `config/battle_backend.json` | `share/boost_gateway/config/battle_backend.json` | 战斗后端配置 |

### 7.3 脚本入口

| 脚本 | 路径 | 说明 |
|------|------|------|
| `verify_release_candidate.py` | `scripts/verify_release_candidate.py` | RC 总门禁 |
| `verify_r4_contract.py` | `scripts/verify_r4_contract.py` | R4 通信契约门禁 |
| `verify_stability_soak.py` | `scripts/verify_stability_soak.py` | 稳定性浸泡门禁 |
| `check_security_release_gate.py` | `scripts/check_security_release_gate.py` | 安全发布门禁 |
| `collect_release_baseline.py` | `scripts/collect_release_baseline.py` | 性能基线聚合 |
| `collect_v2_perf_baseline.py` | `scripts/collect_v2_perf_baseline.py` | v2 多进程基线采集 |
| `verify_production_resilience_gate.py` | `scripts/verify_production_resilience_gate.py` | 长稳故障回滚门禁 |
| `verify_production_evidence_gate.py` | `scripts/verify_production_evidence_gate.py` | 生产证据聚合门禁 |
| `verify_p5_p8_business_closure.py` | `scripts/verify_p5_p8_business_closure.py` | 生产业务闭环门禁 |
| `verify_production_candidate_evidence.py` | `scripts/verify_production_candidate_evidence.py` | 生产候选证据聚合 |
| `verify_tls_production_readiness.py` | `scripts/verify_tls_production_readiness.py` | TLS 上线前置门禁 |
| `verify_fixed_runner_release_capacity.py` | `scripts/verify_fixed_runner_release_capacity.py` | 固定 Runner 性能证据 |
| `verify_preprod_recovery_drill.py` | `scripts/verify_preprod_recovery_drill.py` | 预发恢复演练 |
| `verify_tls_preprod_multi_run.py` | `scripts/verify_tls_preprod_multi_run.py` | TLS 预发多轮证据 |
| `check_production_candidate_audit.py` | `scripts/check_production_candidate_audit.py` | 生产候选审核 |
| `render_production_readiness_report.py` | `scripts/render_production_readiness_report.py` | Readiness Report 渲染 |
| `check_production_evidence_manifest.py` | `scripts/check_production_evidence_manifest.py` | 证据 Manifest 校验 |
| `generate_proto_cpp.ps1` | `scripts/generate_proto_cpp.ps1` | Proto C++ 代码生成（Windows） |
| `generate_proto_cpp.sh` | `scripts/generate_proto_cpp.sh` | Proto C++ 代码生成（Linux/macOS） |
| `deploy_k8s.sh` | `scripts/deploy_k8s.sh` | K8s 一键部署 |
| `build_docker.sh` | `scripts/build_docker.sh` | Docker 构建 |

### 7.4 Docker 镜像列表

| 镜像 | Dockerfile | 基础镜像 | 说明 |
|------|-----------|----------|------|
| `boost-gateway` | `Dockerfile` | ubuntu:22.04 | 全栈网关 + 5 后端 + 工具 |
| `boost-gateway-minimal` | `Dockerfile.minimal` | alpine:3.18 | 最小运行时镜像 |
| `boost-gateway-dev` | `Dockerfile.dev` | ubuntu:22.04 | 开发环境镜像（含构建工具） |
| `boost-gateway-sdk` | `sdk/Dockerfile` | ubuntu:22.04 | SDK 封装镜像（含 Python/C#） |

生产 deploy 脚本：`scripts/build_docker.sh`、`env/docker/docker-compose.yml`。

### 7.5 K8s/Operator YAML 列表

| 文件 | 路径 | 说明 |
|------|------|------|
| `env/k8s/gateway-deployment.yaml` | gateway Deployment |  |
| `env/k8s/login-backend-deployment.yaml` | login backend Deployment |  |
| `env/k8s/room-backend-deployment.yaml` | room backend Deployment |  |
| `env/k8s/battle-backend-deployment.yaml` | battle backend Deployment |  |
| `env/k8s/match-backend-deployment.yaml` | match backend Deployment |  |
| `env/k8s/leaderboard-backend-deployment.yaml` | leaderboard backend Deployment |  |
| `env/k8s/redis-statefulset.yaml` | Redis StatefulSet |  |
| `env/k8s/redis-service.yaml` | Redis Service |  |
| `env/k8s/gateway-service.yaml` | gateway 内部 Service |  |
| `env/k8s/hpa.yaml` | 水平自动扩缩容 |  |
| `env/k8s/pdb.yaml` | Pod 中断预算 |  |
| `env/k8s/namespace.yaml` | 命名空间 |  |
| `operator/boostgateway-operator/` | Operator 源码 | controller-runtime 控制器 |
| `operator/boostgateway-operator/config/crd/` | CRD 定义 | BoostGatewayCluster CRD |
| `operator/boostgateway-operator/config/samples/` | CR 样例 | BoostGatewayCluster 实例 |

### 7.6 Proto 生成入口

| 入口 | 说明 |
|------|------|
| `proto/*.proto` | v3 proto 协议定义（envelope、service common、login、room、battle、match、leaderboard） |
| `scripts/generate_proto_cpp.ps1` | Windows Proto C++ 代码生成脚本 |
| `scripts/generate_proto_cpp.sh` | Linux/macOS Proto C++ 代码生成脚本 |
| CMake target `generate_v3_proto` | CMake 驱动的 proto 生成（`BOOST_BUILD_GRPC=ON` 时） |
| CMake target `generate_v3_grpc` | CMake 驱动的 gRPC stub 生成（`BOOST_BUILD_GRPC=ON` 时） |
| `check_v3_proto_schema` | CMake target，验证 proto schema 完整性 |
| `check_v3_proto_transport_contract` | CMake target，验证 transport contract 字段覆盖 |

## 8. 测试结果与性能基线要求

### 8.1 测试结果要求

| 测试类别 | 通过标准 | 说明 |
|----------|----------|------|
| 单元测试 | 100% 通过 | Release 构建下全部通过，跳过项仅限 Redis/外部依赖 |
| 集成测试 | 100% 通过 | 多进程集成真实拓扑 |
| 架构微基准 | 无崩溃、无断言失败 | `v2_arch_benchmark` 全部规格可行 |
| 稳定性 soak（short） | 通过 | I/O accept、WriteBehind drain/failure、backend timeout/recovery |
| R4 契约门禁 | `passed=true` | proto schema、typed envelope round-trip、恢复/熔断 |
| 安全发布门禁 | 通过 | 生产模式 dev token 禁用、admin 审计键、ACL 边界 |
| P3 数据恢复 | 通过 | replay/result/snapshot、WriteBehind flush/drain、Redis/Raft 恢复 |
| P4 可观测性/限流 | 通过 | rate limit、trace/OTel、backend RED metrics、audit |
| P5 控制面 | 通过 | Operator manifest 静态契约、Go fake-client 测试 |
| P6 生产证据 | 通过 | 聚合 stability、data recovery、specialized、完整性审核 |

### 8.2 性能基线要求

| 场景 | 规模 | 目标门槛 | 状态 |
|------|------|----------|------|
| echo-100 | 100 连接 | P99 ≤ 50ms | ✅ 1ms（v3.4.0） |
| echo-1000 | 1000 连接 | P99 ≤ 50ms | ✅ 5ms（v3.4.0） |
| battle-20 | 20 房间 | P99 ≤ 100ms | ✅ 10ms（v3.4.0） |
| battle-100 | 100 房间 | P99 ≤ 250ms | ✅ 200ms（v3.4.0） |
| capacity 5K | 5000 连接 | 无退化 | ⚠️ 存在连接建立失败 |
| capacity 10K | 10000 连接 | 无退化 | ⚠️ 存在连接建立失败 |
| TLS on/off 对比 | — | 吞吐损耗 ≤ 25% | 待测定 |
| OTel on/off 对比 | — | 不阻塞主链 | 待测定 |
| 长稳 2h | 1000 连接 | RSS 增长 ≤ 5%，无 fd 泄漏 | 待测定 |

> 基线数据来源：`runtime/perf/release-baseline/summary.json`。退化超过发布门槛（P99 退化 > 10% / 吞吐下降 > 15% / 错误率 > 0.1%）时阻断生产候选发布。
