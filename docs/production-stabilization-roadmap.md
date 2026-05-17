# 生产稳定化与交付闭环路线图

更新时间：2026-05-17

本文档记录 v3.3.2 之后生产稳定化与交付闭环阶段的规划和完成状态。当前阶段已经完成 P0-P6 收束；后续继续推进时，以 `docs/production-candidate-hardening-plan.md` 作为新的阶段规划事实源。

## 阶段目标

本阶段的目标不是证明“功能能跑”，而是证明“生产能长期稳定运行、问题能被发现、版本能被部署和回滚、性能退化能被阻断、客户端能可靠接入”。

完成后项目应能回答：

- 当前版本如何在云服务器或 Kubernetes 上完整部署、运行、监控和回滚。
- 单实例在固定硬件上能承载多少连接、吞吐、战斗广播负载和资源成本。
- 哪些模块已经进入默认 release/CI 流水线，哪些仍需要固定 runner 或真实依赖环境。
- Redis、Operator、OTel、SDK full-flow、runtime HTTP 观测、长稳 soak 和容量压测是否有持续证据。
- SDK 是否具备生产客户端接入所需的线程模型、心跳、重连、错误诊断和多语言封装边界。

## 当前判断

当前项目已经具备较完整的功能面和验证入口：

- v2 gateway + login / room / battle / matchmaking / leaderboard 五后端拓扑已经形成。
- SDK 已有 C++ 静态库、C ABI 动态库、Python/C# 轻量封装、业务闭环测试和真实 gateway full-flow 示例。
- Docker Compose、systemd、Kubernetes manifests、Prometheus/Grafana 配置已经存在。
- P3 数据恢复、P4 可观测性/限流、P5 控制面、P6 生产证据聚合 gate 已经形成。
- Operator manifest、RBAC、manager probes、fake-client Go 测试和 kind/envtest 可选验证入口已经存在。

同时，仍有若干生产稳定化短板：

- 默认 release gate 为了保持有界，Redis live、Operator kind、真实 OTel collector、runtime HTTP、settlement replay、capacity baseline 和长稳 soak 多数需要显式参数或固定 runner。
- 性能基线仍有大量指标未沉淀为稳定事实，10K 连接、2h/8h soak、P99、RSS、fd、CPU 和每连接成本仍需固定机器数据。
- Kubernetes 部署面仍需要生产口径收束，例如镜像 tag、版本标签、readiness/liveness 语义、资源限制和回滚验证。
- Prometheus 当前只 scrape gateway `/metrics`；后端服务仍按 TCP health 处理，不能误宣称后端已有 HTTP metrics。
- SDK 当前已经能完成业务闭环，但企业级客户端能力仍需补齐心跳、重连、异步事件、错误码映射、线程模型和语言封装诊断。

## 投产路径建议

### 云服务器 / Docker Compose 试运行

适合首次生产试运行、压测、灰度和运维流程演练。

```bash
docker compose up -d --build
docker compose ps
curl -fsS http://localhost:9080/health
curl -fsS http://localhost:9080/metrics
python3 scripts/verify_sdk_full_flow_client.py --build-dir build/default
```

要求：

- 只向公网暴露 gateway TCP `9201`。
- `9080`、`9090`、`3000` 只允许内网、堡垒机或 VPN 访问。
- Redis 使用持久化 volume，并明确备份和恢复流程。
- Prometheus 使用 `env/monitoring/prometheus.yml` scrape gateway `/metrics`。
- Grafana 导入 `env/monitoring/grafana-dashboard.json`。

### Kubernetes / 云原生部署

适合多副本、滚动发布、资源隔离和控制面演进。

```bash
python3 scripts/build_docker.py all
python3 scripts/deploy_k8s.py --dry-run
python3 scripts/deploy_k8s.py
kubectl -n boost-gateway get pods
kubectl -n boost-gateway port-forward svc/gateway 9080:9080
curl http://localhost:9080/health
```

正式生产前必须完成：

- 镜像 tag 固化，禁止生产 manifest 直接使用 `latest`。
- gateway `/health`、`/ready`、readinessProbe、livenessProbe 语义收束。
- 资源 requests/limits、PDB、HPA、滚动发布和回滚流程验证。
- Operator kind smoke 与 rollout/rollback 证据进入固定 runner。

## P0-P6 完成状态

