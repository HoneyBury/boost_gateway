# 当前项目事实源

更新时间：2026-05-17

本文档作为当前进度的入口事实源。版本号以 `CMakeLists.txt` 中的 `BoostAsioDemo VERSION 3.3.2` 为准；提交状态以 `git HEAD` 为准。

## 稳定能力

- v1.x：维护期能力已经收束，主要保留历史协议、业务边界、运行手册和发布记录。
- v2.x：当前主线。`ActorSystem`、gateway-only ingress、五个后端服务、`BackendEnvelope`、typed envelope adapter、服务健康检查、TTL/readiness、WriteBehind drain 统计与失败上报已经进入可验收状态。
- R4 契约门禁：`scripts/verify_r4_contract.py` 覆盖通信契约、后端恢复、typed envelope、proto schema、gateway-only ingress 和短架构基线入口。
- 稳定性门禁：`scripts/verify_stability_soak.py` 覆盖 I/O accept policy、WriteBehind drain/failure、backend timeout/recovery 和短架构基线，提供 `smoke`、`short`、`medium` soak profile；`.github/workflows/nightly-stability.yml` 已将 `short` profile 纳入夜间任务。
- Windows 后端稳定性：`BackendServer` 已支持多会话跟踪与关闭收口；plain TCP `read_frame()` 不再依赖平台 `select()`，改为 Boost.Asio non-blocking bounded read，降低 Windows/MSVC 下 stale backend 与多客户端 smoke 的挂起风险。
- RC 总门禁：`scripts/verify_release_candidate.py` 汇总可靠性矩阵、R4 契约、稳定性 soak 和可选 Release baseline，并输出结构化 summary。
- 安全发布门禁：`scripts/check_security_release_gate.py` 检查生产模式禁用 dev token fallback 的证据、admin 审计最小键和 ACL 边界说明。

## 增量能力

- v3 proto/gRPC：schema 校验、CMake target 和 release checklist 已存在，当前定位为传输契约与构建入口，不作为默认生产链路。
- Redis/Raft/Operator：已通过专项 E2E 形成独立可靠性闭环；默认发布仍保持有界 smoke，固定本机/runner 可显式启用 Redis live 与 Operator kind 验证。
- Release baseline：`scripts/collect_release_baseline.py` 现在聚合 R4 release contract 与 v2 多进程 `echo/battle` 性能采集；默认 `baseline` profile 适合固定机器执行，`capacity` profile 用于 5K/10K 连接容量专项；`.github/workflows/release-baseline.yml` 提供手动触发入口，固定 runner 接入见 `docs/fixed-runner-playbook.md`。
- P1 性能事实：macOS Release baseline 三轮已刷新，`runtime/perf/release-baseline/summary.json` 中 `release_gates.overall_pass=true`；capacity 单轮已暴露当前退化点，5K/10K echo 存在连接建立失败，battle-500 存在 rejected 与 P99 500ms，详见 `docs/releases/v3.3.2-p1-performance-stabilization.md`。
- 专项 E2E：`scripts/verify_specialized_e2e.py` 聚合 Raft 集群/恢复、Redis 降级与可选 Redis live / Operator kind smoke，作为 Redis/Raft/Operator 独立验收入口；`.github/workflows/specialized-e2e.yml` 提供手动触发入口，固定 runner 接入见 `docs/fixed-runner-playbook.md`。
- P3 数据恢复：`scripts/verify_data_recovery_gate.py` 聚合 replay/result/snapshot、WriteBehind flush/drain、Redis degraded、Raft committed restart replay 和持久化 round trip；Redis live 与 settlement replay 通过显式参数接入固定环境。
- P4 可观测性/限流：`scripts/verify_observability_gate.py` 聚合 rate limit 全局消息类型/IP/user/login/connection、trace/OTel、backend RED metrics、gateway metrics 导出和 audit 事件证据，并接入 RC 总门禁；固定观测 runner 可通过 `--include-otel-collector` 验证 fake collector POST，通过 `--include-runtime-http` 启动真实 `v2_gateway_demo` + SDK full-flow 验证 `/health`、`/ready` 与 `/metrics*`。
- P5 控制面：`scripts/verify_control_plane_gate.py` 聚合 Operator manifest 静态契约、fake-client Go 测试，并接入 RC 总门禁；Go build/module cache 固定到仓库 `runtime/go-cache`，避免依赖用户 HOME 权限；固定 runner 可通过 `--include-envtest` / `--include-kind` 验证 envtest、kind status/components 和样例 CR 删除路径。
- P6 生产证据聚合：`scripts/verify_production_evidence_gate.py` 将 stability soak、P3 data recovery、Redis/Raft/Operator specialized E2E 与可选 release/capacity baseline 聚合为一个固定 runner 入口；默认模式保持有界，长稳、Redis live、Operator kind、settlement replay、capacity baseline 通过显式参数启用。本机 P6 收束验证已覆盖 Release 构建、Redis live、Operator kind 和 3 轮 Release baseline，交付记录见 `docs/releases/v3.3.2-p6-production-evidence.md`。
- P2 固定 runner 证据：`.github/workflows/production-evidence.yml` 已支持 JSON runner 输入、preflight summary 归档、Redis/kind 真实依赖、runtime HTTP observability、release baseline 和 capacity baseline 的手动固定 runner 场景；配置说明见 `docs/production-evidence-runner.md`。
- P3 监控运维：Prometheus 已加载 `env/monitoring/prometheus-alerts.yml`，Grafana dashboard 已对齐当前 gateway `/metrics` 真实指标，`scripts/check_monitoring_operability.py` 会阻断后端 HTTP scrape、旧指标名和 runbook 漂移；运维流程见 `docs/production-operations-runbook.md`。
- P4 SDK 企业级封装：C++ SDK heartbeat 已实作，disconnect callback 可由 heartbeat failure 触发；C ABI 暴露 heartbeat 控制，Python/C# wrapper 增加 native 版本校验和加载/分配诊断；SDK business-flow 与 full-flow client 验证覆盖 login、room、ready、battle、push、reconnect、heartbeat。

## 保留边界

- 长稳 2h/8h soak、10K 连接生产容量基线、跨节点 Redis/Raft、更完整 Operator rollback/probe E2E、更完整角色化 RBAC、外部 OTel collector 长稳和 Prometheus P99 histogram/summary 仍属于后续稳定性专项；P6 与 production-evidence workflow 已提供统一聚合入口，但正式数据仍需固定 runner 持续沉淀。
- 默认 CI/release workflow 使用有界 smoke 门禁，避免长时间占用终端或 runner。
- 文档出现编码显示异常时，以 UTF-8 文件内容和 CI 校验结果为准，PowerShell 控制台乱码不代表文件编码错误。

## 下一步优先级

下一大阶段以 `docs/production-stabilization-roadmap.md` 为规划事实源，主题是生产稳定化与交付闭环，不继续横向扩展功能模块。

1. P0：生产部署口径与发布 runbook 收束。
2. P1：性能基线与容量事实闭环。
3. P2：真实生产证据固定 runner 流水线。
4. P3：监控、告警与运维 runbook 产品化。
5. P4：SDK 企业级封装稳定化。
6. P5：长稳、故障注入与回滚演练。
