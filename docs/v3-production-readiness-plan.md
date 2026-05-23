# v3.x 生产就绪加强规划

> 目标：把当前 v3.4.0 的"功能完整、P0 性能优化与 R7 模块收束已完成"状态推进到"可落地、稳定、高性能、可交付"状态。
> 本文档作为 v3.3.x 之后的执行计划（当前 v3.4.0 阶段正在执行），优先级高于继续横向扩展新功能。

## 1. 当前短板判断

当前项目的主要问题不是功能缺失，而是缺少足够硬的工程事实闭环：

1. **性能数据没有闭环**：`performance-baseline.md` 已经设计了方法、拓扑、指标和表格，但吞吐、P99、资源占用、10K 连接、战斗广播延迟、每连接内存成本等关键指标仍是待测定。
2. **架构验收缺少实测数据**：Actor tell 延迟、跨核投递、消息吞吐、actor 上限、网络吞吐、缓存命中率、战斗 tick 耗时等仍然未测定。
3. **版本与交付面膨胀**：README、CMake project version、install targets、实际可执行文件和文档口径需要重新对齐。
4. **Actor + 多核线程边界需要固化**：当前方向是正确的，但需要明确 owner core、ActorSystem 单线程调度、跨核 mailbox drain、shutdown 顺序和线程安全边界。
5. **通信契约仍在过渡态**：typed `ServiceEnvelope` helper 已接入主链，但还需要继续推进到正式生成式 proto/gRPC 传输和兼容退役计划。
6. **控制面、故障恢复、数据一致性仍需系统级验证**：Operator status、cert-manager、Raft 恢复、Redis 持久化、WriteBehind、故障注入都需要端到端验收。

## 2. 总体交付目标

v3.x 生产就绪阶段完成后，项目必须能够回答以下问题：

- 单 gateway 在指定硬件上能稳定承载多少连接、多少消息吞吐、多少战斗广播负载。
- 每个核心架构模块的延迟、吞吐、资源成本、退化点和瓶颈组件是什么。
- 当前版本安装后实际交付哪些二进制、配置、脚本、镜像和 Operator 能力。
- Actor 运行时哪些对象线程封闭，哪些 API 可跨线程调用，跨核消息何时投递、何时 drain。
- 通信协议的正式契约是什么，legacy raw payload 何时退役。
- 故障发生后，服务发现、重连、熔断、恢复、状态汇总和数据持久化能否被验证。

## 3. 阶段计划

建议以 **12 周** 为一个完整收口周期。每个阶段都必须输出可审查的文档、脚本、测试或实测数据。

| 阶段 | 时间 | 主题 | 核心交付 |
|---|---:|---|---|
| R0 | 1 周 | 版本与交付面收束（当前执行中） | 版本口径统一、install targets 对齐、发布清单 |
| R1 | 3 周 | 性能数据闭环 | Release 基准环境、标准压测矩阵、首版实测性能表 |
| R2 | 2 周 | 架构验收实测 | Actor/I/O/内存/数据/Battle 微基准和验收表 |
| R3 | 2 周 | Actor 多核线程边界 | 并发模型文档、debug 断言、跨核压力测试、shutdown 验证 |
| R4 | 2 周 | 通信契约与可靠性 | proto/gRPC 迁移计划、兼容测试、故障注入矩阵 |
| R5 | 2 周 | 控制面与发布门槛 | Operator e2e、Redis/Raft 恢复、CI 性能门槛、v3.x 生产候选版 |

## 4. R0：版本与交付面收束

### 4.1 目标

消除"文档说有、构建未安装"以及"README 版本与 CMake 版本不一致"造成的成熟度疑虑。

### 4.2 任务

| 编号 | 任务 | 交付物 |
|---|---|---|
| R0-1 | 统一版本口径 | `README.md`、`CHANGELOG.md`、`CMakeLists.txt`、docs 中版本说明一致 |
| R0-2 | 核对 install targets | `v2_gateway_demo`、`v2_login_backend`、`v2_room_backend`、`v2_battle_backend`、`v2_match_backend`、`v2_leaderboard_backend`、`v2_gateway_pressure` 均有明确安装策略 |
| R0-3 | 形成发布清单 | 新增或更新 release checklist，列出二进制、配置、脚本、镜像、Operator、proto 生成入口 |
| R0-4 | 区分版本类型 | 明确 framework version、CMake package version、git tag、文档基线之间的关系 |

### 4.3 验收标准

| 项目 | 标准 |
|---|---|
| 版本一致性 | 顶层 README、CMake project version、当前路线图不出现互相矛盾的主版本口径 |
| 安装完整性 | README 中列出的可执行入口均在 install/package 文档中有明确状态：正式安装、实验入口或仅源码示例 |
| 发布可审计 | 任意一次发布能通过清单确认产物、配置、依赖、测试结果和已知限制 |

