# 生产稳定化与交付闭环路线图

更新时间：2026-05-18

本文档记录 v3.3.2 之后生产稳定化与交付闭环阶段的规划和完成状态。当前阶段已经完成 P0-P8 生产业务闭环接入收束；后续继续推进时，本文档新增的“生产数据沉淀与风险燃尽”作为下一阶段规划事实源。
历史 P 阶段中，当前阶段已经完成 P0-P6 收束，生产候选硬化方向的原始规划入口保留在 `docs/production-candidate-hardening-plan.md`，后续以本文档、`docs/current-state.md` 和各 runtime summary 作为最新事实源。

## 阶段目标

本阶段的目标不是证明“功能能跑”，而是证明“生产能长期稳定运行、问题能被发现、版本能被部署和回滚、性能退化能被阻断、客户端能可靠接入”。

完成后项目应能回答：

- 当前版本如何在云服务器或 Kubernetes 上完整部署、运行、监控和回滚。
- 单实例在固定硬件上能承载多少连接、吞吐、战斗广播负载和资源成本。
- 哪些模块已经进入默认 release/CI 流水线，哪些仍需要固定 runner 或真实依赖环境。
- Redis、Operator、OTel、SDK full-flow、runtime HTTP 观测、长稳 soak 和容量压测是否有持续证据。
- SDK 是否具备生产客户端接入所需的线程模型、心跳、重连、错误诊断和多语言封装边界。

## 下一阶段：生产数据沉淀与风险燃尽

本阶段不继续扩展新的功能模块，重点从“功能和闭环已经能跑”转为“生产环境下能长期稳定、可观测、可回滚、可交付”。所有任务都应尽量接入现有 release / production evidence / business closure 验证入口，并形成可归档的 summary、报告或 runbook 更新。

### N0：固定 Runner 与证据自动化常态化

目标：把 P0-P8 的验证入口从“可手动运行”推进为“可持续沉淀证据”。

任务：

- 整理 release candidate、production resilience、production evidence、P5-P8 business closure、Redis live、raft-ha、OTel runtime、Operator kind 和 K8s full-flow 的固定 runner 运行矩阵。
- 统一 summary 输出字段，至少包含 `overall_pass`、环境信息、构建目录、profile、失败分类、关键 artifact 路径和耗时。
- 将定时运行、手动参数、runner label、依赖预检和 artifact retention 写入固定 runner 文档。
- 对“环境缺失”“外部依赖不可达”“业务逻辑失败”进行清晰分类，避免误判生产能力。
- 在文档中维护证据索引，记录每类能力最后一次通过时间、运行环境和对应产物。

交付物：

- 更新后的 `docs/fixed-runner-playbook.md`
- 更新后的 `docs/production-evidence-runner.md`
- 更新后的 `docs/reliability-matrix.md`
- 固定 runner evidence summary 样例

验收标准：

- 固定 runner 可以按周产出 Redis live、raft-ha、OTel runtime、Operator kind、K8s full-flow 和 P5-P8 business closure 证据。
- 失败时能判断是环境问题、依赖问题还是代码回归。
- 关键证据能被 release checklist 或 current-state 引用。

### N1：长稳压测与容量基线

目标：把性能判断从 smoke 和短测推进到可比较的生产容量事实。

当前收束状态：2026-05-19 已在当前 macOS 机器完成 P0/P1 本机实测收束。
Release baseline 三轮、capacity 三轮、business-capacity 三轮和 long profile
均已通过；产物见 `runtime/validation/p0-release-baseline-summary.json`、
`runtime/perf/p0-capacity-local/`、`runtime/perf/p0-business-capacity-local-r2/`
和 `runtime/validation/p0-long-soak-local-summary.json`。P1 已完成 histogram
精度修正、battle backend 单局锁、gateway 按房间广播索引和压测客户端
response/push 统计修正；`runtime/perf/p1-capacity-battle-lock/` 中
echo-10K 三轮 P99=40ms。随后架构专项完成 response/push 出站优先级隔离、
`battle_finished` 异步 route worker 闭环修复和 battle frame push 降频实验；
`runtime/perf/gateway-arch-priority-route4-push10/` 在当前 macOS 机器上通过
business-capacity release gates，battle-500 P99=400ms、rejected=0、failed=0。
真实 2h/8h wall-clock soak 仍需固定 runner 沉淀。

