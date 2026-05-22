# 当前项目事实源

更新时间：2026-05-23

本文档作为当前进度的入口事实源。版本号以 `CMakeLists.txt` 中的 `BoostAsioDemo VERSION 3.4.0` 为准；提交状态以 `git HEAD` 为准。

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
- P5 长稳/故障/回滚：`scripts/verify_production_resilience_gate.py` 聚合固定 runner 预检、stability soak、data recovery、Redis/Raft/Operator specialized E2E，并可显式追加 Redis live、Operator kind、runtime HTTP observability、release/capacity baseline；默认入口保持有界，summary 写入 `runtime/validation/production-resilience-summary.json`。
- P6 生产证据聚合：`scripts/verify_production_evidence_gate.py` 将 stability soak、P3 data recovery、Redis/Raft/Operator specialized E2E、生产候选完整性审核与可选 release/capacity baseline 聚合为一个固定 runner 入口；默认模式保持有界，长稳、Redis live、Operator kind、settlement replay、capacity baseline 通过显式参数启用。本机 P6 收束验证已覆盖 Release 构建、Redis live、Operator kind 和 3 轮 Release baseline，交付记录见 `docs/releases/v3.3.2-p6-production-evidence.md`。
- P2 固定 runner 证据：`.github/workflows/production-evidence.yml` 已支持 JSON runner 输入、preflight summary 归档、Redis/kind 真实依赖、runtime HTTP observability、release baseline 和 capacity baseline 的手动固定 runner 场景；配置说明见 `docs/production-evidence-runner.md`。
- N0 固定 runner 常态化：`release-baseline.yml`、`specialized-e2e.yml` 已补齐 JSON runner、preflight summary 归档和统一 Step Summary 渲染；`check_fixed_runner_environment.py`、`render_validation_summary.py` 与各聚合 gate summary 已统一到 `summary_version=2`、`overall_pass`、`failed_category`、`environment`、`artifacts` 契约。本地收束证据见 `runtime/validation/n0-release-baseline-preflight-summary.json`、`runtime/validation/n0-specialized-preflight-summary.json`、`runtime/validation/n0-specialized-raft-ha-summary.json`。
- N1 性能证据索引：`docs/performance-baseline.md` 已补 baseline / capacity / bounded soak / long soak / business-flow perf / business-capacity / docker snapshot 统一归档口径，`verify_stability_soak.py` 新增 `long` / `overnight` profile，`collect_v2_perf_baseline.py` 新增 `business-capacity` 与 `business_flow_clients` 入口，作为后续 2h/8h soak、5K/10K capacity、battle-500 与业务并发复测事实源；当前仍保留“固定 runner 实测数据待继续沉淀”的边界，不把短样本误宣称为生产上限。
- N2 监控 SLO：Prometheus alerts 已新增 `BoostGatewayHighRouteLatency`、`BoostGatewayBusinessFlowFailure`，Grafana dashboard 已新增 route latency 与 business-flow success 面板；`docs/production-operations-runbook.md` 已明确 SLI/SLO 口径和告警响应流程。本地收束验证见 `runtime/validation/monitoring-operability-summary.json` 与 `runtime/validation/n2-observability-summary.json`。
- N3 部署恢复/回滚：`scripts/check_production_recovery_gate.py` 已补默认有界静态门禁，覆盖 Docker Compose、Kubernetes rollout/rollback、Redis volume/PVC、RTO/RPO、SDK full-flow 恢复验证和运维记录模板，并接入 `scripts/verify_production_resilience_gate.py` 默认步骤；`docs/production-recovery-drill-record-template.json` 与 `scripts/check_recovery_drill_record.py` 已将真实演练记录固化为可校验 JSON。真实 Docker/K8s 演练仍由固定 runner 或预演环境持续归档。
- N4 传输安全与配置治理：`scripts/check_transport_config_governance.py` 已聚合 TLS/mTLS profile 边界和配置漂移检查；backend 服务端 opt-in TLS listener、五个 backend 入口配置接入、Docker/K8s/Helm Secret/volume profile、backend TLS request/response 实测和本机 TLS profile SDK full-flow 已补齐。默认生产结论仍是 plain TCP，TLS transport 上线需要固定 runner / 预发多轮演练、证书轮换和性能损耗额外证据。
- N5 SDK 企业交付：`scripts/verify_sdk_enterprise_delivery.py` 已聚合 SDK distribution、package consumer、in-process business-flow、真实 gateway full-flow 和 backend TLS profile 下的真实 gateway full-flow；`sdk/docs/compatibility.md` 已补 C++/C ABI/Python/C# 客户端兼容矩阵，`sdk/docs/README.md` 已补生产客户端接入清单和 plain TCP / backend TLS profile 的客户端边界。
- N6 gRPC/proto 取舍：`scripts/check_v3_grpc_poc_decision.py` 已补 v3 proto/gRPC PoC 决策门禁，验证 schema/transport contract、CMake target、TCP baseline 对照和 ADR 边界；当前结论是 generated gRPC 保留实验，不进入默认生产链路。
- R0 生产候选证据聚合：`scripts/verify_production_candidate_evidence.py` 已聚合 fixed-runner preflight、P5 production resilience、P6 production evidence、N5 SDK enterprise delivery，并可显式追加 N4 TLS full-flow 与 N6 gRPC PoC decision；summary 写入 `runtime/validation/r0-production-candidate-evidence-summary.json`。
- R1 TLS 上线前置证据：`scripts/verify_tls_production_readiness.py` 已覆盖 TLS profile full-flow、server CA 校验、证书轮换 full-flow、CA 不匹配 expected failure 诊断和 plain/TLS 单次业务闭环耗时对比；默认生产仍是 plain TCP，R1 只作为启用 backend TLS profile 前的前置证据。
- R2 生产候选证据 Manifest：`docs/production-candidate-evidence-manifest.json` 与 `scripts/check_production_evidence_manifest.py` 已将 R0/R1 本机有界证据、固定 runner release/capacity、预发恢复演练和 TLS 预发多轮证据统一成可校验 manifest；默认校验 R0/R1，`--require-fixed-runner` 用于投产前阻断缺失的固定 runner / 预发 summary。
- R3 生产 Readiness Report：`scripts/render_production_readiness_report.py` 已将 R2 manifest、R0 aggregate 和 R1 TLS readiness 汇总为 Markdown 报告与机器 summary；报告明确区分 bounded local evidence 与 final production readiness，当前固定 runner / 预发缺口仍会作为最终投产阻断项展示。
- R4 固定 Runner Release / Capacity 证据：`scripts/verify_fixed_runner_release_capacity.py` 已将 release baseline、capacity profile 和 business-capacity profile 汇总成 `runtime/validation/fixed-runner-release-capacity-summary.json`，用于解除 R2/R3 中 `fixed_runner_release_capacity` 阻断；最终投产仍建议在固定低噪声性能机器上刷新该 summary。
- R5 预发恢复 / 回滚演练证据：`scripts/verify_preprod_recovery_drill.py` 已将 N3 recovery gate、Docker Compose gateway restart、SDK full-flow、Docker production snapshot 和 recovery drill record validator 串成 `runtime/validation/preprod-recovery-drill-summary.json` producer。本机真实演练已发现并修复 Docker builder 缺 `python3`、gateway backend pool 默认进入实验多连接路径的问题；最终 R5 复测仍需在 Docker 授权恢复后重新执行。
- R6 TLS 预发多轮证据：`scripts/verify_tls_preprod_multi_run.py` 已多轮聚合 R1 TLS readiness，覆盖 TLS full-flow、证书轮换、CA mismatch expected failure 和 plain-vs-TLS overhead ratio，输出 `runtime/validation/tls-preprod-multi-run-summary.json`。该脚本需要本机端口绑定权限；当前普通沙箱会因 `Operation not permitted` 失败，需在授权环境下刷新最终 summary。
- P3 监控运维：Prometheus 已加载 `env/monitoring/prometheus-alerts.yml`，Grafana dashboard 已对齐当前 gateway `/metrics` 真实指标，`scripts/check_monitoring_operability.py` 会阻断后端 HTTP scrape、旧指标名和 runbook 漂移；运维流程见 `docs/production-operations-runbook.md`。
- P4 SDK 企业级封装：C++ SDK heartbeat 已实作，disconnect callback 可由 heartbeat failure 触发；C ABI 暴露 heartbeat 控制，Python/C# wrapper 增加 native 版本校验和加载/分配诊断；SDK business-flow 与 full-flow client 验证覆盖 login、room、ready、battle、push、reconnect、heartbeat。
- H0-H5 生产候选硬化：`scripts/check_production_hardening_gate.py` 聚合固定 runner 定时入口、长稳/容量/K8s/观测/SDK 企业接入证据；`production-resilience.yml` 与 `production-evidence.yml` 已具备 weekly schedule 和 runner fallback。
- 生产性能快照：`scripts/collect_docker_production_perf_snapshot.py` 已补齐 OrbStack / Docker Compose 生产栈运行态采样入口，覆盖 gateway readiness/diagnostics、Prometheus targets、Grafana health 和容器 CPU/RSS/PID/IO 快照；本机实测 `overall_pass=true`，产物见 `runtime/perf/docker-production-snapshot/`。
- 生产业务闭环接入：`docs/production-business-closure-plan.md` 已完成 P0-P8 收束。P0-P2 打通 SDK matchmaking/leaderboard、full-flow 和 battle settlement 自动写榜；P3-P4 将新业务路径纳入性能/监控/快照，并完成 Redis/Raft HA profile；P5-P8 补齐 OTel/trace、TLS 边界、K8s/Operator full-flow 入口和 v3 proto/gRPC ADR。聚合验证入口为 `scripts/verify_p5_p8_business_closure.py`。
- P0-P7 框架现代化与坦克大战 demo：已按 `docs/realtime-framework-modernization-plan.md` 完成 P0-P7 全部 checkpoint。P0 目录与文档结构固化；P1 identity 注册协议与错误码完成；P2 房间大厅支持 list/detail/kick/transfer；P3 实时实例运行时（`v2::realtime::InstanceRuntime`）实现 tick-based 游戏循环；P4 坦克大战仿真（`TankWorld` 20×15 网格）含运动/碰撞/子弹/得分；P5 settlement 与 leaderboard 数据结构就绪；P6 resume/reconnect 支持；P7 回归门禁与验证脚本覆盖 642 测试 + 8 个 checkpoint。demo 全部位于 `demo/games/tank_battle/`，默认不参与生产构建（`BOOST_BUILD_TANK_DEMO=OFF`）。
- R4/R5 ECS 管线增强与 TankBattlePlugin：`InstancePlugin` SPI 正式化（8 虚方法 + noexcept 契约 + try-catch 错误隔离），`TankBattlePlugin` 完整实现（move/attack/shoot/finish）位于 `src/v2/battle/`。新增 ECS 系统：`ProjectileSystem`（弹道飞行/AoE/DoT）、`BattleLifecycleSystem`（自动状态机 kCreated→kRunning→kFinished，空闲超时 300 帧，离线超时 60 帧）、`BattleReplaySystem`（逐帧快照录制）、`AoiSystem`（ECS 集成 AOI + SpatialGrid）。新增组件：`ProjectileComponent`、`DamageOverlayComponent`、`BattleReplayFrameRecord`。数据持久化层：`CachedBattleDataStore`（LRU + WriteBehind）接入 demo_server，`JsonFileBattleDataStore` 文件落地。远程 Actor 通信：`RemoteActorRef::tell()` 跨节点消息投递。gRPC 网关：`GatewayGrpcServer`（login/logout/health）`BOOST_BUILD_GRPC` 编译开关。62+ 新增测试覆盖 lifecycle/replay/AOI/spatial grid/file store/tank battle plugin/projectile system。
- N1 性能刻度（perf scaling）：`demo/games/tank_battle/tests/perf_test.cpp` 新增 500 实例 × 50  ticks 基准规格（保守阈值 100 TPS），覆盖 2/20/100/500 四级并发刻度用于 CI 性能回归。
- N5 SDK Python 示例：`demo/games/tank_battle/client_sdk_adapter/python_demo.py` 作为坦克大战 Python SDK demo，走通 connect→login→room→battle→move→finish→leaderboard→disconnect 全生命周期，输出 JSON 摘要并支持 `--n5-demo` 集成到 `verify_tank_battle_demo.py` 验证脚本。

