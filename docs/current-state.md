# 当前项目事实源

更新时间：2026-07-10

本文档作为当前进度的入口事实源。版本号以 `CMakeLists.txt` 中的 `boost_gateway VERSION 3.5.0` 为准；提交状态以 `git HEAD` 为准。

legacy/helper 迁移边界与 v1 兼容面清单见 `docs/legacy/legacy-helper-inventory.md`。
普通 branch push / PR 不再自动触发流水线；自动触发只保留特定 release tag，当前约定为 `v*`。`.github/workflows/release.yml` 在推送 `v*` tag 时自动执行 release package/publish；`.github/workflows/ci.yml` 也只在 `v*` tag 或手动 dispatch 时运行 Linux Conan 主线验证。`.github/runner-matrix.json` 作为版本化 runner/默认标签配置源；固定 runner / production evidence / release-capacity 默认事实源按同一文件的 `default_runner` / workflow runner 配置收敛到 Linux labels。性能 smoke、nightly stability、fixed-runner evidence、release/capacity 等入口保留 `workflow_dispatch`，具体触发条件以 `.github/workflows/*.yml` 为准。
2026-07-09 的 GitHub 仓库 runner inventory 实际状态是：仅存在离线 Windows self-hosted runner `MyDesktop-Win`，尚无在线 Linux `self-hosted/Linux/X64` runner；因此 fixed-runner evidence workflow 当前能被 dispatch，但不会真正开始执行，直到 Linux runner 注册并上线。

## 稳定能力

- v1.x：维护期能力已经收束，v1 代码已从仓库移除（`include/game`、`src/game`、老示例、v1 测试）。
- v2.x：当前主线。`ActorSystem`、gateway-only ingress、五个后端服务、`BackendEnvelope`、typed envelope adapter、服务健康检查、TTL/readiness、WriteBehind drain 统计与失败上报已经进入可验收状态。
- R4 契约门禁：`scripts/verify_r4_contract.py` 覆盖通信契约、后端恢复、typed envelope、proto schema、gateway-only ingress 和短架构基线入口。
- 稳定性门禁：`scripts/verify_stability_soak.py` 覆盖 I/O accept policy、WriteBehind drain/failure、backend timeout/recovery 和短架构基线，提供 `smoke`、`short`、`medium` soak profile；`.github/workflows/nightly-stability.yml` 已将 `short` profile 纳入夜间任务。
- 后端稳定性：`BackendServer` 已支持多会话跟踪与关闭收口；plain TCP `read_frame()` 使用 Boost.Asio non-blocking bounded read，避免 POSIX `select()` 限制。
- RC 总门禁：`scripts/verify_release_candidate.py` 汇总可靠性矩阵、R4 契约、稳定性 soak 和可选 Release baseline，并输出结构化 summary。
- 安全发布门禁：`scripts/check_security_release_gate.py` 检查生产模式禁用 dev token fallback 的证据、admin 审计最小键和 ACL 边界说明。

## 增量能力