任务：

- [x] 在当前机器上执行 Release baseline、capacity、business-capacity 和 long profile，记录 P99、吞吐、错误率、连接数和业务成功率。
- [x] 补齐 5K / 10K echo、battle-500、SDK full-flow 并发和 leaderboard 写入路径的本机容量测试。
- [x] 将性能退化阈值固化到报告中，明确吞吐、P99、错误率、RSS/fd 增长的阻断标准。
- [x] 对已知容量退化点建立复测路径，例如连接建立失败、battle rejected、P99 超 500ms。
- [x] 区分默认生产配置、实验参数、Redis on/off、OTel on/off 和后端连接池参数对性能的影响。
- [ ] 在固定机器上执行真实 wall-clock 2h / 8h soak，记录 RSS、fd、CPU、错误率、连接数、P50/P90/P99 和业务成功率。
- [x] P1 专项降低 echo-10K 贴边 P99，并定位 battle-500 剩余瓶颈。
- [x] 对 battle-500 剩余瓶颈进入后续架构专项：异步 gateway route、response/push 通道隔离或 battle state push 降频/聚合。

交付物：

- 更新后的 `docs/performance-baseline.md`
- 长稳 summary 与容量 summary
- 性能退化阈值说明

验收标准：

- 当前机器 P0：Release baseline、capacity、business-capacity 和 long profile 均通过。
- 5K / 10K 和 battle-500 的结果能稳定复现或明确标注瓶颈；当前 echo-10K 已从贴边降到 40ms，battle-500 已通过 response 高优先级出站队列和显式 push 降频实验收束到 P99=400ms。
- 性能报告能支持“是否可投产”和“需要多少机器”的初步判断；固定 runner 2h/8h 数据作为投产前最终证据继续沉淀。

### N2：生产监控 SLO 与告警闭环

目标：把当前 Prometheus / Grafana / OTel 能力推进为可运维的 SLO 体系。

当前收束状态：2026-05-19 已将 gateway-observed backend route latency 从
`avg_latency_us` 趋势升级为默认 `/metrics` 指标：
`gateway_backend_route_latency_us_bucket/_sum/_count` 和
`gateway_backend_<service>_p50/p90/p99_latency_us`。Prometheus
`BoostGatewayHighRouteLatency` 已改为 P99 告警，Grafana 增加 P99 与
`histogram_quantile()` 面板；后端服务仍不直接暴露 HTTP `/metrics`，backend RED
继续由 gateway 聚合指标承载。

任务：

- [x] 梳理 gateway route latency、业务成功率、错误率、连接池、后端路由错误、Redis 可用性和 SDK full-flow 成功率指标。
- [x] 评估并实现 route latency histogram / summary，避免只靠单点快照判断延迟。
- [x] 对后端 metrics 选择明确路线：HTTP exporter、sidecar exporter 或 gateway 聚合指标；在完成前不误宣称后端已有 HTTP metrics。
- [x] 扩展 Prometheus alerts，覆盖错误率、P99、gateway readiness、Redis 不可达、backend routing error、RSS/fd 异常和业务闭环失败。
- [x] 将告警触发、排查、恢复和验证步骤写入运维 runbook。

交付物：

- 更新后的 `env/monitoring/prometheus-alerts.yml`
- 更新后的 Grafana dashboard
- 更新后的 `docs/production-operations-runbook.md`
- 监控静态校验和运行验证摘要：`runtime/validation/monitoring-operability-summary.json`

验收标准：

- 人工制造 backend down、Redis down 或 gateway 错误率上升时，指标和告警能体现。
- 关键业务链路具备 SLI/SLO 描述，backend route latency P99 可由 Prometheus 指标直接读取。
- 运维 runbook 能指导定位和恢复。

### N3：部署恢复、回滚与灾备演练

