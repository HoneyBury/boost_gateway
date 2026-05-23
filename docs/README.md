# 项目文档索引

> 当前事实源优先阅读：[当前项目事实源](./current-state.md) 和 [可靠性矩阵](./reliability-matrix.md)。两者用于对齐稳定性、可靠性、性能收束状态，并由 release candidate 门禁校验关键证据。

## 版本类型说明

项目涉及四类版本号，含义不同：

- **Framework version**（`CMakeLists.txt` 中 `project(VERSION ...)`）：项目的程序化版本号，对应 `PROJECT_VERSION` / `boost_gateway_VERSION` 变量，用于包管理和 CMake 依赖解析。当前为 `3.4.0`。版本号变更表示库接口、构建产物或能力集的变更。

- **Git tag**（`v*`）：发布版标记，格式为 `v<major>.<minor>.<patch>`（如 `v3.4.0`）。由 GitHub Actions `release.yml` 触发构建、测试、打包和 Release 发布。Tag 版本号与 Framework version 对齐。

- **CMake package version**（SDK `boost_gateway_sdk`）：SDK 的 CMake 包版本，通过 `find_package(boost_gateway_sdk)` 解析。独立于框架版本演进，当前为 `4.x` 系列。

- **文档基线版本**：文档中标注的版本号（如 `v3.4.0`、`v2.0.0`）表示该文档最后一次与对应代码基线对齐的时间点。历史文档（如 `v1.x` 系列文档）保留原始基线版本，不代表当前仓库状态。当前文档统一以 `v3.4.0` 为基线。

当前项目的工程化文档统一放在 `docs/` 目录下，面向后续把本项目持续演进成一个企业级高性能实时服务框架。

## 文档列表

### v1.x 维护期（历史记录，v2.0.0 已完成）

- [v1.x 能力成熟度矩阵](./v1-maturity-matrix.md) — **维护期单一事实源**，回答"哪些能力是 stable / experimental / reserved / demo-only"
- [v1.x 业务线职责与事实源](./v1-business-fact-source.md) — **`v1.1.5`**：login / room / battle 边界与四个核心验收问答
- [v1.x 业务字符串协议冻结](./v1-string-protocol.md) — **`v1.1.6`**：消息号 / body / `ErrorCode` / 与 `net::msg` 分叉说明
- [v1.x 跨域编排流程](./v1-cross-domain-flows.md) — **`v1.1.7`**：重复登录恢复链（T07）、空房战斗清理链（T08）的单一入口说明
- [v1.x 房间与战斗态边界](./v1-room-battle-boundary.md) — **`v1.1.8`**：状态表、`transfer_session` 契约、`member_user_id`、`end_battle` 与房间的解耦
- [v1.x 二进制 Admin 权限前提与审计最小规则](./v1-admin-audit-rules.md) — **`v1.1.11` / T11**：L3 调用契约 + `admin_invoke` 必备键 + 与治理分层关系
- [v1.x 网关配置字段成熟度](./v1-config-maturity.md) — **`v1.1.12` / T12**：`GatewayAppConfig` 启动/热更新/预留口径 + `ConfigWatcher` 现状
- [v1.x 标准运行时装配与生命周期](./v1-runtime-lifecycle.md) — **`v1.1.13`–`v1.1.14` / T13**：启动 / reload / shutdown 清单；**`io_context.stop()`**；**v1.1.14** 受控 reload（**`try_load_gateway_config`**）与 shutdown 语义 **§6–§7**
- [v1.x 横切能力定位（持久化 / 回放 / 审计）](./v1-cross-cutting-capabilities.md) — **`v1.1.15` / T14**：生命周期节点接线**事实**
- [v1.x 横切动作生命周期绑定规范](./v1-cross-cutting-lifecycle-binding.md) — **`v1.1.16` / T15**：节点 **N1–N7** × 审计 / 持久化 / replay **应收口规范**；showcase 自检清单
- [v1.x 横切数据格式与后端支持级别](./v1-cross-cutting-data-formats.md) — **`v1.1.17` / T16**：player JSON / SQLite / replay 载荷 / **`AUDIT_LOG`** 行格式事实与兼容边界
- [v1.2.0 结构升级决策](./v1-structure-upgrade-decision.md) — **`T21`**：typed protocol / internal bus / battle replay 是否在维护分支转正的决策记录（结论：**不提前推进**）
- [发布流程](./release-process.md) — `develop -> main`、CI/CD、tag release 与归档内容
- [v1.2.5 发布说明](./releases/v1.2.5.md) — v1.x 维护期最终稳定版发布记录
- [v2.0 启动清单](./v2-startup-checklist.md) — v2.0.0 启动基线、分支策略、分批清单与最终实现状态（七大模块全部落地）
- [v2 runtime 原型说明](./v2-runtime.md) — `ActorSystem`、mailbox、单线程调度模型与当前限制
- [v2 协议桥接说明](./v2-protocol-bridge.md) — `SessionAdapter` / `GatewayActor` / `Runtime` 的协议入口与路由边界
- [v2 PlayerActor 生命周期](./v2-player-lifecycle.md) — 登录、顶号、挂起、进房、进战与回切规则
- [v2 RoomActor 生命周期](./v2-room-lifecycle.md) — 成员、ready、开战资格、battle active 与结束回收
- [v2 GatewayServer 接入门槛](./v2-gateway-cutover-criteria.md) — 何时允许从 demo 入口推进到现有主链桥接
- [v2 下一阶段边界](./v2-next-phases.md) — M1-M7 完成状态、各模块进入门槛、远期目标
- [v2 服务拆分规划](./v2-service-split-plan.md) — `M4` 下服务拆分 / 多进程后端的专项规划
- [开发优化文档（v1.0.0 维护期）](./development-optimization.md) — 模块级问题分析、整改路线图、版本批次任务表
- [开发优先级看板](./development-priority.md)
- [当前网关骨架运行说明](./runtime-playbook.md)
- [开发日志模板与阶段记录](./development-log.md)
- [高性能游戏服务器框架规划](./architecture-roadmap.md) — v1.x 架构演进路线
- [工程开发规范](./engineering-guide.md)
- [Git 工作流与提交规范](./git-workflow.md)
- [AI 团队模式提示文档](./ai/README.md) — 面向任务规划、模块实现、单元测试、集成测试的严格提示模板