- v3 proto/gRPC：schema 校验、CMake target 和 release checklist 已存在，当前定位为传输契约与构建入口，不作为默认生产链路。
- Redis/Raft/Operator：已通过专项 E2E 形成独立可靠性闭环；默认发布仍保持有界 smoke，固定本机/runner 可显式启用 Redis live 与 Operator kind 验证。
- Release baseline：`scripts/collect_release_baseline.py` 现在聚合 R4 release contract 与 v2 多进程 `echo/battle` 性能采集；默认 `baseline` profile 适合固定机器执行，`capacity` 与 `business-capacity` profile 用于 5K/10K 连接、battle-500 和 SDK full-flow 业务容量专项；`.github/workflows/release.yml` 已提供手动/定时入口，当前也可在 GitHub-hosted `ubuntu-latest` 上做 bounded validation，但 fixed-runner 接入与最终证据口径仍以 `docs/fixed-runner-playbook.md` 为准。
- 依赖治理 PoC：仓库已新增 `conanfile.py`、`conan/README.md`、仓库内 profile 与 `BOOST_USE_CONAN_DEPS=ON` 路径，用于 Conan 2 最小正式化 PoC；默认依赖入口仍是 `FetchContent/third_party` fallback。
- 依赖治理补充：仓库已新增 `scripts/generate_conan_lock.py`、`conan/profiles/linux-gcc-x64`；`release.yml` 与 `long-soak-capacity.yml` 已支持把 Ubuntu fixed-runner 与同一份 `conan_lockfile` 关联使用。当前默认主线 Conan 路径是 `with_grpc=False`、`with_sqlite=False`；`sqlite3` 继续保留为可选/实验层。
- Conan PoC 当前事实：本机可在仓库内 `CONAN_HOME` 下完成 profile 生成并进入依赖图解析；若访问 `conancenter` 受限，仍需通过内网镜像、预热缓存或离线源完成真正取包。
- 仓库已新增 `scripts/bootstrap_conan.py` 与 `conan/remotes.example.json`，用于优先准备本地 cache / 内网 remote；公网 `conancenter` 不是默认前提。
- bootstrap 现已支持 `conan/remotes.local.json` 覆盖、`CONAN_REMOTE_URL` 环境变量注入和 `--no-remote` 离线模式。
- 仓库已新增独立的 `conan-validate.yml` 手动流水线，用于在不扰动默认 CI 的前提下验证 Conan 依赖链；当前已补 `runner` / `conan_profile` / `conan_lockfile` 输入，默认可切到 Linux fixed-runner。
- `conan-validate.yml` 已完成真实 GitHub Actions dispatch，历史事实是 workflow 已被 GitHub 接受并派发；后续 Linux fixed-runner 结果继续按同一入口归档。
- 2026-07-10/11 已在 GitHub-hosted `ubuntu-latest` 上完成整套主线 workflow 回归收口。`.github/workflows/ci.yml` 的 hosted 主线验证确认当前 `develop` 分支在无 self-hosted runner 参与时也能完成 Conan install、Build、CTest、R4 contract、monitoring operability、workflow Python CLI contract gate 和 legacy/helper inventory gate；对应治理补充包括 workflow 内 Python 脚本参数漂移静态门禁（`scripts/check_workflow_python_cli_contracts.py`）、`ci.yml` 上的显式 `sccache` 安装/缓存目录预创建、Conan cache 恢复试点，以及 DemoServer config watcher teardown 收口。进一步的 hosted 调查在 2026-07-10 形成了完整闭环：run `29106845147` 证明 `ci.yml` 旧版 `sccache` key 会因“配置哈希固定 + exact-hit 不再 save”而冻结在旧缓存，即使 Conan cache 已恢复，warm run 仍出现 `compile_requests=184 / cache_hits=0 / cache_misses=184`；提交 `28bda13` 将 `ci.yml` 调整为“配置哈希前缀 restore + commit exact key save”后，run `29108671173` 已在 exact-hit 同一 `sccache` key 的情况下达到 `compile_requests=184 / cache_hits=184 / cache_misses=0`，总时长从上一轮的 24m34s 收敛到 18m31s。随后，`perf-regression.yml` 在 run `29112908106` 上以 fallback restore 老 `release` key 的方式获得 `compile_requests=202 / cache_hits=199 / cache_misses=3` 并保存自己的 workflow exact key；`perf-commit-check.yml` 在 run `29112908805` 暴露出 `workflow_dispatch` 下 PR comment 假设错误后，通过提交 `8127391` 修正为仅在 `pull_request` 事件评论，重跑 `29113691995` 已通过；`nightly-stability.yml` 在 run `29112908489` 暴露 `verify_stability_soak.py` 默认 120s build timeout 对 hosted Debug 构建过紧后，同样由提交 `8127391` 显式提升 timeout，重跑 `29113691508` 已通过；`release.yml` 首轮 run `29112907891` 暴露测试阶段缺少可观测性，提交 `f365125` 增补 `List tests` 与 `ctest --progress --output-on-failure | tee` 日志后，重跑 `29114301198` 已完成 release gate、baseline、打包全链路验证。当前 GitHub-hosted `ci.yml` / `release.yml` / `perf-commit-check.yml` / `nightly-stability.yml` / `perf-regression.yml` 的 Conan + `sccache` + hosted fallback 链路都已具备真实事实源，剩余重点已经从“workflow 自身可用性排查”转为 fixed-runner inventory、标签治理和 production evidence。
- Conan/fallback 规则当前已明确分层：`fmt`、`spdlog`、`nlohmann_json`、`hiredis`、`boost::headers` 为 Conan-first；`OpenSSL` 保持双轨保守；`protobuf/grpc/sqlite3` 仍属实验或可选层。
- SDK 构建与安装当前已同时兼容 Conan 和 fallback 两套头文件来源；`sdk_tests` 与 SDK 打包不再硬编码依赖 `boost_SOURCE_DIR` 或 `nlohmann_json_SOURCE_DIR`。
- `project_v3` 当前也已去掉对 `hiredis_SOURCE_DIR` 的显式 include 假设，Conan 与 fallback 都统一依赖 `hiredis` target。
- helper/generated contract 收口补充：全部 5 服务域的 29 个业务 handler 已统一接入 adapter，且 29 个均已具备 `EnvelopeMessageKind` / schema-backed typed request/response 覆盖，包含 login 域补齐后的 `register_account` / `guest_login` 以及 room governance / control-plane 风格消息（`room_list`、`room_detail`、`room_kick`、`room_transfer_owner`、`room_state_push`、`room_battle_finished`）。剩余 raw JSON-only 面已收敛到仅内部 Raft raw JSON RPC。
- P1 性能事实：macOS Release baseline 三轮已刷新，`runtime/perf/release-baseline/summary.json` 中 `release_gates.overall_pass=true`；capacity 单轮已暴露当前退化点，5K/10K echo 存在连接建立失败，battle-500 存在 rejected 与 P99 500ms，详见 `docs/archive/releases/v3.3.2-p1-performance-stabilization.md`。
- 专项 E2E：`scripts/verify_specialized_e2e.py` 聚合 Raft 集群/恢复、Redis 降级与可选 Redis live / Operator kind smoke，作为 Redis/Raft/Operator 独立验收入口；`.github/workflows/specialized-e2e.yml` 提供手动触发入口，固定 runner 接入见 `docs/fixed-runner-playbook.md`。
- P3 数据恢复：`scripts/verify_data_recovery_gate.py` 聚合 replay/result/snapshot、WriteBehind flush/drain、Redis degraded、Raft committed restart replay 和持久化 round trip；Redis live 与 settlement replay 通过显式参数接入固定环境。
- P4 可观测性/限流：`scripts/verify_observability_gate.py` 聚合 rate limit 全局消息类型/IP/user/login/connection、trace/OTel、backend RED metrics、gateway metrics 导出和 audit 事件证据，并接入 RC 总门禁；固定观测 runner 可通过 `--include-otel-collector` 验证 fake collector POST，通过 `--include-runtime-http` 启动真实 `v2_gateway_demo` + SDK full-flow 验证 `/health`、`/ready` 与 `/metrics*`。
- P5 控制面：`scripts/verify_control_plane_gate.py` 聚合 Operator manifest 静态契约、fake-client Go 测试，并接入 RC 总门禁；Go build/module cache 固定到仓库 `runtime/go-cache`，避免依赖用户 HOME 权限；固定 runner 可通过 `--include-envtest` / `--include-kind` 验证 envtest、kind status/components 和样例 CR 删除路径。
- P5 长稳/故障/回滚：`scripts/verify_production_resilience_gate.py` 聚合固定 runner 预检、stability soak、data recovery、Redis/Raft/Operator specialized E2E，并可显式追加 Redis live、Operator kind、runtime HTTP observability、release/capacity baseline；默认入口保持有界，summary 写入 `runtime/validation/production-resilience-summary.json`。
- P6 生产证据聚合：`scripts/verify_production_evidence_gate.py` 将 stability soak、P3 data recovery、Redis/Raft/Operator specialized E2E、生产候选完整性审核与可选 release/capacity baseline 聚合为一个固定 runner 入口；默认模式保持有界，长稳、Redis live、Operator kind、settlement replay、capacity baseline 通过显式参数启用。本机 P6 收束验证已覆盖 Release 构建、Redis live、Operator kind 和 3 轮 Release baseline，交付记录见 `docs/archive/releases/v3.3.2-p6-production-evidence.md`。
- P2 固定 runner 证据：`.github/workflows/production-evidence.yml` 已支持 JSON runner 输入、preflight summary 归档、Redis/kind 真实依赖、runtime HTTP observability、release baseline/capacity baseline，以及 R2/R3 fixed-runner 准入报告归档；`.github/workflows/long-soak-capacity.yml` 已补 N1 长稳/容量专用定时入口，配置说明见 `docs/fixed-runner-playbook.md`。
- N0 固定 runner 常态化：`release.yml`、`specialized-e2e.yml` 已补齐 JSON runner、preflight summary 归档和统一 Step Summary 渲染；`check_fixed_runner_environment.py`、`render_validation_summary.py` 与各聚合 gate summary 已统一到 `summary_version=2`、`overall_pass`、`failed_category`、`environment`、`artifacts` 契约。本地收束证据见 `runtime/validation/n0-release-baseline-preflight-summary.json`、`runtime/validation/n0-specialized-preflight-summary.json`、`runtime/validation/n0-specialized-raft-ha-summary.json`。
- N1 性能证据索引：`docs/performance-baseline.md` 已补 baseline / capacity / bounded soak / long soak / business-flow perf / business-capacity / docker snapshot 统一归档口径，`verify_stability_soak.py` 已支持 `long` / `overnight` profile，`verify_production_resilience_gate.py` 与 `run_long_soak_capacity.py` 已打通 2h=`long`、8h=`overnight` 的固定 runner 链路；`collect_release_baseline.py` 与 `run_long_soak_capacity.py` 已支持独立归档 capacity 与 business-capacity。R2 fixed-runner manifest 现在会阻断缺失的 `long_soak_capacity`，但生产容量上限声明仍需后续 sustained-capacity/resource-slope 专项，不把 30s 短样本误宣称为生产上限。
- N2 监控 SLO：Prometheus alerts 已新增 `BoostGatewayHighRouteLatency`、`BoostGatewayBusinessFlowFailure`，Grafana dashboard 已新增 route latency 与 business-flow success 面板；`docs/deployment/production-operations-runbook.md` 已明确 SLI/SLO 口径和告警响应流程。`check_monitoring_operability.py` 已统一输出 `summary_version=2`、`overall_pass`、`environment`、`artifacts`；本地收束验证见 `runtime/validation/monitoring-operability-summary.json`、`runtime/validation/n2-monitoring-operability-summary.json` 与 `runtime/validation/n2-observability-summary.json`。2026-05-24 已再次刷新 `n2-monitoring-operability-summary.json`，当前为 `PASS`。
- N3 部署恢复/回滚：`scripts/check_production_recovery_gate.py` 已补默认有界静态门禁，覆盖 Docker Compose、Kubernetes rollout/rollback、Redis volume/PVC、RTO/RPO、SDK full-flow 恢复验证和运维记录模板，并接入 `scripts/verify_production_resilience_gate.py` 默认步骤；`check_deploy_operability.py` 与 `run_cloud_production_closure.py` 已统一到 `summary_version=2` fixed-runner 契约。`docs/production/production-recovery-drill-record-template.json` 与 `scripts/check_recovery_drill_record.py` 已将真实演练记录固化为可校验 JSON。当前 macOS + OrbStack Docker 环境已形成本机预演证据；2026-05-24 已再次刷新 `runtime/validation/n3-deploy-operability-summary.json` 与 `runtime/validation/preprod-recovery-drill-summary.json`，当前均为 `PASS`，云端固定 runner / K8s 继续按同一 summary 契约持续归档。
- N4 传输安全与配置治理：`scripts/check_transport_config_governance.py` 已聚合 TLS/mTLS profile 边界和配置漂移检查；backend 服务端 opt-in TLS listener、五个 backend 入口配置接入、Docker/K8s/Helm Secret/volume profile、backend TLS request/response 实测和本机 TLS profile SDK full-flow 已补齐。默认生产结论仍是 plain TCP，TLS transport 上线需要固定 runner / 预发多轮演练、证书轮换和性能损耗额外证据。
- N5 SDK 企业交付：`scripts/verify_sdk_enterprise_delivery.py` 已聚合 SDK distribution、package consumer、in-process business-flow、真实 gateway full-flow 和 backend TLS profile 下的真实 gateway full-flow；`sdk/docs/compatibility.md` 已补 C++/C ABI/Python/C# 客户端兼容矩阵，`sdk/docs/README.md` 已补生产客户端接入清单和 plain TCP / backend TLS profile 的客户端边界。2026-05-24 最后一轮已修复 SDK full-flow 动态端口、package consumer Debug/NOCONFIG 映射、business-flow 进程组 timeout 与 fixture 动态端口/teardown 收束，`runtime/validation/n5-sdk-enterprise-delivery-summary.json` 当前为 `PASS`。
- N6 gRPC/proto 取舍：`scripts/check_v3_grpc_poc_decision.py` 已补 v3 proto/gRPC PoC 决策门禁，验证 schema/transport contract、CMake target、TCP baseline 对照和 ADR 边界；当前结论是 generated gRPC 保留实验，不进入默认生产链路。
- N6 gRPC/proto 取舍补充：`tests/perf/grpc_vs_tcp_perf_test.cpp` 已不再是 placeholder，当前已基于真实 TCP backend request 与 gRPC `RequestLogin` RPC 生成 benchmark 数据；`gateway.proto` 与 `GatewayGrpcServer` 当前已覆盖 login/logout/health，以及 room/match/leaderboard/battle 的基础 RPC，`GrpcGatewayAdapter` 也已从 allow-all stub 收口到 `GatewayServiceBridge` 驱动的真实 backend 路由。但 streaming/push、SDK-integrated full-flow、TLS/RBAC/observability 的 gRPC profile 证据仍未完成，因此结论继续保持 `defer_default_transport`。
- R0 生产候选证据聚合：`scripts/verify_production_candidate_evidence.py` 已聚合 fixed-runner preflight、P5 production resilience、P6 production evidence、N5 SDK enterprise delivery，并可显式追加 N4 TLS full-flow 与 N6 gRPC PoC decision；summary 写入 `runtime/validation/r0-production-candidate-evidence-summary.json`。
- R1 TLS 上线前置证据：`scripts/verify_tls_production_readiness.py` 已覆盖 TLS profile full-flow、server CA 校验、证书轮换 full-flow、CA 不匹配 expected failure 诊断和 plain/TLS 单次业务闭环耗时对比；默认生产仍是 plain TCP，R1 只作为启用 backend TLS profile 前的前置证据。
- R2 生产候选证据 Manifest：`docs/production/production-candidate-evidence-manifest.json` 与 `scripts/check_production_evidence_manifest.py` 已将 R0/R1 本机有界证据、固定 runner long-soak/capacity、release/capacity、预发恢复演练和 TLS 预发多轮证据统一成可校验 manifest；默认校验 R0/R1，`--require-fixed-runner` 用于投产前阻断缺失的固定 runner / 预发 summary。
- R3 生产 Readiness Report：`scripts/render_production_readiness_report.py` 已将 R2 manifest、R0 aggregate 和 R1 TLS readiness 汇总为 Markdown 报告与机器 summary；报告明确区分 bounded local evidence 与 final production readiness，当前固定 runner / 预发缺口仍会作为最终投产阻断项展示。
- R4 固定 Runner Release / Capacity 证据：`scripts/verify_fixed_runner_release_capacity.py` 已将 release baseline、capacity profile 和 business-capacity profile 汇总成 `runtime/validation/fixed-runner-release-capacity-summary.json`，并校验 capacity/business-capacity 的 preset、必需 case 与最小 repetitions，用于解除 R2/R3 中 `fixed_runner_release_capacity` 阻断；最终投产仍建议在固定低噪声性能机器上刷新该 summary。
- R5 预发恢复 / 回滚演练证据：`scripts/verify_preprod_recovery_drill.py` 已将 N3 recovery gate、Docker Compose gateway restart、SDK full-flow、Docker production snapshot 和 recovery drill record validator 串成 `runtime/validation/preprod-recovery-drill-summary.json` producer。2026-05-20 已在当前 macOS + OrbStack 环境完成真实复测并通过；本轮同时固化了 Docker builder 补 `python3`、gateway backend pool 默认收敛到 `1`、以及 leaderboard 自动结算可用性修复。
- R6 TLS 预发多轮证据：`scripts/verify_tls_preprod_multi_run.py` 已多轮聚合 R1 TLS readiness，覆盖 TLS full-flow、证书轮换、CA mismatch expected failure 和 plain-vs-TLS overhead ratio，输出 `runtime/validation/tls-preprod-multi-run-summary.json`。2026-05-20 已在当前授权环境完成 2 轮预发多轮验证并通过。
- 脚本与配置治理：`docs/script-inventory.json` 已将顶层脚本划分为 public entrypoint、aggregate gate、producer、tool、platform wrapper 和 legacy；`scripts/check_script_inventory.py`、`scripts/check_validation_summary_contract.py`、`scripts/check_config_source_layout.py` 已用于阻断脚本索引、summary v2 契约和 `env/` 配置事实源漂移。后续如需物理移动脚本，必须先保留顶层 shim 并更新 inventory / reliability matrix。
- 默认主线测试面为 `tests/v2`、SDK 和对应 gate。
- 其中 `admin_service` 已明确留在 legacy-v1 / demo-only 面，不进入默认 gate，也不作为当前 v2 生产控制面承诺。后续如需评估新的 v2 控制面，参考 `docs/legacy/v2-control-plane-preplan.md`。
- P3 监控运维：Prometheus 已加载 `env/monitoring/prometheus-alerts.yml`，Grafana dashboard 已对齐当前 gateway `/metrics` 真实指标，`scripts/check_monitoring_operability.py` 会阻断后端 HTTP scrape、旧指标名和 runbook 漂移；运维流程见 `docs/deployment/production-operations-runbook.md`。
- P4 SDK 企业级封装：C++ SDK heartbeat 已实作，disconnect callback 可由 heartbeat failure 触发；C ABI 暴露 heartbeat 控制，Python/C# wrapper 增加 native 版本校验和加载/分配诊断；SDK business-flow 与 full-flow client 验证覆盖 login、room、ready、battle、push、reconnect、heartbeat。
- H0-H5 生产候选硬化：`scripts/check_production_hardening_gate.py` 聚合固定 runner 定时入口、长稳/容量/K8s/观测/SDK 企业接入证据；`production-resilience.yml` 与 `production-evidence.yml` 已具备 weekly schedule 和 runner fallback。
- 生产性能快照：`scripts/collect_docker_production_perf_snapshot.py` 已补齐 OrbStack / Docker Compose 生产栈运行态采样入口，覆盖 gateway readiness/diagnostics、Prometheus targets、Grafana health 和容器 CPU/RSS/PID/IO 快照；本机实测 `overall_pass=true`，产物见 `runtime/perf/docker-production-snapshot/`。
- 生产业务闭环接入：`docs/production-business-closure-plan.md` 已完成 P0-P8 收束。默认代码主链支持 gateway 同时接入 login / room / battle / matchmaking / leaderboard，Compose、Kubernetes 和当前 systemd 默认入口均按五后端装配；其中 leaderboard 可选 Redis 持久化，未配置 Redis 时保持内存降级。P0-P2 打通 SDK matchmaking/leaderboard、full-flow 和 battle settlement 自动写榜；P3-P4 将新业务路径纳入性能/监控/快照，并完成 Redis/Raft HA profile；P5-P8 补齐 OTel/trace、TLS 边界、K8s/Operator full-flow 入口和 v3 proto/gRPC ADR。聚合验证入口为 `scripts/verify_p5_p8_business_closure.py`。
- P0-P7 框架现代化与坦克大战 demo：已按 `docs/realtime-framework-modernization-plan.md` 完成 P0-P7 全部 checkpoint。P0 目录与文档结构固化；P1 identity 注册协议与错误码完成；P2 房间大厅支持 list/detail/kick/transfer；P3 实时实例运行时（`v2::realtime::InstanceRuntime`）实现 tick-based 游戏循环；P4 坦克大战仿真（`TankWorld` 20×15 网格）含运动/碰撞/子弹/得分；P5 settlement 与 leaderboard 数据结构就绪；P6 resume/reconnect 支持；P7 回归门禁与验证脚本覆盖 642 测试 + 8 个 checkpoint。demo 全部位于 `demo/games/tank_battle/`，默认不参与生产构建（`BOOST_BUILD_TANK_DEMO=OFF`）。
- R4/R5 ECS 管线增强与 TankBattlePlugin：`InstancePlugin` SPI 正式化（8 虚方法 + noexcept 契约 + try-catch 错误隔离），`TankBattlePlugin` 完整实现（move/attack/shoot/finish）位于 `src/v2/battle/`。这些能力当前定位为框架/demonstration/plugin 侧扩展，不属于默认生产 battle 主链；默认 battle 后端仍以 `BattleInstancePlugin` 为当前部署事实。新增 ECS 系统：`ProjectileSystem`（弹道飞行/AoE/DoT）、`BattleLifecycleSystem`（自动状态机 kCreated→kRunning→kFinished，空闲超时 300 帧，离线超时 60 帧）、`BattleReplaySystem`（逐帧快照录制）、`AoiSystem`（ECS 集成 AOI + SpatialGrid）。新增组件：`ProjectileComponent`、`DamageOverlayComponent`、`BattleReplayFrameRecord`。数据持久化层：`CachedBattleDataStore`（LRU + WriteBehind）接入 demo_server，`JsonFileBattleDataStore` 文件落地。远程 Actor 通信：`RemoteActorRef::tell()` 跨节点消息投递。gRPC 网关：`GatewayGrpcServer`（login/logout/health）保留为 `BOOST_BUILD_GRPC` 条件编译下的实验能力，不进入默认生产链路。62+ 新增测试覆盖 lifecycle/replay/AOI/spatial grid/file store/tank battle plugin/projectile system。
- N1 性能刻度（perf scaling）：`demo/games/tank_battle/tests/perf_test.cpp` 新增 500 实例 × 50  ticks 基准规格（保守阈值 100 TPS），覆盖 2/20/100/500 四级并发刻度用于 CI 性能回归。
- N5 SDK Python 示例：`demo/games/tank_battle/client_sdk_adapter/python_demo.py` 作为坦克大战 Python SDK demo，走通 connect→login→room→battle→move→finish→leaderboard→disconnect 全生命周期，输出 JSON 摘要并支持 `--n5-demo` 集成到 `verify_tank_battle_demo.py` 验证脚本。

