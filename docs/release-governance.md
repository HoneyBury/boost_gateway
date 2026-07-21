# 发布治理

更新时间：2026-07-21

本文档收敛了历史上的可靠性矩阵与发布检查清单内容，作为发布门禁和可靠性要求的唯一入口。

## 可靠性场景矩阵

本矩阵只记录已经具备本地证据的可靠性场景。每个场景必须绑定测试、脚本或文档证据；`scripts/check_reliability_matrix.py` 会校验必需场景和证据路径。

| 场景 ID | 状态 | 风险/故障模型 | 证据 |
| --- | --- | --- | --- |
| `backend_timeout_recovery` | stable | 后端请求超时后必须关闭陈旧连接并恢复后续请求 | `tests/v2/integration/service_bus_integrity_test.cpp`, `scripts/verify_r4_contract.py`, `scripts/verify_stability_soak.py` |
| `backend_multisession_shutdown` | stable | 后端测试服务器必须同时跟踪多个 TCP 会话，stop 时关闭所有活动 socket 并等待会话线程退出，避免 Windows smoke/soak 悬挂 | `include/v2/service/backend_server.h`, `src/v2/service/backend_server.cpp`, `tests/v2/integration/service_bus_integrity_test.cpp`, `scripts/verify_stability_soak.py` |
| `backend_plain_tcp_bounded_read` | stable | plain TCP 后端帧读取必须在无完整 header/payload 时按 timeout 返回，不依赖 Unix-only `select()` 路径 | `src/v2/service/backend_frame_codec.cpp`, `tests/v2/integration/service_bus_integrity_test.cpp`, `scripts/verify_stability_soak.py` |
| `circuit_breaker_half_open` | stable | 熔断后必须允许 half-open 探测并恢复健康后端 | `tests/v2/integration/service_bus_integrity_test.cpp`, `scripts/verify_r4_contract.py`, `scripts/verify_stability_soak.py` |
| `readiness_heartbeat_recovery` | stable | 服务 heartbeat/TTL/readiness 状态变化必须可观测并可恢复 | `tests/v2/unit/health_check_test.cpp`, `docs/archive/history-v2/architecture-acceptance-criteria.md`, `scripts/verify_r4_contract.py` |
| `writebehind_drain_failure` | stable | 写后端队列析构 drain 和 delegate failure 必须有统计与测试覆盖 | `tests/v2/unit/write_behind_store_test.cpp`, `include/v2/data/write_behind_store.h`, `src/v2/data/write_behind_store.cpp`, `scripts/verify_stability_soak.py` |
| `proto_transport_contract` | bounded | proto/gRPC 传输契约必须保持 schema/build 入口可验收，默认生产链路不依赖该能力 | `scripts/check_v3_proto_schema.py`, `src/v3/CMakeLists.txt`, `proto/v3/common.proto`, `docs/release-governance.md` |
| `stability_soak_gate` | stable | 发布前必须执行有界 smoke soak；更长 soak 进入夜间或固定机器任务 | `scripts/verify_stability_soak.py`, `scripts/verify_release_candidate.py`, `config/perf/v2_arch_baseline_gates.json`, `docs/current-state.md` |
| `release_perf_baseline` | stable | 发布候选必须有可重复的 Release 多进程性能采集入口；baseline 三轮必须通过 release gates，容量专项记录退化点但不进入默认 tag release gate | `scripts/collect_release_baseline.py`, `scripts/collect_v2_perf_baseline.py`, `docs/performance-baseline.md`, `docs/archive/releases/v3.3.2-p1-performance-stabilization.md`, `docs/release-governance.md` |
| `specialized_e2e_gate` | bounded | Redis/Raft/Operator 专项必须有独立入口；默认跑 Raft 与 Redis 降级，Redis live 和 Operator kind 需要显式启用 | `scripts/verify_specialized_e2e.py`, `scripts/operator_kind_smoke.py`, `tests/v2/unit/raft_test.cpp`, `tests/v2/integration/backend_routing_test.cpp`, `tests/v2/unit/leaderboard_test.cpp` |
| `data_recovery_gate` | stable | replay/result/snapshot、WriteBehind flush/drain、Redis degraded、Raft committed restart replay 和持久化 round trip 必须有统一 P3 门禁；Redis live 与 settlement replay 可按环境显式启用 | `scripts/verify_data_recovery_gate.py`, `tests/v2/unit/cached_data_store_test.cpp`, `tests/v2/unit/write_behind_store_test.cpp`, `tests/v2/integration/data_layer_test.cpp`, `tests/v2/unit/raft_test.cpp`, `tests/v2/integration/settlement_replay_test.cpp` |
| `integration_teardown_no_hang` | stable | v2 集成测试和多进程 fixture 必须有有界 teardown：POSIX 进程组终止、DemoServer 析构 stop、fake collector accept 唤醒和 socket read timeout，避免 fixed-runner 长任务被残留进程或阻塞读挂住 | `src/app/process_supervisor.cpp`, `src/v2/gateway/demo_server.cpp`, `tests/v2/integration/backend_routing_test.cpp`, `tests/v2/integration/demo_server_smoke_test.cpp`, `tests/v2/integration/matchmaking_e2e_test.cpp`, `scripts/check_mainline_readiness.py` |
| `validation_summary_rendering` | stable | CI/手动 workflow 必须能把 JSON summary 渲染为可读 Markdown，避免只上传 artifact 后人工下载排查 | `scripts/render_validation_summary.py`, `.github/workflows/nightly-stability.yml`, `.github/workflows/release.yml`, `.github/workflows/specialized-e2e.yml` |
| `release_sbom_semantics` | stable | 发布 SBOM 必须覆盖发行包全部普通文件的真实 SHA-256，并按 Conan lockfile 列出运行时依赖、版本、recipe revision、PURL 和根包 `DEPENDS_ON`；发布前 enrich/verify，发布后从线上 tarball 独立复验，并要求 standalone SBOM 与已验证 attestation 的 SPDX 2.3 predicate 结构等值，不能以签名或 subject digest 替代内容质量 | `scripts/tools/harden_release_sbom.py`, `config/release/sbom-policy.json`, `.github/workflows/release.yml`, `.github/workflows/release-asset-verification.yml` |
| `fixed_runner_preflight` | stable | 固定 runner 在执行长任务前必须先检查工具链、Redis/kind 依赖和构建目录形态，并输出可归档 summary，减少环境问题造成的长时间失败 | `scripts/check_fixed_runner_environment.py`, `scripts/render_validation_summary.py`, `.github/workflows/release.yml`, `.github/workflows/specialized-e2e.yml`, `.github/workflows/production-gates.yml`, `docs/fixed-runner-playbook.md` |
| `production_auth_audit_gate` | stable | 生产 Login Backend 仅验证带过期时间的外部 RS256 JWT，禁止 dev token、本地签名、注册、guest 登录和 refresh token 签发；legacy demo admin surface 的默认 ACL、拒绝审计和审计最小键必须由 release gate 检查，但这不代表当前 v2 主线提供正式 admin 控制面 | `scripts/check_security_release_gate.py`, `include/v2/login/login_backend_service.h`, `src/v2/login/login_backend_service.cpp`, `examples/v2_login_backend/main.cpp`, `docs/deployment/production-configuration-runbook.md`, `docs/archive/history-v1/v1-admin-audit-rules.md` |
| `rate_limit_key_paths` | stable | 全局消息类型、IP、用户、连接和 login 专项限流必须有可重复测试；gateway 拒绝响应必须携带 rate_limited 与 retry_after_ms | `include/v2/gateway/rate_limiter.h`, `tests/v2/unit/rate_limiter_test.cpp`, `tests/v2/unit/gateway_bridge_test.cpp`, `scripts/verify_observability_gate.py` |
| `observability_release_gate` | stable | trace_id/span_id 贯穿、OTel exporter 非阻塞、backend RED metrics、gateway metrics 导出和 audit 关键事件必须有聚合门禁；fake collector POST 与真实 gateway HTTP 观测口通过显式参数启用；legacy admin 审计仅作为附带边界证据，不代表当前主线控制面 | `scripts/verify_observability_gate.py`, `scripts/verify_gateway_observability_runtime.py`, `tests/v2/integration/service_bus_integrity_test.cpp`, `tests/v2/integration/backend_health_test.cpp`, `tests/v2/integration/backend_routing_test.cpp`, `tests/v2/unit/trace_context_test.cpp`, `tests/v2/unit/otel_persistence_test.cpp`, `tests/v2/unit/runtime_metrics_exporter_test.cpp`, `docs/deployment/production-operations-runbook.md` |
| `monitoring_operability_gate` | stable | Prometheus 只能 scrape 当前真实 gateway `/metrics`，告警和 Grafana 不得引用不存在的后端 HTTP job 或旧指标名；P99、RSS、fd 必须明确依赖 histogram/process exporter 边界 | `scripts/check_monitoring_operability.py`, `env/monitoring/prometheus.yml`, `env/monitoring/prometheus-alerts.yml`, `env/monitoring/grafana-dashboard.json`, `docs/deployment/production-operations-runbook.md`, `docs/deployment/production-deployment-runbook.md` |
| `fixed_runner_evidence_matrix` | stable | 固定 runner 必须形成统一运行矩阵、预检 summary、失败归因和 artifact 索引；Release baseline、specialized E2E、production gates 和 long-soak/capacity 统一输出 summary_version=2 元数据；workflow/summary 归档计划由本地门禁阻断漂移 | `scripts/check_fixed_runner_environment.py`, `scripts/check_fixed_runner_evidence_plan.py`, `scripts/render_validation_summary.py`, `.github/workflows/release.yml`, `.github/workflows/specialized-e2e.yml`, `.github/workflows/production-gates.yml`, `.github/workflows/long-soak-capacity.yml`, `docs/fixed-runner-playbook.md` |
| `long_soak_capacity_evidence` | stable | N1 长稳 soak 与容量基线必须由固定 runner 归档，2h 使用 `long` profile、8h 使用 `overnight` profile；2h summary 与 capacity/R4 summary 独立判定并携带同候选 provenance，后置容量失败不得反向作废已通过的长稳证据；SIGINT/SIGTERM 与 Linux runner parent death 必须停止后续步骤、分层回收进程组并原子保留 `interrupted=true` 的部分失败 summary，workflow 在上传前等待记录的 orchestrator PID 并以独立 `always()` 步骤清理临时 Redis | `scripts/run_long_soak_capacity.py`, `scripts/verify_production_resilience_gate.py`, `scripts/collect_release_baseline.py`, `.github/workflows/long-soak-capacity.yml`, `docs/production/production-candidate-evidence-manifest.json`, `docs/fixed-runner-playbook.md`, `docs/current-state.md` |
| `performance_capacity_evidence` | stable | 性能事实必须包含 baseline/capacity/business-flow 入口、资源快照、artifact 索引和 release gate；长稳/容量边界不得只停留在口头结论 | `scripts/collect_v2_perf_baseline.py`, `scripts/collect_release_baseline.py`, `scripts/verify_stability_soak.py`, `docs/performance-baseline.md`, `docs/current-state.md` |
| `monitoring_slo_operability` | stable | 监控必须形成 route latency、业务成功率、Redis/runtime exporter 边界和告警/runbook 闭环；在后端未实现 HTTP exporter 前不得误宣称后端已有 metrics surface | `scripts/check_monitoring_operability.py`, `env/monitoring/prometheus-alerts.yml`, `env/monitoring/grafana-dashboard.json`, `docs/deployment/production-operations-runbook.md`, `docs/deployment/production-deployment-runbook.md` |
| `sdk_enterprise_client_gate` | stable | SDK 必须具备同步 API 线程模型说明、heartbeat、disconnect callback、push 分发、reconnect 业务闭环、package consumer、真实 gateway full-flow、C ABI heartbeat 和 Python/C# native 版本诊断 | `scripts/verify_sdk_enterprise_delivery.py`, `scripts/check_sdk_distribution.py`, `scripts/verify_sdk_package_consumer.py`, `scripts/verify_sdk_business_flow.py`, `scripts/verify_sdk_full_flow_client.py`, `sdk/src/client.cpp`, `sdk/include/boost_gateway/sdk/client.h`, `sdk/include/boost_gateway/sdk/c_api.h`, `sdk/src/c_api.cpp`, `sdk/python/__init__.py`, `sdk/csharp/SdkClient.cs`, `sdk/tests/sdk_integration_test.cpp`, `sdk/tests/unit/client_test.cpp`, `sdk/docs/README.md`, `sdk/docs/compatibility.md` |
| `control_plane_operator_gate` | stable | Operator manifest、RBAC、manager probes、sample CR、fake-client Go 测试必须通过；固定环境可显式执行 envtest/kind，kind smoke 必须断言 status conditions、components 覆盖和样例 CR 删除路径 | `scripts/verify_control_plane_gate.py`, `scripts/check_operator_manifests.py`, `scripts/operator_kind_smoke.py`, `operator/boostgateway-operator/config/crd/bases/gateway.boost.io_boostgatewayclusters.yaml`, `operator/boostgateway-operator/config/rbac/role.yaml`, `operator/boostgateway-operator/internal/controller/boostgatewaycluster_controller_test.go`, `operator/boostgateway-operator/internal/controller/boostgatewaycluster_envtest_test.go`, `operator/boostgateway-operator/internal/controller/boostgatewaycluster_controller.go`, `docs/archive/plans/k8s-operator-implementation.md` |
| `production_resilience_gate` | stable | P5 必须把长稳 soak、backend/Redis/Raft 恢复路径、Operator kind rollout/delete smoke、runtime HTTP 观测和 release/capacity 回归收束到统一入口；默认入口保持有界，真实 Redis/kind/长项由固定 runner 显式启用 | `scripts/verify_production_resilience_gate.py`, `scripts/check_fixed_runner_environment.py`, `scripts/verify_stability_soak.py`, `scripts/verify_data_recovery_gate.py`, `scripts/verify_specialized_e2e.py`, `scripts/verify_control_plane_gate.py`, `scripts/verify_observability_gate.py`, `.github/workflows/production-gates.yml`, `docs/fixed-runner-playbook.md`, `docs/release-governance.md`, `docs/current-state.md` |
| `production_recovery_gate` | stable | N3 部署恢复、回滚与灾备演练必须有默认有界静态门禁，覆盖 Docker Compose、Kubernetes rollout/rollback、Redis volume/PVC、RTO/RPO、SDK full-flow 恢复验证和运维记录模板；真实 Docker/K8s 演练由固定 runner 持续归档 | `scripts/check_production_recovery_gate.py`, `scripts/verify_production_resilience_gate.py`, `docs/deployment/production-deployment-runbook.md`, `docs/deployment/production-operations-runbook.md`, `env/docker/docker-compose.yml`, `env/k8s/*.yaml` |
| `transport_config_governance_gate` | stable | N4 必须明确默认 plain TCP 边界、TLS/mTLS 灰度前置条件和配置漂移检查；Docker/K8s/Helm 与生产配置事实源漂移必须被脚本发现并进入 resilience gate | `scripts/check_transport_config_governance.py`, `scripts/check_tls_profile.py`, `scripts/check_config_governance.py`, `scripts/verify_production_resilience_gate.py`, `docs/tls-mtls-runbook.md`, `docs/deployment/production-configuration-runbook.md`, `env/k8s/gateway-deployment.yaml`, `env/k8s/helm/boost-gateway/values.yaml` |
| `v3_grpc_poc_decision_gate` | bounded | N6 必须验证 v3 proto/transport contract、CMake 生成入口、TCP baseline 对照和 ADR 取舍；generated gRPC 在没有独立 full-flow/benchmark 前不得进入默认生产链路 | `scripts/check_v3_grpc_poc_decision.py`, `scripts/check_v3_proto_schema.py`, `proto/v3/common.proto`, `proto/README.md`, `docs/archive/process/v3-proto-grpc-adr.md`, `docs/current-state.md`, `runtime/perf/release-baseline/summary.json` |
| `production_evidence_gate` | stable | P6 必须把 stability、data recovery、Redis/Raft/Operator 专项、生产候选完整性审核、runtime observability 和可选 release/capacity baseline 聚合为固定 runner 证据；默认入口保持有界，真实依赖和长项由 production-gates workflow 显式启用并归档 | `scripts/verify_production_evidence_gate.py`, `scripts/check_production_candidate_audit.py`, `scripts/verify_stability_soak.py`, `scripts/verify_data_recovery_gate.py`, `scripts/verify_specialized_e2e.py`, `scripts/verify_observability_gate.py`, `scripts/collect_release_baseline.py`, `.github/workflows/production-gates.yml`, `docs/fixed-runner-playbook.md`, `docs/release-governance.md` |
| `production_hardening_gate` | stable | H0-H5 生产候选硬化必须有统一静态证据：手动 fixed-runner 生产 gate、长稳/容量入口、K8s resource/HPA/PDB、runtime observability、SDK Python/C# 企业接入示例 | `scripts/check_production_hardening_gate.py`, `.github/workflows/production-gates.yml`, `docs/archive/plans/production-candidate-hardening-plan.md`, `sdk/examples/python_full_flow.py`, `sdk/examples/csharp_full_flow/Program.cs`, `env/k8s/gateway-deployment.yaml`, `docs/archive/releases/v3.3.2-h0-h5-production-hardening.md` |
| `production_candidate_evidence_manifest` | stable | R2 必须把 R0/R1 本机有界证据和固定 runner / 预发证据统一到 manifest；默认阻断 R0/R1 缺失或失败，投产前通过 `--require-fixed-runner` 阻断 long-soak/capacity、release/capacity、恢复演练和 TLS 预发多轮证据缺失，并要求 R0/2h/R4/R5/R6 provenance 属于同一候选提交 | `scripts/check_production_evidence_manifest.py`, `scripts/lib/evidence_provenance.py`, `docs/production/production-candidate-evidence-manifest.json`, `scripts/verify_production_candidate_evidence.py`, `scripts/verify_tls_production_readiness.py`, `docs/fixed-runner-playbook.md`, `docs/current-state.md` |
| `production_readiness_report` | stable | R3 必须把 bounded/fixed 两份 R2、R0 aggregate 和 R1 TLS readiness 汇总为投产评审报告；最终模式要求两份 R2 同时通过，不能让旧 bounded summary 或未启用 fixed-runner 检查的结果伪装成最终就绪 | `scripts/render_production_readiness_report.py`, `scripts/check_production_evidence_manifest.py`, `.github/workflows/production-readiness.yml`, `docs/fixed-runner-playbook.md`, `docs/current-state.md` |
| `script_inventory_governance` | stable | 82 个顶层脚本必须有维护分类和公开入口索引；新增脚本必须进入 `docs/script-inventory.json`，避免公开入口、producer、tool、legacy wrapper 继续混杂 | `scripts/check_script_inventory.py`, `docs/script-inventory.json`, `scripts/README.md`, `docs/current-state.md` |
| `legacy_helper_inventory_governance` | stable | legacy raw JSON、typed helper、generated proto 和 v1 legacy example surface 必须有显式清单；新增兼容面不得绕过文档和门禁登记 | `scripts/check_legacy_helper_inventory.py`, `docs/legacy/legacy-helper-inventory.md`, `proto/README.md`, `include/v2/service/envelope_adapter.h`, `docs/project-blueprint.md` |
| `validation_summary_contract` | stable | 核心生产证据 summary 必须保持 `summary_version=2`、`generated_at`、`overall_pass/passed`、`artifacts`；R0/long-soak/R4/R5/R6 还必须具备完整 provenance。正反向契约门禁覆盖跨 SHA、缺字段、缺时间、checkout 不匹配和 R3 双 summary 判定 | `scripts/check_validation_summary_contract.py`, `scripts/check_evidence_provenance_contract.py`, `scripts/render_validation_summary.py`, `docs/fixed-runner-playbook.md` |
| `config_source_layout` | stable | `env/` 是生产 Docker/K8s/monitoring/Redis 配置事实源；根目录 docker/prometheus/grafana/k8s 旧路径必须被标注为 legacy/reference，不能绕过 env 治理 | `scripts/check_config_source_layout.py`, `env/README.md`, `scripts/check_deploy_operability.py`, `scripts/check_monitoring_operability.py`, `scripts/check_config_governance.py` |
| `fixed_runner_release_capacity_gate` | stable | R4 必须把 release baseline、capacity profile 和 business-capacity profile 汇总成可被 R2 manifest 消费的固定 runner release/capacity summary，覆盖 10K echo、battle-500、SDK full-flow business path、capacity/business-capacity preset 和最小 repetitions | `scripts/verify_fixed_runner_release_capacity.py`, `scripts/collect_release_baseline.py`, `scripts/collect_v2_perf_baseline.py`, `docs/production/production-candidate-evidence-manifest.json`, `docs/performance-baseline.md`, `docs/fixed-runner-playbook.md` |
| `preprod_recovery_drill_gate` | stable | R5 必须把预发恢复演练记录、N3 recovery gate、SDK full-flow 和 Docker/K8s 观测 summary 聚合成可被 R2 manifest 消费的 recovery drill summary；Compose 镜像通过动态配置解析和 `missing/never/always` 策略治理，离线缓存证据必须包含 image ID/RepoDigest 和缺失清单 | `scripts/verify_preprod_recovery_drill.py`, `scripts/check_r5_docker_image_policy_contract.py`, `scripts/check_production_recovery_gate.py`, `scripts/check_recovery_drill_record.py`, `docs/production/production-recovery-drill-record-template.json`, `docs/fixed-runner-playbook.md` |
| `tls_preprod_multi_run_gate` | stable | R6 必须把多轮 TLS readiness、证书轮换、CA mismatch expected failure 和 plain-vs-TLS overhead 聚合成可被 R2 manifest 消费的 TLS 预发多轮 summary | `scripts/verify_tls_preprod_multi_run.py`, `scripts/verify_tls_production_readiness.py`, `scripts/verify_sdk_full_flow_client.py`, `docs/tls-mtls-runbook.md`, `docs/fixed-runner-playbook.md` |