目标：证明系统在真实部署形态下可以恢复、回滚和继续服务。

当前收束状态：已补 `scripts/check_production_recovery_gate.py`，并接入
`scripts/verify_production_resilience_gate.py` 的默认步骤。该 gate 默认静态验证
Docker Compose、Kubernetes、Redis PVC/volume、rollout/rollback、runbook 和
SDK full-flow 恢复证据；同时已补 `docs/production-recovery-drill-record-template.json`
和 `scripts/check_recovery_drill_record.py`，让真实 Docker/K8s 演练记录具备机器可校验
的 RTO/RPO、观测、验证 summary 和归档口径。真实演练仍应在固定 runner 或生产预演环境显式执行。

任务：

- [x] 在 OrbStack / Docker Compose 和 K8s 路径分别固化部署、健康检查、发布后验证和回滚证据。
- [x] 建立 Redis 备份恢复、后端重启、gateway rolling restart、配置 reload、镜像回滚和网络抖动的演练矩阵。
- [x] 固化生产服务器目录结构、环境变量边界、配置文件路径、密钥挂载和日志采集策略到部署/运维 runbook。
- [x] 对 K8s 补齐 resource requests/limits、PDB、HPA、readiness/liveness 语义和 rollout/rollback 静态证据。
- [x] 将每类演练的 RTO、失败现象和恢复验证写入报告模板。
- [x] 将恢复演练记录模板和记录校验脚本纳入 N3 gate，避免演练 summary 只靠人工描述。
- [ ] 在固定 runner 或真实预演环境执行 Docker/K8s 实操恢复演练并归档 summary。

交付物：

- 更新后的 `docs/production-deployment-runbook.md`
- 更新后的 `docs/production-operations-runbook.md`
- `scripts/check_production_recovery_gate.py`
- `scripts/check_recovery_drill_record.py`
- `docs/production-recovery-drill-record-template.json`
- K8s / Docker recovery summary
- 备份恢复演练记录

验收标准：

- `python3 scripts/check_production_recovery_gate.py` 通过，并写出 `runtime/validation/production-recovery-summary.json`。
- Docker Compose 和 K8s 都有发布后 SDK full-flow 验证入口。
- 关键故障恢复有可执行步骤、RTO/RPO 记录和验证命令。
- 固定 runner 或预演环境生成的恢复演练记录可通过 `scripts/check_recovery_drill_record.py` 校验。
- 回滚流程不依赖口头经验；固定 runner 实操演练作为后续证据持续沉淀。

### N4：传输安全与配置治理升级

目标：将 TLS/mTLS、配置治理和生产变更流程从边界说明推进到可灰度验证。

任务：

- [x] 明确当前 plain TCP 默认边界、TLS/mTLS 可选 profile 和上线前置条件。
- [x] 接入 TLS/mTLS profile 边界验证入口，并通过 N4 聚合门禁归档。
- [x] 补充证书生成、加载、轮转、过期告警和回滚策略。
- [x] 统一热重载、重启生效和环境变量配置的分类文档，明确生产变更审批和回滚方式。
- [x] 对配置变更增加 drift check，避免 Docker、Helm、K8s 和本地配置互相漂移。
- [x] backend 服务端 TLS listener、五个 backend 入口配置接入、Docker/K8s/Helm Secret/volume profile 和 backend TLS request/response 实测已补齐。
- [x] TLS profile 下完整 SDK full-flow 本机实操已通过，并可由 N4 gate 显式启用归档。
- [ ] TLS profile 固定 runner / 预发多轮演练、证书轮换演练和性能损耗对比仍作为上线前置条件，不计入默认 plain TCP 生产链路。

交付物：

- 更新后的 `docs/tls-mtls-runbook.md`
- 更新后的 `docs/production-configuration-runbook.md`
- TLS/mTLS profile 验证摘要
- 配置漂移检查规则
- `scripts/check_transport_config_governance.py`
- backend TLS listener 集成测试：`V2BackendRoutingTest.BackendTlsListenerCompletesLoginRequest`
- TLS profile SDK full-flow summary：`runtime/validation/n4-tls-full-flow-summary.json`

