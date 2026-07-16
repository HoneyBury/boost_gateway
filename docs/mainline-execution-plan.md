# v3.5.0 项目清理执行计划

更新时间：2026-07-13

本文档是 v3.5.0 版本的执行计划，替代了之前的 `mainline-execution-plan.md`（2026-05-30 版本）。

## 背景

项目从 demo 演进为企业级框架后，积累了大量 Windows 兼容代码、CI 适配逻辑、脚本和文档。本次清理的目标是：

1. **暂停 Windows 支持** — 聚焦 Linux/macOS（类 Unix API 近似）
2. **收敛杂乱面** — 合并重叠文档、整理脚本结构、精简 CI workflow
3. **偿还技术债** — 修复 v2→v1 耦合、测试链接问题、清理遗留引用
4. **版本号更新到 3.5.0**

## 阶段状态

| 阶段 | 主题 | 状态 |
|---|---|---|
| Phase 1 | 删除 Windows 支持 | ✅ 已完成 |
| Phase 2 | 整理脚本 | ✅ 已完成 |
| Phase 3 | 整合文档 | ✅ 已完成 |
| Phase 4 | 修复代码质量 | ✅ 已完成 |
| Phase 5 | 精简 CI/CD | ✅ 已完成 |

## Phase 1: 删除 Windows 支持 ✅

已完成：

- 删除 `windows_service.h/cpp`（~500 行 Windows SCM 代码）
- 简化 8 个双平台文件为 POSIX-only（process_supervisor、crash_handler、perf_counter、highres_timer、hot_path、audit_log、redis_client）
- 删除 43 个 Windows 脚本（.bat 和 .ps1）
- 删除 Windows Dockerfile（`docker/gateway-server.Dockerfile`）
- 删除 `windows-ci.yml` workflow
- 清理 CMake：移除 MSVC 标志、Windows preset、DLL staging 函数
- 更新 11 个 CI workflow 为 Linux-only
- 更新 `runner-matrix.json` 到 schema v2（全部 Linux）
- 版本号更新到 3.5.0
- 构建验证通过（project_app 和 project_v2 编译成功）

## Phase 2: 整理脚本 ✅

目标：减少脚本杂乱，为未分配的 gate 脚本指定 canonical 路径。

已完成：

- 为 15 个未分配 canonical path 的 gate 脚本创建子目录并移动至 `scripts/gates/infrastructure/`、`scripts/gates/k8s/`、`scripts/gates/e2e/`
- 合并 2 对重叠 gate 脚本
- 更新 `script-inventory.json`

## Phase 3: 整合文档 ✅

目标：合并重叠文档，移动专业文档到子目录，创建开发者入门指南。

已完成：

- 合并 `reliability-matrix.md` + `v3-release-checklist.md` → `release-governance.md`
- 合并 `production-evidence-runner.md` → `fixed-runner-playbook.md`
- 移动 9 个专业文档到 `docs/deployment/`、`docs/production/`、`docs/legacy/`
- 新建 `docs/ONBOARDING.md`（开发者入门指南）
- 精简 `current-state.md`（删除 Windows 引用）
- 更新 `project-blueprint.md`

## Phase 4: 修复代码质量 ✅

目标：修复 v2→v1 耦合、测试链接问题、清理遗留引用。

已完成：

- 提取 `v2::gateway::PacketBridge` 接口，`GatewayServerShadowBridge` 不再继承 v1 `GatewayPacketBridge`
- `error_paths_test` 链接问题已处理（Windows 自动链接场景，Windows 支持已删除）
- 清理 v2 代码中的死引用

## Phase 5: 精简 CI/CD ✅

目标：合并重叠 workflow，减少 workflow 数量。

已完成：

- 将 `release-baseline.yml` 的 baseline 采集步骤合入 `release.yml`
- 删除 `release-baseline.yml`
- 新建 `.github/README.md` 记录 CI/CD 架构

## 验证命令

```bash
# 检查 Windows 引用
grep -rn "_WIN32\|WIN32\|MSVC" src/ include/ tests/ --include="*.cpp" --include="*.h"
grep -rn "WIN32\|MSVC" --include="CMakeLists.txt" --include="*.cmake" .
find . -name "*.ps1" -o -name "*.bat"  # 排除 third_party 和 build
grep -rn "Windows" .github/workflows/

# 构建验证
cmake --preset default
cmake --build build/default --parallel
```