## 分层门禁

- PR/本地快速验证：运行 `scripts/verify_release_candidate.py --skip-release-baseline --soak-profile smoke`。
- Tag release：release workflow 运行 RC smoke 门禁，并跳过完整 Release baseline，避免 runner 被长任务占用。
- 性能 Smoke / baseline / capacity：统一通过 `.github/workflows/perf-regression.yml` 手动触发，`perf_preset=smoke|baseline|capacity`，critical gate 会使 workflow 失败。
- 固定性能机器：运行 `scripts/collect_release_baseline.py --perf-preset baseline --perf-repetitions 3` 生成可比较基线；容量专项使用 `--perf-preset capacity`。`release.yml` 使用 `--perf-preset smoke` 收集发布候选的快速性能快照并归档为 artifact。
- 手动稳定性验证（原名 Nightly，当前仅手动触发）：`.github/workflows/nightly-stability.yml` 默认运行 `scripts/verify_stability_soak.py --soak-profile short`；手动触发可选择 `smoke` / `short` / `medium`。
- 专项 E2E：运行 `scripts/verify_specialized_e2e.py` 或手动触发 `.github/workflows/specialized-e2e.yml`；有 Redis/kind 环境时追加 `--include-redis-live` / `--include-operator-kind`。
- P3 数据恢复：运行 `scripts/verify_data_recovery_gate.py --build-dir <build-dir> --skip-build`；固定 Redis 或多进程环境可追加 `--include-redis-live` / `--include-settlement-replay`。
- P4 可观测性/限流：运行 `scripts/verify_observability_gate.py --build-dir <build-dir> --skip-build`；RC 总门禁和 tag release 会默认执行，固定观测 runner 可追加 `--include-otel-collector` / `--include-runtime-http`。
- P3 监控运维：运行 `scripts/check_monitoring_operability.py`，确保 Prometheus alerts、Grafana dashboard 和运维 runbook 与当前真实 metrics surface 对齐。
- P4 SDK 企业级封装：运行 `scripts/check_sdk_distribution.py --build-dir <build-dir>`、`scripts/verify_sdk_business_flow.py --build-dir <build-dir>` 和 `scripts/verify_sdk_full_flow_client.py --build-dir <build-dir>`，验证 SDK heartbeat/reconnect/push、C ABI 和语言封装诊断。
- P5 控制面：运行 `scripts/verify_control_plane_gate.py`；RC 总门禁和 tag release 默认跑 Operator manifest 静态契约与 Go fake-client/operator 单元测试，固定 runner 可追加 `--include-envtest` / `--include-kind`。
- P5 长稳/故障/回滚：运行 `scripts/verify_production_resilience_gate.py --build-dir <build-dir> --skip-build`；固定 runner 可追加 `--soak-profile short|medium|long|overnight`、`--include-redis-live`、`--include-operator-kind`、`--include-runtime-http`、`--include-release-baseline` 或 `--include-capacity-baseline`。
- P5/P6 生产 gate：固定 runner 通过 `.github/workflows/production-gates.yml` 的 `gate=p5-resilience|p6-evidence` 追加 Redis live、Operator kind、settlement replay、runtime observability、release baseline 或 capacity baseline，并归档 preflight 与所有子 summary；底层脚本仍可直接运行。
- H0-H5 生产候选硬化：运行 `scripts/check_production_hardening_gate.py`，验证手动 fixed-runner 生产 gate、长稳/容量/K8s/观测/SDK 企业接入证据链。
- R2 生产候选证据 Manifest：运行 `scripts/check_production_evidence_manifest.py` 校验 R0/R1 本机有界证据；投产前运行 `scripts/check_production_evidence_manifest.py --require-fixed-runner`，要求 long-soak/capacity、release/capacity、固定 runner / 预发证据齐全。
- R3 投产评审报告：运行 `scripts/render_production_readiness_report.py`，生成 `runtime/validation/r3-production-readiness-report.md`。
- 脚本/配置/workflow 治理：运行 `scripts/check_script_inventory.py`、`scripts/check_workflow_catalog.py`、`scripts/check_validation_summary_contract.py`、`scripts/check_evidence_provenance_contract.py` 和 `scripts/check_config_source_layout.py`，确保公开入口、workflow 清单、summary/provenance 契约和配置事实源没有漂移。
- R4 固定 Runner Release / Capacity：运行 `scripts/verify_fixed_runner_release_capacity.py`，生成 `runtime/validation/fixed-runner-release-capacity-summary.json`。
- R5 预发恢复演练：运行 `scripts/verify_preprod_recovery_drill.py --build-dir <build-dir>`，生成 `runtime/validation/preprod-recovery-drill-summary.json`。
- R6 TLS 预发多轮：运行 `scripts/verify_tls_preprod_multi_run.py --build-dir <build-dir> --skip-build`，生成 `runtime/validation/tls-preprod-multi-run-summary.json`。