## P0 性能优化轮次（2026-05-23）

Release 构建 + 5 后端拓扑下完成 P0 收束，4 项性能修复 + 基线验证：

### 修复项
- **后端连接池实验**: `gateway_service_bridge.cpp` 生产默认已回收为 1；多连接池只保留为显式压测/实验参数，不能作为默认投产路径
- **战斗路由线程卸载**: `runtime.cpp` 默认工作线程 0→4
- **CircuitBreaker 线程安全**: `circuit_breaker.h/.cpp` 添加 mutex 保护
- **高精度定时器**: `v2::platform::HighResTimer` RAII 封装，消除粗粒度休眠

### 基线结果（Release, 3 轮）
| 场景 | 阈值 | 优化前 | 优化后 |
|------|------|--------|--------|
| echo-100 | P99 ≤ 50ms | 100ms ❌ | **1ms** ✅ |
| echo-1000 | P99 ≤ 50ms | 150ms ❌ | **5ms** ✅ |
| battle-20 | P99 ≤ 100ms | 750ms ❌ | **10ms** ✅ |
| battle-100 | P99 ≤ 250ms | 5000ms ❌ | **200ms** ✅ |

后端正向延迟从 ~30ms 降至 ~2.5ms，echo 吞吐最高 17,846/s，battle 吞吐 1,424/s。详见性能基线文档。

