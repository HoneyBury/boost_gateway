# AI 团队模式提示文档

本目录用于存放面向 AI 协作的标准提示文档，目标是在**严格遵守现有 `docs/` 规划文档**的前提下，开启项目级团队模式。

## 当前阶段说明

当前项目 `develop` 分支已进入 **v2.0.0 完成后阶段**，七大模块（M1-M7）已全部落地，R1-R4 发布基础设施（cmake --install、Docker、systemd、CI/CD、监控面板）已完成。473 测试通过。

当前判断依据：

- `docs/v2-startup-checklist.md` §7.5 和 §11：全部七大模块完成
- `docs/v2-roadmap.md`：M1-M7 状态全部为 `done`
- `docs/v2-enterprise-roadmap.md`：v2.0.1 → v3.0.0 完整版本规划
- `docs/architecture-acceptance-criteria.md`：10 个维度的量化验收标准

因此，本目录中的 Agent 提示词默认应按以下方式理解项目状态：

1. `develop` 主线当前为 **v2.6.0**（文档收束 + 环境配置），576 测试通过。
2. 下一阶段为 **v3.0.0**（分布式运行时：Cluster Router、Remote Actor、一致性哈希）。
3. `v1` 文档仍然是兼容链路、桥接边界、历史回归和现网语义的重要事实源。
4. 任何新任务都必须参照 `v2-enterprise-roadmap.md` 确定所属版本和验收标准。
5. 当前整体评级为 **”生产”阶段**，v3.0.0 目标为 **”企业”阶段**。
6. SDK (`sdk/`) 是独立可编译库，Agent 修改 SDK 时必须同步更新 `sdk/docs/`。
7. 环境配置 (`env/`) 是运维参考，Agent 修改服务配置时必须同步更新 `env/` 对应文件。

## 文档列表

- [团队模式总控提示](./team-mode-orchestrator.md)
- [任务规划 Agent 提示词](./task-planning-agent.md)
- [子模块实现 Agent 提示词](./module-implementation-agent.md)
- [单元测试 Agent 提示词](./unit-test-agent.md)
- [集成测试 Agent 提示词](./integration-test-agent.md)

## 使用原则

1. 所有 Agent 都必须先读取 `docs/README.md`，再按任务需要读取对应事实源文档。
2. 文档冲突时，按以下优先级处理：
   - 与当前任务阶段直接对应的状态文档与事实源文档
   - `docs/v2-startup-checklist.md`
   - `docs/v2-roadmap.md`
   - `docs/v2-next-phases.md`
   - `docs/development-priority.md`
   - 对应专题事实源文档，例如 `v2-runtime.md`、`v2-protocol-bridge.md`、`v2-player-lifecycle.md`、`v2-room-lifecycle.md`、`v1-business-fact-source.md`、`v1-string-protocol.md`、`v1-runtime-lifecycle.md`
   - `docs/engineering-guide.md`
   - 其他说明性文档
3. 当前阶段默认是 `v2` 实作期；若任务涉及 `GatewayServer` 主链、旧协议兼容、回归护栏或维护分支语义，必须同时引用对应 `v1` 文档。
4. 任何实现、测试、回归修复都必须回链到明确文档依据、明确模块边界、明确验收标准。
5. 如果规划文档没有授权某项能力，Agent 不得自行脑补为“已实现”或“允许开发”；如果 roadmap 只是长期目标，Agent 也不得把它写成“当前已落地”。

## 推荐执行顺序

1. 使用《团队模式总控提示》启动整个多 Agent 协作。
2. 由《任务规划 Agent 提示词》产出任务拆解、优先级、模块边界与交付清单。
3. 每个子模块分别交给《子模块实现 Agent 提示词》执行。
4. 模块实现过程中同步触发《单元测试 Agent 提示词》补齐测试与验收。
5. 阶段完成后由《集成测试 Agent 提示词》完成端到端验收、回归和缺陷闭环。