## 当前未纳入默认门禁的专项

- Redis 跨进程缓存一致性：已有 data recovery、specialized live/degraded 与 production evidence workflow 聚合入口，仍需固定 Redis 环境持续沉淀数据。
- Raft 多节点 leader/follower E2E：已有内存 RPC 与 backend recovery 聚合入口，仍需跨进程/真实网络扰动。
- Kubernetes Operator 真实集群回滚和探针 E2E：已有 control-plane gate、production evidence workflow 与 kind status/delete smoke，仍需固定 kind runner 沉淀更长 rollout/rollback 数据。
- 长稳 2h/8h soak 已有固定机器连续实测。旧 5K/10K 产物因客户端启动、认证和取消生命周期口径不完整，只保留历史诊断价值；新的容量结论必须满足 target/start/TCP connect/authenticate/peak active 全量相等、零取消、完整稳态和进程成功退出后再归档。

---

## 发布检查清单

### 必须通过的门禁

发布前必须运行以下门禁并确保全部通过：

1. **RC 总门禁**: `python3 scripts/verify_release_candidate.py --skip-release-baseline --soak-profile smoke`
2. **R4 契约门禁**: `python3 scripts/verify_r4_contract.py --build-dir build/release --skip-build`
3. **稳定性 soak**: `python3 scripts/verify_stability_soak.py --soak-profile smoke`