### v2.0.0（已完成，2026-05-12）

- [v2.0.0 启动清单与最终实现状态](./v2-startup-checklist.md) — 全部 B0-B8 批次和七大模块（M1-M7）完成记录
- [v2.0.0 企业级架构规划](./v2-roadmap.md) — 七大模块设计目标与实现路线
- [v2.0.0 设计文档](./v2-design.md) — Actor + ECS 混合架构最小设计草案
- [v2 服务拆分规划](./v2-service-split-plan.md) — M4 分布式原语与服务拆分设计
- [v2 下一阶段边界](./v2-next-phases.md) — 各模块进入门槛与完成状态

### v2.0.1 生产加固（已完成，2026-05-12）

- [v2.x 企业级迭代路线](./v2-enterprise-roadmap.md) §2.1 — H1-H6 六项生产加固验收记录（配置热加载/断路器/优雅降级/背压保护/连接限制/内存保护）

### v2.0.2 性能基线（已完成，2026-05-12）

- [性能基线报告](./performance-baseline.md) — 吞吐/延迟/资源基线测量方法、SLO/SLI 定义、容量规划公式与扩容推荐
- [Windows R1 基线结果](./performance-baseline-windows-r1.md) — `2026-05-16` 首轮 Windows baseline 实测、聚合结果与 gate 判定
- [R2 架构微基准闭环](./architecture-baseline-r2.md) — Actor runtime 本地投递、跨 core mailbox、创建与 shutdown 微基准入口
- [R3 Actor 并发模型与线程边界](./actor-concurrency-model-r3.md) — owner core、跨核 mailbox、dispatch 与 shutdown 契约
- [R4 通信契约与兼容迁移计划](./communication-contract-r4.md) — typed envelope、proto/gRPC 路线与 legacy raw JSON 兼容策略
- [v2.x 企业级迭代路线](./v2-enterprise-roadmap.md) §2.2 — B1-B6 六项性能基线验收记录

### v2.x 企业级迭代

