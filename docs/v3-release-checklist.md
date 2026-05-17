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
| P5 控制面门禁 | `python scripts/verify_control_plane_gate.py` | Operator fake-client Go 测试通过；固定 runner 可显式启用 envtest/kind，kind smoke 断言 status conditions、components 覆盖和样例 CR 删除路径 |
| 稳定性短 soak | `python scripts/verify_stability_soak.py --build-dir build/windows-msvc-debug --configuration Debug --skip-build --soak-profile short` | I/O accept 策略、WriteBehind drain/failure、backend timeout/recovery、短架构基线全部通过，并写出 `runtime/validation/stability-soak-summary.json` |
| P3 数据恢复门禁 | `python scripts/verify_data_recovery_gate.py --build-dir <build-dir> --skip-build` | replay/result/snapshot、WriteBehind flush/drain、Redis degraded、Raft committed restart replay 与持久化 round trip 全部通过，并写出 `runtime/validation/data-recovery-summary.json` |
| Proto schema | `cmake --build <build-dir> --target check_v3_proto_schema` | v3 proto 文件、包名、核心 message 存在 |
| Transport contract | `cmake --build <build-dir> --target check_v3_proto_transport_contract` | `ServiceEnvelope` 与各 domain oneof 字段覆盖生成传输实验所需字段 |
| Release baseline 聚合入口 | `python scripts/collect_release_baseline.py --build-dir build/windows-ninja-release --configuration Release --perf-preset baseline --perf-repetitions 3` | R4 release contract 与 v2 多进程 `echo/battle` baseline 均通过，并写出 `runtime/validation/release-baseline-summary.json` 与 `runtime/perf/release-baseline/summary.json` |
| 专项 E2E 聚合入口 | `python scripts/verify_specialized_e2e.py --build-dir <build-dir> --skip-build` | Raft 集群/恢复与 Redis 降级路径通过；Redis live 与 Operator kind 通过显式参数启用 |
| P6 生产证据聚合入口 | `python scripts/verify_production_evidence_gate.py --build-dir <build-dir> --skip-build` | 有界 stability、P3 data recovery、Redis/Raft/Operator 专项证据全部通过；固定 runner 可追加 Redis live、Operator kind、settlement replay、release/capacity baseline |
| P3 SDK 分发门禁 | `python scripts/check_sdk_distribution.py --build-dir <build-dir>` | SDK 版本、CMake package、C ABI 动态库、Python/C# wrapper、C ABI 测试和构建产物一致 |
| P3 SDK 安装消费 | `python scripts/verify_sdk_package_consumer.py --build-dir <build-dir>` | SDK 可安装到临时 prefix，外部 CMake 项目可 `find_package(boost_gateway_sdk)` 并链接 `boost_gateway::sdk` |
| P3 SDK 业务闭环 | `python scripts/verify_sdk_business_flow.py --build-dir <build-dir>` | SDK C++ 业务 API 跑通 login、echo、room、ready、battle、reconnect 与多客户端闭环 |
| P3 SDK 示例联调 | `python scripts/verify_sdk_full_flow_client.py --build-dir <build-dir>` | 启动真实 `v2_gateway_demo` 并运行 `sdk_full_flow_client` 完整流程 |

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

P4 可观测性/限流门禁使用 `scripts/verify_observability_gate.py` 聚合，并已接入 RC 总门禁。默认入口不依赖外部 OTel collector；固定观测 runner 可追加：

```powershell
.\scripts\verify_observability_gate.ps1 -BuildDir build/windows-ninja-debug -SkipBuild -IncludeOtelCollector
python scripts/verify_observability_gate.py --build-dir build/default --skip-build --include-otel-collector
```

P5 控制面门禁使用 `scripts/verify_control_plane_gate.py` 聚合，并已接入 RC 总门禁。默认入口不依赖 Docker/kind；固定 Kubernetes runner 可追加：

```powershell
.\scripts\verify_control_plane_gate.ps1 -IncludeEnvtest
python scripts/verify_control_plane_gate.py --include-kind
python scripts/verify_control_plane_gate.py --include-envtest --include-kind
```

P6 生产证据聚合使用 `scripts/verify_production_evidence_gate.py`。默认入口保持有界，不跑长容量任务；固定 runner 上可逐步打开真实依赖和长项：

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
| `runtime/perf/release-baseline/summary.json` 缺失或 `release_gates.overall_pass=false` | 重新采集多进程性能基线并确认退化原因 |
| R4 release summary 缺失或 `passed=false` | 先修复 R4 contract、schema、恢复路径或短架构基线 |
| typed envelope 新增业务字段未进 `common.proto` contract | 补 schema 与 `check_v3_proto_transport_contract` |
| legacy raw JSON 兼容路径新增主功能 | 必须同时补 typed envelope 测试，否则不发布 |
| 恢复路径缺测试 | 至少补充 backend reconnect/readiness/fault-injection 中对应一项 |
| 专项 E2E 失败 | 若变更涉及 Redis/Raft/Operator，对应专项必须先修复或明确标记为外部环境缺失 |
| 数据恢复门禁失败 | 先修复 replay/result/snapshot、WriteBehind flush/drain、Redis 降级或 Raft committed restart replay 中的失败项 |
| P6 生产证据聚合失败 | 先查看 `runtime/validation/production-evidence-summary.json` 的 `failed_category` / `failed_step`，再修复对应 stability、data recovery、specialized 或 release baseline 子 summary |

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