### 稳定性
- Unit tests: **772 通过 / 63 跳过（Redis 依赖）/ 0 失败**（Release 构建）
- Capacity baseline: P99 尾部无明显退化

## 保留边界

- 2h/8h soak 与 10K capacity 已进入固定 runner 阻断证据链，但真实 summary 仍需在固定机器持续刷新；生产容量上限声明、跨节点 Redis/Raft、更完整 Operator rollback/probe E2E、更完整角色化 RBAC、外部 OTel collector 长稳、Prometheus P99 告警灵敏度多轮实测和 generated gRPC transport PoC 仍属于固定 runner/后续专项；默认生产主链仍是 SDK + TCP gateway + BackendEnvelope + 五后端 + Redis。
- 主线定位为企业级高性能实时服务框架。坦克大战和后续游戏/实时系统样例必须放在 `demo/games/` 作为业务验证 demo，不能把碰撞、地图、胜负、得分公式等业务规则写入 gateway、login、room、leaderboard 或公共 SDK。框架与业务边界以 `docs/realtime-framework-modernization-plan.md`、`docs/realtime-framework-module-boundaries.md`、`docs/realtime-framework-sdk-boundary.md` 和 `demo/games/README.md` 为准。
- 默认 CI/release workflow 使用有界 smoke 门禁，避免长时间占用终端或 runner。