## 保留边界

- 2h/8h soak、10K 连接生产容量基线、跨节点 Redis/Raft、更完整 Operator rollback/probe E2E、更完整角色化 RBAC、外部 OTel collector 长稳、Prometheus P99 告警灵敏度多轮实测和 generated gRPC transport PoC 仍属于固定 runner/后续专项；默认生产主链仍是 SDK + TCP gateway + BackendEnvelope + 五后端 + Redis。
- 主线定位为企业级高性能实时服务框架。坦克大战和后续游戏/实时系统样例必须放在 `demo/games/` 作为业务验证 demo，不能把碰撞、地图、胜负、得分公式等业务规则写入 gateway、login、room、leaderboard 或公共 SDK。框架与业务边界以 `docs/realtime-framework-modernization-plan.md`、`docs/realtime-framework-module-boundaries.md`、`docs/realtime-framework-sdk-boundary.md` 和 `demo/games/README.md` 为准。
- 默认 CI/release workflow 使用有界 smoke 门禁，避免长时间占用终端或 runner。
- 文档出现编码显示异常时，以 UTF-8 文件内容和 CI 校验结果为准，PowerShell 控制台乱码不代表文件编码错误。

## 当前阶段结论

生产稳定化、交付闭环、生产业务闭环接入、N0-N6 生产数据沉淀与风险燃尽，以及 R0-R3 生产候选实证阶段已经完成当前有界收束。当前主线具备生产候选所需的默认有界 gate、固定 runner 入口、部署/运维/SDK 文档、监控告警静态校验、P5 resilience gate、P6 production evidence gate、P5-P8 business closure gate、N3 recovery gate、N4 transport/config governance gate、N5 SDK enterprise delivery gate、N6 gRPC PoC decision gate、R0 production candidate evidence gate、R1 TLS production readiness gate、R2 evidence manifest gate、R3 readiness report 和生产候选完整性审核。