### 发布流程

1. 确保 `main` 分支所有 CI workflow 通过
2. 创建 `v*` tag 触发自动发布
3. `release.yml` workflow 自动执行：构建 → 测试 → 门禁 → 打包 → 发布
4. 检查 GitHub Release 页面确认 artifact 上传成功

### 版本号规则

- 主版本号 (X.0.0): 不兼容的 API 变更
- 次版本号 (x.Y.0): 新功能，向后兼容
- 补丁版本号 (x.y.Z): bug 修复

### 产物清单

发布包应包含：

| 类别 | 内容 |
|---|---|
| **二进制** | v2_gateway_demo, v2_login_backend, v2_room_backend, v2_battle_backend, v2_match_backend, v2_leaderboard_backend |
| **SDK** | libboost_gateway_sdk.a, libboost_gateway_sdk.so, 头文件, CMake config |
| **配置** | config/ 目录下所有 JSON 配置文件 |
| **文档** | README.md, CHANGELOG.md, docs/ 目录下当前维护的文档 |
| **部署** | deploy/systemd/*.service, deploy/README.md |

### 性能基线要求

- echo smoke: P99 ≤ 50ms
- battle smoke: P99 ≤ 250ms
- Release baseline 三轮通过（固定 runner）

### 发布阻断条件

以下任何一项为 FAIL 则阻断发布：

- R4 契约门禁未通过
- 稳定性 soak 未通过
- 任何 public entrypoint 脚本报错
- 性能 smoke gate 未通过