## R7 模块收束（2026-05-23）

基于”模块已实现但未接入生产主流程“的分析，对以下模块进行了收束，将 demo/unit-test-only 的功能接入实际项目路径：

### P0 持久化层 — 编译接入生产构建

- `persistence/writebehind.cpp`、`persistence/replay_storage.cpp`、`persistence/storage_engine_sqlite.cpp`、`persistence/player_data.cpp` 已加入 `project_v2` 静态库构建
- `BOOST_BUILD_SQLITE` CMake 选项：启用后自动检测 sqlite3 并定义 `HAS_SQLITE`
- `ReplayStorage` 已接入 `BattleBackendService`：战斗结算时自动保存 replay
- 接入点：`examples/v2_battle_backend/main.cpp` 通过 `set_replay_storage_dir()` 配置 replay 存储目录

### P1 内存架构 — ECS Entity 存储集成

- `BumpArena` 注入 `SimpleWorld`：`create_entity()` 优先从 arena 分配 `EntityStorage`，arena 不可用时回退到堆分配
- `ObjectPool<EntityHandle>` 接入实体销毁路径：`destroy_entity()` 将 handle 回收到池中
- Entity 生命周期现在完全由 arena/pool 管理，不再依赖单 `generations_` map

### P1+P2 诊断 & 鉴权 — Gateway Runtime 集成