## 当前明确不做

- 不恢复 Windows 支持（大后期再考虑）
- 不扩展功能面
- 不把 gRPC 接入默认生产链路
- 不扩 demo 业务面

## 清理收官后的主线顺序（2026-07-09）

`v3.5.0` 清理阶段已经完成，后续 1-3 个月主线不再是“继续删旧代码”，而是把已经完成的治理项变成稳定、可重复的工程事实。

| 顺序 | 主题 | 状态 | 说明 |
|---|---|---|---|
| 1 | 固定 runner 可用性治理与 GitHub-hosted fallback 固化 | 已完成当前契约收口 | Linux runner 已匹配默认标签；specialized E2E `29145172304`、production resilience `29145497642`、production evidence `29146018657` 已成功。期间修复了 workspace/目录初始化、证书生成、canonical gate 根路径、long-soak preflight profile 和长稳脚本根路径契约 |
| 2 | Ubuntu fixed-runner Conan / baseline / evidence 刷新 | 历史能力已验证，正在刷新同候选证据链 | R5/R6 run `29428322350`、gRPC run `29465329265` 以及 Release/Bounded/Candidate 等离线工作流已分别成功；这些历史事实分属不同提交，不能拼接为最终结论。下一步冻结候选 SHA，在同一提交刷新 R0、真实 2h soak/R4、R5/R6 和 R2/R3 |
| 3 | Conan `nosqlite` 路径升格为唯一推荐主线 | 已完成 | 默认严格 Conan，缺包直接失败；FetchContent 仅保留为显式开发选项，fixed-runner 与发布工作流均固定 `BOOST_DEPENDENCY_PROVIDER=conan` |
| 4 | 生产认证边界 | 已完成当前边界收口 | 生产 `external-jwt` 模式现只验证带 `exp` 的外部 RS256 JWT，拒绝本地签名、注册、guest 和 refresh；账户持久化、JWKS/多 `kid` 轮换和可撤销 refresh token 明确属于外部身份提供方集成，不再由进程内 demo state 伪装承担 |
| 5 | generated proto/gRPC 非登录 full-flow 证据 | 当前闭环完成，继续保持实验边界 | 本机功能证据已覆盖 Room/Match/Leaderboard unary、SDK Login/Room/Battle/Leaderboard full-flow、可取消 stream、RBAC、TLS/mTLS、OTLP 和安装包契约；fixed-runner run `29465329265` 在 `7c1bd4b` 上以 15/15 Conan 包严格离线完成 185/185 构建、17/17 gRPC/OTel 测试、SDK consumer 5/5 和 N6 decision gate。结论继续保持 `experimental_only` / `defer_default_transport` |
| 6 | Developer Guide / 贡献验证矩阵收束 | 进行中 | `docs/ONBOARDING.md` 已提供入口；当前工作是统一 Debug/Release 命令、测试层级与提交验证矩阵，消除文档和实际 workflow 的漂移，不新增重复 gate |

### 当前优先级判断

1. workflow 输入、超时、目录、证书和脚本根路径契约已完成当前收口；后续只接受真实 summary/artifact 作为成功依据。
2. R2/R3 已完成候选提交 provenance 收口：核心 summary 必须记录候选 SHA、实际 checkout、workflow/run、runner、构建配置和 Conan lockfile 摘要，跨提交 artifact 不再允许组合成最终准入结论。历史 R5/R6、gRPC、Release 与有界门禁已经证明执行环境可用；发布前仍须在冻结的同一候选 SHA 上刷新真实 2h soak/R4、R0、R5/R6 和 R2/R3。
3. 生产认证边界已经收口：生产 login backend 只验证外部 RS256 JWT，并拒绝本地身份操作。当前代码侧 gRPC observability、安装包契约和 fixed-runner `BOOST_BUILD_GRPC=ON` run 都已完成；下一优先级不再是补 gRPC 入口事实，而是继续保持 `defer_default_transport` 并转向更高优先级的主线事项。
