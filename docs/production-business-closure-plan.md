# 生产业务闭环接入规划

更新时间：2026-05-18

本文档记录生产稳定化、部署运维、性能采样和 SDK 企业级封装之后的下一阶段规划。阶段目标不是继续横向堆新模块，而是把当前已经有实现、专项测试或部署实例的能力，逐步接入真实客户端业务流水线，形成可上线、可压测、可监控、可回归的生产闭环。

## 阶段判断

当前默认生产链路已经稳定在：

```text
SDK / client
  -> gateway TCP :9201
  -> login backend
  -> room backend
  -> battle backend
  -> gateway push / response
```

Docker Compose 已经启动完整六服务拓扑：gateway、login、room、battle、matchmaking、leaderboard，再加 Redis、Prometheus、Alertmanager、Grafana 和 redis-exporter。gateway 配置也已经包含 `match` 与 `leaderboard` 后端路由。

但是从真实客户端业务闭环看，仍存在几类“单侧完成”：

| 模块 | 当前状态 | 缺口 |
|---|---|---|
| Matchmaking | 后端服务、gateway 路由、typed envelope、专项测试已存在 | SDK 和 full-flow 没有 `match_join` / `match_status`，真实客户端不会自然压到 matchmaking |
| Leaderboard | 后端服务、Redis 持久化、gateway 路由、专项测试已存在 | SDK 和 full-flow 没有 submit/top/rank，battle 结束后也没有自动 settlement 提交 |
| Redis leaderboard | Docker 生产栈已接入 Redis，backend 可降级内存 | 缺少“战斗结算 -> Redis 排行榜 -> 查询”的生产业务证据 |
| Raft | matchmaking/leaderboard 支持 Raft，专项 E2E 覆盖复制/恢复 | 默认生产配置 `node_id` / `peers` 为空，不在默认生产链路 |
| OTel collector | gateway 可通过 `OTEL_EXPORT_ENDPOINT` 接入 exporter | 默认 Compose 不启动 collector，P4 gate 默认不依赖真实 collector |
| TLS/mTLS | gateway 配置和 security policy 已存在 | 默认 `v3_tls_enabled=false` 且 `require_tls=false`，服务间仍是 plain TCP |
| Kubernetes / Operator | manifest、operator、kind smoke 和 gate 已存在 | 当前真实生产演练以 OrbStack Docker Compose 为主，K8s 仍是可选控制面路径 |
| v3 proto/gRPC | proto schema、typed helper、生成入口已存在 | 默认生产传输仍是 v2 TCP + BackendEnvelope，不是 gRPC transport |
| ShardRouter / 多节点服务发现 | ClusterRouter 静态种入后端地址，ShardRouter 有测试 | 没有动态注册中心，也没有多副本 room/battle shard 生产拓扑 |
| AdminService | v1/demo-only 管理命令，ACL/audit 有单测 | 不属于当前 v2 生产 gateway 管理面 |

## 总体目标

1. 让 matchmaking 和 leaderboard 从“部署在线”进入真实 SDK 业务闭环。
2. 让 battle settlement 能产生 leaderboard 数据，并能被客户端查询验证。
3. 让 Redis/Raft/OTel/TLS/K8s 等可选能力明确区分：哪些进入默认生产链路，哪些保留为固定 runner / 高可用专项。
4. 所有接入都必须配套 SDK 示例、生产 Compose 实测、监控指标、文档和 release gate。

## P0：SDK 协议面补齐 Matchmaking / Leaderboard（已完成）

目标：先让客户端有能力调用已经在线的 matchmaking 和 leaderboard 后端，打通最小请求/响应链路。

任务：

- 在 C++ SDK 增加 matchmaking API：
  - `match_join(user_id, mmr, mode)`
  - `match_leave(user_id, mode)`
  - `match_status(user_id, mode)`
- 在 C++ SDK 增加 leaderboard API：
  - `leaderboard_submit(user_id, display_name, score)`
  - `leaderboard_top(k)`
  - `leaderboard_rank(user_id)`
- 补 C ABI、Python、C# wrapper 的对应入口或明确暂不暴露的边界。
- 扩展 SDK codec/message 定义，确保 message id、body 格式、错误码和服务端 handler 一致。
- 增加 SDK 单元测试和 in-process gateway 业务测试。

交付物：

