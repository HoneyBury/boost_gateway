# 可靠性矩阵

更新时间：2026-05-17

本矩阵只记录已经具备本地证据的可靠性场景。每个场景必须绑定测试、脚本或文档证据；`scripts/check_reliability_matrix.py` 会校验必需场景和证据路径。

| 场景 ID | 状态 | 风险/故障模型 | 证据 |
| --- | --- | --- | --- |
| `backend_timeout_recovery` | stable | 后端请求超时后必须关闭陈旧连接并恢复后续请求 | `tests/v2/integration/service_bus_integrity_test.cpp`, `scripts/verify_r4_contract.py`, `scripts/verify_stability_soak.py` |
| `backend_multisession_shutdown` | stable | 后端测试服务器必须同时跟踪多个 TCP 会话，stop 时关闭所有活动 socket 并等待会话线程退出，避免 Windows smoke/soak 悬挂 | `include/v2/service/backend_server.h`, `src/v2/service/backend_server.cpp`, `tests/v2/integration/service_bus_integrity_test.cpp`, `scripts/verify_stability_soak.py` |
| `backend_plain_tcp_bounded_read` | stable | plain TCP 后端帧读取必须在无完整 header/payload 时按 timeout 返回，不依赖 Unix-only `select()` 路径 | `src/v2/service/backend_frame_codec.cpp`, `tests/v2/integration/service_bus_integrity_test.cpp`, `scripts/verify_stability_soak.py` |
| `circuit_breaker_half_open` | stable | 熔断后必须允许 half-open 探测并恢复健康后端 | `tests/v2/integration/service_bus_integrity_test.cpp`, `scripts/verify_r4_contract.py`, `scripts/verify_stability_soak.py` |
| `readiness_heartbeat_recovery` | stable | 服务 heartbeat/TTL/readiness 状态变化必须可观测并可恢复 | `tests/v2/unit/health_check_test.cpp`, `docs/architecture-acceptance-criteria.md`, `scripts/verify_r4_contract.py` |
| `writebehind_drain_failure` | stable | 写后端队列析构 drain 和 delegate failure 必须有统计与测试覆盖 | `tests/v2/unit/write_behind_store_test.cpp`, `include/v2/data/write_behind_store.h`, `src/v2/data/write_behind_store.cpp`, `scripts/verify_stability_soak.py` |
| `proto_transport_contract` | bounded | proto/gRPC 传输契约必须保持 schema/build 入口可验收，默认生产链路不依赖该能力 | `scripts/check_v3_proto_schema.py`, `src/v3/CMakeLists.txt`, `proto/v3/common.proto`, `docs/v3-release-checklist.md` |
| `stability_soak_gate` | stable | 发布前必须执行有界 smoke soak；更长 soak 进入夜间或固定机器任务 | `scripts/verify_stability_soak.py`, `scripts/verify_release_candidate.py`, `config/perf/v2_arch_baseline_gates.json`, `docs/current-state.md` |
| `release_perf_baseline` | bounded | 发布候选必须有可重复的 Release 多进程性能采集入口；完整容量数据在固定机器执行，不进入默认 tag release gate | `scripts/collect_release_baseline.py`, `scripts/collect_v2_perf_baseline.py`, `docs/performance-baseline.md`, `docs/v3-release-checklist.md` |
| `specialized_e2e_gate` | bounded | Redis/Raft/Operator 专项必须有独立入口；默认跑 Raft 与 Redis 降级，Redis live 和 Operator kind 需要显式启用 | `scripts/verify_specialized_e2e.py`, `scripts/verify_specialized_e2e.ps1`, `scripts/operator_kind_smoke.py`, `tests/v2/unit/raft_test.cpp`, `tests/v2/integration/backend_routing_test.cpp`, `tests/v2/unit/leaderboard_test.cpp`, `tests/unit/redis_event_store_test.cpp` |
| `data_recovery_gate` | stable | replay/result/snapshot、WriteBehind flush/drain、Redis degraded、Raft committed restart replay 和持久化 round trip 必须有统一 P3 门禁；Redis live 与 settlement replay 可按环境显式启用 | `scripts/verify_data_recovery_gate.py`, `scripts/verify_data_recovery_gate.ps1`, `tests/unit/persistence_replay_audit_test.cpp`, `tests/unit/redis_event_store_test.cpp`, `tests/v2/unit/cached_data_store_test.cpp`, `tests/v2/unit/write_behind_store_test.cpp`, `tests/v2/integration/data_layer_test.cpp`, `tests/v2/unit/raft_test.cpp`, `tests/v2/integration/settlement_replay_test.cpp` |
| `validation_summary_rendering` | stable | CI/手动 workflow 必须能把 JSON summary 渲染为可读 Markdown，避免只上传 artifact 后人工下载排查 | `scripts/render_validation_summary.py`, `.github/workflows/nightly-stability.yml`, `.github/workflows/release-baseline.yml`, `.github/workflows/specialized-e2e.yml` |
| `fixed_runner_preflight` | stable | 固定 runner 在执行长任务前必须先检查工具链、Redis/kind 依赖和构建目录形态，减少环境问题造成的长时间失败 | `scripts/check_fixed_runner_environment.py`, `.github/workflows/release-baseline.yml`, `.github/workflows/specialized-e2e.yml` |
| `production_auth_audit_gate` | stable | 生产模式不得默认使用 dev token fallback；admin 默认 ACL、拒绝审计和审计最小键必须由 release gate 检查 | `scripts/check_security_release_gate.py`, `include/v2/login/login_backend_service.h`, `src/v2/login/login_backend_service.cpp`, `examples/v2_login_backend/main.cpp`, `include/game/gateway/admin_service.h`, `src/game/gateway/admin_service.cpp`, `docs/v1-admin-audit-rules.md`, `tests/unit/admin_service_test.cpp` |
| `rate_limit_key_paths` | stable | 全局消息类型、IP、用户、连接和 login 专项限流必须有可重复测试；gateway 拒绝响应必须携带 rate_limited 与 retry_after_ms | `include/v2/gateway/rate_limiter.h`, `tests/v2/unit/rate_limiter_test.cpp`, `tests/v2/unit/gateway_bridge_test.cpp`, `scripts/verify_observability_gate.py` |
| `observability_release_gate` | stable | trace_id/span_id 贯穿、OTel exporter 非阻塞、backend RED metrics、gateway metrics 导出和 audit 关键事件必须有聚合门禁；fake collector POST 与真实 gateway HTTP 观测口通过显式参数启用 | `scripts/verify_observability_gate.py`, `scripts/verify_observability_gate.ps1`, `scripts/verify_gateway_observability_runtime.py`, `tests/v2/integration/service_bus_integrity_test.cpp`, `tests/v2/integration/backend_health_test.cpp`, `tests/v2/integration/backend_routing_test.cpp`, `tests/v2/unit/trace_context_test.cpp`, `tests/v2/unit/otel_persistence_test.cpp`, `tests/unit/gateway_metrics_exporter_test.cpp`, `tests/unit/admin_service_test.cpp`, `docs/runtime-playbook.md` |
| `control_plane_operator_gate` | stable | Operator 必须通过 fake-client Go 测试；固定环境可显式执行 envtest/kind，kind smoke 必须断言 status conditions、components 覆盖和样例 CR 删除路径 | `scripts/verify_control_plane_gate.py`, `scripts/verify_control_plane_gate.ps1`, `scripts/operator_kind_smoke.py`, `operator/boostgateway-operator/internal/controller/boostgatewaycluster_controller_test.go`, `operator/boostgateway-operator/internal/controller/boostgatewaycluster_envtest_test.go`, `operator/boostgateway-operator/internal/controller/boostgatewaycluster_controller.go`, `docs/k8s-operator-implementation.md` |
| `production_evidence_gate` | bounded | P6 必须把 stability、data recovery、Redis/Raft/Operator 专项和可选 release/capacity baseline 聚合为固定 runner 证据；默认入口保持有界，长项显式启用 | `scripts/verify_production_evidence_gate.py`, `scripts/verify_production_evidence_gate.ps1`, `scripts/verify_stability_soak.py`, `scripts/verify_data_recovery_gate.py`, `scripts/verify_specialized_e2e.py`, `scripts/collect_release_baseline.py`, `.github/workflows/production-evidence.yml`, `docs/fixed-runner-playbook.md`, `docs/v3-release-checklist.md` |