验收标准：

- `python3 scripts/check_transport_config_governance.py --summary-path runtime/validation/n4-transport-config-governance-summary.json` 通过。
- `python3 scripts/check_transport_config_governance.py --include-tls-full-flow --build-dir build/release` 在本机或固定 runner 通过。
- TLS/mTLS 是否可上线有明确证据，不停留在占位配置；当前结论是默认生产仍为 plain TCP，TLS transport 上线仍需固定 runner / 预发多轮演练、证书轮换和性能损耗证据。
- 每个生产配置项都能判断修改入口、生效方式和回滚方式。
- 配置漂移能被脚本或 release gate 发现。

### N5：SDK 企业交付与客户端兼容矩阵

目标：让 SDK 具备客户端团队可稳定接入、升级和排障的交付质量。

当前收束状态：N5 企业交付门禁已覆盖 SDK distribution、外部 CMake package
consumer、in-process business-flow、真实 gateway full-flow，以及 N4 backend TLS profile
下的真实 gateway full-flow。SDK 对外 API 仍连接默认 TCP gateway；backend TLS profile
由服务端部署治理，对客户端保持透明。

任务：

- [x] 固化 C++、C ABI、Python、C# 的版本兼容矩阵和 native library 加载诊断。
- [x] 完善 SDK 错误码、超时、heartbeat、reconnect、disconnect callback 和 push 分发语义。
- [x] 为 C++、Python、C# 保留真实 gateway full-flow example，并接入 N5 聚合门禁。
- [x] 增加 SDK package consumer matrix，覆盖安装、链接、运行、版本不匹配和错误诊断。
- [x] 明确客户端接入建议，包括线程模型、回调线程、资源释放、并发限制和生产日志字段。
- [x] 将 backend TLS profile 下的真实 gateway SDK full-flow 纳入 N5 交付证据，保证 SDK API 与 N4 服务端 TLS profile 兼容。
- [ ] 正式 wheel/NuGet 签名与多平台包仓库发布仍为后续客户端发布专项。

交付物：

- 更新后的 `sdk/docs/README.md`
- 更新后的 `sdk/docs/compatibility.md`
- SDK full-flow / package consumer summary
- backend TLS profile SDK full-flow summary
- Python/C# 生产接入示例说明
- `scripts/verify_sdk_enterprise_delivery.py`

验收标准：

- 客户端能按文档独立完成安装、连接、登录、进房、战斗、排行榜、重连和排障。
- 版本不匹配和 native 加载失败能给出清晰错误。
- `python3 scripts/verify_sdk_enterprise_delivery.py --build-dir build/release --skip-build` 通过，并归档 distribution、package consumer、business-flow、real gateway full-flow 和 backend TLS real gateway full-flow 子 summary。
- SDK 行为与真实服务端业务链路保持同步。

### N6：gRPC / 协议演进 PoC 与生产取舍

目标：在不影响默认 TCP 主链的前提下，用实测决定 generated gRPC transport 是否值得进入后续主线。

任务：

- [x] 基于现有 v3 proto 契约建立 N6 PoC 决策门禁，限定在独立 profile 或示例中，不替换默认 TCP 主链。
- [x] 使用 TCP gateway + BackendEnvelope release baseline 作为当前对照基线；generated gRPC benchmark 缺失时结论保持实验保留。
- [x] 明确兼容迁移策略，避免一次性替换主链导致生产风险。
- [x] 将结论沉淀到 ADR，给出继续推进、保留实验或暂停的判定条件。
- [ ] 真实 generated gRPC transport profile、SDK full-flow 和 benchmark 仍未进入默认生产链路，作为后续独立 PoC 专项。

交付物：

- 更新后的 `docs/v3-proto-grpc-adr.md`
- `scripts/check_v3_grpc_poc_decision.py`
- N6 PoC decision summary
- TCP baseline 对照和迁移风险清单

验收标准：

