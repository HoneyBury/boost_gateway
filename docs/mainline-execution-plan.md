# v3.5.3 高风险部署证据执行计划

更新时间：2026-07-18

## 阶段目标

v3.5.0-v3.5.2 已完成清理、依赖治理和发布工程收口。v3.5.3 不扩展业务功能，目标是让高风险部署结论建立在同一候选提交、真实外部依赖和可复核产物上。

发布结论必须来自 Linux fixed runner 的实际 summary/artifact；历史数据、跨提交产物和推算值只能作为背景信息。

## 优先级与验收标准

| 优先级 | 工作项 | 交付物 | 完成标准 |
|---|---|---|---|
| P0 | Specialized E2E 测试选择真实性 | Redis/Raft 测试组清单、实际匹配数和逐组结果 | filter 与 GoogleTest 实际名称一致；任何零匹配组直接失败；Redis live 与 Raft 组均有可追溯结果 |
| P0 | Redis/Raft 真实故障恢复 | 运行中 stop/partition/recover、持续 SDK 流量和一致性 summary | 记录 RTO、失败请求、重复/丢失写入；Raft 覆盖重新选主及落后 follower catch-up |
| P0 | 告警生命周期闭环 | Prometheus 查询证据和 recovery drill summary | 关键告警可观察到 `inactive -> pending -> firing -> resolved`；未连接外部告警系统时不得声称告警已验证 |
| P1 | 专项性能矩阵 | 1/2/4 核、matchmaking、leaderboard、TLS、OTel 对照 summary | 使用 CPU affinity/cgroup 限额；至少三轮；输出 P99、吞吐、CPU、RSS、失败率和回归判定 |
| P1 | 同一候选 8h soak 与容量证据 | 8h、capacity、business-capacity 和专项 E2E artifact | 所有核心产物绑定同一冻结 SHA；8h 实际持续时间不少于 28800 秒；失败轮与复测均保留 |
| P2 | Runner 与工作流稳定性 | 统一 preflight、工具版本与缓存身份记录 | Python/Go/CMake/Conan/kind/kubectl 版本可复现；workspace、磁盘和缓存身份在运行前检查 |
| P2 | 文档事实治理 | 开发者入口、性能口径和 workflow 契约检查 | 文档命令可执行；触发方式与 `.github/workflows/` 一致；已完成计划不再作为当前 TODO |

## 执行顺序

1. 先修正 Specialized E2E 的测试发现和零匹配失败语义，重新生成 Redis/Raft 证据。
2. 在真实 Redis/Raft 进程上执行故障恢复，并在故障窗口持续发送 SDK 业务流量。
3. 将 Prometheus 告警状态查询纳入恢复演练，明确区分指标存在、规则加载和告警生命周期通过。
4. 完成专项性能矩阵后冻结候选 SHA；冻结后不再修改采集器、门禁或阈值。
5. 在该 SHA 上运行 8h soak、capacity、business-capacity、Redis/Raft 和告警演练，最后汇总准入结论。

## 当前实现进度

- Specialized E2E 已在运行前发现并验证每个 GTest filter，任一 pattern 零匹配或执行数不一致都会失败。
- Redis Compose 恢复演练已提供 opt-in：持久标记、停服降级、恢复、数据校验和恢复后 SDK full-flow。
- Prometheus verifier 已可与 Redis 故障窗口并发执行，并验证 `BoostGatewayRedisUnavailable` 的 `inactive -> pending -> firing -> resolved`。
- Raft 集成测试已覆盖三节点 TCP Backend RPC、实际 leader 停止、存活节点重新选主、故障期写入和同对象 logical restart 后的缺失日志追赶；持久化进程级恢复仍需 fixed-runner 专项。
- 性能采集器已支持 Linux `--cpu-set`、记录实际 affinity 并聚合 1/2/4 核矩阵；阶段性 fixed-runner 数据已覆盖三档资源边界，最终冻结 SHA 仍需刷新。
- Matchmaking join/status/leave 与 Leaderboard submit/top/rank 已具备 opt-in 并发采集、分操作吞吐/P50/P99 和失败门禁；Redis on/off 对照已包含真实 persistence 证明，最终冻结 SHA 仍需刷新。
- TLS off/on、Redis 告警生命周期和 Redis/Raft 恢复已有阶段性 fixed-runner 证据；OTel off/on 采集器使用回环 collector，并以 exporter、collector 和后端路由计数对账，待最终 SHA 实跑。

## 证据约束

- summary 必须记录候选 SHA、实际 checkout SHA、workflow/run、runner 标签、构建类型、Conan profile 和 lockfile 摘要。
- 核心结论不得组合不同 SHA 的 artifact；失败运行不得删除或覆盖。
- 测试程序返回成功不足以证明覆盖完成，测试组必须记录 discovered/matched/executed 数量。
- 1/2 核结果必须来自操作系统 CPU 配额或 affinity，不以 `--io-cores` 代替机器资源约束。
- 8h soak 必须记录实际持续时间、轮次、失败事件、CPU/RSS/fd 和宿主机资源快照。

## 当前边界

- 不恢复 Windows 支持，不把历史 Windows 数据作为发布容量事实。
- 不扩展 demo、ECS、AOI 或新业务功能。
- gRPC 继续保持 `experimental_only` / `defer_default_transport`。只有 `grpc-experimental.yml` 的 fixed-runner run 在 `BOOST_BUILD_GRPC=ON` 下刷新证据，也不自动改变默认传输结论。
- 不立即删除内部 Raft legacy raw JSON；先增加使用计数、allowlist 和拒绝开关，再通过 ADR 决定迁移窗口。
- Python/C# wrapper、wheel/NuGet、macOS ARM64、JWKS/多 `kid` 和独立 debug symbols 进入 v3.6 ADR，不属于 v3.5.3 实现范围。

## 阶段退出条件

v3.5.3 只有同时满足以下条件才可结束：P0 全部通过；专项性能矩阵不存在未解释的 critical regression；8h 与所有高风险证据来自同一候选 SHA；告警生命周期有真实查询记录；发布清单能够直接定位每个原始 artifact。