- `DiagnosticsManager` / `HealthCheck` 通过 forward declaration + `unique_ptr` 注入 `Runtime`，避免循环依赖
- `DemoServer` 已将 `BackendMetrics` 和 `ServiceRegistry` 数据源接入诊断
- `LoginBackendService` / `RoomBackendService` 全部 handler 使用 `diag_wrap` 包装（try-catch + SPDLOG）
- `Authorizer` RBAC 接入消息分发：`Runtime::handle()` 中 `is_session_allowed()` 在 JWT 验证后执行角色化权限检查
- `set_session_role()` / `is_session_allowed()` 接口已暴露，Player 默认角色新增 match（6001/6004/6006）和 leaderboard（7001/7003/7005）消息 ID

### 未解决问题

（当前无活跃阻塞项）

## 当前阶段结论

生产稳定化、交付闭环、生产业务闭环接入、N0-N6 生产数据沉淀与风险燃尽，以及 R0-R6 生产候选实证阶段已经完成当前有界收束。当前主线具备生产候选所需的默认有界 gate、固定 runner 入口、部署/运维/SDK 文档、监控告警静态校验、P5 resilience gate、P6 production evidence gate、P5-P8 business closure gate、N3 recovery gate、N4 transport/config governance gate、N5 SDK enterprise delivery gate、N6 gRPC PoC decision gate、R0 production candidate evidence gate、R1 TLS production readiness gate、R2 evidence manifest gate、R3 readiness report、R4 fixed-runner release/capacity gate、R5 preprod recovery drill gate、R6 TLS preprod multi-run gate 和生产候选完整性审核。

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