- `python3 scripts/check_v3_grpc_poc_decision.py --build-dir build/release` 通过。
- gRPC 是否进入下一阶段有数据支撑；当前结论是保留实验，不进入默认生产链路。
- 默认生产主链不因 PoC 引入不稳定性。
- 协议演进路径和回退策略清晰。

## R 阶段：生产候选实证与上线前置燃尽

N0-N6 已完成默认有界收束后，R 阶段不扩功能，目标是把“可以跑的验证入口”收成“投产前可反复执行、可归档、可解释失败原因”的候选证据。R0/R1 默认仍是本机/固定 runner 可运行的有界门禁；真实 2h/8h soak、多轮预发、跨节点和云上容量数据继续由固定 runner 或预发环境沉淀。

### R0：生产候选证据聚合

当前收束状态：已新增 `scripts/verify_production_candidate_evidence.py`，默认聚合 fixed-runner preflight、P5 production resilience、P6 production evidence、N5 SDK enterprise delivery；可显式追加 N4 backend TLS full-flow 和 N6 gRPC PoC decision。该入口用于发布前一次性确认生产候选证据没有被文档、脚本、SDK 或传输治理回归打断。

任务：

- [x] 将 P5 resilience、P6 production evidence、N5 SDK enterprise delivery 聚合成 R0 生产候选入口。
- [x] 支持 `--include-tls-full-flow` 和 `--include-n6-grpc-decision`，把 N4/N6 的高风险边界纳入同一次候选实证。
- [x] 输出 `runtime/validation/r0-production-candidate-evidence-summary.json`，记录失败分类、子 summary 路径和执行范围。
- [x] 将生产候选完整性审核继续作为 P6/R0 的文档一致性阻断项，避免文档与实际交付漂移。
- [ ] 固定 runner / 预发环境继续追加 Redis live、Operator kind、runtime HTTP、release/capacity baseline 和更长 soak。

验收标准：

- `python3 scripts/verify_production_candidate_evidence.py --build-dir build/release --skip-build --include-tls-full-flow --include-n6-grpc-decision` 通过。
- R0 summary 能定位失败属于环境预检、生产韧性、生产证据、SDK 交付、TLS 治理或 gRPC 取舍。

### R1：TLS 上线前置证据

当前收束状态：已新增 `scripts/verify_tls_production_readiness.py`，在本机真实启动 gateway、五个 backend 和 SDK full-flow client，覆盖默认 TLS profile、server CA 校验、证书轮换后的 full-flow、CA 不匹配失败诊断，以及 plain/TLS 单次业务闭环耗时对比。`scripts/gen_certs.py` 已支持输出到指定目录，`scripts/verify_sdk_full_flow_client.py` 已支持指定证书目录和 gateway TLS verify mode。

任务：

- [x] 将 backend TLS profile full-flow 从“默认开发证书”扩展为可指定证书目录、CA 和 verify mode 的验证入口。
- [x] 生成 current / rotated / mismatched 三套证书，验证轮换证书能完成业务闭环，错误 CA 会触发可诊断失败。
- [x] 对 plain TCP 与 backend TLS profile 的 SDK full-flow 做 smoke 级耗时对比，并输出到 R1 summary。
- [x] 将 TLS 上线前置命令写入 TLS runbook，明确它是上线前置证据，不代表默认生产已启用 TLS。
- [ ] 预发/固定 runner 多轮 TLS 性能、证书过期告警、mTLS client cert 缺失和服务名校验仍需继续沉淀。

验收标准：

- `python3 scripts/verify_tls_production_readiness.py --build-dir build/release --skip-build` 通过。
- R1 summary 同时包含 plain full-flow、TLS full-flow、rotated TLS full-flow、mismatched CA expected failure 和耗时对比。

### R2：生产候选证据 Manifest 与预发准入

当前收束状态：已新增 `docs/production-candidate-evidence-manifest.json` 与 `scripts/check_production_evidence_manifest.py`。R2 把 R0/R1 以及固定 runner / 预发多轮证据归一到可校验 manifest，默认要求本机有界候选证据全部存在且通过；运行 `--require-fixed-runner` 时会把 release/capacity、恢复演练和 TLS 预发多轮证据提升为阻断项，用于最终投产审批前检查。