## 5. R1：性能数据闭环

### 5.1 企业级高性能游戏服务器性能事实标准

性能验收必须从"目标口号"变为"事实表"。每组性能数据都必须包含环境、构建、命令、原始结果和结论。

| 维度 | 企业级事实标准 | 说明 |
|---|---|---|
| 可重复性 | 同一场景至少运行 3 次，记录 min/median/max | 只接受 Release 构建和独占压测环境数据 |
| 连接规模 | 必须覆盖 1K、5K、10K 长连接 | 10K 作为单实例基础门槛，不代表最终上限 |
| 延迟统计 | 必须记录 P50/P90/P99/Max | 只看平均值不满足验收 |
| 资源统计 | 必须记录 CPU、RSS、fd、线程数、每连接内存成本 | 资源曲线必须能解释瓶颈 |
| 版本对比 | 每个关键版本保留与上一版本的吞吐、P99、RSS 对比 | 退化必须有解释或修复项 |
| 开关对比 | Redis、TLS、OTel、typed envelope 必须有 on/off 成本数据 | 避免把可选能力的成本混入基线 |
| 发布门槛 | 性能退化超过阈值时禁止进入生产候选 | 阈值见 R1.5 |

### 5.2 标准压测矩阵

| 场景 | 规模 | 指标 | 目标门槛 |
|---|---|---|---|
| Echo 单进程 | 1K/5K/10K 连接，30s/5min | msg/s、P99、RSS、CPU | 10K 连接可稳定完成，错误率 ≤ 0.1% |
| Gateway 多进程桥接 | login/room/battle/match/leaderboard | backend RTT P99、错误率 | backend RTT P99 ≤ 10ms，本机多进程端到端 P99 ≤ 50ms |
| 登录链路 | 1K 并发登录，token 校验 | login/s、P99、错误率 | P99 ≤ 50ms，错误率 ≤ 0.1% |
| 房间链路 | create/join/ready/start | op/s、P99、状态一致性 | P99 ≤ 50ms，无状态丢失 |
| 战斗广播 | 10/50/100/500 房间 | input->broadcast P99、fan-out、CPU | 100 房间 P99 ≤ 100ms，500 房间给出退化点 |
| Matchmaking | 1K/10K queued players | 匹配耗时、队列长度、CPU | P99 匹配耗时 ≤ 30s，队列无异常增长 |
| Leaderboard | Redis on/off | submit/query P99、QPS、RSS | Redis query P99 ≤ 5ms，本地内存 query P99 ≤ 1ms |
| TLS/mTLS | TLS off/on | 吞吐损耗、P99 损耗 | TLS 开启后吞吐损耗需量化，目标 ≤ 25% |
| OTel | exporter off/on | P99 损耗、丢弃率 | exporter 不可用时不得阻塞主链 |
| 稳定性浸泡 | 2h/8h | RSS 增长、fd 泄漏、错误率 | RSS 增长 ≤ 5%，fd 无泄漏，错误率 ≤ 0.1% |

### 5.3 数据落地要求

| 产物 | 路径建议 | 要求 |
|---|---|---|
| 原始压测 JSON | `runtime/perf/<date>/` | 保留原始输出，不手工改写 |
| 汇总表 | `docs/performance-baseline.md` | 填入实际数据，注明环境 |
| 对比表 | `docs/performance-baseline.md` 或独立 release note | 记录上一版本和当前版本差异 |
| 瓶颈分析 | `docs/performance-baseline.md` | 每个未达标项必须给出瓶颈判断和后续任务 |

### 5.4 优化方向

| 触发条件 | 优先优化方向 |
|---|---|
| Echo P99 > 50ms | 编解码路径、写队列、批量发送、跨线程投递 |
| 10K 连接 RSS > 2GB | Session 内存布局、buffer 生命周期、对象池复用 |
| 多核扩展系数 < 0.7 | accept 分布、core affinity、跨核 mailbox |
| Battle P99 > 100ms | AOI、广播 fan-out、BattleActor tick、输入队列 |
| Redis leaderboard P99 > 5ms | 连接池、pipeline、超时和降级策略 |
| OTel/TLS 损耗过高 | 异步导出、采样率、TLS session reuse |

### 5.5 发布门槛

| 指标 | 门槛 |
|---|---|
| P99 延迟 | 相比上一基线退化 > 10% 必须解释；退化 > 20% 阻断生产候选 |
| 吞吐 | 相比上一基线下降 > 15% 阻断生产候选 |
| RSS | 相比上一基线增长 > 20% 必须解释 |
| 错误率 | 标准压测错误率 > 0.1% 阻断生产候选 |
| 资源泄漏 | 2h 浸泡出现 fd 泄漏或持续 RSS 增长阻断生产候选 |