后续长期开发以 `docs/project-blueprint.md` 为规划依据。本文档继续作为”已经实现/当前默认链路”的事实源；`docs/fixed-runner-playbook.md`、`docs/release-governance.md` 和各 runtime summary 继续作为生产候选证据事实源。
当前 1-3 个月的实际主线执行顺序，见 `docs/mainline-execution-plan.md`。

当前默认有界收束已经完成；长稳 2h/8h、10K 固定机器容量、TLS 预发多轮性能、真实 gRPC transport profile 等继续作为固定 runner 或后续专项持续沉淀。业务验证型后续工作必须继续遵守“框架与业务隔离”：demo 只放在 `demo/games/` 或后续 demo 目录，不能把坦克大战等具体业务规则写入公共框架主链。

下一阶段执行优先级概括为：

1. 短期：固定 runner 可用性治理与 GitHub-hosted fallback 固化。当前 hosted 主 CI 已补上 Conan + `sccache` + workflow CLI drift 治理，短期剩余重点不再是“让 CI 勉强可跑”，而是把 runner inventory、默认标签、无效排队处理和手动 fallback 流程固化到文档与 workflow 操作手册。
2. 中期：Ubuntu fixed-runner 容量事实沉淀与 Conan 主线路径升格。目标是在 fixed-runner 上补齐 lockfile install、release baseline、long-soak/capacity、production evidence，并在证据稳定后把 Conan `nosqlite` 路径提升为唯一推荐依赖入口。
3. 中期：generated proto/gRPC 从 login-only 对照扩展到非登录 full-flow 证据。重点不是扩大 PoC 面，而是补 Room/Battle/Match/Leaderboard 非登录路径、真实性能对照、TLS/RBAC/observability 证据。
4. 长期：Developer Guide 与贡献路径、通用实时服务 plugin 生态、macOS ARM64 等更多平台、固定/高性能 runner 趋势化容量报告。

当前命名与默认维护面状态：

- 对外产品/框架名称按 `BoostGateway` 收敛。
- 仓库历史名 `BoostAsioDemo` 暂时保留，用于兼容历史引用与路径。