任务：

- [x] 建立生产候选 evidence manifest，统一记录 evidence id、类别、summary 路径、freshness 和固定 runner 要求。
- [x] 校验 R0/R1 必选 summary 是否存在、是否通过、是否在 freshness 窗口内。
- [x] 校验 R0 子 summary 是否被 R0 aggregate artifacts 引用，避免孤立 JSON 被误当作聚合证据。
- [x] 保留固定 runner / 预发证据入口，并通过 `--require-fixed-runner` 切换为投产前阻断项。
- [ ] 在固定 runner / 预发环境填充 `fixed_runner_release_capacity`、`preprod_recovery_drill` 和 `tls_preprod_multi_run` 的真实 summary。

验收标准：

- `python3 scripts/check_production_evidence_manifest.py` 在当前本机 R0/R1 证据齐全时通过。
- `python3 scripts/check_production_evidence_manifest.py --require-fixed-runner` 在最终投产前必须通过；当前若缺少固定 runner / 预发 summary，应明确失败而不是误报投产就绪。

### R3：生产 Readiness Report

当前收束状态：已新增 `scripts/render_production_readiness_report.py`，读取 R2 manifest summary、R2 fixed-runner 准入 summary、R0 aggregate summary 和 R1 TLS readiness summary，生成 `runtime/validation/r3-production-readiness-report.md` 与机器可读 summary。默认判定以本机有界证据为通过条件，同时单独输出 `final_production_ready`，避免把固定 runner / 预发缺口误报成已投产就绪。

任务：

- [x] 将 R0/R1/R2 的 JSON summary 汇总为投产评审 Markdown。
- [x] 在报告顶部明确区分 bounded local candidate evidence 与 final production fixed-runner/pre-production readiness。
- [x] 将 `fixed_runner_release_capacity`、`preprod_recovery_drill`、`tls_preprod_multi_run` 列为最终投产阻断项。
- [ ] 固定 runner / 预发 summary 补齐后，R3 report 应自然更新为 `final_production_ready=true`。

验收标准：

- `python3 scripts/render_production_readiness_report.py` 通过，并生成 Markdown 报告。
- 报告必须展示 R2 evidence table、最终投产阻断项、R0 聚合步骤和 R1 TLS 耗时对比。

### R4：固定 Runner Release / Capacity 证据

当前收束状态：已新增 `scripts/verify_fixed_runner_release_capacity.py`，用于把 release baseline、capacity profile 和 business-capacity profile 汇总为 R2 manifest 需要的 `runtime/validation/fixed-runner-release-capacity-summary.json`。脚本默认消费已有固定 runner / 本机实测性能产物；也可以通过 `--collect-smoke` 追加一次 fresh smoke，避免最终报告只依赖历史 JSON。

任务：

- [x] 校验 release baseline aggregate summary 是否通过。
- [x] 校验 capacity profile 覆盖 `echo-1000`、`echo-5000`、`echo-10000`、`battle-100`、`battle-500` 且 release gates 通过。
- [x] 校验 business-capacity profile 覆盖 `echo-1000`、`battle-100`、`battle-500`，并要求 SDK full-flow business path 通过。
- [x] 将 R4 producer 写入 R2 evidence manifest，使 `--require-fixed-runner` 能消费该 summary。
- [ ] 在固定低噪声性能机器上重跑 release/capacity/business-capacity 多轮，并用固定 runner 产物替换本机历史产物。

验收标准：

- `python3 scripts/verify_fixed_runner_release_capacity.py` 通过，并生成 `runtime/validation/fixed-runner-release-capacity-summary.json`。
- 重新运行 `python3 scripts/check_production_evidence_manifest.py --require-fixed-runner` 时，`fixed_runner_release_capacity` 不再是阻断项。

### R5：预发恢复 / 回滚演练证据

当前收束状态：已新增 `scripts/verify_preprod_recovery_drill.py`，在 Docker 可用且镜像已存在时执行真实 Docker Compose gateway restart 演练：启动生产栈、等待 gateway ready、重启 gateway、再次等待 ready、运行 SDK full-flow、采集 Docker production snapshot，并生成/校验 recovery drill record。没有 Docker 时可保留 bounded-local 模式，但投产前应使用真实预发记录。