| 阶段 | 状态 | 交付记录 / 证据 |
| --- | --- | --- |
| P0 生产部署口径与发布 Runbook | completed | `docs/releases/v3.3.2-p0-production-deployment.md`、`docs/production-deployment-runbook.md` |
| P1 性能基线与容量事实 | completed | `docs/releases/v3.3.2-p1-performance-stabilization.md`、`runtime/perf/release-baseline/summary.json` |
| P2 真实生产证据固定 Runner | completed | `docs/releases/v3.3.2-p2-production-evidence-runner.md`、`.github/workflows/production-evidence.yml` |
| P3 监控告警与运维 Runbook | completed | `docs/releases/v3.3.2-p3-monitoring-operations.md`、`scripts/check_monitoring_operability.py` |
| P4 SDK 企业级封装稳定化 | completed | `docs/releases/v3.3.2-p4-sdk-enterprise-runtime.md`、`scripts/verify_sdk_full_flow_client.py` |
| P5 长稳故障回滚演练 | completed | `docs/releases/v3.3.2-p5-production-resilience.md`、`.github/workflows/production-resilience.yml` |
| P6 生产证据与候选审核 | completed | `docs/releases/v3.3.2-p6-production-evidence.md`、`scripts/check_production_candidate_audit.py` |

## 历史优先级规划

### P0：生产部署口径与发布 Runbook 收束

目标：把“能部署”变成“可发布、可回滚、可审计”。

任务：

- 新增或完善生产部署 runbook，覆盖云服务器、Docker Compose、systemd、Kubernetes、监控、回滚、备份和发布后验证。
- 统一 Docker、Kubernetes、systemd、Prometheus、文档中的版本和拓扑口径。
- 修正部署文档中与当前实现不一致的描述，例如后端 metrics scrape 范围、Kubernetes 版本标签和镜像 tag。
- 扩展 `scripts/check_deploy_operability.py`，检查生产 manifest 禁止 `latest`、版本标签一致、gateway 后端路由完整、监控 scrape 目标与真实能力一致。
- 明确 gateway `/health` 与 `/ready` 的生产语义，避免把 liveness stub 当成业务 readiness。

交付物：

- `docs/production-deployment-runbook.md`
- 更新后的 `deploy/README.md`、`env/README.md`、Kubernetes manifests 和部署 gate
- `runtime/validation/deploy-operability-summary.json`

验收标准：

- Docker Compose 可启动完整 6 服务 + Redis + Prometheus/Grafana。
- SDK full-flow 能在部署后的真实 gateway 上通过。
- K8s dry-run 和部署静态 gate 通过。
- 文档与脚本对后端 HTTP metrics、gateway 管理口和生产暴露边界没有冲突描述。

### P1：性能基线与容量事实闭环

目标：把性能从目标口号变成可比较、可回归、可阻断发布的事实。

任务：

- 在固定 Release 机器上运行 `collect_release_baseline.py --perf-preset baseline --perf-repetitions 3`。
- 运行 capacity profile，覆盖 1K / 5K / 10K echo 和 battle 100 / 500 场景。
- 回填 `docs/performance-baseline.md` 的吞吐、P50/P90/P99、RSS、fd、线程、CPU、每连接成本和瓶颈判断。
- 将性能退化阈值接入 release summary，明确退化阻断标准。
- 区分默认稳定基线与实验参数，例如 backend connection pool、OTel、TLS、Redis on/off。

交付物：

- `runtime/perf/release-baseline/summary.json`
- `runtime/perf/release-baseline/report.md`
- 更新后的 `docs/performance-baseline.md`
- 性能退化 gate 规则

验收标准：

- baseline 至少 3 轮，记录 min / median / max。
- capacity profile 产物可归档。
- `release_gates.overall_pass=true`。
- P99、吞吐、RSS、错误率和资源泄漏出现越线时能阻断生产候选。

### P2：真实生产证据固定 Runner 流水线

目标：把 P6 聚合入口从“可手动运行”推进到“固定环境持续沉淀证据”。

任务：

- 配置 self-hosted runner：`production-evidence`、`release-baseline`、`redis-live`、`operator-kind`、`observability`。
- 固定运行 Redis live、Operator kind、runtime HTTP observability、settlement replay、release baseline、capacity baseline 和 short/medium soak。
- 所有 summary 渲染到 GitHub Step Summary，并归档 artifact。
- 区分环境缺失、外部依赖不可达和业务逻辑失败。
- 将新形成的稳定证据同步到 `docs/reliability-matrix.md`。

交付物：

- 固定 runner 配置说明
- 更新后的 `.github/workflows/production-evidence.yml`
- `runtime/validation/production-evidence-summary.json`
- 更新后的 `docs/reliability-matrix.md`

验收标准：

- `verify_production_evidence_gate.py` 在固定 runner 上完整通过。
- Redis live 和 Operator kind 不再只依赖本地手动验证。
- 每个 stable 承诺都有测试、脚本或运行证据。

### P3：监控、告警与运维 Runbook 产品化

目标：上线后能看见问题、定位问题，并按流程处理问题。

任务：

- 标准化 gateway `/metrics`、`/metrics/json`、`/metrics/diagnostics/json` 字段和兼容边界。
- 增加 Prometheus alert rules：gateway down、backend routing error、P99 上升、rate limit 激增、Redis 不可达、RSS/fd 异常、gateway readiness 异常。
- 梳理 Grafana dashboard，使面板与真实 metrics 字段一致。
- 编写运维 runbook：backend down、Redis down、gateway 错误率升高、连接数异常、发布失败、回滚、日志采集。
- 评估后端 HTTP metrics、sidecar exporter 或文件指标采集方案，短期不误宣称后端已有 HTTP metrics。

