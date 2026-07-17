# 文档索引

更新时间：2026-07-17

本文档是 BoostGateway 项目的文档入口。当前事实源以 `current-state.md` 为准。

## 核心文档

| 文档 | 用途 |
|---|---|
| [开发者入门](ONBOARDING.md) | **新贡献者从这里开始** — 环境搭建、构建、测试、开发工作流、编码规范、协议/SDK/demo 开发指南 |
| [当前状态](current-state.md) | 已实现能力的权威事实源 |
| [架构总览](architecture-overview.md) | 组件、数据流、部署模型 |
| [项目蓝图](project-blueprint.md) | 中长期规划和决策门禁 |
| [执行计划](mainline-execution-plan.md) | 当前版本执行计划和阶段状态 |
| [v3.5.x 维护计划](v3.5.x-maintenance-plan.md) | v3.5.1-v3.5.3 的补丁版本边界、顺序和冻结条件 |
| [Runner Inventory](runner-inventory.md) | GitHub Actions runner 拓扑单一事实源 |
| [Legacy / Helper 清单](legacy/legacy-helper-inventory.md) | 当前保留的 legacy/helper 兼容面与治理边界 |

## 贡献入口

| 资源 | 用途 |
|---|---|
| [PR 模板](../.github/PULL_REQUEST_TEMPLATE.md) | 提交 PR 时的检查清单 |
| [Bug 报告](../.github/ISSUE_TEMPLATE/bug_report.md) | 报告可复现的缺陷 |
| [功能请求](../.github/ISSUE_TEMPLATE/feature_request.md) | 提议新能力或改进 |

## 发布与可靠性

| 文档 | 用途 |
|---|---|
| [发布治理](release-governance.md) | 可靠性矩阵 + 发布检查清单 |
| [性能基线](performance-baseline.md) | 性能数据和归档口径 |
| [固定 Runner 手册](fixed-runner-playbook.md) | 固定 runner 操作指南 |
| [Runner Gate Standard](runner-gate-standard.md) | runner 命名、标签、Conan/Docker 缓存与 R5 证据准入规范 |
| [Runner Inventory](runner-inventory.md) | runner 在线状态与默认 fixed-runner 阻断说明 |

## 安全与传输

| 文档 | 用途 |
|---|---|
| [TLS/mTLS Runbook](tls-mtls-runbook.md) | 传输安全配置和运维 |

## 子目录

| 目录 | 内容 |
|---|---|
| [deployment/](deployment/) | 部署快速入门、生产部署/运维/配置 Runbook |
| [production/](production/) | 生产候选证据 manifest、恢复演练模板 |
| [legacy/](legacy/) | Legacy helper 清单、控制面预案、脚本整合计划 |
| [archive/](archive/) | 历史版本文档（仓库路径 `docs/archive/`，仅供参考，不作为当前事实源） |

## 文档优先级规则

1. 如果本文档与其他文档冲突，以 `current-state.md` 为准
2. 蓝图规划以 `project-blueprint.md` 为准
3. 归档文档 (`archive/`) 仅用于历史追溯，不代表当前实现
