# 项目蓝图

更新时间：2026-07-24

本文档定义 BoostGateway 六个月以上的产品和工程方向。已经实现的事实以
[current-state.md](current-state.md) 为准，当前两个月执行顺序以
[mainline-execution-plan.md](mainline-execution-plan.md) 为准。旧版蓝图已归档到
[project-blueprint-2026-07-09.md](archive/plans/project-blueprint-2026-07-09.md)。

## 定位

BoostGateway 是企业级、高性能、跨平台的 C++20 实时服务框架，而不是游戏业务代码
集合。框架负责连接、协议、路由、session、实时实例运行时、观测、持久化、部署和 SDK；
具体地图、碰撞、胜负、计分等业务规则只存在于 `demo/games/` 或外部 plugin。

对外命名保持：

- 产品和项目名：`BoostGateway`
- CMake project：`boost_gateway`
- 描述：`Enterprise-grade C++20 realtime service framework`
- `BoostAsioDemo`：仅作为历史名称和兼容标识

## 当前基线

| 领域 | 当前事实 |
|---|---|
| 发布 | v3.6.2 三平台 runtime、SDK 4.2.0、符号、SBOM 和 provenance 已发布复验 |
| 默认链路 | SDK + TCP Gateway + 五 backend；gRPC 保持实验 |
| 平台 | Linux x64、Linux ARM64、macOS ARM64 原生证据独立维护 |
| 依赖 | Conan 2.8.1、profile/lockfile、严格 CMake provider |
| 协议 | typed envelope 为业务主线，Raft 保留受治理的 legacy/protobuf 兼容窗口 |
| 运维 | Compose/Kubernetes/Operator、监控、恢复和发布门禁均有入口 |
| 当前工作 | Ubuntu 24.04 x64 单节点运营闭环、72 小时预演和 30 天不可变验证 |

## 设计原则

1. **事实优先**：能力声明必须绑定代码、测试、workflow、不可变资产或 runtime summary。
2. **默认链路保守**：实验协议、可选 storage 和 demo 不因实现存在而自动进入生产默认值。
3. **平台原生**：不同 OS、架构、编译器和 package format 的证据不可互换。
4. **兼容可回滚**：协议、存储、身份和 SDK 演进必须具备兼容窗口与 downgrade 证据。
5. **运营先于扩张**：当前阶段优先修复部署、观测、恢复、治理和长期运行缺口。
6. **自动门禁可解释**：门禁必须输出结构化 summary、provenance 和明确失败类别。

## 模块边界

| 模块 | 长期职责 | 约束 |
|---|---|---|
| `include/src/v2` | Gateway、backend、runtime、service、observability 公共框架 | 不接收具体游戏业务规则 |
| `include/src/v3` | Raft、Redis、持久化和协议演进 | 激活受 migration/rollback 门禁控制 |
| `proto/v3` | 版本化 schema | schema 改动必须保持兼容和生成物治理 |
| `sdk` | C++ API、C ABI、Python/C# wrapper 和分发 | API 改动绑定协议、兼容测试和版本策略 |
| `env/deploy/operator` | 生产配置、容器、Kubernetes 和运维 | 配置事实源唯一且可审计 |
| `demo/games` | 框架能力的业务验证 | 默认 OFF，不作为生产能力承诺 |
| `scripts` | gate、producer、tool 和稳定 public entrypoint | 分类由 `script-inventory.json` 治理 |

## 路线图

### O1：单节点企业运营闭环

周期：当前两个月。

- release-driven Compose 安装、升级和回滚，服务器不编译源码。
- host preflight、外部 canary、45 天 metrics 和 evidence ledger。
- 异机备份、Redis/host/runtime 恢复演练与明确 RTO/RPO。
- 72 小时上线预演和同一 tag/SHA/digest 的 30 天不可变运行。
- required checks、review、CODEOWNERS、SECURITY 和 Action SHA pinning。

完成标准由 [单节点运营计划](single-node-enterprise-validation-plan.md)定义。

### O2：协议和状态演进

- Raft protobuf writer 只在全 peer capability、mixed-binary、恢复和回退门禁通过后激活。
- gRPC 继续作为实验 transport profile；只有相对 TCP 主链出现明确收益且运维成本可控时，
  才进入默认传输评审。
- legacy raw JSON 兼容面只缩不增，删除前必须有 fixture、dual-reader 和 rollback 证据。
- 所有新业务 contract 使用 typed/schema 表达，不添加新的字符串协议债务。

### O3：SDK 和分发生态

- 维持 C ABI 作为 Python/C# native 边界，避免语言 wrapper 直接绑定 C++ ABI。
- GitHub Release 资产保持 checksum、SBOM、provenance 和 clean consumer 验证。
- PyPI/NuGet.org trusted publishing 与 Apple signing/notarization 分别通过独立 ADR 和门禁。
- SDK 新 API 必须包含 server compatibility、package consumer 和 full-flow 覆盖。

### O4：多节点和恢复能力

- 单节点 30 天验证完成前，不扩大多节点生产支持声明。
- 后续 HA 工作以故障模型为单位推进：leader loss、network partition、stale peer、storage
  corruption、rolling upgrade 和 downgrade。
- Kubernetes/Operator 支持必须覆盖真实 rollout、restart、scale、rollback 和 cleanup，
  不能只依赖 manifest 静态检查。

### O5：性能和资源治理

- 性能声明绑定 workload、候选 SHA、runner、lockfile、CPU set 和原始样本。
- 区分延迟/吞吐事实、单机容量、部署 SLO 和多节点扩展，禁止相互替代。
- 优化由长期指标或 incident/RCA 驱动，不为通过门禁放宽阈值。
- 持续维护 Linux x64、Linux ARM64、macOS ARM64 原生基线和趋势。

### O6：维护面收缩

- 清理无 workflow、public script、install 或测试消费者的 compatibility shim。
- 历史计划和完成记录及时迁入 `docs/archive/`，顶层只保留当前维护事实。
- 默认 build/install 只表达主线 runtime、SDK、配置和当前文档。
- 删除前运行文档、脚本 inventory、release consumer 和相关行为门禁。

## 决策门禁

| 决策 | 允许推进 | 必须阻断 |
|---|---|---|
| gRPC 进入默认链路 | full-flow、SDK、观测、RBAC、TLS、性能和运维收益同时成立 | 只有 schema/RPC 或专项 PoC |
| Raft protobuf writer 默认开启 | mixed-binary、恢复、capability 撤销和 downgrade 全部通过 | reader 可用但 writer/rollback 证据不完整 |
| 扩大平台支持 | 原生构建、package、runtime、符号和独立资产消费闭环 | 用其它平台或模拟器结果代替 |
| 发布到公共 package registry | trusted publishing、所有权、SBOM/provenance 和回滚策略明确 | 只有 GitHub Release package |
| 声明生产容量 | 固定 workload、多轮原始证据、资源约束和 SLO 一致 | 单次本地结果或配置请求率上限 |
| 删除兼容层 | 无当前消费者且替代路径、fixture、迁移和回滚均验证 | 仍有 workflow/install/runtime 引用 |

## 蓝图验收

蓝图不是完成清单。每项能力只有同时满足以下条件才可写入当前事实源：

1. 默认或显式配置边界已经进入代码。
2. 对应 unit/integration/e2e 或专项测试存在。
3. 目标平台和依赖环境已实际运行。
4. summary/provenance/asset 可独立复验。
5. 文档、部署、回滚和运维入口一致。

版本化执行任务以 [TODO Board](todos/BOARD.md) 为准；蓝图不重复维护短周期任务状态。