- `sdk/include/boost_gateway/sdk/client.h`
- `sdk/src/client.cpp`
- `sdk/include/boost_gateway/sdk/c_api.h`
- `sdk/python/__init__.py`
- `sdk/csharp/SdkClient.cs`
- SDK 单测 / 集成测试
- 网关协议与路由接入：
  - `include/net/protocol.h`
  - `include/v2/gateway/message_types.h`
  - `src/v2/gateway/gateway_actor.cpp`
  - `src/v2/gateway/runtime.cpp`
  - `include/v2/gateway/gateway_command_parser.h`
  - `src/v2/gateway/gateway_command_parser.cpp`

验收标准：

- SDK 能通过 gateway 调用 matchmaking 与 leaderboard，并收到有效响应。
- API 错误码、超时、断线和服务端错误能被清晰映射。
- 不要求 battle 自动提交排行榜；P0 只完成“客户端可调用”。

完成记录（2026-05-18）：

- C++ SDK 已暴露 `match_join` / `match_leave` / `match_status`、`leaderboard_submit` / `leaderboard_top` / `leaderboard_rank`。
- C ABI、Python wrapper、C# wrapper 已补齐同名能力。
- gateway 已识别 6001/6004/6006 与 7001/7003/7005 协议消息，并将请求路由到 matchmaking / leaderboard 后端。
- `sdk_business_flow_tests` 已启动真实 login、room、battle、matchmaking、leaderboard 后端和 `DemoServer`，验证 SDK 经 gateway 访问 matchmaking 与 leaderboard 的闭环。
- 本阶段验证命令：
  - `cmake --build build --target sdk_tests sdk_business_flow_tests project_v2_unit_tests`
  - `./build/sdk/tests/sdk_tests`
  - `./build/tests/v2/project_v2_unit_tests '--gtest_filter=V2GatewayCommandParserTest.*'`
  - `./build/sdk/tests/sdk_business_flow_tests --gtest_filter=GatewayFixture.SdkMatchmakingRoundTripThroughBackend:GatewayFixture.SdkLeaderboardRoundTripThroughBackend`

## P1：扩展 Full-flow，形成真实客户端业务闭环（已完成）

目标：把 SDK full-flow 从 login/room/battle 扩展为覆盖 matchmaking 和 leaderboard 的完整业务流。

任务：

- 扩展 C++ `sdk_full_flow_client`：
  - connect
  - login
  - match_join / match_status
  - create/join room
  - ready/start battle/input/finish
  - leaderboard_submit
  - leaderboard_top / leaderboard_rank
  - reconnect / heartbeat
- 增加 Python/C# full-flow 示例的对应步骤，或形成独立 `match_leaderboard_flow` 示例。
- 更新 `scripts/verify_sdk_full_flow_client.py`，让真实 gateway full-flow 覆盖 matchmaking/leaderboard。
- 在当前 OrbStack Docker Compose 生产栈上跑通一次真实业务闭环。

交付物：

- `sdk/examples/full_flow_client/main.cpp`
- `sdk/examples/python_full_flow.py`
- `sdk/examples/csharp_full_flow/Program.cs`
- `scripts/verify_sdk_full_flow_client.py`
- `runtime/validation/sdk-full-flow-summary.json`
- `examples/v2_gateway_demo/main.cpp`：新增 `--port`，让 full-flow gate 可用随机 TCP 端口隔离本机已有服务

验收标准：

- Docker Compose 全栈运行时，SDK full-flow 能覆盖六个后端：login、room、battle、matchmaking、leaderboard、Redis。
- gateway `/metrics/diagnostics/json` 中能看到 matchmaking/leaderboard backend metrics 增长。
- Redis leaderboard 中能查询到 full-flow 提交的数据。

完成记录（2026-05-18）：

- C++ `sdk_full_flow_client` 已扩展为 connect/login、match_join/status/leave、room、ready/battle/finish、leaderboard_submit/top/rank、reconnect 的完整流程。
- Python 与 C# full-flow 示例已同步增加 matchmaking 和 leaderboard 步骤。
- `scripts/verify_sdk_full_flow_client.py` 现在会启动 login、room、battle、matchmaking、leaderboard 五个真实后端，启动带 HTTP diagnostics 的 gateway，并验证 SDK full-flow 后五类 backend metrics 均有请求增长。
- 本机验证产物：`runtime/validation/p1-sdk-full-flow-client-summary.json`，结果 `PASS (15/15 checks)`。

边界说明：