本机真实演练补充结论：R5 已暴露两个生产部署缺口，并已在代码层修复。第一，Docker builder 镜像缺 `python3` 会导致当前 CMake/proto/gRPC PoC 配置失败；`env/docker/Dockerfile.backend` 与 `env/docker/Dockerfile.gateway` 已补齐。第二，gateway backend connection pool 默认值不应进入当前实验多连接路径；`src/v2/gateway/gateway_service_bridge.cpp` 默认值和 Compose 显式环境变量已收敛到 `V2_BACKEND_CONNECTION_POOL_SIZE=1`，直到 backend 长连接复用协议完成生产化。

任务：

- [x] 将 N3 recovery gate、SDK full-flow、Docker snapshot 和 drill record validator 串成 R5 producer。
- [x] 输出 `runtime/validation/preprod-recovery-drill-summary.json`，供 R2 manifest 消费。
- [x] 生成 `runtime/validation/r5-preprod-recovery-drill-record.json` 并通过 `scripts/check_recovery_drill_record.py` 校验。
- [x] 修复 Docker builder 缺 `python3` 导致生产镜像无法重建的问题。
- [x] 将 gateway backend pool 生产默认收敛为 1，避免实验多连接池破坏 SDK full-flow leaderboard 闭环。
- [ ] 在云端预发或固定 Docker/K8s 环境补 gateway/backend/Redis/rollback 多场景记录。

验收标准：

- `python3 scripts/verify_preprod_recovery_drill.py --build-dir build/release` 通过。
- R2 `--require-fixed-runner` 不再阻断 `preprod_recovery_drill`。

### R6：TLS 预发多轮证据

当前收束状态：已新增 `scripts/verify_tls_preprod_multi_run.py`，多次调用 R1 TLS readiness，聚合 TLS full-flow、证书轮换、CA mismatch expected failure 和 plain-vs-TLS overhead ratio，输出 `runtime/validation/tls-preprod-multi-run-summary.json`。

本机验证注意：R6 会启动本机服务并绑定临时 TCP 端口。普通沙箱会在 `reserve_free_port()` 阶段触发 `Operation not permitted`，因此最终 R6 evidence 应在已授权的本机或固定 runner 上刷新。

任务：

- [x] 将 R1 TLS readiness 从单次 smoke 扩展为多轮聚合 evidence。
- [x] 聚合每轮 plain/TLS full-flow 耗时和 overhead ratio。
- [x] 将 R6 producer 写入 R2 evidence manifest。
- [ ] 在预发环境继续补充服务名校验、client cert 缺失、过期证书告警和容量级 TLS 性能。

验收标准：

- `python3 scripts/verify_tls_preprod_multi_run.py --build-dir build/release --skip-build` 通过。
- R2 `--require-fixed-runner` 不再阻断 `tls_preprod_multi_run`。

## 下一阶段推荐执行顺序

1. N0 固定 Runner 与证据自动化常态化。
2. N1 长稳压测与容量基线。
3. N2 生产监控 SLO 与告警闭环。
4. N3 部署恢复、回滚与灾备演练。
5. N4 传输安全与配置治理升级。
6. N5 SDK 企业交付与客户端兼容矩阵。
7. N6 gRPC / 协议演进 PoC 与生产取舍。
8. R0 生产候选证据聚合。
9. R1 TLS 上线前置证据。
10. R2 生产候选证据 Manifest 与预发准入。
11. R3 生产 Readiness Report。
12. R4 固定 Runner Release / Capacity 证据。
13. R5 预发恢复 / 回滚演练证据。
14. R6 TLS 预发多轮证据。

推荐先从 N0 和 N1 开始，因为它们会直接暴露当前系统在真实生产环境下的波动、容量边界和回归风险；N2-N5 再围绕这些事实完善观测、部署、配置和客户端交付；N6 放在最后，避免在主链稳定前引入新的传输复杂度。

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