## 6. R2：架构验收实测

### 6.1 Actor 运行时

| 指标 | 目标 | 验收方法 |
|---|---|---|
| 本地 `tell()` 延迟 | P99 ≤ 1us | 微基准，单 core owner actor |
| 跨核 `tell()` 延迟 | P99 ≤ 10us | SPSC mailbox 基准 |
| 单 core 消息吞吐 | ≥ 1M msg/s | actor ping-pong / fan-in 基准 |
| actor 创建延迟 | P99 ≤ 10us | 批量创建 100K actor |
| actor 停止延迟 | P99 ≤ 5us | 批量 stop + drain |
| 并发 actor 上限 | ≥ 100K actor | 内存、调度延迟和消息丢失验证 |

### 6.2 I/O 引擎

| 指标 | 目标 | 验收方法 |
|---|---|---|
| 连接建立延迟 | P99 ≤ 1ms | 连接风暴压测 |
| 10K 连接消息吞吐 | ≥ 1M msg/s 总吞吐作为目标线 | Release 环境压测 |
| 多核 accept 均匀性 | 单 core 偏差 ≤ 20% | 统计每 core accepted connections |
| 背压行为 | 超阈值后限速或拒绝，不无限堆积 | 慢客户端测试 |

### 6.3 内存与数据层

| 指标 | 目标 | 验收方法 |
|---|---|---|
| BumpArena 分配 | P99 ≤ 10ns | 微基准 |
| ObjectPool acquire/release | P99 ≤ 50ns | 微基准 |
| LRU 命中率 | 读多写少场景 ≥ 80% | 业务模拟 |
| WriteBehind flush | P99 ≤ flush interval × 2 | 持久化压力测试 |
| 每连接内存成本 | 10K 下可量化，目标 ≤ 150KB/conn | RSS 差值法 |

### 6.4 ECS/Battle

| 指标 | 目标 | 验收方法 |
|---|---|---|
| 单场 tick 耗时 | 100 entities 下 ≤ 5ms | battle runtime benchmark |
| 输入到广播延迟 | P99 ≤ 100ms | battle broadcast benchmark |
| 并发战斗数 | 目标 ≥ 500 场，记录退化点 | 压力测试 |
| replay 确定性 | 相同输入跨平台输出一致 | replay/determinism 测试 |

## 7. R3：Actor + 多核线程边界

### 7.1 必须明确的线程契约

| 对象/路径 | 线程边界要求 |
|---|---|
| `ActorSystem` | 明确是否单线程 owner 调度；actor map、ready queue、scheduled messages 归 owner core |
| `ActorRef::tell()` | 允许跨线程调用时必须只进入线程安全 mailbox，不直接触碰目标 actor |
| 跨核 mailbox | 明确入队 happens-before、drain 触发点、批量 drain 上限 |
| actor 生命周期 | create/stop/destroy 必须在 owner core 完成；跨核投递到已销毁 actor 必须安全失败 |
| shutdown | stop 顺序必须固定：停止接入、drain mailbox、取消 scheduled messages、销毁 actor、停止 io core |

### 7.2 代码落地

| 任务 | 验收 |
|---|---|
| 编写 Actor 并发模型文档 | 覆盖 owner core、跨核投递、drain、shutdown |
| Debug owner-core 断言 | 错误线程调用在 debug/test 中直接暴露 |
| 跨核压力测试 | 高频 cross-core tell 不丢消息、不崩溃、不死锁 |
| shutdown race 测试 | stop 与 timer/message/create/destroy 并发时行为确定 |

## 8. R4：通信契约与可靠性

### 8.1 通信契约

| 项目 | 验收标准 |
|---|---|
| typed envelope | `login/room/battle/match/leaderboard` 请求和响应均有固定 kind、meta、payload 契约 |
| generated proto | proto 生成链进入常规构建或明确作为可选 target，并有 CI 验证 |
| legacy 兼容 | raw JSON payload 兼容期、退役版本和测试覆盖明确 |
| 错误传播 | gateway、backend、client SDK 看到的错误码和 trace_id 一致 |
| 性能成本 | envelope/proto 编解码成本有微基准和多进程端到端数据 |

### 8.2 可靠性

| 场景 | 验收标准 |
|---|---|
| backend 宕机 | ≤ 5s 摘除，gateway 不崩溃，错误可观测 |
| backend 恢复 | ≤ 5s 重新上线，路由恢复 |
| 连接超时 | gateway 超时后不泄漏 pending request |
| 熔断 | 连续失败达到阈值后进入熔断，半开探测成功后恢复 |
| Raft leader 切换 | leader 切换期间无已提交状态回退 |
| follower 追平 | follower 重启后能追平提交日志 |