- P1 已完成“SDK 真实业务闭环”和 gateway diagnostics 证据；Redis live 查询属于后续 P2/P4 与生产 Compose/固定 runner 场景继续强化。

## P2：Battle Settlement 到 Leaderboard 的服务端流水线（已完成）

目标：让排行榜不再只由客户端手工 submit，而是成为战斗结算流水线的一部分。

任务：

- 定义 battle settlement 输出中的 score / result / reason / player_id 口径。
- 在 battle finish 后增加 settlement hook：
  - 可选方案 A：gateway 收到 battle finished push 后调用 leaderboard backend。
  - 可选方案 B：battle backend 直接发 leaderboard submit。
  - 优先选择对现有职责边界侵入最小、可观测性最好的方案。
- 增加幂等键，避免重试导致重复提交。
- 增加 settlement replay / recovery 测试，确保重启后可恢复或可重放。
- 将 leaderboard submit 失败纳入 backend RED metrics 和 runbook。

交付物：

- battle settlement 数据契约
- leaderboard submit 幂等策略
- 服务端集成测试
- 更新 `docs/production-operations-runbook.md`

验收标准：

- 完成 battle 后，无需客户端手动 submit，也能在 leaderboard 查询到结算分数。
- leaderboard 不可用时，业务失败可观测、可重试、可降级，不能静默丢数据。
- full-flow 可选择验证“自动提交”和“手动 submit”两种路径。

完成记录（2026-05-18）：

- 选择方案 A：gateway 在收到本地 `BattleSettlementPreparedMsg` 或 bridged battle backend 的 `battle_finished` push 后调用 leaderboard backend。这样 battle backend 仍只负责权威战斗结算，跨服务提交、错误观测和后端 RED metrics 留在 gateway 侧。
- settlement submit payload 口径：
  - `user_id`
  - `display_name`
  - `score`
  - `idempotency_key = battle_id:user_id`
  - `source = battle_settlement`
  - `battle_id`
  - `room_id`
  - `reason`
- gateway 侧用 `leaderboard_settlement_keys_` 防止同一进程重复提交；leaderboard backend 也接受 `idempotency_key` 并对重复请求返回 `{"idempotent":true}`，避免重试重复应用。
- `sdk_full_flow_client`、Python/C# 示例现在先查询自动 settlement 写入的 leaderboard，再保留手动 submit 路径。
- `project_v2_multi_process_tests` 已扩展 leaderboard 后端，并用随机端口与空配置路径隔离本机已有生产服务。
- 本机验证产物：
  - `runtime/validation/p2-sdk-full-flow-client-summary.json`：`PASS (15/15 checks)`
  - `project_v2_multi_process_tests --gtest_filter=MultiProcessFixture.BusinessFlowFullCycle`：通过

边界说明：

- 当前 P2 已覆盖“自动提交”和“手动 submit”两条业务路径。leaderboard backend 不可用时，gateway 会通过 `GatewayServiceBridge::route()` 记录 leaderboard backend errors/timeouts，并打错误日志；长期重放/补偿队列属于 P3/P4 之后的可靠性增强。

## P3：生产监控与性能闭环扩展到新业务路径（已完成）

目标：新增业务路径进入生产后，监控和性能基线也必须覆盖它们。

任务：

- 扩展 `collect_v2_perf_baseline.py` 或新增 case，覆盖：
  - match_join / match_status
  - leaderboard_submit / top / rank
  - battle finish -> leaderboard settlement
- 扩展 Prometheus/Grafana：
  - matchmaking backend requests/errors/timeouts
  - leaderboard backend requests/errors/timeouts
  - Redis exporter 关键指标
  - settlement submit failure / retry 指标
- 扩展 `collect_docker_production_perf_snapshot.py` 报告，突出业务 backend metrics 是否发生变化。
- 更新 `docs/performance-baseline.md` 与运维 runbook。

交付物：

- 性能采集 case
- Grafana dashboard / Prometheus rules 更新
- 生产快照报告字段更新

验收标准：

- 新业务路径有吞吐、P99、错误率、Redis 成本和资源快照。
- 任一后端错误增长能在 `/metrics`、Prometheus 或 diagnostics 中定位到具体服务。

完成记录（2026-05-18）：

