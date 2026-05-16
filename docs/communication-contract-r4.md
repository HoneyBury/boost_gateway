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
| generated proto/gRPC | `.proto` schema 已存在；`generate_v3_proto_cpp` 可生成 stub；`check_v3_proto_schema` 可在无 protoc 环境下做 schema 验证 |

## 2. 已完成闭环

1. `BackendEnvelope` 已支持 `gateway/login/room/battle/match/matchmaking/leaderboard` 服务名解析。
2. `include/v2/service/envelope_adapter.h` 提供 BackendEnvelope 与 `v3::proto::TypedEnvelope` 的集中转换入口。
3. leaderboard 的历史 backend message type 使用 `leaderboard_submit` / `leaderboard_top` / `leaderboard_rank`，adapter 内部明确映射到 typed kind。
4. 单测覆盖 meta、payload、message kind、trace/span/error_code 保留，以及 unknown message type 和 malformed JSON 拒绝路径。
5. `v2_arch_benchmark` 已纳入通信契约短基线，collector 对三个契约指标设置 P99 门禁。
6. `GatewayServiceBridge::route()` 已有 integration 测试覆盖 trace/span 写入 backend request，并验证 backend error code 与 correlation id 返回到 routing result。
7. login/room/battle/match/leaderboard 主业务 handler 已复用 `v2::service::decode_handler_payload()` 与 `wrap_typed_response_if_needed()`，typed envelope 与 legacy raw JSON 的边界处理集中到 adapter。
8. legacy raw JSON 路径已有集中 deprecation 标记：`legacy_raw_json_deprecation_notice()`；handler 仍保持兼容，但 adapter 会标识 `HandlerPayloadEncoding::kLegacyRawJson`。

## 3. 性能基线

采集命令：

```powershell
python scripts\collect_v2_arch_baseline.py --build-dir build\windows-msvc-debug --output-root runtime\perf\v2-arch-baseline --timeout-seconds 30
```

最近一次采集结果：通过。

| 指标 | 样本 | P99 | 门禁 |
|---|---:|---:|---:|
| `backend_envelope_json_roundtrip` | 10000 | 130.7us | <= 1000us |
| `typed_envelope_json_roundtrip` | 10000 | 164.2us | <= 1000us |
| `backend_typed_adapter_roundtrip` | 10000 | 19.0us | <= 1000us |

## 4. 迁移原则

1. 先冻结 typed envelope 语义，再推进 generated proto。
2. legacy raw JSON payload 只保留在 gateway 边界和兼容路径。
3. 所有新 domain/message kind 必须有 domain 映射、round-trip 测试和错误传播测试。
4. gateway/backend/SDK 看到的 `correlation_id`、`trace_id`、`error_code` 必须一致。
5. 通信契约成本必须进入短基线，不能只依赖功能测试。

## 5. 兼容期策略

| 阶段 | 默认请求 | 默认响应 | 兼容要求 |
|---|---|---|---|
| R4-A | raw JSON 或 typed envelope 均接受 | request 是 envelope 时返回 envelope，否则返回 raw JSON | adapter 单测和 integration 覆盖 |
| R4-B | typed envelope 优先 | typed envelope | raw JSON 标记 deprecated，继续解析 |
| R4-C | generated proto/gRPC 可选 transport | typed/proto 按配置切换 | CI 同时跑 JSON envelope、proto schema check 和生成 target |
| R4-D | generated proto/gRPC 主路径 | proto/gRPC | raw JSON 只保留工具迁移入口 |

## 6. 下一步优先级

1. 将 `check_v3_proto_schema` 接入 CI 或本地 release checklist，确保 proto schema 变更不会漂移。
2. 在 gateway/backend trace/error integration 中增加 typed envelope request/response 路径覆盖。
3. 继续推进 generated protobuf/gRPC stub 到独立实验 transport，不替换现有 JSON bridge。
4. 清理其他中文文档的编码显示问题，确保 PowerShell 和编辑器下均为 UTF-8 可读。
