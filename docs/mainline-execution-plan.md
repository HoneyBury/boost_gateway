# v3.5.0 项目清理执行计划

更新时间：2026-06-30

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