- `scripts/collect_v2_perf_baseline.py` 已把标准拓扑扩展为 gateway + login / room / battle / matchmaking / leaderboard 五后端；battle 压测结束后会通过 P2 settlement hook 增长 leaderboard backend metrics。
- 性能采集新增 `--include-business-flow`，会复用 `scripts/verify_sdk_full_flow_client.py --skip-build` 跑 SDK full-flow，覆盖 match_join/status、leaderboard 自动 settlement 查询、manual submit、top/rank 和 reconnect，并把结果写入同一份 `summary.json` / `report.md`。
- `scripts/verify_sdk_full_flow_client.py` 增加 `--skip-build` 和 step duration，便于 release/capacity 采集复用既有构建并沉淀业务流耗时。
- `scripts/collect_docker_production_perf_snapshot.py` 新增 `business_backend_metrics` 摘要，在报告中直接显示 login/room/battle/matchmaking/leaderboard 的 requests/successes/errors/timeouts/avg latency/samples。
- Prometheus/Grafana 现有配置已经覆盖 gateway backend RED counters、leaderboard/Redis dependent errors、Redis exporter 和容器资源面板；P3 文档同步到 `docs/performance-baseline.md` 与 `docs/production-operations-runbook.md`。

边界说明：

- 当前 Prometheus 仍只 scrape gateway、Prometheus 自身和 Redis exporter；后端 TCP 服务不直接暴露 HTTP `/metrics`。后端业务指标通过 gateway RED counters 和 diagnostics 观测。
- settlement submit failure/retry 目前通过 `gateway_backend_leaderboard_errors_total`、`gateway_backend_leaderboard_timeouts_total` 和 gateway 日志 `leaderboard settlement submit failed` 定位；独立 retry queue 指标属于后续可靠性增强。

## P4：Redis / Raft 高可用业务化（已完成）

目标：把 Redis 和 Raft 从专项验证推进到与排行榜/匹配业务相关的高可用验证。

任务：

- Redis：
  - 明确生产 Redis 持久化、备份、恢复和降级策略。
  - 增加 Redis down / reconnect / data recovery 的业务 full-flow 验证。
- Raft：
  - 为 matchmaking 和 leaderboard 设计多节点拓扑配置样例。
  - 明确 Raft 是否进入默认生产部署；如果不进入，文档标为 HA profile。
  - 跑 leader failover、follower catch-up、重启恢复、重复提交幂等验证。
- 固定 runner 增加 `redis-live` 和 `raft-ha` profile。

交付物：

- HA 配置样例
- Redis/Raft 生产运行说明
- 固定 runner evidence

验收标准：

- Redis 故障不会导致排行榜数据不可解释地丢失。
- Raft profile 能证明 leader 切换后 matchmaking/leaderboard 状态一致。
- 默认生产链路和 HA profile 边界清晰。

完成记录（2026-05-18）：

- 新增 `docs/redis-raft-ha-runbook.md`，明确 Redis AOF/RDB/备份/恢复/降级策略，以及 Redis 恢复后必须跑 `battle finish -> leaderboard settlement -> top/rank` 业务闭环。
- 新增 matchmaking / leaderboard 三节点 HA 配置样例：
  - `config/environments/ha/matchmaking-node1.json`
  - `config/environments/ha/matchmaking-node2.json`
  - `config/environments/ha/matchmaking-node3.json`
  - `config/environments/ha/leaderboard-node1.json`
  - `config/environments/ha/leaderboard-node2.json`
  - `config/environments/ha/leaderboard-node3.json`
- `scripts/verify_specialized_e2e.py` 新增 `--profile default|redis-live|raft-ha|all`：
  - `redis-live` 自动启用 Redis live client/event-store/connection-pool/leaderboard gates。
  - `raft-ha` 明确运行 Raft cluster/persistence 与 matchmaking/leaderboard Raft-backed service recovery gates。
- `.github/workflows/specialized-e2e.yml` 新增 `specialized_profile` 输入，固定 runner 可以按 `redis-live` 或 `raft-ha` 产出独立 evidence。

边界说明：

- 默认生产配置 `config/environments/production/matchmaking.json` 与 `leaderboard.json` 的 `raft.node_id` 仍为空，因此默认生产链路不启用 Raft。
- HA profile 必须显式配置 `CONFIG_PATH=config/environments/ha/<node>.json` 或对应 `RAFT_*` 环境变量，并为 `raft.storage_dir` 挂载持久盘。

## P5：OTel / Trace / 延迟指标接入真实业务流（已完成）

目标：让真实业务闭环可追踪、可定位慢请求，而不只在单测里验证 trace。

任务：

