# BoostAsioDemo

高性能 C++20 实时服务框架，当前主线版本基线为 `v3.4.0`。项目主链已经收束到 `gateway + login + room + battle + matchmaking + leaderboard` 六服务闭环，并具备 SDK、Redis/Raft、TLS profile、生产候选证据门禁等能力。

## 当前状态

- 当前事实源：`docs/current-state.md`
- 当前主线定位：企业级实时服务框架，而不是继续扩张功能面的 demo 集合
- 当前完成状态：
  - 明文 SDK full-flow 通过
  - backend TLS SDK full-flow 通过
  - SDK package consumer 通过
  - N5 SDK enterprise delivery 通过
  - R0 production candidate evidence 通过

## 快速入口

- 主文档入口：`docs/README.md`
- 当前事实源：`docs/current-state.md`
- 架构总览：`docs/architecture-overview.md`
- 可靠性矩阵：`docs/reliability-matrix.md`
- 性能事实：`docs/performance-baseline.md`
- 发布/验收门禁：`docs/v3-release-checklist.md`

## 常用验证入口

```bash
python scripts/verify_release_candidate.py --skip-release-baseline --soak-profile smoke
python scripts/check_current_docs_install.py
python scripts/check_mainline_readiness.py
python scripts/check_p3_p4_release_readiness.py
python scripts/verify_sdk_enterprise_delivery.py --build-dir build/Release --skip-build
python scripts/verify_production_candidate_evidence.py --build-dir build/Release --skip-build
python scripts/check_production_evidence_manifest.py
python scripts/render_production_readiness_report.py
```

## 运行入口

```bash
# gateway
build/Release/examples/v2_gateway_demo/Release/v2_gateway_demo.exe

# login backend
build/Release/examples/v2_login_backend/Release/v2_login_backend.exe

# room backend
build/Release/examples/v2_room_backend/Release/v2_room_backend.exe

# battle backend
build/Release/examples/v2_battle_backend/Release/v2_battle_backend.exe

# matchmaking backend
build/Release/examples/v2_match_backend/Release/v2_match_backend.exe

# leaderboard backend
build/Release/examples/v2_leaderboard_backend/Release/v2_leaderboard_backend.exe
```

## 文档策略

- `docs/` 顶层只保留当前仍维护的主文档
- 历史版本、阶段计划、交付记录和旧 runbook 已迁入 `docs/archive/`
- 如果顶层文档和归档文档冲突，以 `docs/current-state.md` 为准

## 许可证

MIT