## 9. R5：控制面、数据层、安全与发布门槛

### 9.1 Operator 与部署

| 项目 | 验收标准 |
|---|---|
| `Ready` | 所有核心组件 available 后为 True |
| `Progressing` | rollout 过程中为 True，完成后为 False |
| `Degraded` | observedGeneration 滞后、updatedReplicas 不足、available 异常时为 True |
| `TLSReady` | Secret 或 Certificate 就绪后为 True |
| `components[]` | gateway/login/room/battle/match/leaderboard/redis 状态独立可见 |
| kind smoke | CI 至少验证一次创建 CR、等待 Ready、读取 status、删除 CR |

### 9.2 数据层

| 项目 | 验收标准 |
|---|---|
| Redis leaderboard | Redis on/off 均可用；Redis 不可用时降级策略明确 |
| CachedBattleDataStore | write-back 语义可验证，flush 后数据不丢失 |
| replay/result/snapshot | 重启恢复测试覆盖 committed 数据 |
| 数据格式 | version/magic/schema 兼容策略明确 |

### 9.3 安全与可观测性

| 项目 | 验收标准 |
|---|---|
| JWT/Auth | dev provider 与生产 provider 明确隔离；生产模式不得默认 dev token |
| RBAC/Admin | admin 命令必须有权限前提和审计事件 |
| Rate limit | 全局、IP、用户、消息类型至少覆盖关键路径 |
| Trace | gateway 到 backend trace_id 贯穿，OTel exporter 不阻塞主链 |
| Metrics | 每个服务暴露 RED 指标和资源指标 |
| Audit | 登录、管理、结算、封禁、恢复动作有结构化审计 |

### 9.4 生产候选发布门槛

| 维度 | 必须满足 |
|---|---|
| 功能 | login/room/battle/match/leaderboard 全链路通过 |
| 性能 | R1 标准压测通过，退化不超过门槛 |
| 架构 | R2 架构验收表不允许仍为大面积"未测定" |
| 并发 | R3 跨核与 shutdown 压力测试通过 |
| 可靠性 | R4 故障注入矩阵通过 |
| 控制面 | R5 Operator kind smoke 和 status 断言通过 |
| 数据 | Redis/WriteBehind/replay 恢复测试通过 |
| 安全 | 生产模式 dev token 禁用或明确不可达 |
| 文档 | README、docs、release checklist 与实际产物一致 |

## 10. 优先级总表

| 优先级 | 主题 | 原因 |
|---|---|---|
| P0 | 性能数据闭环 | 决定架构可信度和后续优化方向 |
| P0 | 架构验收实测 | 把"设计标准"变成"可证明标准" |
| P1 | 版本与交付面收束 | 影响项目成熟度判断和外部使用 |
| P1 | Actor 线程边界 | 决定后续多核功能的稳定性上限 |
| P1 | 通信契约正式化 | 避免 typed helper 与 proto/gRPC 长期双轨漂移 |
| P1 | 故障恢复与控制面 | 决定生产环境可运维性 |
| P2 | 安全与审计完善 | 支撑生产模式和管理能力 |
| P2 | 文档与发布流程 | 保证交付可复现、可审计 |

## 11. 推荐交付节奏

| 周期 | 应完成内容 | 不应继续推进的内容 |
|---|---|---|
| 第 1 周 | 版本/安装/发布清单收束 | 新增业务模块 |
| 第 2-4 周 | 性能基线实测和瓶颈分析 | 未经测量的性能优化 |
| 第 5-6 周 | 架构微基准和验收表补齐 | 扩大协议面 |
| 第 7-8 周 | Actor 线程边界和并发压力测试 | 复杂新 Actor 行为 |
| 第 9-10 周 | proto/gRPC 契约、故障注入 | 继续保留未定义兼容语义 |
| 第 11-12 周 | Operator/data/security/release gate | 放宽发布门槛 |

## 12. 完成定义

当以下条件全部满足时，v3.x 可以被标记为生产就绪候选：

- `performance-baseline.md` 不再以"待测定"作为核心结论，至少有一套受控环境实测数据。
- `architecture-acceptance-criteria.md` 中核心运行时、I/O、内存、Battle、数据层指标已有实测或明确未达标任务。
- README、CMake、install/package、release checklist 的交付口径一致。
- Actor 并发模型有文档、断言和压力测试。
- typed envelope 到 generated proto/gRPC 的路线明确，并有兼容测试。
- Redis、Raft、Operator、TLS、OTel 的失败路径可被测试复现。
- CI 能阻断明显的性能退化、关键集成失败和控制面状态错误。