- 在 Compose 增加可选 OTel collector profile。
- full-flow 运行时携带 trace id，确认 gateway -> backend -> settlement 的 trace 传播。
- 为 route latency 增加 Prometheus histogram/summary，或明确离线采集替代方案。
- Grafana 增加业务路径延迟面板。
- OTel collector 故障必须不影响 gateway 主流程。

交付物：

- OTel Compose profile
- trace/full-flow 验证脚本
- 延迟指标文档和 dashboard

验收标准：

- 能按一次 full-flow 找到关键 route span 或对应 trace id。
- Prometheus/Grafana 能观察 match/leaderboard/settlement 延迟。
- collector 不可用时业务继续可用，且有告警或日志。

完成记录（2026-05-18）：

- 新增 `env/monitoring/otel-collector.yml` 和 Docker Compose `otel` profile；gateway 可通过 `OTEL_EXPORT_ENDPOINT=http://otel-collector:4318/v1/traces` 启用 OTLP HTTP 导出。
- `scripts/verify_observability_gate.py` 已覆盖 trace context、typed envelope trace/error 保留、backend RED metrics、OTel exporter、fake collector POST、collector 未配置不崩溃和 runtime HTTP observability。
- 新增 `docs/observability-trace-runbook.md`，明确默认链路仍以 gateway `/metrics*`、diagnostics、Prometheus/Grafana 和性能采集报告为事实源。
- 新增 `scripts/verify_p5_p8_business_closure.py`，P5 会聚合 `verify_observability_gate.py`，可按需启用 `--include-otel-collector` 与 `--include-runtime-http`。

边界说明：

- 默认 Compose 不启动 OTel collector；collector profile 是可选观测增强。
- Prometheus route latency histogram/summary 还不是默认 scrape 指标；P99 和波动仍以 `collect_v2_perf_baseline.py --include-business-flow`、release baseline 和固定 runner soak 为准。

## P6：TLS/mTLS 与生产安全链路（已完成，默认 transport 边界明确）

目标：把当前存在于配置和策略里的 TLS/mTLS 能力推进到可演练、可灰度的生产链路。

任务：

- 明确 gateway->backend TLS/mTLS 的证书生成、轮换和部署方式。
- 增加 Docker Compose TLS profile。
- 将 `v3_tls_enabled`、`security_policy.require_tls` 与真实连接行为打通验证。
- 增加错误诊断：证书过期、CA 不匹配、服务名不匹配、mTLS client cert 缺失。
- 更新生产配置 runbook 和安全发布 gate。

交付物：

- TLS/mTLS profile
- 证书生成和轮换 runbook
- TLS 集成测试和生产演练记录

验收标准：

- TLS profile 下 full-flow 通过。
- 错误证书会被拒绝并产生可诊断日志。
- 默认 plain TCP 与 TLS profile 的边界清晰，灰度切换可控。

完成记录（2026-05-18）：

- 新增 `docs/tls-mtls-runbook.md`，明确当前默认生产仍是 plain TCP，不误宣称全链路 TLS/mTLS 已上线。
- 新增 `scripts/check_tls_profile.py`，检查：
  - 默认 `v3_tls_enabled=false`、`rollout_percentage=0`。
  - 默认 `security_policy.require_tls=false`。
  - leaderboard 保留 `mtls_required=true` 敏感策略。
  - backend client connection 存在 TLS handshake 路径。
  - gateway bridge 通过 security policy 与 feature flag 控制 TLS。
  - `scripts/gen_certs.py` 可生成开发证书且证书可读。
- `scripts/verify_p5_p8_business_closure.py` 会执行 P6 TLS profile gate 与 security release gate。

边界说明：

- 当前 P6 收束的是 TLS/mTLS 配置、证书、灰度策略、backend client TLS 能力和发布边界；默认 Docker/K8s 主链仍不强制 TLS。
- “TLS profile 下 full-flow 通过”和错误证书诊断需要 backend 服务端 TLS listener、Secret/volume 挂载和 TLS SDK/客户端路径全部完成后才能作为下一阶段 transport 专项，不在本次默认生产链路中误标完成。

## P7：Kubernetes / Operator 生产闭环（已完成）

目标：在 Docker Compose 闭环稳定后，把同一套业务闭环迁移到 Kubernetes / Operator 路径验证。

任务：

- 用固定镜像 tag 部署 gateway、五后端、Redis、Prometheus/Grafana。
- 运行 SDK full-flow 覆盖 match/leaderboard/settlement。
- 增加 rollout/rollback、readiness/liveness、PDB/HPA/resource limits 验证。
- Operator GameServer CR 覆盖创建、更新、删除、状态条件和回滚。