- [v2.x 企业级迭代路线](./v2-enterprise-roadmap.md) — 从 v2.0.0 到 v3.0.0 的完整版本规划与验收标准
- [架构验收标准](./architecture-acceptance-criteria.md) — 五维度量化验收标准（性能/可靠性/可观测性/安全性/可运维性）
- [实时系统框架实施改造计划](./realtime-framework-modernization-plan.md) — 约束主线继续作为企业级实时服务底座演进，demo 只能作为验证样例接入
- [实时系统框架模块边界](./realtime-framework-module-boundaries.md) — 定义 gateway、identity、room、matchmaking、realtime instance、leaderboard、observability 与 demo 的允许/禁止职责
- [SDK 封装边界与演进说明](./realtime-framework-sdk-boundary.md) — 定义 SDK 作为框架客户端接入层的通用 API 边界，禁止业务专属方法进入公共 SDK
- [服务端框架与坦克大战 Demo 开发计划](./server-framework-and-tank-demo-development-plan.md) — 仅服务端范围的阶段计划、优先级、时间安排、验收标准、测试与回归约束
- [v3.x 生产就绪加强规划](./v3-production-readiness-plan.md) — v3.3.x 之后的 12 周收口计划，覆盖性能数据闭环、架构实测、交付面、Actor 多核线程边界、通信契约、控制面和发布门槛
- [生产稳定化与交付闭环路线图](./production-stabilization-roadmap.md) — v3.3.2 之后的大阶段执行规划，聚焦生产部署、性能基线、固定 runner 证据、监控运维、SDK 企业级封装和长稳故障演练
- [生产候选实测与发布硬化规划](./production-candidate-hardening-plan.md) — P0-P6 收束后的下一阶段规划，聚焦固定 runner 常态化、长稳、容量、Kubernetes 发布演练、观测闭环和 SDK 企业接入包
- [生产业务闭环接入规划](./production-business-closure-plan.md) — H0-H5 收束后的下一阶段规划，聚焦 matchmaking、leaderboard、Redis settlement、Raft、OTel、TLS、K8s/Operator 和 proto/gRPC 从单侧能力进入真实业务流水线
- [生产部署运行手册](./production-deployment-runbook.md) — P0 生产部署事实源，覆盖云服务器、Docker Compose、systemd、Kubernetes、监控、备份、回滚和发布后验证
- [生产运维 Runbook](./production-operations-runbook.md) — P3 告警响应、backend/Redis/gateway 排障、发布失败、回滚和日志采集流程
- [生产配置 Runbook](./production-configuration-runbook.md) — gateway / backend / Docker / systemd 配置入口、热重载边界、修改方式和生产配置建议
- [Redis / Raft HA Runbook](./redis-raft-ha-runbook.md) — P4 Redis 持久化/备份/恢复/降级策略、Raft HA profile 边界、三节点样例和固定 runner 验证入口
- [Observability / Trace Runbook](./observability-trace-runbook.md) — P5 OTel collector profile、trace/full-flow 验证、diagnostics 与延迟指标边界
- [TLS / mTLS Runbook](./tls-mtls-runbook.md) — P6 TLS/mTLS 配置、证书、灰度策略、默认 plain TCP 边界和发布前置条件
- [Kubernetes Business Flow Runbook](./k8s-business-flow-runbook.md) — P7 K8s manifest 发布、SDK full-flow 验证和 Operator kind evidence
- [v3 Proto / gRPC ADR](./v3-proto-grpc-adr.md) — P8 proto/gRPC 决策记录，明确 proto 契约已完成、generated gRPC transport 暂不默认上线
- [v3.x 发布清单](./v3-release-checklist.md) — v3.x 阶段的版本口径、P3 数据恢复、配置脚本、控制面入口和发布阻断条件
- [固定 Runner 执行手册](./fixed-runner-playbook.md) — Release baseline、Redis live、Operator kind 固定 runner 的 label、环境预检和手动 workflow 参数
- [生产证据固定 Runner 配置说明](./production-evidence-runner.md) — P2 生产证据 workflow 的 runner JSON 输入、真实依赖场景、预检 summary 和 artifact 归档标准
- [v3.3.2 P1 性能稳定化记录](./releases/v3.3.2-p1-performance-stabilization.md) — 性能事实管线、backend pool 实验和默认 baseline 稳定线
- [v3.3.2 P0 生产部署收束记录](./releases/v3.3.2-p0-production-deployment.md) — 生产部署 runbook、Kubernetes tag 固化、部署 gate 和监控口径收束
- [v3.3.2 P2 部署运维收束记录](./releases/v3.3.2-p2-deploy-operability.md) — Docker Compose、systemd、K8s、Prometheus 与部署预检门禁收束
- [v3.3.2 P2 生产证据固定 Runner 收束记录](./releases/v3.3.2-p2-production-evidence-runner.md) — production-evidence workflow、preflight summary、真实依赖场景和归档标准收束
- [v3.3.2 P3 监控运维收束记录](./releases/v3.3.2-p3-monitoring-operations.md) — Prometheus alerts、Grafana dashboard、生产运维 runbook 和监控静态 gate 收束
- [v3.3.2 P3 SDK 企业级封装记录](./releases/v3.3.2-p3-sdk-distribution.md) — SDK 版本、CMake package、C ABI、Python/C# wrapper 与分发门禁收束
- [v3.3.2 P4 SDK 企业级运行语义记录](./releases/v3.3.2-p4-sdk-enterprise-runtime.md) — SDK heartbeat、disconnect callback、push/reconnect、C ABI heartbeat 与 Python/C# 版本诊断收束
- [v3.3.2 P4 运行态可观测性记录](./releases/v3.3.2-p4-observability-runtime.md) — 真实 `v2_gateway_demo` + SDK 流量验证 `/health`、`/ready` 与 `/metrics*`
- [v3.3.2 P5 控制面门禁记录](./releases/v3.3.2-p5-control-plane.md) — Operator manifest 静态契约、仓库本地 Go cache 与默认控制面门禁收束
- [v3.3.2 P5 长稳故障回滚记录](./releases/v3.3.2-p5-production-resilience.md) — P5 resilience gate、bounded soak、故障恢复、Redis/Raft/Operator、runtime HTTP 与回滚演练入口收束
- [v3.3.2 P6 生产证据收束记录](./releases/v3.3.2-p6-production-evidence.md) — 本机 Redis/kind/Release baseline 真实依赖验证与最终门禁结果
- [v3.3.2 H0-H5 生产候选硬化记录](./releases/v3.3.2-h0-h5-production-hardening.md) — 固定 runner、长稳/容量入口、K8s、观测和 SDK 企业接入包证据收束
- [v3.3.2 OrbStack 生产部署上线记录](./releases/v3.3.2-orbstack-production-deployment.md) — 本机 OrbStack Docker Compose 全栈部署、监控入口和 SDK 真实业务闭环证据
- [部署手册](../deploy/README.md) — Docker Compose + systemd 部署运行手册
- [业务 demo 接入规范](../demo/games/README.md) — `demo/games/` 下游戏和实时系统样例的目录、验证和生产 gate 隔离规则
- [坦克大战 demo 规划](../demo/games/tank-battle-demo-plan.md) — 多人在线 2D 坦克大战作为业务验证样例的流程、边界、SDK adapter 和验收标准

