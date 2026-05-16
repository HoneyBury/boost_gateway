# 可靠性矩阵

更新时间：2026-05-16

本矩阵只记录已经具备本地证据的可靠性场景。每个场景必须绑定测试、脚本或文档证据；`scripts/check_reliability_matrix.py` 会校验必需场景和证据路径。

| 场景 ID | 状态 | 风险/故障模型 | 证据 |
| --- | --- | --- | --- |
| `backend_timeout_recovery` | stable | 后端请求超时后必须关闭陈旧连接并恢复后续请求 | `tests/v2/integration/service_bus_integrity_test.cpp`, `scripts/verify_r4_contract.py`, `scripts/verify_stability_soak.py` |
| `circuit_breaker_half_open` | stable | 熔断后必须允许 half-open 探测并恢复健康后端 | `tests/v2/integration/service_bus_integrity_test.cpp`, `scripts/verify_r4_contract.py`, `scripts/verify_stability_soak.py` |
| `readiness_heartbeat_recovery` | stable | 服务 heartbeat/TTL/readiness 状态变化必须可观测并可恢复 | `tests/v2/unit/health_check_test.cpp`, `docs/architecture-acceptance-criteria.md`, `scripts/verify_r4_contract.py` |
| `writebehind_drain_failure` | stable | 写后端队列析构 drain 和 delegate failure 必须有统计与测试覆盖 | `tests/v2/unit/write_behind_store_test.cpp`, `include/v2/data/write_behind_store.h`, `src/v2/data/write_behind_store.cpp`, `scripts/verify_stability_soak.py` |
| `proto_transport_contract` | bounded | proto/gRPC 传输契约必须保持 schema/build 入口可验收，默认生产链路不依赖该能力 | `scripts/check_v3_proto_schema.py`, `src/v3/CMakeLists.txt`, `proto/v3/common.proto`, `docs/v3-release-checklist.md` |
| `stability_soak_gate` | stable | 发布前必须执行有界 smoke soak；更长 soak 进入夜间或固定机器任务 | `scripts/verify_stability_soak.py`, `scripts/verify_release_candidate.py`, `config/perf/v2_arch_baseline_gates.json`, `docs/current-state.md` |

## 分层门禁

- PR/本地快速验证：运行 `scripts/verify_release_candidate.py --skip-release-baseline --soak-profile smoke`。
- Tag release：release workflow 运行 RC smoke 门禁，并跳过完整 Release baseline，避免 runner 被长任务占用。
- 固定性能机器：运行 `scripts/collect_release_baseline.py`，使用 `release` profile 生成可比较基线。
- 夜间稳定性：运行 `scripts/verify_stability_soak.py --soak-profile short` 或 `--soak-profile medium`。

## 当前未纳入默认门禁的专项

- Redis 跨进程缓存一致性。
- Raft 多节点 leader/follower E2E。
- Kubernetes Operator 部署、回滚和健康探针 E2E。
- 长稳 2h/8h soak 和 10K 连接容量基线。
