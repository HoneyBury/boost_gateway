# R4 通信契约与兼容迁移计划

> 日期：`2026-05-16`  
> 范围：typed envelope、BackendEnvelope 兼容层、generated proto/gRPC 路线、legacy raw JSON 兼容期、错误传播与性能基线入口。

## 1. 当前事实

当前项目处于通信契约迁移期：

| 层级 | 当前状态 |
|---|---|
| 客户端 TCP wire | 仍以 `message_id + request_id + error_code + body` 为主 |
| gateway 内部 typed 消息 | `v2::actor::MessagePayload` 已使用 typed variant |
| backend 服务间 envelope | `v2::service::BackendEnvelope` JSON 已冻结并有单测 |
| v3 typed envelope helper | `v3::proto::encode_typed_envelope()` / `decode_typed_envelope()` 已覆盖 login/room/battle/match/leaderboard |
| BackendEnvelope 到 TypedEnvelope | 已新增 `v2::service::to_typed_envelope()` / `to_backend_envelope()` 显式转换层 |
| generated proto/gRPC | `.proto` schema 已存在，常规生成链和 gRPC transport 仍未接主链 |

## 2. 已完成闭环

1. `BackendEnvelope` 已支持 `gateway/login/room/battle/match/matchmaking/leaderboard` 服务名解析。
2. `include/v2/service/envelope_adapter.h` 提供 BackendEnvelope 与 `v3::proto::TypedEnvelope` 的集中转换入口。
3. leaderboard 的历史 backend message type 使用 `leaderboard_submit` / `leaderboard_top` / `leaderboard_rank`，adapter 内部明确映射到 typed kind。
4. 单测覆盖 meta、payload、message kind、trace/span/error_code 保留，以及 unknown message type 和 malformed JSON 拒绝路径。
5. `v2_arch_benchmark` 已纳入通信契约短基线，collector 对三个契约指标设置 P99 门禁。
6. `GatewayServiceBridge::route()` 已有 integration 测试覆盖 trace/span 写入 backend request，并验证 backend error code 与 correlation id 返回到 routing result。

## 3. 性能基线

采集命令：

```powershell
python scripts\collect_v2_arch_baseline.py --build-dir build\windows-msvc-debug --output-root runtime\perf\v2-arch-baseline --timeout-seconds 30
```

最近一次采集时间：`2026-05-16T13:28:14Z`，结果：通过。

| 指标 | 样本 | P99 | 门禁 |
|---|---:|---:|---:|
| `backend_envelope_json_roundtrip` | 10000 | 130.7us | ≤ 1000us |
| `typed_envelope_json_roundtrip` | 10000 | 164.2us | ≤ 1000us |
| `backend_typed_adapter_roundtrip` | 10000 | 19.0us | ≤ 1000us |

## 4. 迁移原则

1. 先冻结 typed envelope 语义，再推进 generated proto。
2. legacy raw JSON payload 只保留在 gateway 边界和兼容路径。
3. 所有新 domain/message kind 必须有 domain 映射、round-trip 测试和错误传播测试。
4. gateway/backend/SDK 看到的 `correlation_id`、`trace_id`、`error_code` 必须一致。
5. 通信契约成本必须进入短基线，不能只依赖功能测试。

## 5. 兼容期策略

| 阶段 | 默认请求 | 默认响应 | 兼容要求 |
|---|---|---|---|
| R4-A | raw JSON 或 typed envelope 均接受 | request 是 envelope 时返回 envelope，否则返回 raw JSON | `maybe_wrap_typed_response()` 和 adapter 单测覆盖 |
| R4-B | typed envelope 优先 | typed envelope | raw JSON 标记 deprecated，继续解析 |
| R4-C | generated proto/gRPC 可选 transport | typed/proto 按配置切换 | CI 同时跑 JSON envelope 和 proto schema 测试 |
| R4-D | generated proto/gRPC 主路径 | proto/gRPC | raw JSON 只保留工具迁移入口 |

## 6. 下一步优先级

1. 将 adapter 接入 match/leaderboard 以外的主业务 handler 边界，减少散落的手写 message type 逻辑。
2. 增加 generated proto CMake 可选目标，只做 schema 生成验证，不立刻替换 transport。
3. 把 raw JSON compatibility 路径集中标记为 deprecated，为 R4-B 切换做准备。
4. 将 gateway/backend trace/error integration 覆盖扩展到 typed envelope request/response 路径。