## 文档使用建议

1. **维护期判断能力是否可依赖**：先查《v1.x 能力成熟度矩阵》。`README.md` / `CHANGELOG.md` / `runtime-playbook.md` 与本表冲突时，以本表为准。
2. **开始新阶段前**：先看《开发优化文档》§7、§8、§11 的整改路线和任务批次；不要跳过 `v1.1.x` 维护版本直接做 `v1.2.0` 或 v2.0 内容。
3. **日常开发**：按《工程开发规范》执行。
4. 每次较大迭代结束后更新《开发日志模板与阶段记录》和 `CHANGELOG.md`。
5. 所有分支流转、提交信息和合并动作遵循《Git 工作流与提交规范》。
6. v2.0.0 七大模块（M1-M7）已于 2026-05-12 全部落地，v3.4.0 已完成 P0 性能优化与 R7 模块收束，当前主线已进入 **v3.x 生产就绪加强执行阶段**：
   - typed `ServiceEnvelope` helper 已接入 `login/room/battle/match/leaderboard`
   - Operator 已具备 `Certificate` reconcile 与 rollout-aware `status.conditions`
   - 恢复/追平验证与 P4 验证脚本已持续补强
7. 对当前主线最有参考价值的文档是：
   - `README.md`
   - `docs/development-log.md`
   - `docs/v2-enterprise-roadmap.md`
   - `docs/realtime-framework-modernization-plan.md`
   - `docs/realtime-framework-module-boundaries.md`
   - `docs/realtime-framework-sdk-boundary.md`
   - `docs/server-framework-and-tank-demo-development-plan.md`
   - `docs/v3-production-readiness-plan.md`
   - `docs/v3-release-checklist.md`
   - `docs/p4-validation-checklist.md`
   - `docs/k8s-operator-implementation.md`