交付物：

- `env/monitoring/prometheus-alerts.yml`
- 更新后的 Grafana dashboard
- `docs/production-operations-runbook.md`
- 监控 gate 或静态校验脚本

验收标准：

- Prometheus 能 scrape 当前真实存在的 gateway metrics。
- 人工制造 backend down 或 Redis down 时，指标和告警能体现。
- 运维文档能指导排查和恢复。

### P4：SDK 企业级封装稳定化

目标：让 SDK 从业务示例可用推进到生产客户端可依赖。

任务：

- 明确 SDK 线程模型、回调线程、同步/异步 API 边界、disconnect 生命周期和资源释放语义。
- 实现或明确约束 heartbeat、reconnect、push 分发、超时、取消和背压。
- 强化错误码映射，保证 gateway、backend、SDK 看到的错误码和 trace 信息可对应。
- 强化 C ABI、Python、C# wrapper 的版本校验、加载失败诊断和完整业务示例。
- 将 SDK 业务闭环、真实 gateway full-flow、reconnect 和 heartbeat 纳入 release checklist。

交付物：

- 更新后的 `sdk/docs/README.md` 和 SDK 接入 runbook
- SDK reconnect / heartbeat / push 测试
- 更新后的 `scripts/verify_sdk_full_flow_client.py`
- Python/C# 最小生产接入示例

验收标准：

- C++ SDK 能稳定完成 login、room、ready、battle、push、reconnect、heartbeat。
- Python/C# wrapper 能校验 native library 版本并给出清晰加载错误。
- SDK 与 gateway 版本兼容矩阵进入发布清单。

### P5：长稳、故障注入与回滚演练

目标：证明系统不仅能启动，而且能在故障、重启和升级中保持可恢复。

任务：

- 执行 2h / 8h soak，记录 RSS、fd、CPU、错误率和 P99。
- 演练 backend kill/restart、Redis restart、gateway rolling restart、网络抖动和慢客户端。
- 演练 Kubernetes rollout/rollback。
- 扩展 Operator kind smoke，覆盖 status、components、rollback、probe 和 CR 删除。
- 归档故障注入报告和恢复时间。

交付物：

- `runtime/validation/long-soak-summary.json`
- 故障注入 summary
- K8s rollout/rollback 演练记录
- 更新后的 `docs/reliability-matrix.md`

验收标准：

- 2h soak 无 fd 泄漏，RSS 增长可解释。
- backend 恢复后 gateway 路由恢复。
- Redis 不可达时降级行为符合文档。
- K8s rollback 可以按 runbook 完成。

## 阶段完成定义

当以下条件全部满足时，可以认为当前项目具备生产候选资格：

- 生产部署 runbook、运维 runbook、SDK 接入文档和 release checklist 与实际代码一致。
- Docker Compose 和 Kubernetes 路径都能完成部署、健康检查、监控和回滚验证。
- 性能基线不再以“待测定”作为核心结论，至少有一套固定机器 Release 数据。
- P6 生产证据在固定 runner 上可以稳定通过。
- 监控、告警、故障注入和长稳数据能支撑生产运维判断。
- SDK 能以稳定线程模型和版本兼容策略被客户端项目接入。

## 阶段收束结论

截至提交 `d5c8493 Close P6 production evidence delivery`，本阶段的核心目标已经完成：

- 生产部署、运维、SDK、release checklist 与实际代码有静态和运行证据约束。
- Docker Compose、systemd、Kubernetes manifest、Prometheus/Grafana、SDK full-flow 和 P6 production evidence 具备统一验证入口。
- Release baseline、capacity baseline、P5 resilience、P6 production evidence 均有固定 runner 或本机执行手册。
- P6 默认 gate 已包含 production candidate audit，能检查证据链、workflow、summary 契约和关键文档是否漂移。

仍需持续沉淀的内容不再阻断当前阶段交付，转入下一阶段“生产候选实测与发布硬化”：

- 2h/8h soak 的固定机器长期数据。
- 5K/10K 连接容量边界的多轮可比数据。
- 更完整的 Kubernetes rollout/rollback/probe、PDB/HPA/resource limits 实战。
- 外部 OTel collector 和 route latency histogram/summary。
- 跨节点 Redis/Raft 与 SDK 多语言生产示例。

## 历史推荐执行顺序

1. P0：先收束生产部署口径与 runbook。
2. P1：再沉淀固定机器性能和容量事实。
3. P2：把真实依赖和长项接入固定 runner。
4. P3：完善监控、告警和运维排障。
5. P4：稳定 SDK 企业级接入面。
6. P5：完成长稳、故障注入和回滚演练。

该顺序的原则是先保证“交付物说得清、部署得上、验证得动”，再逐步提升性能证据、生产证据、运维能力和客户端接入质量。
