# 文档索引

更新时间：2026-07-24

`docs/` 顶层只保留当前仍维护的事实源、规范、计划和操作手册。已经结束的版本计划、
交付记录和旧 runbook 进入 [`docs/archive/`](archive/README.md)，不作为当前事实源，
也不得作为当前实施依据。

## 从这里开始

| 读者/任务 | 入口 |
|---|---|
| 新贡献者 | [开发者入门](ONBOARDING.md) |
| 了解当前能力和边界 | [当前状态](current-state.md) |
| 理解组件和请求链路 | [架构总览](architecture-overview.md) |
| 查看当前执行优先级 | [主线执行计划](mainline-execution-plan.md) |
| 部署和运维 | [部署文档](deployment/) |
| Ubuntu 运营主机准入 | [运营主机准入手册](deployment/operations-host-admission-runbook.md) |
| 不可变 Release 单节点部署 | [不可变 Release 部署手册](deployment/immutable-release-deployment-runbook.md) |

## 当前事实与规划

| 文档 | 维护范围 |
|---|---|
| [当前状态](current-state.md) | 已发布版本、已实现能力、默认链路、当前阻断和主任务 |
| [架构总览](architecture-overview.md) | 组件、端口、数据流、代码边界和部署模型 |
| [项目蓝图](project-blueprint.md) | 六个月以上的方向、取舍原则和长期门禁 |
| [主线执行计划](mainline-execution-plan.md) | v3.6.2 发布后两个月的具体执行顺序 |
| [单节点运营与 30 天验证](single-node-enterprise-validation-plan.md) | 自动部署、SLI/SLO、备份恢复、72 小时预演和 30 天运行契约 |
| [平台生产边界](platform-production-boundaries.md) | Linux x64、Linux ARM64、macOS ARM64 的不可互换证据边界 |
| [平台边界清单](platform-production-boundaries.json) | 平台契约的机器可读事实源 |
| [TODO Board](todos/BOARD.md) | 当前版本化任务状态；由 `tasks.json` 生成 |

## 开发与治理

| 文档 | 维护范围 |
|---|---|
| [开发者入门](ONBOARDING.md) | Conan/CMake、测试、CLion、编码规范、协议和 SDK 开发 |
| [发布治理](release-governance.md) | 可靠性矩阵、发布门禁、产物和阻断条件 |
| [性能基线](performance-baseline.md) | 测量命令、有效证据、历史数据和容量声明边界 |
| [TLS/mTLS Runbook](tls-mtls-runbook.md) | TLS profile、证书、验证、轮换和回滚 |
| [Legacy/Helper 清单](legacy/legacy-helper-inventory.md) | 当前仍保留的兼容面和删除条件 |
| [脚本索引](script-inventory.json) | public entrypoint、gate、producer、tool 和 legacy 分类 |

## Runner 与生产证据

| 文档 | 维护范围 |
|---|---|
| [Runner Inventory](runner-inventory.md) | 当前 runner 身份、在线状态和平台能力 |
| [Runner Gate Standard](runner-gate-standard.md) | 命名、标签、准入生命周期和缓存隔离规则 |
| [固定 Runner 手册](fixed-runner-playbook.md) | Conan/Docker 预热、R4/R5/R6 和专项证据操作 |
| [生产证据](production/) | 当前 manifest 和恢复演练模板 |

## 决策与配置

| 目录 | 内容 |
|---|---|
| [decisions/](decisions/) | 已接受 ADR；接受不等于默认激活，当前状态以 decision manifest 为准 |
| [v3.6 decision manifest](decisions/v3.6-decision-manifest.json) | 身份、SDK、Raft、macOS ARM64 和符号决策的机器可读状态 |
| [deployment/](deployment/) | 快速部署、生产配置、发布、运维和迁移 runbook |
| [legacy/](legacy/) | 仍受治理的兼容清单和预案，不是新功能入口 |

## 历史归档

以下文档记录已经结束的版本工作，只用于追溯：

| 文档 | 历史范围 |
|---|---|
| [v3.5.x 维护计划](archive/releases/v3.5.x-maintenance-plan.md) | v3.5.1-v3.5.3 补丁版本计划与结果 |
| [v3.5.2 冻结清单](archive/releases/v3.5.2-freeze-todo.md) | 第二 runner、发布和资产复验清单 |
| [v3.6 实现状态](archive/releases/v3.6-implementation-status.md) | v3.6.0-v3.6.2 P0-P6 交付和跨平台证据 |
| [完整归档索引](archive/README.md) | v1/v2 历史、旧计划、流程、release 和 runbook |

## 贡献入口

| 资源 | 用途 |
|---|---|
| [PR 模板](../.github/PULL_REQUEST_TEMPLATE.md) | PR 检查清单 |
| [Bug 报告](../.github/ISSUE_TEMPLATE/bug_report.md) | 可复现缺陷 |
| [功能请求](../.github/ISSUE_TEMPLATE/feature_request.md) | 新能力或改进建议 |
| [提交规范](../.github/COMMIT_CONVENTION.md) | Conventional Commit 格式和 scope |

## 事实优先级

1. 已实现能力和当前边界以 [current-state.md](current-state.md) 为准。
2. 当前两个月执行顺序以 [mainline-execution-plan.md](mainline-execution-plan.md) 为准。
3. 六个月以上方向以 [project-blueprint.md](project-blueprint.md) 为准。
4. runner 在线状态以 [runner-inventory.md](runner-inventory.md) 为准。
5. `archive/` 仅用于历史追溯；与当前文档冲突时一律服从当前事实源。