交付物：

- K8s full-flow 验证脚本
- Operator kind smoke 扩展
- 发布/回滚证据

验收标准：

- K8s 环境能跑通与 Compose 等价的生产业务闭环。
- 回滚后 SDK full-flow 恢复通过。
- Operator 状态与实际 Deployment/Service 状态一致。

完成记录（2026-05-18）：

- 新增 `scripts/verify_k8s_full_flow.py`，可对已部署的 Kubernetes gateway Service 执行 port-forward、SDK full-flow、HTTP health 和 backend metrics 覆盖检查。
- 新增 `docs/k8s-business-flow-runbook.md`，记录 manifest 发布、rollout、SDK full-flow 和 Operator kind smoke 的执行方式。
- `scripts/verify_control_plane_gate.py` 已覆盖 Operator manifest、Go fake-client/unit tests、可选 kind smoke；kind 路径验证 `Ready/Progressing/Degraded/TLSReady`、六组件 `status.components[]` 和 sample CR 删除。
- `scripts/verify_p5_p8_business_closure.py` 默认执行 control-plane gate；可通过 `--include-operator-kind` 和 `--include-k8s-full-flow` 显式进入真实 K8s/Operator 证据路径。

边界说明：

- 默认本地 P5-P8 smoke 不强制要求 Kubernetes 集群和镜像仓库；`--include-k8s-full-flow` 适用于已部署的预发/固定 runner 环境。
- Docker Compose 仍是当前本机生产部署主链，K8s/Operator 是可选发布面和固定 runner 证据路径。

## P8：v3 proto/gRPC 传输正式化评估（已完成）

目标：在业务闭环稳定后，再决定是否把 proto/gRPC 从契约层推进到生产传输层。

任务：

- 评估 v2 TCP + BackendEnvelope 是否继续作为主链。
- 若推进 gRPC：
  - 生成 C++ stubs。
  - 增加 gateway/backend 双栈兼容。
  - 定义迁移窗口、回滚策略和性能基线。
- 若不推进：
  - 明确 proto 仅作为 schema/typed envelope 契约，不误宣称 gRPC 已上线。

交付物：

- gRPC ADR / 决策记录
- 双栈 PoC 或保留契约说明
- 性能对比报告

验收标准：

- 不影响 P0-P7 已形成的生产业务闭环。
- 有明确上线/不上的工程理由和性能证据。

完成记录（2026-05-18）：

- 新增 `docs/v3-proto-grpc-adr.md`，决策为：v3 proto 作为 schema/typed envelope 契约层继续保留，generated gRPC 暂不进入默认生产 transport。
- 当前默认生产继续使用 v2 TCP + BackendEnvelope；typed envelope helper 和 adapter 作为兼容迁移层。
- `scripts/check_v3_proto_schema.py --require-transport-contract` 已作为 P8 contract gate。
- `scripts/verify_p5_p8_business_closure.py` 会执行 proto schema 与 transport contract 检查。

边界说明：

- `.proto` schema、typed helper、生成 helper和 contract gate 已完成；generated gRPC transport 需要独立 PoC、性能对比、TLS/负载均衡/deadline/retry/backpressure/rollback 方案后才能推进。

## 推荐执行顺序

1. P0：先补 SDK 协议面，给客户端真实调用能力。
2. P1：立即扩展 full-flow，把六服务业务闭环跑起来。
3. P2：再做 battle settlement 自动接入 leaderboard，让排行榜进入服务端流水线。
4. P3：随后补监控、性能和生产快照，让新路径可观测可压测。
5. P4：在业务路径稳定后推进 Redis/Raft HA，不把高可用复杂度提前压进主线。
6. P5：接入 OTel/trace/延迟指标，提升定位能力。
7. P6：推进 TLS/mTLS 安全链路。
8. P7：迁移同一闭环到 Kubernetes / Operator。
9. P8：最后评估 v3 proto/gRPC 是否正式进入生产传输层。

## 当前阶段结论

P0-P8 已完成主线收束。当前默认生产闭环为 SDK / TCP gateway / five backend / Redis / Prometheus-Grafana / Docker Compose；Redis live、Raft HA、OTel collector、Operator kind、K8s full-flow 和 generated gRPC 均被明确为可选 profile 或后续专项，不混入默认主链口径。
