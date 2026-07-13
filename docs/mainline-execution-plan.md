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
| 2 | Ubuntu fixed-runner Conan / baseline / evidence 刷新 | 部分完成，等待 R5 环境恢复 | R0 candidate `29152333112`、P5/P6 bounded summary 已成功；`29183833041` 已完成 Conan/Release 预检及 capacity、business-capacity 三轮闭环，battle-500 P99 均低于 500ms 且 SDK full-flow 通过。`29186343065` 已在 `4855dc0` 完成 R6 两轮 TLS 预发验证，但 R5 因 runner 到 Docker Hub 的连接被重置而未完成。待 runner 恢复 Docker 镜像预热后，依次刷新 R5/R6、真实 2h soak、R0，再生成 R2/R3 连续准入事实链 |
| 3 | Conan `nosqlite` 路径升格为唯一推荐主线 | 待开始 | 当前默认已 Conan-first，但仍保留 fallback；要在 fixed-runner summary 稳定后再收紧推荐口径 |
| 4 | 生产认证边界 | 已完成当前边界收口 | 生产 `external-jwt` 模式现只验证带 `exp` 的外部 RS256 JWT，拒绝本地签名、注册、guest 和 refresh；账户持久化、JWKS/多 `kid` 轮换和可撤销 refresh token 明确属于外部身份提供方集成，不再由进程内 demo state 伪装承担 |
| 5 | generated proto/gRPC 非登录 full-flow 证据 | 当前闭环完成，继续保持实验边界 | 本机 Conan `grpc/1.67.1` 构建与真实 `GrpcGatewayAdapterE2ETest` 已验证 Room/Match/Leaderboard unary 后端 E2E、`NOT_FOUND` 错误语义、实验 SDK `GrpcClient` 驱动的 Login/Room/Battle/Leaderboard full-flow、Battle 可取消的持续订阅 server stream（100-5000ms 限速、正常/取消 CQ 回收、流生命周期与 backend route metrics），以及 trusted principal + `Authorizer` RBAC allow/deny、TLS full-flow、mTLS 缺/带 client cert 两条路径。OTLP collector E2E、实验 SDK 安装包契约和独立 `grpc-experimental.yml` fixed-runner 入口都已补齐，并已在 fixed-runner run `29196150703` 成功完成 `BOOST_BUILD_GRPC=ON` 验证；当前剩余结论只是不把该链路升格为默认生产主线 |
| 6 | Developer Guide / 贡献验证矩阵收束 | 待开始 | 当前脚本和 gate 足够多，但开发者入口、测试层级与提交流程还需要更直接的维护面说明 |

### 当前优先级判断

1. workflow 输入、超时、目录、证书和脚本根路径契约已完成当前收口；后续只接受真实 summary/artifact 作为成功依据。
2. R2/R3 已完成候选提交 provenance 收口：核心 summary 必须记录候选 SHA、实际 checkout、workflow/run、runner、构建配置和 Conan lockfile 摘要，跨提交 artifact 不再允许组合成最终准入结论。R5 仍是当前唯一的预发环境阻断项：`29186343065` 的 R6 已通过，但 R5 不能以 R6 或本机演练替代。runner 恢复 Docker Hub 或受信任 mirror 访问并预热镜像后，先冻结一个候选 SHA，再在该 SHA 上依次刷新完整 R5/R6、真实 2h soak/R4、R0 和 R2/R3。只有同一候选的连续事实链稳定，`BOOST_USE_CONAN_DEPS=ON` 才应从“默认值”升级为“唯一推荐路径”。
3. 生产认证边界已经收口：生产 login backend 只验证外部 RS256 JWT，并拒绝本地身份操作。当前代码侧 gRPC observability、安装包契约和 fixed-runner `BOOST_BUILD_GRPC=ON` run 都已完成；下一优先级不再是补 gRPC 入口事实，而是继续保持 `defer_default_transport` 并转向更高优先级的主线事项。
