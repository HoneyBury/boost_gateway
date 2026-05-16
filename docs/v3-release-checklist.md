# v3 发布检查清单

> 目标：在发布前确认 v3/R4 通信契约、typed envelope 迁移、恢复路径和短性能基线都有可重复验证入口。

## 1. 必跑门禁

| 门禁 | 命令 | 通过标准 |
| --- | --- | --- |
| R4 快速契约 | `python scripts/verify_r4_contract.py --build-dir build/windows-msvc-debug --configuration Debug` | proto contract、聚焦单测、聚合集成测试、短性能基线全部通过，并写出 `runtime/validation/r4-contract-summary.json` |
| CI 快速契约 | `python scripts/verify_r4_contract.py --build-dir <build-dir> --skip-build --skip-arch-baseline` | 不重编译、不跑性能基线，验证 schema 与 R4 聚焦测试；失败时 summary 标出 `failed_category` 和 `failed_step` |
| Proto schema | `cmake --build <build-dir> --target check_v3_proto_schema` | v3 proto 文件、包名、核心 message 存在 |
| Transport contract | `cmake --build <build-dir> --target check_v3_proto_transport_contract` | `ServiceEnvelope` 与各 domain oneof 字段覆盖生成传输实验所需字段 |

## 2. R4 契约状态

| 项目 | 当前状态 | 证据 |
| --- | --- | --- |
| typed envelope adapter | login/room/battle/match/leaderboard 后端主 handler 已接入统一 adapter | `include/v2/service/envelope_adapter.h`、`src/v2/service/envelope_adapter.cpp` |
| legacy raw JSON 兼容 | 保留兼容并标记 deprecation notice | `V2ServiceBoundaryTest.DecodeHandlerPayloadMarksLegacyRawJsonDeprecated` |
| trace/error 传播 | raw bridge 与 typed envelope 路径均有聚合测试 | `ServiceBusIntegrity.GatewayBridgeRoutePropagatesTraceAndErrorCode`、`ServiceBusIntegrity.GatewayBridgeTypedEnvelopePreservesTraceAndError` |
| proto round-trip | 五个业务后端已有 typed envelope 往返测试 | `ServiceBusIntegrity.ProtoEnvelopeRoundTripsThrough*Backend` |
| 连接恢复 | backend config 更新后可恢复路由，超时后旧连接关闭且可恢复 | `ServiceBusIntegrity.GatewayBridgeRecoversAfterBackendConfigUpdate`、`ServiceBusIntegrity.GatewayBridgeTimeoutClosesStaleConnectionAndRecovers` |
| 熔断恢复 | 连续失败后打开 circuit breaker，等待窗口后半开探测成功并闭合 | `ServiceBusIntegrity.GatewayBridgeCircuitBreakerHalfOpenProbeRecovers` |

## 3. 性能基线

短基线由 `scripts/collect_v2_arch_baseline.py` 采集，输出到 `runtime/perf/v2-arch-baseline/summary.json`。当前 R4 门禁默认使用较小参数，目标是证明基线链路和退化 gate 可运行；正式发布前应在固定机器上使用 Release 构建重复采集。

推荐发布前命令：

```powershell
python scripts/verify_r4_contract.py --build-dir build/windows-ninja-release --configuration Release
```

如果只想验证契约而不更新性能数据：

```powershell
python scripts/verify_r4_contract.py --build-dir build/windows-ninja-release --configuration Release --skip-build --skip-arch-baseline
```

## 4. CI/Release 集成

GitHub Actions 已在 `ci.yml` 与 `release.yml` 中接入 `scripts/verify_r4_contract.py --skip-build --skip-arch-baseline`。原因是完整 `ctest` 已覆盖全量测试，R4 gate 只负责把通信契约相关风险集中为短阻断项，避免 CI 时间和波动放大。

## 5. 发布阻断项

| 阻断项 | 处理要求 |
| --- | --- |
| R4 gate 失败 | 先修复失败测试或 schema contract，再继续发布 |
| `summary.json` 缺失或 `release_gates.passed=false` | 重新采集短性能基线并确认退化原因 |
| typed envelope 新增业务字段未进 `common.proto` contract | 补 schema 与 `check_v3_proto_transport_contract` |
| legacy raw JSON 兼容路径新增主功能 | 必须同时补 typed envelope 测试，否则不发布 |
| 恢复路径缺测试 | 至少补充 backend reconnect/readiness/fault-injection 中对应一项 |

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