P0-P7 框架现代化已在 `main` 分支提交，commit 范围 `7bb4898..5a43edd`。

当前默认可执行入口：

1. 本地/PR 快速：`python3 scripts/verify_release_candidate.py --skip-release-baseline --soak-profile smoke`
2. 坦克大战 demo 验证：`python3 demo/games/tank_battle/scripts/verify_tank_battle_demo.py --build-dir build`
3. P5 resilience：`python3 scripts/verify_production_resilience_gate.py --build-dir build/default --skip-build`
3. P6 production evidence：`python3 scripts/verify_production_evidence_gate.py --build-dir build/default --skip-build`
4. P5-P8 business closure：`python3 scripts/verify_p5_p8_business_closure.py --build-dir build/default --skip-build`
5. N4 transport/config governance：`python3 scripts/check_transport_config_governance.py`
6. N5 SDK enterprise delivery：`python3 scripts/verify_sdk_enterprise_delivery.py --build-dir build/default --skip-build`
7. N6 gRPC PoC decision：`python3 scripts/check_v3_grpc_poc_decision.py --build-dir build/default`
8. R0 production candidate evidence：`python3 scripts/verify_production_candidate_evidence.py --build-dir build/default --skip-build`
9. R1 TLS production readiness：`python3 scripts/verify_tls_production_readiness.py --build-dir build/default --skip-build`
10. R2 production evidence manifest：`python3 scripts/check_production_evidence_manifest.py`
11. R3 production readiness report：`python3 scripts/render_production_readiness_report.py`
12. R4 fixed-runner release/capacity evidence：`python3 scripts/verify_fixed_runner_release_capacity.py`
13. R5 preprod recovery drill：`python3 scripts/verify_preprod_recovery_drill.py --build-dir build/default`
14. R6 TLS preprod multi-run：`python3 scripts/verify_tls_preprod_multi_run.py --build-dir build/default --skip-build`
15. 生产候选审核：`python3 scripts/check_production_candidate_audit.py`

## 下一阶段优先级

当前“生产数据沉淀与风险燃尽”以 `docs/production-stabilization-roadmap.md` 的 N0-N6 与 R0-R3 为事实源，默认有界收束已经完成；长稳 2h/8h、10K 固定机器容量、TLS 预发多轮性能、真实 gRPC transport profile 等继续作为固定 runner 或后续专项持续沉淀。

业务验证型下一阶段以”框架与业务隔离”为前提：`docs/realtime-framework-modernization-plan.md` 的 M0-M5 已全部完成，identity、lobby/room、realtime instance、business plugin SPI、SDK 通用 API 和 demo gate 边界均已固化。

近期服务端实施以 `docs/server-framework-and-tank-demo-development-plan.md` 为执行计划：P0-P7 全部 checkpoint 已完成，包含坦克大战 demo 的运行、结算、断线重连、性能回归门禁。当前阶段不实现正式客户端。

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