## 分层门禁

- PR/本地快速验证：运行 `scripts/verify_release_candidate.py --skip-release-baseline --soak-profile smoke`。
- Tag release：release workflow 运行 RC smoke 门禁，并跳过完整 Release baseline，避免 runner 被长任务占用。
- 固定性能机器：运行 `scripts/collect_release_baseline.py --perf-preset baseline --perf-repetitions 3` 或手动触发 `.github/workflows/release-baseline.yml` 生成可比较基线；容量专项使用 `--perf-preset capacity`。
- 夜间稳定性：`.github/workflows/nightly-stability.yml` 默认运行 `scripts/verify_stability_soak.py --soak-profile short`；手动触发可选择 `smoke` / `short` / `medium`。
- 专项 E2E：运行 `scripts/verify_specialized_e2e.py` 或手动触发 `.github/workflows/specialized-e2e.yml`；有 Redis/kind 环境时追加 `--include-redis-live` / `--include-operator-kind`。
- P3 数据恢复：运行 `scripts/verify_data_recovery_gate.py --build-dir <build-dir> --skip-build`；固定 Redis 或多进程环境可追加 `--include-redis-live` / `--include-settlement-replay`。
- P4 可观测性/限流：运行 `scripts/verify_observability_gate.py --build-dir <build-dir> --skip-build`；RC 总门禁和 tag release 会默认执行，固定观测 runner 可追加 `--include-otel-collector` / `--include-runtime-http`。
- P5 控制面：运行 `scripts/verify_control_plane_gate.py`；RC 总门禁和 tag release 默认跑 Go fake-client/operator 单元测试，固定 runner 可追加 `--include-envtest` / `--include-kind`。
- P6 生产证据：运行 `scripts/verify_production_evidence_gate.py --build-dir <build-dir> --skip-build`；固定 runner 可追加 `--include-redis-live`、`--include-operator-kind`、`--include-settlement-replay`、`--include-release-baseline` 或 `--include-capacity-baseline`。

## 当前未纳入默认门禁的专项

- Redis 跨进程缓存一致性：已有 data recovery 与 specialized live/degraded 聚合入口，仍需固定 Redis 环境沉淀数据。
- Raft 多节点 leader/follower E2E：已有内存 RPC 与 backend recovery 聚合入口，仍需跨进程/真实网络扰动。
- Kubernetes Operator 真实集群回滚和探针 E2E：已有 control-plane gate 与 kind status/delete smoke，仍需固定 kind runner 沉淀更长 rollout/rollback 数据。
- 长稳 2h/8h soak；10K 连接容量基线已有 `capacity` 采集入口，但尚未沉淀固定机器实测数据。
