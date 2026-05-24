# 开发优化文档（v1.0.0 维护期）

## 1. 文档目的

本文档用于记录当前项目在 `v1.0.0` 维护与更新阶段发现的设计不合理点、实现风险点和可优化方向。

约束：

- 以“指出问题 + 结合代码上下文给建议”为主
- 当前不直接给出代码修改
- 每次只针对一个明确模块补充分析
- 优先记录会影响 `v1.0.0` 可维护性、可测试性、边界清晰度和后续演进成本的问题

---

## 2. 当前基线说明

基于当前仓库状态，项目存在两个容易混淆的版本边界：

- `22defe8` 为 `release: v1.0.0 — Boost Game Server Framework`
- 当前 `develop` 分支头部为 `8ec6140`，已经继续演进到 `P17`，能力明显超出发布版

这意味着后续所有“设计是否合理”的判断，都必须先说明分析对象是哪一层基线：

- `v1.0.0` 发布版是否存在设计问题
- `develop` 后续增强是否引入了新的复杂度或边界混乱

如果不先区分这两个基线，后续评审很容易把“版本维护问题”和“未来特性演进问题”混在一起，导致建议不可执行。

---

## 3. 当前已确认的全局问题

### 3.1 版本语义不清：`v1.0.0` 与 `develop` 能力边界混用

现象：

- `README.md` 直接以 `Boost 游戏服务器框架 v1.0.0` 命名
- 但当前 `README.md` 描述的能力已经包含 `P8+` 之后的内容，例如：
  - 自动压缩
  - 分片传输
  - TLS
  - 管理指令
  - 匹配服务
  - 配置热加载
  - 多进程架构
- 这些能力来自 `v1.0.0` 发布后的持续提交，而不是单纯“只看 release tag 的最小发布面”

不合理点：

- 对外版本标识和仓库当前实际能力没有清晰分层
- 后续做 1.0.0 维护时，很难判断一个问题属于发布版缺陷还是开发分支增量复杂度
- 文档读者容易把“当前开发头部能力”误认为“1.0.0 稳定承诺能力”

建议：

- 明确区分“发布基线文档”和“开发分支现状文档”
- `README.md` 至少要标明：
  - 哪些内容属于 `v1.0.0` 发布能力
  - 哪些内容属于 `develop` 分支增量能力
- 后续模块分析时，每条问题都补充“适用范围”：`release-1.0.0` / `develop`

优先级：高

### 3.2 文档一致性不足：同一能力在多份文档中表述冲突

已确认冲突：

- 协议格式冲突
  - `README.md` 写的是：`[4字节长度][2字节消息号][4字节请求序号][4字节错误码][1字节标记位][消息体]`
  - `../runbooks/runtime-playbook.md` 第 2 节仍写成：`[4字节长度][2字节消息号][4字节请求序号][4字节错误码][消息体]`
  - 同一份 `runtime-playbook.md` 的后续章节又承认协议包含 `flags`
- 压测场景数量冲突
  - `README.md` / `CHANGELOG.md` 写 8 种场景
  - `../plans/development-priority.md`、`../runbooks/runtime-playbook.md`、`docs/architecture-roadmap.md` 仍保留 6 种场景表述
- 测试规模表述存在阶段残留
  - `docs/development-log.md` 中持续保留 `28/28`、`32/32`、`34/34` 等阶段性数字
  - 但总览文档已经按 `54 个测试` 对外描述

不合理点：

- 当前文档体系更像“迭代痕迹叠加”，而不是“以一个统一事实源维护”
- 当协议、测试、压测工具继续演进时，多个总览文档同时失真
- 这类不一致会直接降低后续模块分析的可信度

建议：

- 建立“单一事实源”原则
  - 协议现状：以一份主文档为准
  - 压测场景现状：以配置/工具支持能力为准
  - 测试规模：以当前测试清单或 CI 汇总为准
- `development-log` 保留阶段历史；`README` / `runtime-playbook` / `priority` 只保留当前事实，不应混入过期阶段描述
- 后续每次分析模块时，如果发现“文档说法”和“代码事实”不一致，统一记入本文档

优先级：高

### 3.3 路线文档过多，但职责边界还不够硬

当前 `docs/` 下同时存在：

- `architecture-roadmap.md`
- `v2-roadmap.md`
- `development-priority.md`
- `development-log.md`
- `runtime-playbook.md`
- `engineering-guide.md`

现状问题：

- 规划文档、现状文档、阶段记录文档、运行文档之间有明显内容交叉
- 同一能力会在多份文档重复出现，后续非常容易只更新其中一份
- 某些文档写“当前已完成”，某些文档写“建议补齐”，但没有严格绑定到具体版本上下文

不合理点：

- 文档职责重叠会导致维护成本线性上升
- 模块设计评审时，先找哪份文档作为依据并不直观

建议：

- 收敛文档职责
  - `README`：面向仓库入口，写当前稳定事实
  - `runtime-playbook`：只写运行、协议、可观测与测试入口
  - `development-log`：只保留阶段历史
  - `development-priority`：只维护“当前待办和优先级”
  - `architecture-roadmap`：只保留 v1.x 架构演进路线
  - `v2-roadmap`：只讨论 2.0 愿景，不混入 1.x 已完成项
- 后续按模块分析时，同时检查该模块的“代码实现 / 运行文档 / 规划文档”是否职责越界

优先级：中

### 3.4 当前仓库更像“功能全集成演示仓库”，维护视角下缺少稳定面定义

从目录看，当前仓库同时承载：

- 框架核心代码
- 多种示例程序
- 压测工具
- 观测配置
- Docker 与 CI 产物
- 多进程演示入口

不合理点：

- 对“1.0.0 维护”而言，哪些是稳定支持面、哪些只是演示能力，边界不够清晰
- 当某个模块设计不合理时，难以快速判断应先优化核心库、示例装配，还是部署/测试辅助层

建议：

- 后续模块评审统一按三层看：
  - 核心框架层：`include/` + `src/`
  - 组装与演示层：`examples/`
  - 交付与验证层：`tests/` `config/` `docker` `grafana` `prometheus`
- 如果某个问题只存在于示例装配层，不应上升为核心架构问题
- 如果某个设计缺陷在核心层已经固化，即使目前只在 demo 中暴露，也应高优先级记录

优先级：中

---

## 4. 后续模块分析记录模板

后续你指定具体模块后，按下面结构补充：

### 模块名

- 分析范围：
- 基线版本：
- 相关代码：
- 相关文档：

### 现状

- 当前职责
- 当前关键调用链
- 当前依赖关系

### 不合理点

- 问题 1
- 问题 2

### 风险

- 维护风险
- 演进风险
- 测试风险

### 优化建议

- 建议 1
- 建议 2

### 优先级

- 高 / 中 / 低

---

## 5. 下一步分析原则

后续按你指定的模块逐个分析时，统一遵循以下原则：

- 先看代码真实职责，再看文档宣称职责
- 先指出设计边界问题，再讨论性能优化
- 先判断问题是否属于 `v1.0.0` 维护范围，再判断是否属于 `develop` 增量复杂度
- 建议必须结合当前代码结构给出，不写脱离仓库上下文的泛泛建议

---

## 6. 模块分析记录

### 协议通信模块

- 分析范围：`protocol`、`packet_codec`、`packet_fragment`、`packet_compressor`、`message_dispatcher`，以及它们在 `Session` / `GatewayServer` / `InternalBus` 中的接入方式
- 基线版本：`develop`
- 相关代码：
  - [include/net/protocol.h](/D:/Program/boost/include/net/protocol.h)
  - [include/net/packet_codec.h](/D:/Program/boost/include/net/packet_codec.h)
  - [include/net/packet_fragment.h](/D:/Program/boost/include/net/packet_fragment.h)
  - [include/net/packet_compressor.h](/D:/Program/boost/include/net/packet_compressor.h)
  - [include/net/message_dispatcher.h](/D:/Program/boost/include/net/message_dispatcher.h)
  - [src/net/session.cpp](/D:/Program/boost/src/net/session.cpp)
  - [include/net/internal_bus.h](/D:/Program/boost/include/net/internal_bus.h)
  - [src/game/gateway/gateway_server.cpp](/D:/Program/boost/src/game/gateway/gateway_server.cpp)
- 相关文档：
  - [../runbooks/runtime-playbook.md](/D:/Program/boost/../runbooks/runtime-playbook.md)
  - [docs/architecture-roadmap.md](/D:/Program/boost/docs/architecture-roadmap.md)

### 现状

- `protocol.h` 负责消息号和错误码常量定义
- `packet_codec.h` 负责网络包头编解码，当前固定格式为：长度 + `message_id` + `request_id` + `error_code` + `flags` + `body`
- `packet_compressor.h` 提供基于 `flags::kCompressed` 的包体压缩/解压
- `packet_fragment.h` 提供大包分片和重组能力，但当前只看到定义和测试，没有接入 `Session` 收发主链路
- `message_dispatcher.h` 同时负责：
  - handler 注册
  - middleware 链
  - 消息线程池路由
  - 异步投递执行
- `Session` 在收包后直接做解码、解压，然后把消息交给上层 handler；发包前做压缩并直接编码
- `GatewayServer` 和 `InternalBus` 都把解码后的结果直接交给同一个 `MessageDispatcher`

### 不合理点

#### 6.1 `flags` 位语义设计冲突，分片标记与压缩/加密标记没有统一位布局

代码事实：

- [include/net/packet_codec.h:22](/D:/Program/boost/include/net/packet_codec.h:22) 定义了：
  - `kCompressed = 0x01`
  - `kEncrypted = 0x02`
- [include/net/packet_fragment.h:19](/D:/Program/boost/include/net/packet_fragment.h:19) 定义了：
  - `kFragment = 0x10`
  - `kLastFragment = 0x20`
- [include/net/packet_fragment.h:91](/D:/Program/boost/include/net/packet_fragment.h:91) 又把 `total_fragments << 4` 直接写进高半字节
- [include/net/packet_fragment.h:42](/D:/Program/boost/include/net/packet_fragment.h:42) 用低 2 位保存 `frag_index`

不合理点：

- 同一个 `flags` 字节同时承载“能力标记”和“分片元数据”，但没有统一位布局规范
- `kFragment = 0x10` 和 `total_fragments << 4` 实际共享高半字节，编码语义重叠
- `frag_index` 只保留 2 位，最多安全表达 0-3，但 `total_fragments` 没有同步限制，超过 4 片后编码语义已经不可靠
- `kLastFragment = 0x20` 也落在高半字节里，与总片数编码继续耦合

这不是单纯“实现还没完善”，而是协议元数据设计本身不稳定。

建议：

- 先定义清晰的 `flags` 位布局，再决定是否继续把分片元数据塞进同一个字节
- 更合理的做法是：
  - `flags` 只存布尔语义：压缩、加密、分片、控制消息等
  - 分片相关的 `fragment_index`、`fragment_count` 放入扩展头或独立分片头
- 如果仍坚持单字节方案，至少要硬性限制：
  - 最大分片数
  - 分片索引位宽
  - 各位段不可重叠

优先级：高

#### 6.2 分片能力是“存在但未接入”的孤岛能力，主链路不闭环

代码事实：

- [include/net/packet_fragment.h:76](/D:/Program/boost/include/net/packet_fragment.h:76) 提供 `fragment_packet()`
- [include/net/packet_fragment.h:32](/D:/Program/boost/include/net/packet_fragment.h:32) 提供 `FragmentAssembler`
- 但引用检索只出现在测试中，没有接入 `Session` 或 `InternalBus`
- [src/net/session.cpp:203](/D:/Program/boost/src/net/session.cpp:203) 收包后直接 `decode_payload()`
- [src/net/session.cpp:232](/D:/Program/boost/src/net/session.cpp:232) 解码后直接回调上层 `packet_handler_`
- [src/net/session.cpp:266](/D:/Program/boost/src/net/session.cpp:266) 发包时直接压缩后 `encode()`，没有分片路径
- [include/net/internal_bus.h:91](/D:/Program/boost/include/net/internal_bus.h:91) 内部总线读包后也直接 `decode_payload()` 然后 `dispatch()`

不合理点：

- 当前“支持分片”更像实验性工具函数，而不是协议栈真正能力
- 上层如果按文档理解为“超大包自动分片”，实际并没有进入网络主路径
- 分片重组状态也没有生命周期管理、超时回收、乱序处理、异常包处理

这会导致两个问题：

- 文档能力承诺大于代码真实能力
- 后续如果临时把分片接进 `Session`，会把很多未设计好的边界问题一次性暴露出来

建议：

- 在维护期先明确分片能力状态：
  - 要么声明“当前未正式启用分片”
  - 要么补成真正的收发闭环能力
- 如果要补闭环，接入顺序应该是：
  - 发包阶段统一决定“压缩后是否分片”
  - 收包阶段先重组，再解压，再交业务
  - 为 assembler 增加超时、总大小限制和 request 维度隔离

优先级：高

#### 6.3 压缩语义和实现语义不一致，无 zlib 时仍会打上“已压缩”标记

代码事实：

- [src/net/session.cpp:266](/D:/Program/boost/src/net/session.cpp:266) 只要 `should_compress()` 为真，就设置 `kCompressed`
- [include/net/packet_compressor.h:63](/D:/Program/boost/include/net/packet_compressor.h:63) 在没有 `HAS_ZLIB` 时，`compress_body()` 只是“4 字节长度前缀 + 原文透传”
- [include/net/packet_compressor.h:75](/D:/Program/boost/include/net/packet_compressor.h:75) 对应 `decompress_body()` 只是按长度截取原文

不合理点：

- 从协议语义上讲，`kCompressed` 应表示“包体经过某种压缩算法编码”
- 但当前 fallback 实现本质上不是压缩，只是换了一种包体封装方式
- 这会造成协议理解混乱：
  - 同一个标记位，在不同构建条件下代表不同语义
  - 如果未来引入跨语言客户端或独立后端服务，不同构建目标可能对 `kCompressed` 理解不一致

建议：

- `kCompressed` 应只代表“真正做过压缩算法处理”
- 如果没有 zlib：
  - 不要设置 `kCompressed`
  - 或者引入明确的 `encoding`/`codec` 语义，而不是复用压缩标记
- 最好在协议层声明压缩算法类型，而不是只给一个布尔位

优先级：高

#### 6.4 编解码层和消息语义层割裂，项目同时维护两套 body 协议

代码事实：

- 项目已经有 [include/net/message_serializer.h](/D:/Program/boost/include/net/message_serializer.h)
- 但当前业务服务仍大量手写字符串协议：
  - [src/game/login/login_service.cpp:15](/D:/Program/boost/src/game/login/login_service.cpp:15) 自己解析 `user|token|display_name`
  - [src/game/login/login_service.cpp:95](/D:/Program/boost/src/game/login/login_service.cpp:95) 返回 `login_ok:...`
  - [src/game/room/room_service.cpp:47](/D:/Program/boost/src/game/room/room_service.cpp:47) 返回 `room_created:...`
  - [src/game/battle/battle_service.cpp:69](/D:/Program/boost/src/game/battle/battle_service.cpp:69) 返回 `battle_started:...`
- 集成测试也直接断言这些字符串格式，如 [tests/integration/gateway_integration_test.cpp:198](/D:/Program/boost/tests/integration/gateway_integration_test.cpp:198)

不合理点：

- 网络包头已经结构化，但消息体语义仍大量依赖手写字符串拼接
- `message_serializer` 已存在却没有成为统一入口，说明协议层抽象没有真正落地
- 后续一旦要做跨语言、版本兼容、字段扩展、灰度发布，这类字符串协议会成为维护负担

建议：

- 明确 body 协议只能有一种主路径
- 如果决定走 `message_serializer`：
  - handler 入参应尽量在协议层完成反序列化
  - service 层不要继续手写 `:`、`|` 分隔字符串
- 如果暂时保留字符串协议，也应把“字符串 body”限定为 demo/兼容层，而不是核心业务协议

优先级：高

#### 6.5 `MessageDispatcher` 职责过重，协议入口、执行模型和网关策略耦合在一起

代码事实：

- [include/net/message_dispatcher.h:46](/D:/Program/boost/include/net/message_dispatcher.h:46) 负责 handler 注册
- [include/net/message_dispatcher.h:66](/D:/Program/boost/include/net/message_dispatcher.h:66) 负责 middleware 注册
- [include/net/message_dispatcher.h:41](/D:/Program/boost/include/net/message_dispatcher.h:41) 负责线程池范围路由
- [include/net/message_dispatcher.h:76](/D:/Program/boost/include/net/message_dispatcher.h:76) 负责异步投递执行
- [src/game/gateway/gateway_service.cpp:15](/D:/Program/boost/src/game/gateway/gateway_service.cpp:15) 把鉴权白名单、限频这样的网关策略直接挂在 dispatcher middleware 上
- [include/net/internal_bus.h:92](/D:/Program/boost/include/net/internal_bus.h:92) 内部总线也复用同一个 dispatcher

不合理点：

- `MessageDispatcher` 现在既像“协议分发表”，又像“执行器路由器”，又像“网关拦截器容器”
- 网关接入消息和内部总线消息进入同一 dispatcher，但两者的认证、频控、上下文约束并不完全相同
- 这会让协议模块逐渐背负业务入口职责，后续很难拆分 client ingress 和 backend ingress

建议：

- 拆分职责层次：
  - dispatcher 只负责“message_id -> handler” 映射与调用
  - middleware/ingress policy 放到更外层的 pipeline
  - 线程池路由作为执行策略层，而不是和 handler 表注册强绑定
- 至少把“客户端入口消息”和“内部服务消息”的接入链路分开，不要继续共享同一个中间件语义

优先级：中

#### 6.6 `dispatch()` 每次复制 middleware 列表，协议热路径存在额外开销

代码事实：

- [include/net/message_dispatcher.h:100](/D:/Program/boost/include/net/message_dispatcher.h:100) 在 `dispatch()` 中把 `middlewares_` 整体复制到局部变量
- [include/net/message_dispatcher.h:99](/D:/Program/boost/include/net/message_dispatcher.h:99) handler 也会复制一份

不合理点：

- 这在功能上没有问题，但属于热路径上的结构性开销
- middleware 越多，dispatch 的固定成本越高
- 当前工程已经在做压缩、批量发包、BufferPool 等性能优化，这里却还保留了按消息复制整条 middleware 链的实现，优化方向不一致

建议：

- 维护期内可以先不做复杂无锁优化，但至少应统一思路：
  - 中间件注册阶段构造不可变快照
  - 分发阶段只读共享快照，避免每次复制 `std::vector<std::function<...>>`
- 如果后续继续扩 middleware，这个点会越来越明显

优先级：中

#### 6.7 线程池范围路由是“按消息号硬编码分片”，可维护性一般

代码事实：

- [include/net/message_dispatcher.h:41](/D:/Program/boost/include/net/message_dispatcher.h:41) 通过 `[min_id, max_id]` 绑定线程池
- [include/net/message_dispatcher.h:117](/D:/Program/boost/include/net/message_dispatcher.h:117) dispatch 时按范围顺序查找匹配池

不合理点：

- 这种模型默认“消息号区间 == 业务执行域”，但这只是当前命名约定，不是强类型约束
- 消息号一旦调整、插空或跨模块复用，线程模型也会被隐式改变
- 它把协议编号策略和执行并发策略耦合到了一起

建议：

- 线程路由最好绑定“消息类别/服务类别/handler 标签”，而不是直接绑定消息号区间
- 维护期内如果不改结构，至少补文档约束：
  - 哪些号段归属哪个执行池
  - 修改消息号时必须同时评估线程路由影响

优先级：中

### 风险

- 维护风险：
  - 协议真实能力和文档承诺不一致，后续 bug 很容易被误判
  - 分片、压缩、序列化处于“半接入”状态，改一个点容易破另一个点
- 演进风险：
  - 后续做多进程、跨语言客户端、RPC 总线时，字符串 body 协议和模糊 `flags` 会快速放大成本
  - 当前 dispatcher 已经被客户端入口和内部总线共用，后续拆服时会形成耦合阻力
- 测试风险：
  - 当前测试覆盖了 codec、compressor、dispatcher 的局部行为
  - 但没有覆盖“分片 + 压缩 + Session + Dispatcher”的真实主链路组合场景

### 优化建议

- 第一优先级先统一协议元数据设计：
  - 固定 `flags` 位定义
  - 明确哪些信息属于固定头，哪些信息属于扩展头
- 第二优先级统一 body 协议主路径：
  - 选定结构化序列化作为核心协议，不再扩散字符串拼接 body
- 第三优先级补齐收发闭环：
  - 压缩、分片、解压、重组必须按确定顺序进入 `Session` / `InternalBus`
- 第四优先级收敛 dispatcher 职责：
  - 把消息映射、入口策略、执行路由逐步拆开

### 优先级

- 高：
  - `flags` 位设计冲突
  - 分片能力未闭环接入
  - 压缩标记语义不稳定
  - 两套 body 协议并存
- 中：
  - dispatcher 职责过重
  - dispatch 热路径复制中间件
  - 线程池按消息号区间硬编码

### 协议通信模块子分析 A：`Session + packet_codec/compressor/fragment`

- 分析范围：`Session` 的收包、发包、背压、关闭与协议增强能力接入顺序
- 基线版本：`develop`
- 相关代码：
  - [include/net/session.h](/D:/Program/boost/include/net/session.h)
  - [src/net/session.cpp](/D:/Program/boost/src/net/session.cpp)
  - [include/net/packet_codec.h](/D:/Program/boost/include/net/packet_codec.h)
  - [include/net/packet_compressor.h](/D:/Program/boost/include/net/packet_compressor.h)
  - [include/net/packet_fragment.h](/D:/Program/boost/include/net/packet_fragment.h)

#### 现状补充

- 入口顺序：
  - `start()` 直接启动 `do_read_header()` 和心跳定时器
  - `do_read_header()` 只负责读 4 字节长度头
  - `do_read_body()` 负责读完整 payload，然后立刻 `decode_payload()`
  - 解码后如果带 `kCompressed`，立即解压
  - 最终把 `PacketMessage` 直接交给上层 handler
- 出口顺序：
  - `send()` / `send_batch()` 会在编码前判断是否压缩
  - 压缩后直接 `encode()`
  - 没有分片发送阶段

#### 不合理点

##### 6.A.1 `Session` 的协议处理顺序没有抽象成明确 pipeline，增强能力只能硬塞在收发函数里

代码事实：

- [src/net/session.cpp:198](/D:/Program/boost/src/net/session.cpp:198) 读包后直接 `decode_payload()`
- [src/net/session.cpp:201](/D:/Program/boost/src/net/session.cpp:201) 在 `do_read_body()` 内直接处理解压
- [src/net/session.cpp:261](/D:/Program/boost/src/net/session.cpp:261) 发包前在 `enqueue_write()` 内直接判断压缩
- [src/net/session.cpp:266](/D:/Program/boost/src/net/session.cpp:266) 之后立即 `encode()`

不合理点：

- 当前收发过程是“写死在 `Session` 方法体里的顺序逻辑”，不是清晰的协议处理流水线
- 所以任何新增能力，例如：
  - 分片
  - 加密
  - 校验
  - tracing 元数据
  都只能继续堆进 `do_read_body()` / `enqueue_write()` / `send_batch()`
- 这会让 `Session` 逐渐演化成“传输层 + 协议增强层 + 部分安全层”的混合体

建议：

- 把协议处理顺序显式化，例如拆成：
  - 入站：`decode header -> decode payload -> reassemble -> decrypt -> decompress -> deliver`
  - 出站：`serialize body -> compress -> encrypt -> fragment -> encode`
- 维护期内即使不重构实现，也建议先把这个顺序固定为文档和代码注释中的唯一事实源

优先级：高

##### 6.A.2 `send_batch()` 与 `send()` 的语义不一致，批量发包路径丢失单包级元数据和观测语义

代码事实：

- [src/net/session.cpp:40](/D:/Program/boost/src/net/session.cpp:40) `send_batch()` 接收多个 `PacketMessage`
- [src/net/session.cpp:47](/D:/Program/boost/src/net/session.cpp:47) 将多条消息编码成一个连续 buffer
- [src/net/session.cpp:77](/D:/Program/boost/src/net/session.cpp:77) 最终只把 `messages.back()` 存进 `PendingWrite.message`
- [src/net/session.cpp:346](/D:/Program/boost/src/net/session.cpp:346) 发包完成后 `send_observer_` 只能看到这一条 `sent_message`

不合理点：

- 从网络上看是批量发送多条协议包，但内部队列只保留“最后一条消息”的语义信息
- 这会导致：
  - 发送观察器拿到的并不是完整批次事实
  - 指标统计如果依赖 observer，会出现语义偏差
  - 后续若要按消息维度做发送审计、限流、trace，很难复用这条链路

建议：

- `PendingWrite` 不应只保留单个 `PacketMessage`
- 批量发送要么：
  - 保存整个批次元数据
  - 要么在入队前就完成逐消息观测统计，再把底层写 buffer 与观测语义解耦

优先级：中

##### 6.A.3 `Session::stop()` 与 `handle_close()` 是两条不同关闭路径，生命周期收口不一致

代码事实：

- [src/net/session.cpp:90](/D:/Program/boost/src/net/session.cpp:90) `stop()` 只设置 `stopped_`、取消心跳、关闭 socket
- [src/net/session.cpp:366](/D:/Program/boost/src/net/session.cpp:366) `handle_close()` 才会调用 `close_handler_`
- [src/game/gateway/gateway_server.cpp:58](/D:/Program/boost/src/game/gateway/gateway_server.cpp:58) `GatewayServer::stop()` 通过遍历 `session->stop()` 关闭所有连接
- [src/game/gateway/gateway_server.cpp:150](/D:/Program/boost/src/game/gateway/gateway_server.cpp:150) 会话清理、房间移除、指标回收、连接计数递减都放在 `close_handler_`

不合理点：

- 主动关闭和异常关闭没有统一收口
- 这意味着主动停服时：
  - `session_manager_` / `room_manager_` 清理路径不一定被触发
  - `active_connection_count_` 不一定递减
  - `metrics_.on_session_closed()` 不一定执行
- 这是实打实的生命周期设计问题，不只是“代码风格不同”

建议：

- `stop()` 应复用统一关闭收口逻辑，而不是绕过 `handle_close()`
- 更稳妥的方式是：
  - 统一由一个私有 `close_once(reason)` 负责状态切换、资源释放、回调通知
  - `stop()` / 读写错误 / 超时都走同一条路径

优先级：高

##### 6.A.4 收包和发包都只接了压缩，没有接入分片，导致“能力组合顺序”根本未定义

代码事实：

- [src/net/session.cpp:201](/D:/Program/boost/src/net/session.cpp:201) 入站只做解压，不做分片重组
- [src/net/session.cpp:261](/D:/Program/boost/src/net/session.cpp:261) 出站只做压缩，不做分片发送
- [include/net/packet_fragment.h:32](/D:/Program/boost/include/net/packet_fragment.h:32) `FragmentAssembler` 需要上层主动调用
- [include/net/packet_fragment.h:76](/D:/Program/boost/include/net/packet_fragment.h:76) `fragment_packet()` 也需要上层主动调用

不合理点：

- 目前不仅是“没接分片”，更关键的是“压缩和分片先后顺序”都没有明确
- 这在协议设计上是大问题，因为：
  - `compress -> fragment`
  - `fragment -> compress`
  是两种完全不同的协议语义和实现复杂度

建议：

- 先在协议层定死组合顺序，建议：
  - 出站：先压缩，再分片
  - 入站：先重组，再解压
- 否则即使后面实现了分片，也很容易与当前压缩逻辑互相打架

优先级：高

##### 6.A.5 `FragmentAssembler` 以 `request_id` 为唯一 key，状态模型过于乐观

代码事实：

- [include/net/packet_fragment.h:44](/D:/Program/boost/include/net/packet_fragment.h:44) `assemblies_[fragment.request_id]`
- [include/net/packet_fragment.h:45](/D:/Program/boost/include/net/packet_fragment.h:45) 默认 `frag_index == 0` 视为初始化
- [include/net/packet_fragment.h:54](/D:/Program/boost/include/net/packet_fragment.h:54) 直接 append body
- [include/net/packet_fragment.h:57](/D:/Program/boost/include/net/packet_fragment.h:57) 通过 `is_last` 或收到数量够了就认为完成

不合理点：

- 没校验：
  - 分片是否乱序
  - `message_id` 是否一致
  - 总大小是否超限
  - 中间缺片是否真的存在
  - 同一 `request_id` 是否被新消息覆盖
- 这说明当前 assembler 是“演示级 append 逻辑”，不是可上线的协议重组状态机

建议：

- 维护期内如果准备正式接入分片，assembler 至少要增加：
  - `(session, request_id, message_id)` 维度的唯一键
  - 片序校验
  - 超时清理
  - 总大小上限
  - 非法片直接丢弃并统计

优先级：高

### 协议通信模块子分析 B：`MessageDispatcher + GatewayService`

- 分析范围：消息分发入口、中间件、线程池路由、网关接入策略
- 基线版本：`develop`
- 相关代码：
  - [include/net/message_dispatcher.h](/D:/Program/boost/include/net/message_dispatcher.h)
  - [src/game/gateway/gateway_service.cpp](/D:/Program/boost/src/game/gateway/gateway_service.cpp)
  - [include/game/gateway/gateway_service.h](/D:/Program/boost/include/game/gateway/gateway_service.h)
  - [include/net/internal_bus.h](/D:/Program/boost/include/net/internal_bus.h)
  - [src/game/gateway/gateway_server.cpp](/D:/Program/boost/src/game/gateway/gateway_server.cpp)

#### 现状补充

- `GatewayServer` 收到客户端消息后，直接把 `Session::PacketMessage` 投给 `dispatcher_.dispatch(...)`
- `GatewayService::register_handlers()` 同时向 dispatcher 注册：
  - 全局中间件：鉴权白名单、限频
  - 心跳 handler
- `InternalBus` 收到后端消息后，也直接 `dispatcher_.dispatch(nullptr, ...)`

#### 不合理点

##### 6.B.1 网关中间件默认假设 `context.session` 非空，但 dispatcher 已被设计成可接收无 session 消息

代码事实：

- [include/net/message_dispatcher.h:76](/D:/Program/boost/include/net/message_dispatcher.h:76) `dispatch()` 接受 `shared_ptr<Session>`，允许为空
- [include/net/internal_bus.h:92](/D:/Program/boost/include/net/internal_bus.h:92) 内部总线明确用 `dispatch(nullptr, ...)`
- [src/game/gateway/gateway_service.cpp:45](/D:/Program/boost/src/game/gateway/gateway_service.cpp:45) `should_allow_message()` 直接 `session_manager_.is_authenticated(context.session)`
- [src/game/gateway/gateway_service.cpp:50](/D:/Program/boost/src/game/gateway/gateway_service.cpp:50) 直接 `context.session->send(...)`
- [src/game/gateway/gateway_service.cpp:62](/D:/Program/boost/src/game/gateway/gateway_service.cpp:62) `check_rate_limit()` 直接 `context.session.get()`

不合理点：

- dispatcher 抽象层说“允许无 session 的消息”
- 网关中间件实现层却默认“所有消息都有 session”
- 这两个设计拼在一起，意味着一旦 `InternalBus` 和网关 middleware 真正共享同一 dispatcher 链，空指针问题是直接存在的

建议：

- 客户端入口和内部服务入口必须分开
- 至少要做到其中之一：
  - dispatcher 按入口类型区分 middleware 链
  - `GatewayService` middleware 明确只挂载到 client ingress dispatcher
- 当前实现不适合继续扩展为统一总线入口

优先级：高

##### 6.B.2 鉴权和限频是在业务线程池里执行，不是在接入线程做前置判定

代码事实：

- [include/net/message_dispatcher.h:125](/D:/Program/boost/include/net/message_dispatcher.h:125) middleware 是在 `post(*target_pool, ...)` 之后执行
- [src/game/gateway/gateway_server.cpp:139](/D:/Program/boost/src/game/gateway/gateway_server.cpp:139) 收到消息后立刻投递给 dispatcher
- [src/game/gateway/gateway_service.cpp:15](/D:/Program/boost/src/game/gateway/gateway_service.cpp:15) 白名单和限频是 dispatcher middleware

不合理点：

- 这意味着：
  - 非法消息先进入业务线程池排队
  - 然后才被鉴权/限频拦截
- 对低流量 demo 没问题，但对网关设计来说，这是入口前置能力放错层
- 心跳这种极轻逻辑也被放进业务线程池，而不是在接入层就快速处理

建议：

- 入口型校验应尽量前置到接入层或至少 dispatcher 投递前执行
- 可按层分为：
  - ingress filter：白名单、包级限频、协议合法性
  - business middleware：登录态、参数校验、审计
- 当前这种“全部 post 后再拦截”的模型会增加无效调度成本

优先级：高

##### 6.B.3 `GatewayService` 维护的限频状态没有明确回收点，生命周期依赖裸指针键值

代码事实：

- [include/game/gateway/gateway_service.h:35](/D:/Program/boost/include/game/gateway/gateway_service.h:35) `rate_limits_` 使用 `const net::Session*` 作为 key
- 检索结果中没有看到会话关闭后主动删除对应 `rate_limits_` 项
- [src/game/gateway/gateway_service.cpp:68](/D:/Program/boost/src/game/gateway/gateway_service.cpp:68) 每次访问时按裸指针创建/更新窗口数据

不合理点：

- 这是典型的“状态跟着地址走，但没有回收钩子”的设计
- 即使短期问题不明显，也会带来：
  - map 累积脏 entry
  - 地址复用时的潜在歧义
  - 限频状态与真实 session 生命周期脱钩

建议：

- 限频状态不要直接用裸指针做长期 key
- 更合适的方式：
  - 用 session id / connection id
  - 或在 `close_handler` 里显式清理

优先级：中

##### 6.B.4 `MessageDispatcher` 的 handler 签名过于原始，导致网关和业务层反复做“上下文解释”

代码事实：

- [include/net/message_dispatcher.h:21](/D:/Program/boost/include/net/message_dispatcher.h:21) `DispatchContext` 只有原始元数据和 `body`
- handler 只能拿到 `std::string body`
- 所以上层 service 继续自己做解析和状态判断

不合理点：

- dispatcher 虽然叫“消息分发器”，但没有把“消息类型”提升到一等抽象
- 结果是：
  - 协议层不知道 body 的真实类型
  - 业务层重复做解析
  - middleware 也只能基于 message_id 和原始 body 做粗粒度决策

建议：

- 后续如果继续维护 1.x，而不是直接跳 2.0，建议考虑把 dispatcher 往“typed handler”方向收一层
- 即使不全面模板化，也可以先把：
  - decode
  - handler 选择
  - typed request 交付
  形成更清晰的责任链

优先级：中

##### 6.B.5 心跳处理走完整 dispatcher 链，接入层和业务层边界仍不干净

代码事实：

- [src/game/gateway/gateway_service.cpp:22](/D:/Program/boost/src/game/gateway/gateway_service.cpp:22) 心跳是普通 handler
- [src/game/gateway/gateway_service.cpp:24](/D:/Program/boost/src/game/gateway/gateway_service.cpp:24) 心跳响应通过 `context.session->send(...)` 回包
- [include/net/message_dispatcher.h:125](/D:/Program/boost/include/net/message_dispatcher.h:125) 仍需要进入线程池异步处理

不合理点：

- 心跳本质上更接近连接保活协议，而不是业务消息
- 当前把它完全放到分发器里，会让连接保活机制依赖业务执行通道

建议：

- 心跳、协议级 pong、甚至部分包级错误响应，可以考虑在 `Session` 或 ingress 层就快速处理
- 这样能减少业务线程无意义调度，也让保活机制更稳定

优先级：中

### 协议通信模块子分析 C：`message_serializer + message_types + 各业务 service`

- 分析范围：结构化消息体定义是否真正成为唯一协议主路径
- 基线版本：`develop`
- 相关代码：
  - [include/net/message_types.h](/D:/Program/boost/include/net/message_types.h)
  - [include/net/message_serializer.h](/D:/Program/boost/include/net/message_serializer.h)
  - [src/game/login/login_service.cpp](/D:/Program/boost/src/game/login/login_service.cpp)
  - [src/game/room/room_service.cpp](/D:/Program/boost/src/game/room/room_service.cpp)
  - [src/game/battle/battle_service.cpp](/D:/Program/boost/src/game/battle/battle_service.cpp)
  - [tests/integration/gateway_integration_test.cpp](/D:/Program/boost/tests/integration/gateway_integration_test.cpp)

#### 现状补充

- `message_types.h` 已定义 Echo/Login/Room/Battle 等结构化消息类型
- `message_serializer.h` 已提供对应的二进制序列化/反序列化函数
- 但从调用关系看，这套结构化消息几乎没有进入业务服务主路径
- 当前业务服务依然主要基于：
  - 手写 `string_view` 拆分
  - `:` / `|` 约定字符串拼接
  - 集成测试直接断言字符串响应

#### 不合理点

##### 6.C.1 `message_types` 和真实业务协议并不一致，当前更像“并行草案”而不是实际契约

代码事实：

- [include/net/message_types.h:43](/D:/Program/boost/include/net/message_types.h:43) `LoginResponse` 只有 `user_id`、`display_name`、`ok`
- 但真实登录响应在 [src/game/login/login_service.cpp:95](/D:/Program/boost/src/game/login/login_service.cpp:95) 可能是：
  - `login_ok:user:display_name`
  - `login_ok:user:display_name:room=...`
- [include/net/message_types.h:30](/D:/Program/boost/include/net/message_types.h:30) `SessionResumedPush` 只有 `room_id`
- 但真实推送在 [src/game/login/login_service.cpp:99](/D:/Program/boost/src/game/login/login_service.cpp:99) 还包含 `battle=0/1`
- [include/net/message_types.h:65](/D:/Program/boost/include/net/message_types.h:65) `RoomJoinResponse` 只有 `room_id`
- 但真实返回在 [src/game/room/room_service.cpp:77](/D:/Program/boost/src/game/room/room_service.cpp:77) 带 `player_count`

不合理点：

- 结构化消息定义与真实线上消息体已经发生分叉
- 这说明 `message_types.h` 不是当前业务事实源
- 一旦后续有人以 `message_types + message_serializer` 为准开发客户端或后端服务，会直接产生协议错配

建议：

- 先明确谁是协议事实源
- 如果准备保留结构化消息体系：
  - 先把 `message_types` 补齐到与当前真实业务返回完全一致
  - 再考虑替换现有字符串 body
- 如果暂不启用结构化协议，就不应把这套类型宣称为现状能力

优先级：高

##### 6.C.2 结构化序列化缺少“消息号 <-> 类型”绑定层，导致它无法自然接入 dispatcher

代码事实：

- `message_serializer.h` 只提供独立的 `serialize(T)` / `deserialize_xxx()` 函数
- `MessageDispatcher` 仍然只按 `message_id + raw body` 分发
- 当前没有看到类似：
  - message id 到消息类型的注册表
  - typed handler adapter
  - decode failure 到统一错误响应的桥接层

不合理点：

- 这意味着即使有了结构化 serializer，也无法自然接到当前 dispatcher 主路径
- 每个 service 仍然需要自己判断消息、自己解析 body、自己决定错误响应
- 所以 `message_serializer` 无法成为默认路径，只能停留在工具库层

建议：

- 如果要让结构化协议真正落地，需要补中间桥接层：
  - `message_id -> request/response type`
  - `raw body -> typed message`
  - `typed handler -> raw response`
- 否则结构化 serializer 会一直是“存在但不参与主流程”的死抽象

优先级：高

##### 6.C.3 当前测试基线绑定字符串协议，反过来阻止结构化消息体系落地

代码事实：

- [tests/integration/gateway_integration_test.cpp:198](/D:/Program/boost/tests/integration/gateway_integration_test.cpp:198) 断言 `login_ok:...`
- [tests/integration/gateway_integration_test.cpp:204](/D:/Program/boost/tests/integration/gateway_integration_test.cpp:204) 断言 `room_created:...`
- [tests/integration/gateway_integration_test.cpp:247](/D:/Program/boost/tests/integration/gateway_integration_test.cpp:247) 断言 `battle_started:...`
- [tests/integration/gateway_integration_test.cpp:305](/D:/Program/boost/tests/integration/gateway_integration_test.cpp:305) 断言 `session_kicked:...`

不合理点：

- 集成测试已经把字符串 body 格式固化成真实契约
- 所以任何结构化消息接入都会先大面积改测试，而不是自然兼容切换
- 这会让团队倾向于继续写字符串协议，因为测试成本更低

建议：

- 后续如果准备切换结构化消息，测试层也要分层：
  - 协议层测试：断言序列化/反序列化语义
  - 业务层测试：断言业务字段，不直接依赖字符串拼接格式
- 维护期内至少应补一份“结构化协议尚未启用”的说明，避免误导

优先级：中

##### 6.C.4 `message_serializer` 的覆盖面和业务现实不一致，抽象边界不完整

代码事实：

- `message_types.h` 定义了部分消息，但没有完整覆盖当前实际响应语义
- 例如当前项目还有：
  - `kAdmin*` 管理类消息
  - 部分错误响应仍直接发字符串
  - 恢复态、踢线态、房间广播、战斗广播都带额外上下文
- [include/net/message_types.h](/D:/Program/boost/include/net/message_types.h) 没有形成完整协议族

不合理点：

- 结构化协议只覆盖一部分消息，会导致项目长期处于“半结构化”状态
- 半结构化通常比纯字符串更难维护，因为两套习惯长期共存

建议：

- 要么把结构化协议定位为下一阶段方案，不混入当前主链
- 要么按消息族完整推进，不要只覆盖局部 happy path

优先级：中

### 协议通信模块子分析 D：`internal_bus + service_router + 多进程入口`

- 分析范围：当前多进程/拆服相关抽象是否已经形成真实架构闭环
- 基线版本：`develop`
- 相关代码：
  - [include/net/internal_bus.h](/D:/Program/boost/include/net/internal_bus.h)
  - [include/net/service_router.h](/D:/Program/boost/include/net/service_router.h)
  - [include/net/service_registry.h](/D:/Program/boost/include/net/service_registry.h)
  - [include/game/gateway/backend_router.h](/D:/Program/boost/include/game/gateway/backend_router.h)
  - [examples/login/login_server_main.cpp](/D:/Program/boost/examples/login/login_server_main.cpp)
  - [examples/room/room_server_main.cpp](/D:/Program/boost/examples/room/room_server_main.cpp)
  - [examples/battle/battle_server_main.cpp](/D:/Program/boost/examples/battle/battle_server_main.cpp)

#### 现状补充

- 当前仓库确实有 `login_server` / `room_server` / `battle_server` 三个独立入口
- 但这三个入口本质上仍是各自启动一套 `GatewayServer + SessionManager + Dispatcher + 对应业务服务`
- `internal_bus`、`service_router`、`service_registry`、`backend_router` 主要停留在头文件抽象层
- 代码检索没有看到它们在真实主流程中被完整接线

#### 不合理点

##### 6.D.1 当前“多进程架构”更接近多份单体实例，而不是网关-后端拆分后的真实服务协作

代码事实：

- [examples/login/login_server_main.cpp:61](/D:/Program/boost/examples/login/login_server_main.cpp:61) 直接启动 `GatewayServer`
- [examples/room/room_server_main.cpp:49](/D:/Program/boost/examples/room/room_server_main.cpp:49) 也直接启动 `GatewayServer`
- [examples/battle/battle_server_main.cpp:49](/D:/Program/boost/examples/battle/battle_server_main.cpp:49) 同样启动 `GatewayServer`

不合理点：

- 这不是“Gateway 接入 + Backend 逻辑服务”的拆分，而是“每个服务自己都带接入层”
- 所以虽然可执行文件被拆开了，但协议边界并没有真正分离：
  - 客户端协议
  - 内部服务协议
  仍然没有分层

建议：

- 如果要把这套入口继续称为多进程架构，需要明确：
  - 哪个进程对外接客户端
  - 哪个进程只接内部服务消息
- 否则更准确的说法应是“按模块拆出的独立 demo 入口”，不是完成态拆服架构

优先级：高

##### 6.D.2 `ServiceRouter` 只是在本地进程里转发 `DispatchContext`，还不是跨进程消息路由层

代码事实：

- [include/net/service_router.h:26](/D:/Program/boost/include/net/service_router.h:26) `ForwardHandler` 直接吃 `DispatchContext`
- [include/net/service_router.h:35](/D:/Program/boost/include/net/service_router.h:35) `route()` 只是本地函数调用

不合理点：

- `DispatchContext` 带有 `shared_ptr<Session>`，这是明显的进程内对象语义
- 这种抽象天然不适合作为跨进程总线的长期协议边界
- 如果未来真要拆服，路由层应处理的是“可序列化内部消息”，而不是本地会话上下文对象

建议：

- 把本地路由抽象和跨进程路由抽象分开
- 内部总线层应基于独立的内部消息协议，而不是直接复用外部接入的 `DispatchContext`

优先级：高

##### 6.D.3 `InternalBus` 直接复用外部 packet codec 和 dispatcher，客户端协议与内部协议没有隔离

代码事实：

- [include/net/internal_bus.h:61](/D:/Program/boost/include/net/internal_bus.h:61) 出站直接 `packet::encode(...)`
- [include/net/internal_bus.h:91](/D:/Program/boost/include/net/internal_bus.h:91) 入站直接 `packet::decode_payload(...)`
- [include/net/internal_bus.h:92](/D:/Program/boost/include/net/internal_bus.h:92) 解码后直接喂给同一个 `dispatcher_`

不合理点：

- 这意味着内部服务通信和客户端通信默认共用同一协议格式、同一 message_id 空间、同一 dispatch 语义
- 但两者的需求通常不同：
  - 客户端协议更关注兼容、压缩、限频、权限
  - 内部协议更关注服务标识、来源、路由、超时、重试、幂等

建议：

- 内部总线应有独立 envelope，至少补齐：
  - source service
  - target service
  - correlation id
  - timeout / status
  - version
- 不建议继续把客户端 packet 结构直接当内部 RPC 包结构

优先级：高

##### 6.D.4 `BackendRouter` 目前只有号段映射和空 handler，占位意义大于实战意义

代码事实：

- [include/game/gateway/backend_router.h:23](/D:/Program/boost/include/game/gateway/backend_router.h:23) 注册的是空 lambda
- [include/game/gateway/backend_router.h:32](/D:/Program/boost/include/game/gateway/backend_router.h:32) 只按消息号范围返回 `ServiceId`
- 检索中没有看到它进入真实收包转发主链

不合理点：

- 当前它更多只是“说明未来可按号段路由”的草图
- 但号段路由本身并不足以支撑真实拆服：
  - 没有服务发现接线
  - 没有转发失败策略
  - 没有请求响应映射
  - 没有 session/user 归属同步

建议：

- 在维护文档里不要把它描述成已完成能力
- 如果后续真要推进拆服，`BackendRouter` 应升级为：
  - 可用实例选择
  - 超时/重试
  - 会话与用户路由信息维护
  - 响应回传绑定

优先级：中

##### 6.D.5 `ServiceRegistry` 当前只是内存表，不足以支撑真实服务发现语义

代码事实：

- [include/net/service_registry.h:26](/D:/Program/boost/include/net/service_registry.h:26) 只是往 `vector` 里注册实例
- [include/net/service_registry.h:31](/D:/Program/boost/include/net/service_registry.h:31) `mark_healthy()` 只按 host/port 标记
- [include/net/service_registry.h:41](/D:/Program/boost/include/net/service_registry.h:41) `healthy_instances()` 只是返回过滤结果

不合理点：

- 没有：
  - TTL/过期
  - 主动摘除
  - 负载选择
  - 订阅变更
  - 与 `/health` 的真实联动
- 所以它更像配置对象，而不是服务发现组件

建议：

- 维护期内可继续保留轻量抽象，但应把其定位写清楚：
  - “实验性本地注册表”
  - 而不是“已具备服务发现”

优先级：中

### 协议通信模块子分析 E：`PushService + 各业务 service`

- 分析范围：响应、错误、推送、广播的协议封装是否真正统一
- 基线版本：`develop`
- 相关代码：
  - [include/game/gateway/push_service.h](/D:/Program/boost/include/game/gateway/push_service.h)
  - [src/game/gateway/push_service.cpp](/D:/Program/boost/src/game/gateway/push_service.cpp)
  - [src/game/login/login_service.cpp](/D:/Program/boost/src/game/login/login_service.cpp)
  - [src/game/room/room_service.cpp](/D:/Program/boost/src/game/room/room_service.cpp)
  - [src/game/battle/battle_service.cpp](/D:/Program/boost/src/game/battle/battle_service.cpp)

#### 现状补充

- `PushService` 统一封装了三类发送动作：
  - `send_ok()`
  - `send_error()`
  - `send_push()`
- 但它对 body 本身完全不做结构约束，只是把 `std::string` 透传给 `Session::send()`
- 各业务 service 继续自己决定：
  - body 格式
  - push 内容
  - 广播内容
  - 是否排除自己

#### 不合理点

##### 6.E.1 `PushService` 只统一了发送动作，没有统一响应协议

代码事实：

- [src/game/gateway/push_service.cpp:5](/D:/Program/boost/src/game/gateway/push_service.cpp:5) `send_ok()` 只负责写 `error_code = kOk`
- [src/game/gateway/push_service.cpp:15](/D:/Program/boost/src/game/gateway/push_service.cpp:15) `send_error()` 只负责把错误码和 body 发出去
- [src/game/gateway/push_service.cpp:29](/D:/Program/boost/src/game/gateway/push_service.cpp:29) `send_push()` 只固定 `request_id = 0`

不合理点：

- 这意味着“成功响应”“错误响应”“推送响应”虽然走同一个类，但协议内容依旧是分散定义的
- 当前 `PushService` 更像 `Session::send()` 的语义别名，而不是“统一响应协议层”

建议：

- 如果保留 `PushService` 这个名字，它应该承担更完整的协议收口责任：
  - 统一 success/error/push 的 body 结构
  - 统一 trace/request 语义
  - 统一可观测字段
- 否则这个类的抽象价值偏弱，容易给人“协议已经统一”的错觉

优先级：中

##### 6.E.2 各业务 service 仍在定义自己的字符串协议，`PushService` 没能阻止协议碎片化

代码事实：

- 登录：
  - [src/game/login/login_service.cpp:95](/D:/Program/boost/src/game/login/login_service.cpp:95) `login_ok:...`
  - [src/game/login/login_service.cpp:99](/D:/Program/boost/src/game/login/login_service.cpp:99) `session_resumed:...`
- 房间：
  - [src/game/room/room_service.cpp:47](/D:/Program/boost/src/game/room/room_service.cpp:47) `room_created:...`
  - [src/game/room/room_service.cpp:83](/D:/Program/boost/src/game/room/room_service.cpp:83) `room_joined:...`
  - [src/game/room/room_service.cpp:121](/D:/Program/boost/src/game/room/room_service.cpp:121) `room_left:...`
  - [src/game/room/room_service.cpp:198](/D:/Program/boost/src/game/room/room_service.cpp:198) `room_state:...`
- 战斗：
  - [src/game/battle/battle_service.cpp:69](/D:/Program/boost/src/game/battle/battle_service.cpp:69) `battle_started:...`
  - [src/game/battle/battle_service.cpp:72](/D:/Program/boost/src/game/battle/battle_service.cpp:72) `battle_state:started:...`
  - [src/game/battle/battle_service.cpp:112](/D:/Program/boost/src/game/battle/battle_service.cpp:112) `battle_input_accepted:...`
  - [src/game/battle/battle_service.cpp:115](/D:/Program/boost/src/game/battle/battle_service.cpp:115) `battle_input:...`

不合理点：

- `PushService` 的存在没有把协议表达集中起来，反而让业务层继续自由拼字符串
- 这会导致协议演进只能靠“人记住每个前缀和分隔符”

建议：

- 如果后续不打算全面接 `message_serializer`，也至少应把 body 构造集中到独立协议 helper，而不是分散在各 service 方法里
- 维护期优先目标不是“立刻二进制化”，而是先把协议表达从业务逻辑里拆出去

优先级：高

##### 6.E.3 广播协议仍由业务 service 自己拼接，`PushService::broadcast()` 没有承担消息构造职责

代码事实：

- [src/game/gateway/push_service.cpp:38](/D:/Program/boost/src/game/gateway/push_service.cpp:38) `broadcast()` 只循环调用 `send_push()`
- [src/game/room/room_service.cpp:194](/D:/Program/boost/src/game/room/room_service.cpp:194) 房间状态 body 在 `build_room_state_body()` 里拼接
- [src/game/battle/battle_service.cpp:140](/D:/Program/boost/src/game/battle/battle_service.cpp:140) 战斗广播 body 直接由业务层传入

不合理点：

- 广播路径只统一了“怎么发”，没有统一“发什么”
- 对协议维护来说，最难变的是“发什么”，而不是“怎么循环发”

建议：

- 如果广播是核心能力，建议至少把广播消息体构造和广播发送分层
- 否则后续很难做：
  - 广播协议版本化
  - 广播去重/批量优化
  - 广播观测和审计

优先级：中

##### 6.E.4 `send_push()` 直接把 `request_id` 固定为 0，推送语义缺少更明确的协议约束

代码事实：

- [src/game/gateway/push_service.cpp:32](/D:/Program/boost/src/game/gateway/push_service.cpp:32) push 的 `request_id` 固定为 `0`

不合理点：

- 这当然可以工作，但目前没有看到更明确的协议层约束，例如：
  - `request_id = 0` 是否永远代表服务端主动推送
  - 客户端是否可以依赖这个约定
  - 内部服务消息是否也采用同样约定

建议：

- 把 push 的 request 语义写进协议约定
- 如果未来有内部总线或服务端回调链路，最好不要只靠 `request_id == 0` 进行弱区分

优先级：中

### 协议通信模块子分析 F：`GatewayServer + SessionManager + RoomManager + BattleManager`

- 分析范围：会话态、房间态、战斗态之间的边界、生命周期与归属关系
- 基线版本：`develop`
- 相关代码：
  - [src/game/gateway/gateway_server.cpp](/D:/Program/boost/src/game/gateway/gateway_server.cpp)
  - [include/game/gateway/session_manager.h](/D:/Program/boost/include/game/gateway/session_manager.h)
  - [src/game/gateway/session_manager.cpp](/D:/Program/boost/src/game/gateway/session_manager.cpp)
  - [include/game/room/room_manager.h](/D:/Program/boost/include/game/room/room_manager.h)
  - [src/game/room/room_manager.cpp](/D:/Program/boost/src/game/room/room_manager.cpp)
  - [include/game/battle/battle_manager.h](/D:/Program/boost/include/game/battle/battle_manager.h)
  - [src/game/battle/battle_manager.cpp](/D:/Program/boost/src/game/battle/battle_manager.cpp)

#### 现状补充

- `SessionManager` 管会话和登录态
- `RoomManager` 管房间、成员、房主、ready、room -> session 关系
- `BattleManager` 管 battle context、输入序列、spectator、frame
- `GatewayServer` 在会话关闭时同时编排这三者的清理顺序

#### 不合理点

##### 6.F.1 生命周期编排散落在 `GatewayServer`，manager 之间没有清晰的拥有关系

代码事实：

- [src/game/gateway/gateway_server.cpp:152](/D:/Program/boost/src/game/gateway/gateway_server.cpp:152) 关闭时先 `room_manager_.remove_session(session_ptr)`
- [src/game/gateway/gateway_server.cpp:154](/D:/Program/boost/src/game/gateway/gateway_server.cpp:154) 如果房间没人了再 `battle_manager_.remove_room(*room_id)`
- [src/game/gateway/gateway_server.cpp:157](/D:/Program/boost/src/game/gateway/gateway_server.cpp:157) 最后 `session_manager_.remove_session(session_ptr)`

不合理点：

- 资源回收顺序是 `GatewayServer` 知道的，而不是状态对象自己知道的
- 这意味着 `GatewayServer` 已经不是纯接入层，而是在承担跨领域生命周期编排
- 以后如果关闭场景变多，例如：
  - 踢线
  - 停服迁移
  - 后台封禁
  - 内部路由切换
  就很容易出现多处重复编排逻辑

建议：

- 状态清理最好收敛成明确的会话下线流程或 domain coordinator
- 接入层不应成为房间/战斗资源清理知识的主要持有者

优先级：高

##### 6.F.2 `RoomManager` 和 `BattleManager` 以同一个 `room_id` 为耦合键，但没有统一的领域边界

代码事实：

- [src/game/battle/battle_manager.cpp:24](/D:/Program/boost/src/game/battle/battle_manager.cpp:24) `active_battles_` 以 `room_id` 为 key
- [src/game/room/room_manager.cpp:209](/D:/Program/boost/src/game/room/room_manager.cpp:209) `RoomManager` 自己也维护 `battle_started`
- [src/game/battle/battle_service.cpp:63](/D:/Program/boost/src/game/battle/battle_service.cpp:63) 战斗开始后还要反向调用 `room_manager_.mark_battle_started(...)`

不合理点：

- “是否在战斗中”这个状态同时存在于两个 manager：
  - `RoomManager::battle_started`
  - `BattleManager::active_battles_.contains(room_id)`
- 双写状态天然有一致性风险

建议：

- 维护期内至少明确一个单一事实源：
  - 要么 `BattleManager` 是 battle 状态唯一事实源
  - 要么 `RoomManager` 只保留派生缓存，不做独立状态判断
- 当前双写模式在扩展战斗结束、断线恢复、观战切换时风险会放大

优先级：高

##### 6.F.3 `SessionManager`、`RoomManager` 都大量使用裸指针作为 key，跨 manager 关联依赖对象地址稳定性

代码事实：

- [include/game/gateway/session_manager.h:43](/D:/Program/boost/include/game/gateway/session_manager.h:43) `SessionKey = const net::Session*`
- [include/game/room/room_manager.h:100](/D:/Program/boost/include/game/room/room_manager.h:100) 同样使用 `SessionKey = const net::Session*`
- `transfer_session()`、`session_rooms_`、`user_index_` 都依赖这个 key

不合理点：

- 当前多个状态管理器之间的关系不是通过稳定 ID，而是通过内存地址绑定
- 这在单进程 demo 阶段可以工作，但对以下场景不友好：
  - 会话迁移
  - 内部路由
  - 持久化/恢复
  - 多进程拆服

建议：

- 项目已经有 `SessionManager::session_id`，但目前没有向外形成稳定主键抽象
- 后续应逐步从“裸指针关联”迁移到“稳定会话 ID / 用户 ID / 房间 ID”的明确边界

优先级：高

##### 6.F.4 `RoomManager` 负责会话迁移，说明房间层已经感知了连接替换细节

代码事实：

- [src/game/room/room_manager.cpp:117](/D:/Program/boost/src/game/room/room_manager.cpp:117) `transfer_session(from_session, to_session)`
- [src/game/login/login_service.cpp:85](/D:/Program/boost/src/game/login/login_service.cpp:85) 重复登录时直接调用 `room_manager_.transfer_session(...)`

不合理点：

- 重连/顶号本质上是会话生命周期问题
- 但房间层现在需要知道如何把旧连接替换成新连接，这使得房间状态直接依赖连接对象形态

建议：

- 如果以后要支持真正的恢复、迁移、拆服，房间层更适合绑定“玩家身份”而不是“连接对象”
- 当前的 `transfer_session()` 可以工作，但它暴露了状态建模仍是“session-first”而不是“player-first”

优先级：中

##### 6.F.5 `BattleManager` 当前更像输入日志容器，战斗生命周期语义还比较薄

代码事实：

- [src/game/battle/battle_manager.cpp:48](/D:/Program/boost/src/game/battle/battle_manager.cpp:48) 输入只是按 `room_id` 追加进 vector
- [src/game/battle/battle_manager.cpp:68](/D:/Program/boost/src/game/battle/battle_manager.cpp:68) `advance_frame()` 每次线性扫描全部输入找本帧数据
- [src/game/battle/battle_manager.cpp:92](/D:/Program/boost/src/game/battle/battle_manager.cpp:92) `end_battle()` 的结算基本是按输入数量计分

不合理点：

- 这不是 bug，而是说明 battle 层目前仍偏 demo 实现
- 但它已经被房间层和会话恢复逻辑当成真实领域状态依赖
- 一旦继续往里面挂更多功能，管理器职责会迅速膨胀

建议：

- 在维护文档里要把 `BattleManager` 当前定位说清楚：
  - 它现在是轻量 battle context 管理器
  - 不是完整战斗领域模型
- 否则后续容易在这个类里继续堆路由、广播、结算、回放、spectator 等全部逻辑

优先级：中

### 协议通信模块子分析 G：`GatewayMetrics + MetricsExporter + audit/logging`

- 分析范围：观测口径是否准确反映当前协议/状态链路，日志与审计是否能稳定支撑维护
- 基线版本：`develop`
- 相关代码：
  - [include/game/gateway/gateway_metrics.h](/D:/Program/boost/include/game/gateway/gateway_metrics.h)
  - [src/game/gateway/gateway_metrics.cpp](/D:/Program/boost/src/game/gateway/gateway_metrics.cpp)
  - [include/game/gateway/gateway_metrics_exporter.h](/D:/Program/boost/include/game/gateway/gateway_metrics_exporter.h)
  - [src/game/gateway/gateway_metrics_exporter.cpp](/D:/Program/boost/src/game/gateway/gateway_metrics_exporter.cpp)
  - [include/app/audit_log.h](/D:/Program/boost/include/app/audit_log.h)
  - [include/app/logging.h](/D:/Program/boost/include/app/logging.h)
  - [src/app/logging.cpp](/D:/Program/boost/src/app/logging.cpp)
  - [src/game/gateway/gateway_server.cpp](/D:/Program/boost/src/game/gateway/gateway_server.cpp)

#### 不合理点

##### 6.G.1 当前包量/字节量指标统计的是“业务视角 body”，不是“线上真实传输量”

代码事实：

- [src/game/gateway/gateway_server.cpp:129](/D:/Program/boost/src/game/gateway/gateway_server.cpp:129) `receive_observer_` 把 `message.body.size()` 记为收到字节数
- [src/game/gateway/gateway_server.cpp:134](/D:/Program/boost/src/game/gateway/gateway_server.cpp:134) `send_observer_` 把 `message.body.size()` 记为发送字节数
- [src/net/session.cpp:201](/D:/Program/boost/src/net/session.cpp:201) 收包前若带压缩标记会先解压
- [src/net/session.cpp:261](/D:/Program/boost/src/net/session.cpp:261) 发包前可能先压缩
- 当前还存在批量发送、未来分片等增强链路

不合理点：

- 现在的指标更像“应用层消息体大小”，不是 socket 线上真实收发字节
- 一旦启用压缩、批量发包、未来接分片，这两个口径会进一步偏离
- 结果是观测上看不清：
  - 网络真实带宽
  - 协议头/压缩/分片带来的额外成本
  - 业务消息量和线路流量之间的差值

建议：

- 至少把指标语义显式拆成两类：
  - `application_message_bytes`
  - `wire_bytes`
- 否则 Prometheus 和日志中的字节量很容易被误读成“实际网络吞吐”

优先级：高

##### 6.G.2 观测口径建立在当前关闭路径和状态清理正确的前提上，但这个前提并不稳

代码事实：

- [src/game/gateway/gateway_metrics_exporter.cpp:41](/D:/Program/boost/src/game/gateway/gateway_metrics_exporter.cpp:41) 活跃会话来自 `session_manager.snapshot()`
- [src/game/gateway/gateway_metrics_exporter.cpp:52](/D:/Program/boost/src/game/gateway/gateway_metrics_exporter.cpp:52) 活跃房间来自 `room_manager.room_count()`
- [src/game/gateway/gateway_metrics_exporter.cpp:53](/D:/Program/boost/src/game/gateway/gateway_metrics_exporter.cpp:53) 活跃战斗来自 `battle_manager.active_battle_count()`
- 但前文已经确认 `Session::stop()` 与 `handle_close()` 不是统一关闭路径

不合理点：

- 当前 gauge 的可信度依赖状态清理链正确执行
- 一旦主动停服或异常路径绕过统一清理，这些 gauge 可能与真实生命周期偏离
- 这说明观测层现在是在“读取多个 manager 的本地状态”，而不是建立在更稳定的生命周期事件流上

建议：

- 在修复关闭路径前，不应把这些 gauge 视为绝对可信的运维依据
- 后续如果继续增强观测，建议增加：
  - 状态不一致告警
  - 生命周期事件计数与 gauge 的交叉校验

优先级：中

##### 6.G.3 审计日志自称 JSON 行格式，但 `details` 没有转义，严格来说不是稳定 JSON

代码事实：

- [include/app/audit_log.h:41](/D:/Program/boost/include/app/audit_log.h:41) 手工拼 JSON 字符串
- [include/app/audit_log.h:42](/D:/Program/boost/include/app/audit_log.h:42) 直接把 `details` 拼进 `"details":"..."` 中
- 代码里 `details` 来源可能包含用户输入或特殊字符，例如 user_id、IP、自由文本等

不合理点：

- 只要 `details` 中包含引号、反斜杠或换行，日志行就不再是合法 JSON
- 这会影响后续：
  - 审计日志解析
  - SIEM/日志平台接入
  - 脚本检索稳定性

建议：

- 审计日志应使用真正的 JSON 库或最少做字符串转义
- 当前实现更像“看起来像 JSON”，不应把它当作稳定结构化日志格式

优先级：高

##### 6.G.4 全局 logger 只允许初始化一次，不同入口程序的日志配置复用语义比较隐蔽

代码事实：

- [src/app/logging.cpp:16](/D:/Program/boost/src/app/logging.cpp:16) 使用 `std::once_flag`
- [src/app/logging.cpp:29](/D:/Program/boost/src/app/logging.cpp:29) `init(app_name, log_directory)` 只有第一次生效

不合理点：

- 对单一可执行文件没问题，但对测试或复杂 demo 组合场景，这个语义是隐式的
- 后续如果某些组件尝试用不同名字初始化日志，会被静默忽略

建议：

- 维护期内至少把“logger 只初始化一次”的行为写进注释或文档
- 如果未来希望支持多组件日志名或可重配，应避免把 `init()` 设计成静默一次性全局锁死

优先级：低

### 协议通信模块子分析 H：`config + runtime-playbook + examples`

- 分析范围：配置、运行文档、示例入口是否准确表达当前实现成熟度
- 基线版本：`develop`
- 相关代码：
  - [include/app/config.h](/D:/Program/boost/include/app/config.h)
  - [src/app/config.cpp](/D:/Program/boost/src/app/config.cpp)
  - [include/app/config_watcher.h](/D:/Program/boost/include/app/config_watcher.h)
  - [config/gateway.json](/D:/Program/boost/config/gateway.json)
  - [../runbooks/runtime-playbook.md](/D:/Program/boost/../runbooks/runtime-playbook.md)
  - [examples/echo/server_main.cpp](/D:/Program/boost/examples/echo/server_main.cpp)
  - [examples/login_demo/login_demo_main.cpp](/D:/Program/boost/examples/login_demo/login_demo_main.cpp)
  - [examples/room_demo/room_demo_main.cpp](/D:/Program/boost/examples/room_demo/room_demo_main.cpp)
  - [examples/battle_demo/battle_demo_main.cpp](/D:/Program/boost/examples/battle_demo/battle_demo_main.cpp)
  - [examples/admin_demo/admin_demo_main.cpp](/D:/Program/boost/examples/admin_demo/admin_demo_main.cpp)

#### 不合理点

##### 6.H.1 配置结构里已经定义了一批能力开关，但实际接线并不完整

代码事实：

- [include/app/config.h:41](/D:/Program/boost/include/app/config.h:41) 有 `max_guests`
- [include/app/config.h:47](/D:/Program/boost/include/app/config.h:47) 有 `TlsConfig tls`
- [src/app/config.cpp:223](/D:/Program/boost/src/app/config.cpp:223) 会解析 `tls.*`
- 但检索结果中没看到 `max_guests` 的真实使用
- `echo_server` 等入口仍然实例化的是普通 [src/game/gateway/gateway_server.cpp](/D:/Program/boost/src/game/gateway/gateway_server.cpp) 对应链路，而不是 TLS 接入器

不合理点：

- 配置层已经暴露了某些能力，但运行时主链路并未一致接线
- 这会造成“配置看起来支持，实际上未生效”的维护风险

建议：

- 对每个配置项标明状态：
  - 已接线
  - 部分接线
  - 预留未接线
- 尤其 `TLS`、`guest` 这类容易被误认为已支持的能力，必须标清

优先级：高

##### 6.H.2 `load_gateway_config()` 采用宽松默认回退，运维层面容易掩盖配置错误

代码事实：

- [src/app/config.cpp:170](/D:/Program/boost/src/app/config.cpp:170) 配置加载失败时直接返回默认配置
- [src/app/config.cpp:171](/D:/Program/boost/src/app/config.cpp:171) 只打一条 warning
- 各字段解析失败时大多也是“缺省就沿用默认值”

不合理点：

- 这对 demo 很方便，但对维护版本不够严谨
- 常见风险：
  - 拼错配置 key
  - 写了无效数值
  - 路径失效
  最终程序还能启动，但不是按预期配置启动

建议：

- 至少区分：
  - demo 宽松模式
  - 生产严格模式
- 对关键配置项应支持失败即退出，避免“带错配置运行”

优先级：中

##### 6.H.3 `ConfigWatcher` 的热加载能力目前只改少数字段，容易被误读为“全量热加载”

代码事实：

- [include/app/config_watcher.h:48](/D:/Program/boost/include/app/config_watcher.h:48) 变更后重新加载整个 `GatewayAppConfig`
- 但 `echo_server` 的 reload 回调在 [examples/echo/server_main.cpp:159](/D:/Program/boost/examples/echo/server_main.cpp:159) 只更新连接限制
- `login_demo` 和 `admin_demo` 也只做很有限的字段处理

不合理点：

- 现在“支持配置热加载”更准确的说法是“支持监听配置文件变化，并手工应用部分字段”
- 如果文档直接写“配置热加载”，读者容易理解为 session、auth、metrics、TLS 等都会自动生效

建议：

- 文档要改成“部分配置热应用”
- 最好列清楚当前真正可热更新的字段范围

优先级：中

##### 6.H.4 `examples/` 当前承担了过多“产品能力展示”，但很多示例其实是全栈拼装而不是单点示例

代码事实：

- [examples/echo/server_main.cpp:117](/D:/Program/boost/examples/echo/server_main.cpp:117) 同时组装网关、登录、房间、战斗、持久化、热加载、优雅关闭
- `login_demo`、`room_demo`、`battle_demo`、`admin_demo` 也都不是纯局部演示，而是再拼一套完整 server

不合理点：

- 示例本应帮助定位单一能力，但当前很多 demo 实际上是“另一份定制版集成入口”
- 这会导致：
  - 重复组装代码增多
  - 某些能力的真实接线方式分散在多个 demo 中
  - 文档和 README 更容易把 demo 能力当成框架稳定能力

建议：

- 区分两类示例：
  - `minimal examples`：单一能力
  - `showcase apps`：完整拼装展示
- 当前命名和文档应明确它们不是同一层含义

优先级：中

##### 6.H.5 `runtime-playbook` 与当前实现成熟度表达不匹配，容易把预留能力写成现状能力

代码事实：

- 文档中把 `net::msg` 结构化序列化、`ServiceRouter`、多种工程能力并列写成“已完成能力”
- 但前面的代码分析已经确认：
  - 结构化消息没有真正进入主链
  - `ServiceRouter` / `InternalBus` 还停留在预埋抽象层
  - 多进程也更像多份独立入口

不合理点：

- 当前运行文档有把“已存在抽象”直接表述成“已可依赖能力”的倾向
- 这会让维护工作一开始就建立在过高预期上

建议：

- 运行文档中的能力项应按成熟度标注，例如：
  - 已上线主链
  - 实验性
  - 预留抽象
- 这比只列“有这个类/有这个文件”更有维护价值

优先级：高

---

## 7. 协议/网关链路维护整改路线图

本节不是新增问题，而是对前述问题按“依赖关系 + 落地顺序”做收敛，便于作为 `v1.x` 维护阶段的实际执行路线。

### 7.1 整改目标

目标不是一次性重写架构，而是先把当前协议/网关链路从“可跑但边界混杂”收敛到“语义稳定、文档一致、可持续维护”的状态。

维护阶段优先解决四类问题：

- 协议事实源不统一
- 生命周期收口不统一
- 入口链路职责混杂
- 文档/配置/示例对能力成熟度表达失真

### 7.2 推荐执行顺序

#### 第一阶段：先统一事实源，不先动大结构

目标：

- 先让“代码事实、协议事实、文档事实”一致

建议动作：

- 明确当前主协议事实源
  - 固定包头格式
  - 固定 `flags` 位定义
  - 固定 push / request / error 的语义
- 明确当前消息体主路径
  - 选“结构化消息体”为主
  - 或选“字符串 body”为现阶段主路径
  - 但不能继续两套并行、都算现状
- 修正文档成熟度表述
  - `runtime-playbook`
  - `README`
  - `development-priority`
  - 示例说明

为什么先做这一步：

- 不先统一事实源，后续任何代码整改都会在错误前提下讨论
- 这一步改动相对小，但能显著降低后续沟通成本

优先级：最高

#### 第二阶段：统一 `Session` 收发与关闭收口

目标：

- 先把协议/网关主链路中最危险的生命周期问题收住

建议动作：

- 统一 `Session::stop()` 和 `handle_close()` 的关闭路径
- 固定协议处理顺序
  - 入站：解码 -> 重组 -> 解压 -> 投递
  - 出站：序列化 -> 压缩 -> 分片 -> 编码
- 暂时明确分片状态
  - 未正式启用就明确标注未启用
  - 要启用就接成真正闭环，不再停留在工具函数层
- 区分观测口径
  - 业务 body bytes
  - 真实 wire bytes

为什么第二步做：

- 关闭路径和收发顺序属于底座问题
- 这部分不稳定，后续 dispatcher、manager、metrics 都会继续带偏

优先级：最高

#### 第三阶段：拆分入口职责，收敛 dispatcher 角色

目标：

- 把“客户端接入策略”和“业务分发执行”拆开

建议动作：

- 把 ingress filter 前置
  - 白名单
  - 基础限频
  - 协议级快速拒绝
- 弱化 `MessageDispatcher` 的职责
  - 保留 handler 映射
  - 执行路由单独抽象
  - middleware 区分 client ingress / internal ingress
- 心跳等连接级协议尽量从业务线程池中拿出来

为什么第三步做：

- 这一步能直接降低协议链路复杂度
- 同时为未来 internal bus / backend ingress 留出边界

优先级：高

#### 第四阶段：收敛状态边界，减少 manager 之间的隐式耦合

目标：

- 把会话态、房间态、战斗态从“互相知道对方细节”收敛到“通过稳定事实源协作”

建议动作：

- 明确 battle 状态单一事实源
- 减少 `GatewayServer` 持有的跨域清理知识
- 逐步从 `const Session*` 关联迁移到稳定 ID
- 把 `transfer_session()` 这类连接替换语义逐步从房间层剥离

为什么放在第四步：

- 这一步改动面更大，依赖前面的协议和入口层已经稳定

优先级：高

#### 第五阶段：再决定是否正式推进结构化消息与内部总线

目标：

- 在基础链路稳定后，再决定是增强 1.x，还是为 2.x 铺路

建议动作：

- 若保留 1.x：
  - 让 `message_types + message_serializer` 真正接入主链
  - 统一 body 协议
- 若为拆服铺路：
  - 客户端协议和内部服务协议明确分层
  - `InternalBus` 使用独立 envelope
  - `ServiceRouter` 不再直接传 `DispatchContext`

为什么最后做：

- 结构化消息和内部总线都不是当前最紧迫的运行风险点
- 它们依赖前四阶段已经把基础边界理顺

优先级：中高

### 7.3 不建议的整改顺序

以下顺序看起来“技术上有意思”，但不适合当前维护阶段：

- 先上多进程拆服，再回头补协议边界
- 先把 serializer 全量替换进业务层，但不先统一真实协议事实源
- 先做 metrics/grafana 优化，但不先修复关闭路径和状态一致性
- 先扩 battle/room 功能，再继续沿用当前字符串协议和双写状态

原因：

- 这些顺序会把现有边界问题放大，而不是缩小

### 7.4 推荐维护里程碑

可按三个小里程碑执行：

#### M1：协议基线收敛

- 文档事实统一
- `flags` 语义固定
- 当前 body 协议路线定稿
- 配置/示例/运行文档按成熟度校正

完成标准：

- 后续讨论协议问题时，不再出现“代码一套、文档一套、示例一套”

#### M2：主链路收口

- `Session` 关闭路径统一
- 收发 pipeline 顺序固定
- 分片能力状态明确
- metrics 字节口径分清

完成标准：

- 协议/会话主链路具备稳定可解释性

#### M3：入口与状态边界收敛

- ingress 和 dispatcher 职责拆分
- manager 状态边界清晰
- battle / room / session 的清理关系明确
- 为后续 serializer 或 internal bus 留出稳定边界

完成标准：

- 1.x 维护进入“可持续演进”状态，而不是继续在现有混合边界上叠功能

### 7.5 当前最建议先动的点

如果只允许先做 5 个动作，建议顺序如下：

1. 修正文档与运行说明中的能力成熟度表述
2. 统一 `Session` 主动关闭与异常关闭路径
3. 固定协议增强顺序，并明确分片当前是否启用
4. 把 ingress 白名单/限频从业务线程池分发前置
5. 明确 battle 状态单一事实源，停止 `RoomManager` / `BattleManager` 双写语义继续扩散

### 7.6 暂不建议立即做的点

- 立即全面替换为 protobuf / flatbuffers
- 立即推进真正多进程拆服
- 立即把所有字符串 body 改成结构化消息
- 立即重写 battle 领域模型

原因：

- 这些动作依赖更底层的边界已经稳定
- 当前维护阶段更需要先止损，而不是先扩张

---

## 8. 协议/网关链路五步改造清单

本节将第 `7.5` 节的 5 个优先动作细化成可执行任务，默认面向 `v1.x` 维护阶段，不以大重构为目标，而以“先收口、再稳定、再为后续演进留边界”为目标。

### 8.0 建议版本落点

以下版本号不是现有 tag，而是建议用于 `v1.x` 维护阶段的发布落点，目的是让“验收点”和“改动边界”绑定，避免一次版本里混入过多不相干调整。

建议版本节奏：

- `v1.1.1`
  聚焦认知基线校准，不改核心协议行为
- `v1.1.2`
  聚焦 `Session` 生命周期与收发链路收口
- `v1.1.3`
  聚焦 ingress 前置与分发职责收敛
- `v1.1.4`
  聚焦状态边界与 battle 单一事实源
- `v1.2.0`
  在前面四个维护版本稳定后，再进入结构化消息或内部总线的正式演进

这样划分的原因：

- `v1.1.1` 到 `v1.1.4` 更像维护版收口，适合做兼容性优先的稳定整理
- `v1.2.0` 才适合承接协议主路径或内部通信模型的明显增强

### 8.1 第一步：修正文档与运行说明中的能力成熟度表述

建议验收版本：`v1.1.1`

#### 目标

- 让仓库入口、运行说明、示例说明、配置说明与当前真实实现保持一致
- 明确哪些能力是：
  - 已进入主链
  - 实验性
  - 预留抽象

#### 改造任务

1. 统一 `README.md`、`../runbooks/runtime-playbook.md`、`../plans/development-priority.md` 中对以下能力的表述：
   - 结构化消息体是否已进入主链
   - 分片能力是否已正式启用
   - 多进程/拆服是否已完成
   - `InternalBus` / `ServiceRouter` 是否仅为预埋抽象
2. 对 `examples/` 目录中的入口分类：
   - `echo_server` 归为“集成样例/主展示入口”
   - `login_demo` / `room_demo` / `battle_demo` / `admin_demo` 归为“showcase app”
   - `login_server` / `room_server` / `battle_server` 标记为“独立入口实验版”而不是“完整拆服架构”
3. 在 `runtime-playbook` 中增加成熟度标签：
   - `stable`
   - `experimental`
   - `reserved`
4. 补一段“当前协议事实源说明”：
   - 当前包头格式
   - 当前 body 主协议
   - 当前 push 语义

#### 验收点

- 文档中不再同时出现“结构化序列化已完成”和“业务主链仍用字符串 body”这种冲突表达
- 文档中不再把 `ServiceRouter` / `InternalBus` 直接写成可依赖完成态能力
- 新同事只读文档，不会对协议成熟度产生误判
- `README` / `runtime-playbook` / `development-priority` / `examples` 说明中的能力分级一致

#### 风险提示

- 这一步主要是校准认知，不会直接修代码问题
- 但如果不先做，后续技术讨论会持续偏题

### 8.2 第二步：统一 `Session` 主动关闭与异常关闭路径

建议验收版本：`v1.1.2`

#### 目标

- 让所有会话结束路径都经过同一个生命周期收口
- 保证状态清理、指标回收、房间/战斗解绑逻辑一致执行

#### 改造任务

1. 收敛 `Session::stop()` 与 `handle_close()`：
   - 不允许主动关闭绕过 `close_handler_`
   - 统一到一个只执行一次的关闭入口
2. 明确关闭原因模型：
   - 主动停服
   - 顶号踢线
   - 心跳超时
   - 包非法
   - 写队列溢出
   - 网络错误
3. 检查并统一关闭后的回调顺序：
   - 连接标记关闭
   - 计时器取消
   - socket shutdown/close
   - `close_handler_`
4. 校验 `GatewayServer::stop()` 路径：
   - 调用后 `active_connection_count_` 是否归零
   - `session_manager_` 是否清空
   - `room_manager_` / `battle_manager_` 是否进入一致状态
5. 补针对主动关闭路径的测试：
   - 主动 `server.stop()`
   - 重复登录触发旧连接关闭
   - 管理指令踢人关闭

#### 验收点

- 所有关闭路径最终都触发一致的资源回收
- `closed_sessions` 指标与实际关闭数一致
- 主动停服后 `active_sessions` / `active_rooms` / `active_battles` 不残留脏状态
- `Session` 收发主链路的顺序在代码、测试、文档中一致可描述

#### 风险提示

- 这一步会影响多个已有测试的行为假设
- 应先锁定预期关闭语义，再改实现

### 8.3 第三步：固定协议增强顺序，并明确分片当前是否启用

建议验收版本：`v1.1.2`

#### 目标

- 把协议增强链路从“代码里隐式堆逻辑”改成“顺序明确的主链”
- 消除压缩、分片、flags 语义的歧义

#### 改造任务

1. 定义并文档化固定顺序：
   - 入站：`decode -> fragment_reassemble -> decompress -> dispatch`
   - 出站：`serialize -> compress -> fragment -> encode`
2. 重新定义 `flags` 位布局：
   - 压缩位
   - 加密位
   - 分片标记位
   - 其他扩展位
   - 禁止元数据和布尔标记共享同一位段
3. 对分片做状态决定：
   - 若当前不启用：从文档和样例中移除“已支持自动分片”的表述
   - 若启用：把 `fragment_packet()` / `FragmentAssembler` 正式接入 `Session` 和 `InternalBus`
4. 明确无 `zlib` 时的压缩策略：
   - 不打 `kCompressed`
   - 或单独定义非压缩包装语义，不能复用压缩标记
5. 补组合测试：
   - 普通包
   - 压缩包
   - 分片包
   - 压缩后分片包
   - 非法 flags 包

#### 验收点

- 同一个包在不同构建条件下不会产生不同协议语义
- 文档、测试、代码对分片/压缩顺序说法一致
- 不存在“代码里有分片类，但主链根本不经过它”的情况
- `flags` 位定义在协议文档与代码常量中一一对应

#### 风险提示

- 这一步是协议边界清理，容易牵动现有测试和示例客户端
- 必须先定协议语义，再改实现

### 8.4 第四步：把 ingress 白名单/限频从业务线程池分发前置

建议验收版本：`v1.1.3`

#### 目标

- 让接入层校验回到接入层
- 减少非法消息占用业务线程池
- 为后续 client ingress / internal ingress 分离做准备

#### 改造任务

1. 重新划分入口处理层次：
   - `Session/GatewayServer`：包级合法性、基础准入、连接级快速拒绝
   - ingress filter：登录前白名单、连接维度基础限频
   - dispatcher：只处理已通过入口校验的业务消息
2. 将 `GatewayService` 里的以下逻辑前置：
   - `auth_whitelist`
   - 连接级 `rate_limit`
3. 明确 dispatcher 中还保留哪些 middleware：
   - 业务权限校验
   - 审计
   - 参数校验
   - 慢路径统计
4. 区分入口来源：
   - client ingress
   - internal ingress
   - admin ingress
5. 为前置过滤补观测指标：
   - ingress rejected
   - rate limited before dispatch
   - dropped before business queue

#### 验收点

- 非法/未登录/超限消息在进入业务线程池前就能被拒绝
- 心跳等轻量消息不再依赖完整业务分发链
- `InternalBus` 消息不会再误触客户端入口中间件
- 入口前置拒绝与业务分发后的拒绝在指标和日志中可以区分

#### 风险提示

- 需要小心不要改变现有错误响应语义
- 维护期重点是前置校验位置调整，不是立刻设计完整网关框架

### 8.5 第五步：明确 battle 状态单一事实源，停止 `RoomManager` / `BattleManager` 双写继续扩散

建议验收版本：`v1.1.4`

#### 目标

- 让“房间是否在战斗中”只有一个可靠事实源
- 避免后续功能继续建立在双写状态之上

#### 改造任务

1. 明确战斗态归属：
   - 推荐：`BattleManager` 为 battle 状态唯一事实源
2. 重新定义 `RoomManager::battle_started` 的角色：
   - 删除该字段
   - 或仅作为派生缓存，不参与独立判定
3. 检查所有 battle 状态读取点：
   - `RoomService::join_room`
   - `RoomService::set_ready`
   - `BattleService::start_battle`
   - 会话恢复逻辑
   - metrics / snapshot 输出
4. 重构清理路径：
   - 房间空了是否清理战斗
   - 战斗结束是否回写房间状态
   - 重连恢复时如何判断是否在战斗中
5. 补一致性测试：
   - 房间起战斗
   - 战斗结束
   - 成员离房
   - 房主掉线
   - 顶号重连

#### 验收点

- 不存在两个 manager 各自维护 battle 状态并相互推断的情况
- `room in battle` 的判断只依赖一处事实源
- 战斗结束、断线恢复、房间解散后的状态转换都可解释
- 登录恢复、房间广播、战斗广播这三条链路对 battle 状态的读法一致

#### 风险提示

- 这一步会波及 `RoomManager`、`BattleManager`、`BattleService`、`LoginService`
- 需要在做之前先画清状态转换图，否则容易边改边乱

### 8.6 建议的任务编排方式

建议不要按文件分配任务，而按“链路闭环”分任务：

1. 文档/配置成熟度校准任务
2. 会话关闭与收发链路收口任务
3. ingress 前置过滤任务
4. battle 单一事实源任务
5. 之后再开结构化消息或 internal bus 的后续任务

这样更符合当前维护阶段的真实依赖关系。

### 8.7 每步建议产出物

为避免只改代码不留基线，建议每一步都产出以下内容：

- 1 份短设计说明
- 1 组新增/更新测试
- 1 次运行文档同步
- 1 条开发日志补充记录

这样后面的模块分析就能建立在稳定的阶段边界之上。

### 8.8 建议版本与范围对照

#### `v1.1.1`：基线校准版

- 范围：
  - 文档成熟度校准
  - 配置/示例/运行说明纠偏
  - 不改主链协议行为
- 适合验收：
  - 第一步

#### `v1.1.2`：会话与协议收口版

- 范围：
  - `Session` 关闭路径统一
  - 收发 pipeline 固定
  - 分片状态明确
  - `flags` 语义固定
  - metrics 字节口径澄清
- 适合验收：
  - 第二步
  - 第三步

#### `v1.1.3`：入口收敛版

- 范围：
  - ingress 前置
  - dispatcher 角色收敛
  - 心跳/白名单/基础限频位置调整
- 适合验收：
  - 第四步

#### `v1.1.4`：状态边界收敛版

- 范围：
  - `SessionManager` / `RoomManager` / `BattleManager` 的状态边界整理
  - battle 单一事实源
  - 登录恢复与房间/战斗状态读取方式统一
- 适合验收：
  - 第五步

#### `v1.2.0`：协议主路径增强版

- 范围：
  - 结构化消息正式接入
  - 或内部总线 envelope 正式化
  - 或两者之一作为新主能力进入主链
- 说明：
  - 不建议在 `v1.1.x` 维护版本里直接承载

---

## 9. 下一阶段模块线分析

### 登录模块

- 分析范围：`TokenValidator` 族、`LoginService`、登录请求解析、登录成功/失败语义、重复登录与恢复流程
- 基线版本：`develop`
- 相关代码：
  - [include/game/login/token_validator.h](/D:/Program/boost/include/game/login/token_validator.h)
  - [src/game/login/token_validator.cpp](/D:/Program/boost/src/game/login/token_validator.cpp)
  - [include/game/login/http_token_validator.h](/D:/Program/boost/include/game/login/http_token_validator.h)
  - [src/game/login/http_token_validator.cpp](/D:/Program/boost/src/game/login/http_token_validator.cpp)
  - [include/game/login/login_service.h](/D:/Program/boost/include/game/login/login_service.h)
  - [src/game/login/login_service.cpp](/D:/Program/boost/src/game/login/login_service.cpp)
  - [tests/unit/token_validator_test.cpp](/D:/Program/boost/tests/unit/token_validator_test.cpp)
  - [tests/integration/gateway_integration_test.cpp](/D:/Program/boost/tests/integration/gateway_integration_test.cpp)

#### 现状

- 登录请求当前走字符串 body：`user_id|token|display_name`
- 登录校验通过 `TokenValidator` 抽象提供三类实现：
  - `DevTokenValidator`
  - `JsonFileTokenValidator`
  - `HttpTokenValidator`
- 登录成功后：
  - `SessionManager` 写入登录态
  - 可能触发重复登录替换
  - 可能触发房间恢复
  - 通过 `PushService` 回发 `login_ok` / `session_resumed` 等字符串消息

#### 不合理点

##### 9.1 登录协议仍是手写字符串协议，认证层和协议层边界混在一起

代码事实：

- [src/game/login/login_service.cpp:19](/D:/Program/boost/src/game/login/login_service.cpp:19) `parse_login_request()` 手工按 `|` 拆分
- [src/game/login/login_service.cpp:95](/D:/Program/boost/src/game/login/login_service.cpp:95) 登录成功后直接拼 `login_ok:...`
- [src/game/login/login_service.cpp:99](/D:/Program/boost/src/game/login/login_service.cpp:99) 恢复态再拼 `session_resumed:...`

不合理点：

- 登录模块本应是最适合统一协议的地方，因为它是客户端进入业务域的第一步
- 但现在登录协议仍与业务字符串拼接深耦合
- 认证结果、恢复结果、顶号结果都没有结构化边界

建议：

- 登录模块应优先成为结构化消息改造的试点模块，而不是继续扩展字符串 body
- 即使暂不改主链，也应先把：
  - 请求解析
  - 成功响应构造
  - 恢复态推送构造
  收敛到独立 helper，而不是散在 `LoginService`

优先级：高

##### 9.2 `TokenValidationResult` 中的 TTL/过期信息没有真正进入登录态模型

代码事实：

- [include/game/login/token_validator.h:19](/D:/Program/boost/include/game/login/token_validator.h:19) `TokenValidationResult` 带 `expires_at`
- [src/game/login/token_validator.cpp:29](/D:/Program/boost/src/game/login/token_validator.cpp:29) `DevTokenValidator` 返回 1h 或 24h TTL
- [src/game/login/login_service.cpp:78](/D:/Program/boost/src/game/login/login_service.cpp:78) 登录成功后只把 `user_id` 和 `display_name` 写入 `SessionManager`
- [include/game/gateway/session_manager.h:21](/D:/Program/boost/include/game/gateway/session_manager.h:21) `LoginContext` 本身也不包含过期时间

不合理点：

- 登录校验层已经计算了 token 生命周期
- 但登录态管理层完全没有保留这部分事实
- 结果是 TTL 目前只在“登录当下”有意义，对后续会话有效期管理没有任何约束作用

建议：

- 如果系统声称支持 token 生命周期，就应让过期信息进入稳定登录态
- 否则当前 TTL 更像“校验器内部实现细节”，不应被描述为完整能力

优先级：高

##### 9.3 `expired` 语义在现有校验器族中基本没有真实落地

代码事实：

- [include/game/login/token_validator.h:16](/D:/Program/boost/include/game/login/token_validator.h:16) 有 `expired` 字段
- `DevTokenValidator` 与 `JsonFileTokenValidator` 当前都不会返回 `expired = true`
- [src/game/login/http_token_validator.cpp:29](/D:/Program/boost/src/game/login/http_token_validator.cpp:29) `parse_auth_response()` 也没有解析过期态，只解析 `valid`
- [src/game/login/login_service.cpp:73](/D:/Program/boost/src/game/login/login_service.cpp:73) 却已经按 `expired ? token_expired : invalid_token` 分支发送错误

不合理点：

- 代码接口层面看起来已支持“token 过期”
- 但实际实现里几乎没有一条主路径真正产出 `expired = true`
- 这是典型的“接口能力超前于实现能力”

建议：

- 要么补齐 `expired` 的真实生产路径
- 要么在维护阶段先把“token_expired”降级为预留语义，不要写成已完整支持能力

优先级：中

##### 9.4 `HttpTokenValidator` 是同步阻塞实现，和当前异步网关模型不一致

代码事实：

- [src/game/login/http_token_validator.cpp:85](/D:/Program/boost/src/game/login/http_token_validator.cpp:85) 在 `validate()` 里同步 `resolve`
- [src/game/login/http_token_validator.cpp:89](/D:/Program/boost/src/game/login/http_token_validator.cpp:89) 同步 `connect`
- [src/game/login/http_token_validator.cpp:98](/D:/Program/boost/src/game/login/http_token_validator.cpp:98) 同步 `write`
- [src/game/login/http_token_validator.cpp:102](/D:/Program/boost/src/game/login/http_token_validator.cpp:102) 同步 `read`
- [include/game/login/http_token_validator.h:16](/D:/Program/boost/include/game/login/http_token_validator.h:16) 虽然有 `timeout_` 字段，但当前实现没有真正用于 socket 超时控制

不合理点：

- 这意味着开启 HTTP 鉴权时，登录 handler 会在业务线程池里做阻塞网络 I/O
- 当前架构前面一直强调异步和线程池分发，但这里登录最关键路径仍是同步外部调用
- `timeout_` 只是配置存在，没形成真实超时约束

建议：

- 维护期内至少要明确标注：
  - 当前 HTTP 鉴权为同步阻塞实现
  - 不适合高并发生产链路
- 后续若保留该能力，应优先改造成真正异步客户端或隔离到专门线程池

优先级：高

##### 9.5 登录防暴力破解/游客限制等能力已有抽象，但登录主链未真正接入

代码事实：

- [include/net/rate_limiter.h:16](/D:/Program/boost/include/net/rate_limiter.h:16) 有 `login_max_attempts`
- [include/net/rate_limiter.h:79](/D:/Program/boost/include/net/rate_limiter.h:79) 有 `check_login_attempt()`
- [include/net/rate_limiter.h:105](/D:/Program/boost/include/net/rate_limiter.h:105) 有 `record_login_failure()`
- [include/app/config.h:41](/D:/Program/boost/include/app/config.h:41) 有 `max_guests`
- 但当前登录主链检索没有看到这些能力真正接入 `LoginService`

不合理点：

- 登录模块是安全策略最应落地的地方
- 但现在很多“已声明能力”仍停留在抽象或 demo 描述层

建议：

- 后续若做登录模块维护，应把“已接线能力”和“预留能力”明确分开
- 不建议继续在登录文档里把游客限制、登录防爆破直接当完成态能力写出

优先级：高

##### 9.6 登录成功时耦合了房间恢复和踢线迁移，导致登录模块承担跨域编排职责

代码事实：

- [src/game/login/login_service.cpp:78](/D:/Program/boost/src/game/login/login_service.cpp:78) 登录成功直接调用 `session_manager_.authenticate(...)`
- [src/game/login/login_service.cpp:85](/D:/Program/boost/src/game/login/login_service.cpp:85) 重复登录时直接调用 `room_manager_.transfer_session(...)`
- [src/game/login/login_service.cpp:97](/D:/Program/boost/src/game/login/login_service.cpp:97) 又直接读取 `room_manager_.room_snapshot_of(...)`

不合理点：

- 登录模块本应关心“身份建立”
- 但现在它同时负责：
  - 会话替换
  - 房间归属迁移
  - 恢复态推送拼装
- 这让登录模块变成跨域编排入口，而不是纯登录服务

建议：

- 后续应把“登录成功后的恢复编排”与“身份校验本身”拆开
- 当前至少应在文档中明确：登录服务目前不仅做认证，还承担恢复编排

优先级：中

##### 9.7 登录模块测试覆盖了 happy path，但没把能力边界测清楚

代码事实：

- [tests/unit/token_validator_test.cpp](/D:/Program/boost/tests/unit/token_validator_test.cpp) 只覆盖 dev/json file 基础校验
- 现有集成测试主要覆盖：
  - 登录成功
  - 非法 token
  - 重复登录
- 没看到以下边界测试：
  - token 过期
  - HTTP 鉴权超时/异常响应
  - display_name 特殊字符
  - 畸形登录 body
  - 登录失败频控

不合理点：

- 当前测试足以证明“能跑”
- 但不足以证明登录模块的能力表述成立

建议：

- 登录模块后续若进入整改，应优先补边界测试，不要只改实现

优先级：中

### 房间模块

- 分析范围：`RoomManager`、`RoomService`、房间状态广播、会话迁移、与登录/战斗模块的边界
- 基线版本：`develop`
- 相关代码：
  - [include/game/room/room_manager.h](/D:/Program/boost/include/game/room/room_manager.h)
  - [src/game/room/room_manager.cpp](/D:/Program/boost/src/game/room/room_manager.cpp)
  - [include/game/room/room_service.h](/D:/Program/boost/include/game/room/room_service.h)
  - [src/game/room/room_service.cpp](/D:/Program/boost/src/game/room/room_service.cpp)
  - [tests/unit/room_manager_test.cpp](/D:/Program/boost/tests/unit/room_manager_test.cpp)
  - [tests/integration/gateway_integration_test.cpp](/D:/Program/boost/tests/integration/gateway_integration_test.cpp)

#### 现状

- `RoomManager` 负责：
  - 房间创建/加入/离开
  - 房主切换
  - ready 状态
  - `session -> room` 映射
  - 会话迁移 `transfer_session()`
  - battle 标记 `battle_started`
- `RoomService` 负责：
  - 房间消息 handler 注册
  - 房间响应 body 拼装
  - 房间状态广播 body 拼装
  - 在离房时协调 `BattleManager::remove_room()`

#### 不合理点

##### 9.8 `RoomManager` 同时管理“房间领域状态”和“连接对象迁移”，职责边界过宽

代码事实：

- [src/game/room/room_manager.cpp:117](/D:/Program/boost/src/game/room/room_manager.cpp:117) `transfer_session(from_session, to_session)`
- [src/game/room/room_manager.cpp:138](/D:/Program/boost/src/game/room/room_manager.cpp:138) 直接替换成员中的 `session`
- [src/game/room/room_manager.cpp:143](/D:/Program/boost/src/game/room/room_manager.cpp:143) 如旧连接是房主，还要同步替换 `owner`

不合理点：

- 房间模块本应优先表达“玩家在房间中的关系”
- 当前却直接持有并替换连接对象，这说明房间状态仍然是 `session-first`
- 这会导致房间层天然感知：
  - 顶号
  - 重连
  - 连接替换
  这些并非纯房间领域语义

建议：

- 后续应逐步把房间成员从“连接对象”提升为“稳定玩家身份”
- 维护期内至少要把 `transfer_session()` 定位为兼容性桥接逻辑，而不是房间核心职责

优先级：高

##### 9.9 房间状态广播依赖 `SessionManager` 回查用户信息，领域数据并不自洽

代码事实：

- [src/game/room/room_service.cpp:180](/D:/Program/boost/src/game/room/room_service.cpp:180) 广播时通过 `session_manager_.login_context_of(member.session)` 反查 user_id
- [src/game/room/room_service.cpp:189](/D:/Program/boost/src/game/room/room_service.cpp:189) 房主 user_id 也通过 `SessionManager` 反查
- `RoomManager::RoomMember` 本身只存 `session` 和 `ready`

不合理点：

- 房间快照不是自洽的领域快照，还依赖外部 `SessionManager` 补全业务身份信息
- 一旦登录态与房间态有瞬时不一致，房间广播内容会退化成 `"unknown"`
- 这也说明房间层当前并没有真正拥有“成员是谁”的稳定业务事实

建议：

- 房间层的快照应尽量直接包含业务身份，而不是广播时临时跨模块回查
- 维护期内至少要明确：当前房间快照是“连接视角快照”，不是完整玩家视角快照

优先级：高

##### 9.10 `RoomService` 负责字符串协议拼装，协议层和房间业务层没有分离

代码事实：

- [src/game/room/room_service.cpp:47](/D:/Program/boost/src/game/room/room_service.cpp:47) `room_created:...`
- [src/game/room/room_service.cpp:84](/D:/Program/boost/src/game/room/room_service.cpp:84) `room_joined:room_id:player_count`
- [src/game/room/room_service.cpp:122](/D:/Program/boost/src/game/room/room_service.cpp:122) `room_left:...`
- [src/game/room/room_service.cpp:154](/D:/Program/boost/src/game/room/room_service.cpp:154) `room_ready:on/off`
- [src/game/room/room_service.cpp:193](/D:/Program/boost/src/game/room/room_service.cpp:193) `room_state:...`

不合理点：

- 房间 service 现在同时承担：
  - handler
  - 业务流程
  - 协议字符串定义
  - 广播格式定义
- 这会让房间协议变更和房间业务变更永久耦合

建议：

- 房间模块应优先成为“协议表达外移”的第二个试点模块
- 至少先把房间响应与广播 body 构造从 `RoomService` 主逻辑里抽出来

优先级：高

##### 9.11 `ready` 请求解析过于宽松，协议语义不够严格

代码事实：

- [src/game/room/room_service.cpp:145](/D:/Program/boost/src/game/room/room_service.cpp:145) `split_once(context.body)`
- [src/game/room/room_service.cpp:146](/D:/Program/boost/src/game/room/room_service.cpp:146) 只要 `action == "1"/"true"` 或 `value == "1"/"true"` 就认为 ready

不合理点：

- 当前解析规则更像兼容性兜底，而不是明确协议
- 类似 `"foo|true"` 也会被认为合法 ready 请求
- 这种宽松解析短期方便，但长期会降低协议边界的可测试性和可解释性

建议：

- 明确 `RoomReadyRequest` 的唯一合法表示
- 若暂时不切结构化协议，至少限制为单一字符串形式，而不是模糊兼容多种写法

优先级：中

### 战斗模块

- 分析范围：`BattleManager`、`BattleService`、`ReplayPlayer`、观战/回放/结算相关抽象，以及与 `RoomManager`、`SessionManager` 的边界关系
- 基线版本：`develop`
- 相关代码：
  - [include/game/battle/battle_manager.h](/D:/Program/boost/include/game/battle/battle_manager.h)
  - [src/game/battle/battle_manager.cpp](/D:/Program/boost/src/game/battle/battle_manager.cpp)
  - [include/game/battle/battle_service.h](/D:/Program/boost/include/game/battle/battle_service.h)
  - [src/game/battle/battle_service.cpp](/D:/Program/boost/src/game/battle/battle_service.cpp)
  - [include/game/battle/replay_player.h](/D:/Program/boost/include/game/battle/replay_player.h)
  - [include/game/gateway/session_migration.h](/D:/Program/boost/include/game/gateway/session_migration.h)
  - [tests/unit/battle_manager_test.cpp](/D:/Program/boost/tests/unit/battle_manager_test.cpp)
  - [tests/integration/gateway_integration_test.cpp](/D:/Program/boost/tests/integration/gateway_integration_test.cpp)

#### 现状

- `BattleManager` 当前负责：
  - 按 `room_id` 创建 active battle
  - 接收玩家输入并分配 `sequence`
  - 维护 `current_frame`
  - 提供 `snapshot()`、`advance_frame()`、`end_battle()`
  - 提供观战者列表增删查
- `BattleService` 当前负责：
  - 校验登录态、房间态、房主权限
  - 从 `RoomManager` 拉房间成员并检查 ready
  - 调用 `BattleManager::start_battle()` / `submit_input()`
  - 直接拼装 `battle_started`、`battle_state`、`battle_input` 等字符串协议
  - 向房间成员广播战斗状态与输入
- `ReplayPlayer` 当前只负责从 `IBattleReplayStore` 读取 JSON 回放并逐帧回调，未见接入主战斗链路

#### 不合理点

##### 9.15 `BattleManager` 更像“按房间组织的输入收集器”，但上层已经把它当成完整战斗域在使用

代码事实：
- [src/game/battle/battle_manager.cpp:25](/D:/Program/boost/src/game/battle/battle_manager.cpp:25) 创建 battle 时只保存 `player_ids`、`next_sequence`、`current_frame`、`inputs`
- [src/game/battle/battle_manager.cpp:65](/D:/Program/boost/src/game/battle/battle_manager.cpp:65) `advance_frame()` 只是抽取某一帧的输入集合
- [src/game/battle/battle_manager.cpp:90](/D:/Program/boost/src/game/battle/battle_manager.cpp:90) `end_battle()` 的“结算”只是统计每个玩家输入条数

不合理点：
- 当前 battle 核心状态里没有真正的战斗领域事实，例如：
  - 战斗唯一标识
  - 战斗阶段
  - 结算原因
  - 生命周期时间点
  - 断线/重连后的同步状态
- 但外围模块已经把 `battle_started`、`battle_state`、`battle replay`、`battle migration` 当成真实能力来引用
- 这说明当前战斗模块的“内部模型复杂度”明显低于“外部被期待承担的职责复杂度”

建议：
- 先在维护文档中明确：当前 `BattleManager` 是轻量房间内战斗上下文，不是完整战斗领域引擎
- 后续所有 battle 相关能力判断都应基于这个定位，避免继续把 replay、spectator、结算、断线恢复都叠到当前模型上

优先级：高

##### 9.16 战斗事实源仍然依附房间，`room_id` 既是战斗键又近似战斗 ID，边界没有独立出来

代码事实：
- [include/game/battle/battle_manager.h:61](/D:/Program/boost/include/game/battle/battle_manager.h:61) `StartBattleOutcome` 只有 `room_id`
- [src/game/battle/battle_service.cpp:60](/D:/Program/boost/src/game/battle/battle_service.cpp:60) 启动战斗时直接传 `room_snapshot->room_id`
- [src/game/battle/battle_service.cpp:69](/D:/Program/boost/src/game/battle/battle_service.cpp:69) 响应体直接回 `battle_started:{room_id}:{player_count}`
- [include/net/message_types.h:100](/D:/Program/boost/include/net/message_types.h:100) 结构化消息里其实预留了 `battle_id`

不合理点：
- 这说明 battle 在协议草案层被当成独立实体，但在真实实现层仍然只是 room 的附属状态
- `room_id == battle key` 会让后续很多能力都失去清晰边界：
  - 一间房多局连续战斗
  - 战斗记录持久化
  - 战斗历史查询
  - 战斗回放定位
  - 结算后房间继续保留

建议：
- 维护期至少要统一口径：当前 battle 是否允许独立于 room 存在
- 如果 1.x 不打算引入独立 `battle_id`，那就不应再把结构化 battle 消息写成 battle 独立实体的样子
- 如果后续准备演进，则 battle ID 必须先成为稳定事实源，不能继续用 `room_id` 兼任

优先级：高

##### 9.17 `BattleService` 承担了大量跨域编排逻辑，战斗入口并不纯粹

代码事实：
- [src/game/battle/battle_service.cpp:25](/D:/Program/boost/src/game/battle/battle_service.cpp:25) 先校验是否已登录
- [src/game/battle/battle_service.cpp:31](/D:/Program/boost/src/game/battle/battle_service.cpp:31) 再读取 `RoomManager::room_snapshot_of()`
- [src/game/battle/battle_service.cpp:37](/D:/Program/boost/src/game/battle/battle_service.cpp:37) 再校验房主权限
- [src/game/battle/battle_service.cpp:44](/D:/Program/boost/src/game/battle/battle_service.cpp:44) 再遍历房间成员并回查 `SessionManager`
- [src/game/battle/battle_service.cpp:63](/D:/Program/boost/src/game/battle/battle_service.cpp:63) 成功后再反向写回 `room_manager_.mark_battle_started(...)`

不合理点：
- 战斗启动链路当前同时知道：
  - 会话是否登录
  - 当前连接在哪个房间
  - 谁是房主
  - 所有成员是否 ready
  - 战斗启动后如何反写房间态
- 这说明战斗服务不是“战斗域入口”，而是“房间转战斗编排器”
- 一旦后续加入匹配开战、重连恢复、旁观进入、战斗结算回房，这个 service 会继续膨胀

建议：
- 维护期内至少应把“房间资格校验”和“战斗启动结果表达”从 `BattleService` 主流程里收敛出来
- 文档上要明确：当前 `BattleService` 不是独立 battle API，而是 room-based battle orchestration

优先级：高

##### 9.18 `RoomManager` 与 `BattleManager` 继续双写 battle 状态，单一事实源仍然缺失

代码事实：
- [src/game/battle/battle_service.cpp:63](/D:/Program/boost/src/game/battle/battle_service.cpp:63) battle 启动成功后调用 `room_manager_.mark_battle_started(...)`
- [src/game/room/room_manager.cpp:220](/D:/Program/boost/src/game/room/room_manager.cpp:220) `RoomManager` 自己维护 `battle_started`
- [src/game/battle/battle_manager.cpp:128](/D:/Program/boost/src/game/battle/battle_manager.cpp:128) `BattleManager` 自己也维护 active battle 是否存在
- [include/game/gateway/session_migration.h:37](/D:/Program/boost/include/game/gateway/session_migration.h:37) 会话迁移态又通过 `BattleManager::battle_started()` 采集 battle 状态

不合理点：
- 当前至少存在两套“战斗是否开始”的事实源：
  - 房间视角的 `room.battle_started`
  - 战斗视角的 `active_battles_.contains(room_id)`
- 只要有任何一条异常关闭、清理顺序变化、未来增加延迟结算或回放保留期，这两套状态就很容易分叉

建议：
- 维护期应继续坚持前面路线图里的原则：battle 状态只能有一个稳定事实源
- 另一侧如果必须保留，只能是派生视图，不能再作为平级状态长期存在

优先级：高

##### 9.19 战斗协议表达仍停留在手写字符串层，且与 `message_types` 已经出现事实分叉

代码事实：
- [src/game/battle/battle_service.cpp:69](/D:/Program/boost/src/game/battle/battle_service.cpp:69) `battle_started:{room_id}:{player_count}`
- [src/game/battle/battle_service.cpp:72](/D:/Program/boost/src/game/battle/battle_service.cpp:72) `battle_state:started:{room_id}:{player_count}`
- [src/game/battle/battle_service.cpp:112](/D:/Program/boost/src/game/battle/battle_service.cpp:112) `battle_input_accepted:{room_id}:{sequence}`
- [src/game/battle/battle_service.cpp:115](/D:/Program/boost/src/game/battle/battle_service.cpp:115) `battle_input:{room_id}:{user_id}:{sequence}:{payload}`
- [include/net/message_types.h:100](/D:/Program/boost/include/net/message_types.h:100) `BattleStartResponse` 设计的是 `battle_id + room_id`
- [include/net/message_types.h:119](/D:/Program/boost/include/net/message_types.h:119) `BattleStatePush` 设计的是 `battle_id + room_id + state`

不合理点：
- 当前 battle 模块同时存在两套互不一致的协议事实：
  - 真实运行链路：房间导向的字符串协议
  - 结构化草案：战斗实体导向的 typed message
- 这不是单纯“还没迁移完”，而是消息语义已经分叉

建议：
- battle 模块应被视为结构化协议整改的重点风险点之一
- 在 1.x 维护阶段，至少先冻结一套权威协议描述，避免继续一边改字符串 body、一边维护另一套 typed 定义

优先级：高

##### 9.20 `SubmitInputResult::kPlayerNotInBattle` 被映射成 `AuthRequired`，错误语义明显失真

> **维护状态（`v1.1.6`）**：已新增 `ErrorCode::kPlayerNotInBattle`（3004），`BattleService` 使用该码；默认 body 由 `to_string` 得到 `player_not_in_battle`。本节保留为历史问题陈述。

代码事实（修正前）：
- `battle_manager.h` 定义了 `kPlayerNotInBattle`
- `battle_service.cpp` 曾使用 `ErrorCode::kAuthRequired` 并手写 body `"player_not_in_battle"`

不合理点：
- 从协议层看，这会制造“错误码语义”和“错误消息语义”不一致：
  - 错误码表示未认证
  - 文本却表示不在当前战斗中
- 客户端如果依赖 `error_code` 做分支，将得到错误行为
- 这也说明当前错误码体系和业务域错误没有认真对齐

建议：
- 维护期内至少要把 battle 域错误和认证错误区分清楚
- 即使暂不新增错误码，也不应继续让 battle 业务错误伪装成 auth 错误

优先级：高

##### 9.21 `advance_frame()` 与输入存储模型偏演示化，难以支撑真正的帧同步或长期战斗

代码事实：
- [src/game/battle/battle_manager.cpp:54](/D:/Program/boost/src/game/battle/battle_manager.cpp:54) 所有输入统一累积在 `inputs`
- [src/game/battle/battle_manager.cpp:69](/D:/Program/boost/src/game/battle/battle_manager.cpp:69) 每次推进帧都线性扫描所有历史输入
- [src/game/battle/battle_manager.cpp:149](/D:/Program/boost/src/game/battle/battle_manager.cpp:149) `snapshot()` 直接复制整个输入列表

不合理点：
- 这套模型更适合演示“能记录输入”，不适合真实帧同步主链路
- 战斗持续时间一长，以下成本会不断上升：
  - 帧推进扫描成本
  - 快照复制成本
  - 回放序列持有成本
- 目前也没有输入窗口裁剪、历史归档、增量快照等设计

建议：
- 维护期不一定要立刻重写，但应在文档中明确该实现的适用边界：
  - 适合短局、低输入量 demo
  - 不适合被当作长期可扩展 battle core
- 后续若继续保留 battle 能力，应先决定 battle 是“演示型输入广播”还是“真实帧同步内核”

优先级：中

##### 9.22 回放与观战能力处于“抽象存在但主链未闭环”的状态，容易被误判为已成熟能力

代码事实：
- [include/game/battle/replay_player.h:23](/D:/Program/boost/include/game/battle/replay_player.h:23) `ReplayPlayer` 能从 `IBattleReplayStore` 读取回放
- [include/game/persistence/player_store.h:75](/D:/Program/boost/include/game/persistence/player_store.h:75) 也定义了 replay store 接口
- 但当前 battle 主链路里没有看到：
  - 正式 replay 生成与保存
  - 回放查询入口
  - 回放与 `end_battle()` 的闭环
- [include/game/battle/battle_manager.h:78](/D:/Program/boost/include/game/battle/battle_manager.h:78) 已有 spectator 接口
- 但 `BattleService` 当前并没有观战加入/退出/同步协议

不合理点：
- 回放和观战都已在代码结构里占了位置，但都没形成完整产品级闭环
- 这类“半接线能力”最容易在文档、示例和维护讨论里被误读成已支持

建议：
- 文档上应把 replay / spectator 明确标记为预留或演示级能力
- 在未形成闭环前，不建议继续围绕这些抽象扩写外围说明

优先级：中

##### 9.23 战斗模块测试覆盖了最基本 happy path，但没有覆盖当前最危险的边界

代码事实：
- [tests/unit/battle_manager_test.cpp:5](/D:/Program/boost/tests/unit/battle_manager_test.cpp:5) 只覆盖了：
  - 开战成功
  - 重复开战失败
  - 输入成功
  - remove_room 后状态清除
- [tests/integration/gateway_integration_test.cpp:214](/D:/Program/boost/tests/integration/gateway_integration_test.cpp:214) 只覆盖了“两人同房开战并发送输入”的主路径
- 当前未见专门覆盖：
  - 非房主开战
  - 有成员未 ready
  - 玩家已离房但 battle 仍持有旧玩家 ID
  - `room_manager` / `battle_manager` 状态分叉
  - `player_not_in_battle` 错误码语义
  - 长局输入累积与帧推进行为
  - replay / spectator 抽象能力

不合理点：
- 当前测试足以说明模块“能跑通”
- 但不足以说明 battle 当前复杂边界是稳定的，尤其不足以支撑后续 1.x 维护修改

建议：
- battle 模块后续如果进入整改，应优先补边界测试而不是先改实现
- 尤其要先把“房间态与战斗态一致性”相关测试补起来，否则后续清理双写状态时容易引入回归

优先级：中

### 登录 / 房间 / 战斗业务线维护整改路线图

- 适用范围：`login`、`room`、`battle` 三条业务模块线，以及它们与 `SessionManager`、`GatewayServer` 的协作边界
- 目标：在不引入大规模重构的前提下，先把 1.x 维护期最容易失控的边界问题收口，避免业务域复杂度继续沿着字符串协议、跨域编排和双写状态扩散

#### 总体判断

- 当前三条业务线不是彼此独立的模块，而是一条连续的业务链：
  - `login` 负责建立身份并触发恢复
  - `room` 负责组织玩家关系并承接连接迁移
  - `battle` 负责在房间上下文里启动输入流
- 现阶段最核心的问题不是“单个模块实现不够强”，而是：
  - 业务事实源不稳定
  - 协议表达不统一
  - 跨域编排散落在 service 和 gateway 层
- 因此整改顺序必须先收口边界，再谈能力增强

#### 建议执行顺序

##### 第一步：先统一业务事实源与模块定位

目标：
- 先停止三条业务线继续各自扩张职责
- 明确哪些状态是稳定事实，哪些只是派生视图或兼容桥接

具体任务：
- 明确 `login` 只负责身份建立，登录成功后的恢复编排单独标注为恢复链路，不再默认混在“认证成功”语义里
- 明确 `room` 的核心事实是“玩家与房间关系”，不是“连接对象本身”
- 明确 `battle` 当前只是 room-based battle context，不是完整独立战斗域
- 明确 `battle_started` 的唯一事实源，禁止 `RoomManager` / `BattleManager` 双写继续扩散

验收点：
- 文档层面能清楚回答以下问题：
  - 登录成功是否等于恢复完成
  - 房间成员是按 `session` 还是按稳定玩家身份建模
  - 战斗是否有独立实体，还是房间附属状态
  - `battle_started` 以哪一处状态为准

建议版本：`v1.1.5`

##### 第二步：收敛三条业务线的协议事实源

目标：
- 阻止 `login`、`room`、`battle` 继续各自维护一套手写字符串 body 事实
- 先把真实线上协议描述冻结，再决定是否迁移结构化协议

具体任务：
- 为登录、房间、战斗分别整理当前真实运行中的请求/响应/push 协议表
- 标记出与 `message_types` / `message_serializer` 已分叉的消息
- 明确哪些字符串 body 仍是 1.x 正式协议，哪些 typed message 只是预留草案
- 收敛错误码语义，至少消除 battle 业务错误伪装成 auth 错误这类明显失真项

验收点：
- 任一条业务消息都能从文档中唯一确认：
  - `message_id`
  - body 结构
  - 错误码语义
  - push 与 response 的区别
- 不再存在“代码运行一套、typed 定义另一套”却没有标注的情况

建议版本：`v1.1.6`

##### 第三步：拆解跨域编排，给恢复链和状态清理链单独收口

目标：
- 把当前散落在 `LoginService`、`RoomService`、`BattleService`、`GatewayServer` 里的跨域知识收口
- 降低 service 层继续膨胀的风险

具体任务：
- 收敛“重复登录 -> 顶号 -> 房间迁移 -> 恢复态推送”这条恢复链
- 收敛“离房 / 断线 / 空房 -> battle 清理”这条清理链
- 收敛“房间资格校验 -> 开战条件校验 -> battle 启动结果广播”这条开战编排链
- 至少先把这些链路在文档和代码职责说明里变成可识别的独立流程，而不是散落在各 service 内部

验收点：
- 能明确指出每一条跨域流程的统一入口和统一清理点
- 不再出现同一条清理知识在 `GatewayServer` 和某个业务 service 中各写一份的情况

建议版本：`v1.1.7`

##### 第四步：把房间态与战斗态的边界收紧

目标：
- 先控制 `room` 与 `battle` 的耦合面，不让后续 replay、spectator、结算继续叠在模糊边界上

具体任务：
- 让房间快照尽量成为自洽快照，减少广播时回查 `SessionManager`
- 限定 `transfer_session()` 的定位，只作为兼容性桥接，不继续扩张为房间核心职责
- 明确 `battle` 结束后房间状态如何回收或保留
- 明确观战、回放、结算在 1.x 中属于预留能力还是正式能力

验收点：
- 房间广播不再严重依赖登录态回查补全身份
- `battle` 与 `room` 的状态关系可以被一张简明状态表说明
- replay / spectator 的成熟度表述与实际主链一致

建议版本：`v1.1.8`

##### 第五步：补边界测试，再决定是否做协议结构化迁移

目标：
- 先用测试把 1.x 维护边界钉住，再决定是否继续推进 typed message 或内部演进

具体任务：
- 补登录边界测试：
  - token 过期
  - HTTP 鉴权异常/超时
  - 畸形 body
  - 登录失败频控
- 补房间边界测试：
  - `transfer_session()`
  - 房间状态广播构造
  - 空房 battle 清理
  - battle 标记一致性
- 补战斗边界测试：
  - 非房主开战
  - 未 ready 开战
  - `player_not_in_battle` 错误语义
  - 房间态与战斗态分叉
  - 长局输入与帧推进
- 在边界测试稳定前，不建议直接推进 battle/replay/spectator 进一步扩写

验收点：
- 三条业务线的关键边界都能被自动化测试描述
- 后续若做 typed message 或 battle 增强，已有测试能明确指出是否破坏了 1.x 维护契约

建议版本：`v1.2.1`

#### 维护阶段优先改造清单

如果只按最小必要顺序推进，我建议先做这 6 个动作：

1. 明确 `battle_started` 的唯一事实源，并停止双写继续扩散
2. 整理 `login`、`room`、`battle` 当前真实协议表，冻结 1.x 协议事实
3. 修正 battle 域错误码语义，去掉 `player_not_in_battle -> auth_required` 这类失真映射
4. 把“重复登录恢复链”和“空房 battle 清理链”各自定义为一条独立流程
5. 收敛房间快照与成员身份表达，降低对 `SessionManager` 的广播时回查依赖
6. 先补边界测试，再决定是否正式推进 typed message 接管业务协议

#### 版本建议

- `v1.1.5`：业务事实源校准版
- `v1.1.6`：业务协议冻结版
- `v1.1.7`：跨域编排收口版
- `v1.1.8`：房间/战斗边界收紧版
- `v1.2.1`：业务边界测试加固版

### Gateway / Admin / Management 模块

- 分析范围：`GatewayService`、`GatewayServer`、`AdminService`、`HttpManager` 以及运行时治理入口、管理命令、连接治理和可观测导出的边界关系
- 基线版本：`develop`
- 相关代码：
  - [include/game/gateway/gateway_service.h](/D:/Program/boost/include/game/gateway/gateway_service.h)
  - [src/game/gateway/gateway_service.cpp](/D:/Program/boost/src/game/gateway/gateway_service.cpp)
  - [include/game/gateway/gateway_server.h](/D:/Program/boost/include/game/gateway/gateway_server.h)
  - [src/game/gateway/gateway_server.cpp](/D:/Program/boost/src/game/gateway/gateway_server.cpp)
  - [include/game/gateway/admin_service.h](/D:/Program/boost/include/game/gateway/admin_service.h)
  - [include/net/http_manager.h](/D:/Program/boost/include/net/http_manager.h)
  - [src/net/http_manager.cpp](/D:/Program/boost/src/net/http_manager.cpp)
  - [tests/unit/admin_service_test.cpp](/D:/Program/boost/tests/unit/admin_service_test.cpp)
  - [tests/integration/http_management_test.cpp](/D:/Program/boost/tests/integration/http_management_test.cpp)
  - [examples/admin_demo/admin_demo_main.cpp](/D:/Program/boost/examples/admin_demo/admin_demo_main.cpp)
  - [examples/login_demo/login_demo_main.cpp](/D:/Program/boost/examples/login_demo/login_demo_main.cpp)

#### 现状

- `GatewayService` 当前负责：
  - 注册 ingress middleware
  - 认证白名单放行
  - 简单按 session 维度限频
  - heartbeat 直接回包
- `GatewayServer` 当前负责：
  - TCP accept
  - 连接总数 / 单 IP 限制
  - `Session` 生命周期回调挂接
  - HTTP management 端点启动
  - 周期 metrics 日志与文件导出
- `AdminService` 当前负责：
  - 注册 kick / ban / status / reload 四类消息号 handler
  - 通过 callback 把实际执行逻辑交给外部
- `HttpManager` 当前只提供：
  - `GET /health`
  - `GET /metrics`
  - `GET /metrics/json`

#### 不合理点

##### 9.24 运行时治理入口分成“二进制协议管理命令”和“HTTP 管理端点”两套体系，但权限与职责模型未统一

代码事实：
- [include/net/protocol.h:59](/D:/Program/boost/include/net/protocol.h:59) 定义了 `5001-5005` 管理消息号
- [include/game/gateway/admin_service.h:30](/D:/Program/boost/include/game/gateway/admin_service.h:30) `AdminService` 通过业务 dispatcher 注册管理命令
- [src/net/http_manager.cpp:77](/D:/Program/boost/src/net/http_manager.cpp:77) HTTP 管理端点同时暴露 `/health`、`/metrics`、`/metrics/json`
- [src/game/gateway/gateway_server.cpp:42](/D:/Program/boost/src/game/gateway/gateway_server.cpp:42) `GatewayServer` 直接在启动时挂接 HTTP management

不合理点：
- 当前系统同时有两套治理入口：
  - 走客户端协议栈的管理命令
  - 绕过客户端协议栈的 HTTP 管理端点
- 但两套入口的职责边界并没有统一定义：
  - 哪些能力应该走内网 HTTP
  - 哪些能力应该走二进制协议
  - 哪些能力需要强认证
  - 哪些能力只允许只读
- 结果是治理能力的“暴露方式”先出现了，但“控制模型”还没有成型

建议：
- 维护期至少先定义治理入口分层：
  - 健康检查与指标导出
  - 只读状态查询
  - 有副作用的运维命令
- 不建议继续把新的治理动作随意加进二进制消息号或 HTTP 端点，而不先明确入口等级

优先级：高

##### 9.25 `AdminService` 当前没有可见的权限校验，管理命令接近“知道消息号即可调用”

代码事实：
- [include/game/gateway/admin_service.h:30](/D:/Program/boost/include/game/gateway/admin_service.h:30) `kAdminServerStatus` handler 直接执行
- [include/game/gateway/admin_service.h:36](/D:/Program/boost/include/game/gateway/admin_service.h:36) `kAdminReloadConfig` handler 直接执行 callback
- [include/game/gateway/admin_service.h:42](/D:/Program/boost/include/game/gateway/admin_service.h:42) `kAdminKickPlayer` 直接使用 `ctx.body`
- [include/game/gateway/admin_service.h:48](/D:/Program/boost/include/game/gateway/admin_service.h:48) `kAdminBanIp` 直接使用 `ctx.body`
- 当前 `AdminService` 仅持有 `SessionManager&`，但源码里没有看到基于登录态、角色、来源地址或专用 admin token 的判断

不合理点：
- 从实现上看，admin 指令只是普通业务消息号中的另一组 handler
- 如果它被注册进生产主链，而没有额外外围隔离，那么任何能建立连接并构造消息的客户端都可能触发管理动作
- 这不是“能力未完善”的普通问题，而是典型的治理面暴露风险

建议：
- 在 1.x 维护阶段，必须明确：
  - admin 命令是否仅限 demo
  - 是否允许进入正式主链
  - 若允许，权限依据是什么
- 在权限模型未定之前，不应把这组命令描述为通用可用的正式管理能力

优先级：高

##### 9.26 `AdminService` 在主库里更像演示级可插拔组件，而不是已纳入统一运行时治理模型的正式模块

代码事实：
- 搜索结果显示 `AdminService` 主要在 [examples/admin_demo/admin_demo_main.cpp](/D:/Program/boost/examples/admin_demo/admin_demo_main.cpp) 和 [examples/login_demo/login_demo_main.cpp](/D:/Program/boost/examples/login_demo/login_demo_main.cpp) 中手工接线
- 当前 `GatewayServer`、`GatewayService` 本身并不持有或编排 `AdminService`
- [tests/unit/admin_service_test.cpp:11](/D:/Program/boost/tests/unit/admin_service_test.cpp:11) 主要只验证 handler 是否注册成功

不合理点：
- 这说明 admin 相关能力当前更像 example-level assembly，而不是 core runtime contract
- 但协议号、回包格式和文档表达又很容易让人误解为“网关已经正式支持管理命令”

建议：
- 文档应把 `AdminService` 明确标注为示例式治理扩展示范，而不是默认启用的核心能力
- 若后续要转正，应先把：
  - 权限模型
  - 审计模型
  - 错误处理
  - 幂等性
  - 接线方式
  补成统一规范

优先级：高

##### 9.27 HTTP management 当前只有裸露只读端点，没有健康语义分层，也没有接入鉴权或来源控制

代码事实：
- [src/net/http_manager.cpp:77](/D:/Program/boost/src/net/http_manager.cpp:77) `/health` 固定返回 `{"status":"ok"}`
- [src/net/http_manager.cpp:80](/D:/Program/boost/src/net/http_manager.cpp:80) `/metrics` 直接返回指标文本
- [src/net/http_manager.cpp:83](/D:/Program/boost/src/net/http_manager.cpp:83) `/metrics/json` 直接返回 JSON 指标
- [include/net/http_manager.h:16](/D:/Program/boost/include/net/http_manager.h:16) 虽然声明了 `HealthProvider`，但当前没有真正使用

不合理点：
- 当前 `/health` 不是“基于系统状态计算的健康检查”，而是固定字面量
- `HealthProvider` 已经预留，但未形成主链闭环
- 同时 HTTP management 没有看到：
  - 来源限制
  - basic auth / token auth
  - 是否仅监听内网接口
  - 健康级别区分
- 这意味着它更像 scrape/demo 端点，而不是成熟运维接口

建议：
- 文档中应明确：当前 HTTP management 主要用于基础观测演示，不应过度描述为完整管理面
- 如果 1.x 打算保留，应先把健康检查与指标导出分开定义成熟度

优先级：高

##### 9.28 `GatewayServer` 已经承担接入、清理、metrics、HTTP 管理等多类职责，正在演变成运行时总装配器

代码事实：
- [src/game/gateway/gateway_server.cpp:85](/D:/Program/boost/src/game/gateway/gateway_server.cpp:85) accept 阶段处理连接限流
- [src/game/gateway/gateway_server.cpp:125](/D:/Program/boost/src/game/gateway/gateway_server.cpp:125) 接线 session metrics observer
- [src/game/gateway/gateway_server.cpp:140](/D:/Program/boost/src/game/gateway/gateway_server.cpp:140) 关闭时处理房间/battle/session 清理
- [src/game/gateway/gateway_server.cpp:175](/D:/Program/boost/src/game/gateway/gateway_server.cpp:175) 定时日志和文件导出 metrics
- [src/game/gateway/gateway_server.cpp:42](/D:/Program/boost/src/game/gateway/gateway_server.cpp:42) 还负责启动 HTTP management

不合理点：
- `GatewayServer` 现在不仅是网络接入器，还在承担：
  - 连接治理
  - 会话退出编排
  - 观测导出编排
  - 管理端点装配
- 这类职责集中短期看方便，但长期会让所有运行时治理改动都堆到同一个类上
- 它和前面业务线里的 `LoginService`、`BattleService` 一样，都有“总装配器膨胀”的趋势

建议：
- 维护期内至少应把 `GatewayServer` 的职责说明写清楚，避免后续继续往这里塞更多治理动作
- 后续若继续演进，建议把：
  - 会话关闭后业务清理
  - metrics 导出
  - management 端点
  看作独立运行时能力，而不是都挂在网关接入器本体上

优先级：中

##### 9.29 限频、审计、连接限制的治理口径彼此分散，缺少统一的“运行时控制策略层”

代码事实：
- [src/game/gateway/gateway_service.cpp:58](/D:/Program/boost/src/game/gateway/gateway_service.cpp:58) 业务消息限频在 `GatewayService`
- [src/game/gateway/gateway_server.cpp:92](/D:/Program/boost/src/game/gateway/gateway_server.cpp:92) 连接数 / 单 IP 限制在 `GatewayServer`
- [src/game/gateway/gateway_service.cpp:86](/D:/Program/boost/src/game/gateway/gateway_service.cpp:86) rate limit 审计只打 `"session=remote_endpoint"`
- [examples/admin_demo/admin_demo_main.cpp:70](/D:/Program/boost/examples/admin_demo/admin_demo_main.cpp:70) ban / kick 等治理动作又在 example 里手工拼审计

不合理点：
- 这些都属于“运行时控制面”，但当前分散在：
  - 接入层
  - middleware
  - example 装配
  - audit 调用点
- 结果是每项能力都像单独存在，但没有统一的控制语义：
  - 谁来决定控制策略
  - 谁来负责审计字段
  - 谁来做来源归因
  - 谁来定义治理动作结果

建议：
- 维护期内至少先把“连接控制 / 消息控制 / 管理动作 / 指标导出”归为同一类运行时控制能力
- 文档应把这些能力的当前落点和成熟度拆开写，避免继续在 examples 中临时拼装后被当成框架事实

优先级：中

##### 9.30 管理与治理相关测试更多证明“端点存在”，但不足以证明“控制面安全且可依赖”

代码事实：
- [tests/unit/admin_service_test.cpp:11](/D:/Program/boost/tests/unit/admin_service_test.cpp:11) 主要验证 admin handler 注册
- [tests/integration/http_management_test.cpp:120](/D:/Program/boost/tests/integration/http_management_test.cpp:120) 主要验证 `/health`、`/metrics`、`/metrics/json`、404
- 当前未见专门覆盖：
  - admin 权限边界
  - reload callback 的副作用和失败语义
  - kick / ban 参数格式约束
  - HTTP management 来源限制
  - `/health` 与真实运行状态联动
  - 管理动作审计完整性

不合理点：
- 现有测试证明“接口可访问”，但不能证明“治理入口设计合理”
- 这会让管理能力在演示环境里显得可用，但进入长期维护时风险不可见

建议：
- 后续如果这条线进入 1.x 维护整改，应先补“治理边界测试”，而不是只补 handler/path 覆盖
- 尤其要先明确哪些能力是 demo-only，哪些才需要纳入正式回归面

优先级：中

### Gateway / Admin / Management 维护整改路线图

- 适用范围：`GatewayService`、`GatewayServer`、`AdminService`、`HttpManager` 以及相关运行时治理入口
- 目标：先把治理入口的成熟度、权限边界和装配方式收口，避免 1.x 维护期继续把演示能力包装成正式控制面

#### 建议执行顺序

##### 第一步：先划清治理入口分层

目标：
- 区分观测入口、状态查询入口和有副作用的运维入口

具体任务：
- 定义 HTTP management 目前只承担哪些职责
- 定义二进制 admin 消息目前是否仅限示例使用
- 定义哪些治理动作需要强认证或仅限内网

验收点：
- 文档能明确区分：
  - 健康检查
  - 指标导出
  - 状态查询
  - 运维动作

建议版本：`v1.1.9`

##### 第二步：冻结治理能力成熟度和接线方式

目标：
- 停止“协议号已存在、example 已演示、就被默认为正式能力”的扩散

具体任务：
- 标注 `AdminService` 当前是 demo-only 还是正式能力
- 标注 HTTP management 当前是观测端点还是完整管理面
- 标注 `GatewayServer` 当前负责的运行时能力边界

验收点：
- 文档与示例不再暗示未正式收口的治理能力已经可稳定依赖

建议版本：`v1.1.10`

##### 第三步：把权限与审计模型补成统一约束

目标：
- 在进入正式维护面前，先把治理动作的准入规则讲清楚

具体任务：
- 定义 admin 指令调用前提
- 定义治理动作审计最小字段
- 定义 reload / kick / ban 的失败语义和副作用边界

验收点：
- 任一治理动作都能回答：
  - 谁能调用
  - 调用失败怎么返回
  - 审计记录最少包含什么

建议版本：`v1.1.11`

##### 第四步：补治理边界测试

目标：
- 先证明治理入口“受控”，再继续扩能力

具体任务：
- 补 admin 权限与参数约束测试
- 补 `/health` 健康语义测试
- 补 reload / kick / ban 的行为测试
- 补治理动作审计验证

验收点：
- 管理能力的正式回归面不再只停留在 handler/path 是否存在

建议版本：`v1.2.2`

#### 最小必要动作

1. 定义治理入口分层和成熟度说明
2. 明确 `AdminService` 是否允许进入正式主链
3. 给治理动作补统一权限和审计规则
4. 把治理测试从“存在性测试”升级为“边界测试”

### Config / Watcher / Graceful Shutdown / Runtime Assembly 模块

- 分析范围：`config`、`ConfigWatcher`、`GracefulShutdown`、示例主程序装配方式，以及运行时配置、热更新、优雅停服和进程启动拼装边界
- 基线版本：`develop`
- 相关代码：
  - [include/app/config.h](/D:/Program/boost/include/app/config.h)
  - [src/app/config.cpp](/D:/Program/boost/src/app/config.cpp)
  - [include/app/config_watcher.h](/D:/Program/boost/include/app/config_watcher.h)
  - [include/app/graceful_shutdown.h](/D:/Program/boost/include/app/graceful_shutdown.h)
  - [examples/echo/server_main.cpp](/D:/Program/boost/examples/echo/server_main.cpp)
  - [examples/login_demo/login_demo_main.cpp](/D:/Program/boost/examples/login_demo/login_demo_main.cpp)
  - [examples/admin_demo/admin_demo_main.cpp](/D:/Program/boost/examples/admin_demo/admin_demo_main.cpp)

#### 现状

- `GatewayAppConfig` 当前集中承载：
  - 端口
  - 线程数
  - metrics 导出路径
  - auth provider
  - 连接限制
  - session 限制
  - heartbeat
  - TLS 配置
- `ConfigWatcher` 当前通过轮询文件 `last_write_time` 触发 reload callback
- `GracefulShutdown` 当前只负责监听 `SIGINT / SIGTERM` 并执行一个回调
- 运行时装配目前主要体现在多个 `examples/*main.cpp` 中，每个入口手工组装：
  - config 加载
  - validator 选择
  - service 注册
  - watcher 启动
  - shutdown 回调
  - player store 持久化

#### 不合理点

##### 9.31 配置面已经暴露很多能力字段，但真正可热更新、可生效的范围很窄，容易造成“配置已支持”的错觉

代码事实：
- [include/app/config.h:23](/D:/Program/boost/include/app/config.h:23) `GatewayAppConfig` 包含大量运行时参数
- [include/app/config_watcher.h:38](/D:/Program/boost/include/app/config_watcher.h:38) reload 时只是重新 `load_gateway_config()`
- [examples/echo/server_main.cpp:135](/D:/Program/boost/examples/echo/server_main.cpp:135) 热更新回调里实际只调用 `server.set_connection_limits(...)`
- [examples/login_demo/login_demo_main.cpp:126](/D:/Program/boost/examples/login_demo/login_demo_main.cpp:126) 与 [examples/admin_demo/admin_demo_main.cpp:147](/D:/Program/boost/examples/admin_demo/admin_demo_main.cpp:147) 也是同类模式

不合理点：
- 配置对象层面已经暴露：
  - auth provider
  - HTTP management port
  - metrics 路径
  - session 参数
  - heartbeat
  - TLS
- 但热更新主链实际只应用了极少数字段
- 这会让“配置可改”和“运行中可生效”两件事被混为一谈

建议：
- 文档必须区分：
  - 启动时生效参数
  - 支持热更新参数
  - 仅预留未接线参数
- 在 1.x 维护期，不建议继续把“配置项存在”直接表述成“运行时可调能力”

优先级：高

##### 9.32 `ConfigWatcher` 更像轻量文件轮询器，不是完整配置热更新框架

代码事实：
- [include/app/config_watcher.h:23](/D:/Program/boost/include/app/config_watcher.h:23) `start()` 只是记录 `last_write_time`
- [include/app/config_watcher.h:34](/D:/Program/boost/include/app/config_watcher.h:34) 到时后调用 `check_and_reload()`
- [include/app/config_watcher.h:46](/D:/Program/boost/include/app/config_watcher.h:46) 检测变更后直接 `load_gateway_config(path_)` 并回调
- 没看到：
  - 配置校验失败回滚
  - 部分字段增量应用策略
  - reload 成功/失败状态传播
  - reload 去抖/并发保护

不合理点：
- 当前 watcher 是一个可用的小工具，但不是“配置热更新能力”本身
- 它只负责告诉上层“文件变了”，真正应用逻辑完全依赖各个入口手工处理
- 继续把它对外描述成完整热更新，会误导维护判断

建议：
- 维护期应把 `ConfigWatcher` 明确定位为“文件变更触发器”
- 热更新能力成熟度应由“字段应用链是否闭环”决定，而不是由 watcher 存在与否决定

优先级：高

##### 9.33 `GracefulShutdown` 只提供信号触发回调，真正停服语义仍散落在各个 example 主程序中

代码事实：
- [include/app/graceful_shutdown.h:14](/D:/Program/boost/include/app/graceful_shutdown.h:14) `GracefulShutdown` 只有一个 `on_shutdown` 回调
- [examples/echo/server_main.cpp:142](/D:/Program/boost/examples/echo/server_main.cpp:142) 停服时手工保存 player store 再 `server.stop()`
- [examples/login_demo/login_demo_main.cpp:135](/D:/Program/boost/examples/login_demo/login_demo_main.cpp:135) 和 [examples/admin_demo/admin_demo_main.cpp:155](/D:/Program/boost/examples/admin_demo/admin_demo_main.cpp:155) 也是各自手工编排

不合理点：
- 当前所谓“优雅停服”更多是一个信号钩子，而不是统一运行时生命周期模型
- 是否保存玩家数据、先停监听还是先停会话、是否停止 watcher、是否导出最终指标，都依赖各入口自己决定
- 这会导致不同示例入口对“优雅停服”的语义并不一致

建议：
- 文档应明确 `GracefulShutdown` 当前只是停服触发器
- 若后续要正式维护停服语义，应把 shutdown sequence 先收口成统一流程说明

优先级：高

##### 9.34 运行时装配逻辑大量散落在 `examples/*main.cpp`，框架级装配约束没有真正沉淀

代码事实：
- [examples/echo/server_main.cpp:67](/D:/Program/boost/examples/echo/server_main.cpp:67) 手工选择 token validator
- [examples/login_demo/login_demo_main.cpp:85](/D:/Program/boost/examples/login_demo/login_demo_main.cpp:85) 手工注册 admin / login / gateway
- [examples/admin_demo/admin_demo_main.cpp:68](/D:/Program/boost/examples/admin_demo/admin_demo_main.cpp:68) 手工拼 kick / ban / status callback
- 不同入口虽然结构相似，但没有统一 runtime builder / bootstrap 抽象

不合理点：
- 这说明当前仓库的“运行时装配模式”主要靠复制和变体维护
- 只要接入一个新能力，就需要在多个入口各自决定：
  - 是否接线
  - 如何接线
  - 是否纳入热更新
  - 是否纳入停服流程
- 这会不断放大 example 与 core 的能力漂移

建议：
- 维护期至少要把“标准运行时装配步骤”整理成一份统一约束
- 即使不立刻抽公共 builder，也应先停止不同入口各自演化运行时语义

优先级：高

##### 9.35 配置加载层对字段做了解析，但对能力完整性的约束较弱，容易出现“字段已开但主链未接”的情况

代码事实：
- [src/app/config.cpp:178](/D:/Program/boost/src/app/config.cpp:178) 读取 `http_management_port`
- [src/app/config.cpp:190](/D:/Program/boost/src/app/config.cpp:190) 读取 metrics 导出路径
- [src/app/config.cpp:193](/D:/Program/boost/src/app/config.cpp:193) 读取 `auth_provider`
- [src/app/config.cpp:213](/D:/Program/boost/src/app/config.cpp:213) 读取 session heartbeat 参数
- [src/app/config.cpp:219](/D:/Program/boost/src/app/config.cpp:219) 读到 `tls.cert_chain_path` 就把 `config.tls.enabled = true`

不合理点：
- 配置解析层已经接收了大量能力信号，但没有同时保证这些能力一定在运行主链闭环
- 例如某些能力在配置层可见，在 example 中未必统一接线，在热更新链上更未必可应用
- 这使得配置文件容易被误读为“系统完整能力清单”

建议：
- 后续文档应把配置字段按成熟度分类，而不是按是否能被 parser 读取分类
- 配置层不应成为能力成熟度的事实源

优先级：中

##### 9.36 当前运行时装配相关测试偏向配置解析，缺少对“装配一致性”和“生命周期行为”的验证

代码事实：
- `config_test` 主要验证字段解析
- 当前未见专门覆盖：
  - watcher reload 失败路径
  - shutdown sequence 顺序
  - 不同 example 入口装配一致性
  - 热更新后真正生效的字段范围
  - 观测导出路径与停服行为配合

不合理点：
- 这意味着当前测试更证明“配置对象能读出来”
- 但无法证明“运行时装配行为在不同入口下一致且可依赖”

建议：
- 如果这条线进入 1.x 维护整改，应优先补生命周期与装配层面的回归验证
- 至少先补“watcher 触发后哪些行为真的会变”“shutdown 时哪些动作必须发生”的测试

优先级：中

### Runtime Assembly 维护整改路线图

- 适用范围：`config`、`ConfigWatcher`、`GracefulShutdown`、examples 级运行时组装
- 目标：先把运行时装配语义和生命周期语义固定下来，避免配置字段、watcher 回调和 example 装配继续各自漂移

#### 建议执行顺序

##### 第一步：明确配置字段成熟度和生效时机

目标：
- 先区分“可解析”“启动时生效”“支持热更新”“仅预留”

具体任务：
- 给 `GatewayAppConfig` 字段建立成熟度表
- 标注每个字段的应用入口
- 标注哪些字段需要重启才能生效

验收点：
- 任何配置项都能回答：
  - 谁读取
  - 何时生效
  - 是否支持热更新

建议版本：`v1.1.12`

##### 第二步：收敛标准运行时装配步骤

目标：
- 降低不同 example 主程序之间的运行时语义漂移

具体任务：
- 整理标准启动顺序
- 整理标准 watcher 应用顺序
- 整理标准 shutdown sequence

验收点：
- 不同入口的装配流程可以用同一份运行时清单描述

建议版本：`v1.1.13`

##### 第三步：把 watcher 和 shutdown 从“触发器”说明成“受控流程”

目标：
- 先把生命周期行为定义清楚，再决定是否增强实现

具体任务：
- 明确 reload 成功/失败语义
- 明确 shutdown 最小保证动作
- 明确 metrics / persistence / session stop 的收口顺序

验收点：
- 文档能描述 reload 和 shutdown 的统一流程，而不是依赖示例代码推断

建议版本：`v1.1.14`

##### 第四步：补生命周期与装配验证

目标：
- 让运行时装配进入正式维护回归面

具体任务：
- 补 watcher 生效范围测试
- 补 shutdown sequence 测试
- 补不同入口关键装配一致性检查

验收点：
- 运行时装配行为不再只能靠读 example 推断

建议版本：`v1.2.3`

#### 最小必要动作

1. 给配置字段补成熟度与生效时机表
2. 定义标准启动 / reload / shutdown 顺序
3. 明确 watcher 和 graceful shutdown 当前只是触发器还是正式生命周期组件
4. 补装配与生命周期回归验证

### Persistence / Replay / Audit 模块

- 分析范围：`IPlayerStore`、`JsonFilePlayerStore`、`SqlitePlayerStore`、`IBattleReplayStore`、`ReplayPlayer`、`audit_log`，以及它们与登录、战斗、停服流程的接线方式
- 基线版本：`develop`
- 相关代码：
  - [include/game/persistence/player_store.h](/D:/Program/boost/include/game/persistence/player_store.h)
  - [include/game/persistence/sqlite_store.h](/D:/Program/boost/include/game/persistence/sqlite_store.h)
  - [include/game/battle/replay_player.h](/D:/Program/boost/include/game/battle/replay_player.h)
  - [include/app/audit_log.h](/D:/Program/boost/include/app/audit_log.h)
  - [src/game/login/login_service.cpp](/D:/Program/boost/src/game/login/login_service.cpp)
  - [examples/echo/server_main.cpp](/D:/Program/boost/examples/echo/server_main.cpp)
  - [examples/login_demo/login_demo_main.cpp](/D:/Program/boost/examples/login_demo/login_demo_main.cpp)
  - [examples/admin_demo/admin_demo_main.cpp](/D:/Program/boost/examples/admin_demo/admin_demo_main.cpp)
  - [examples/battle_demo/battle_demo_main.cpp](/D:/Program/boost/examples/battle_demo/battle_demo_main.cpp)

#### 现状

- `IPlayerStore` / `JsonFilePlayerStore` / `SqlitePlayerStore` 当前提供玩家记录读取与保存抽象
- `IBattleReplayStore` / `JsonFileBattleReplayStore` 当前提供战斗回放二进制文本落盘抽象
- `ReplayPlayer` 当前只负责从回放存储读取 JSON 并逐帧播放
- `audit_log` 当前提供进程内全局日志文件写入宏 `AUDIT_LOG`
- 持久化和审计当前主要通过以下方式接入：
  - 登录成功/失败写审计日志
  - 限频和连接拒绝写审计日志
  - 示例程序在停服时手工保存玩家记录
  - battle demo 声明存在 replay store，但未见主战斗链实际生成 replay

#### 不合理点

##### 9.37 持久化能力存在，但“何时保存、由谁保存、保存什么”仍主要由 example 主程序手工决定

代码事实：
- [include/game/persistence/player_store.h:29](/D:/Program/boost/include/game/persistence/player_store.h:29) `IPlayerStore` 只有 `load/save`
- [examples/echo/server_main.cpp:156](/D:/Program/boost/examples/echo/server_main.cpp:156) 停服时手工遍历 session 保存玩家记录
- [examples/login_demo/login_demo_main.cpp:124](/D:/Program/boost/examples/login_demo/login_demo_main.cpp:124) 与 [examples/admin_demo/admin_demo_main.cpp:132](/D:/Program/boost/examples/admin_demo/admin_demo_main.cpp:132) 也是同类模式
- 业务主链里没有看到登录成功后、登出时、结算后自动写玩家持久化的统一流程

不合理点：
- 当前 player persistence 更像“停服前尽量落一份快照”，而不是稳定的领域持久化策略
- 是否保存、保存哪些字段、保存时机和失败处理都依赖入口程序自行决定
- 这会导致不同运行入口对“玩家数据持久化”语义并不一致

建议：
- 维护期应明确：当前玩家持久化到底是 demo 级停服备份，还是正式业务事实存储
- 在未统一保存时机前，不建议把 player store 表述成完整账号/玩家状态持久化能力

优先级：高

##### 9.38 `PlayerRecord` 与真实登录/业务状态模型耦合很弱，持久化对象并不是当前业务事实的自然投影

代码事实：
- [include/game/persistence/player_store.h:17](/D:/Program/boost/include/game/persistence/player_store.h:17) `PlayerRecord` 只有 `user_id`、`display_name`、`score`、`last_login_ts`
- [src/game/login/login_service.cpp:78](/D:/Program/boost/src/game/login/login_service.cpp:78) 登录成功后主链只写 `SessionManager`
- [examples/echo/server_main.cpp:178](/D:/Program/boost/examples/echo/server_main.cpp:178) 停服保存时临时组装 `PlayerRecord`

不合理点：
- 当前玩家持久化模型和运行时真实状态之间没有稳定映射层
- 它既不是登录态快照，也不是房间态，也不是战斗态，也不是完整玩家档案
- 这意味着 store 抽象虽然存在，但持久化内容更多是“示例级可保存字段”，不是当前业务域的明确事实源

建议：
- 应先定义 `PlayerRecord` 在 1.x 中的定位：
  - 基础资料缓存
  - 演示型停服归档
  - 还是正式玩家档案
- 在定位未定前，不应继续往这个结构里随意叠字段

优先级：高

##### 9.39 replay 读取链已经存在，但 replay 生成链在主战斗流程中没有闭环

代码事实：
- [include/game/battle/replay_player.h:23](/D:/Program/boost/include/game/battle/replay_player.h:23) `ReplayPlayer` 可以 `load(battle_id)`
- [include/game/persistence/player_store.h:75](/D:/Program/boost/include/game/persistence/player_store.h:75) `IBattleReplayStore` 提供 `save_replay/load_replay`
- [examples/battle_demo/battle_demo_main.cpp:89](/D:/Program/boost/examples/battle_demo/battle_demo_main.cpp:89) 创建了 `JsonFileBattleReplayStore`
- 但当前 `BattleManager::end_battle()` 和 `BattleService` 主链里没有看到正式 replay 序列化与保存调用

不合理点：
- 当前回放能力是“播放器已存在、存储接口已存在、示例声明已存在”，但生产 replay 的主链不存在
- 这会让 replay 非常容易被误读成“系统已支持战斗回放”

建议：
- 文档中应明确 replay 当前处于预留/演示级，不是闭环能力
- 后续若转正，应先定义 replay 生成时机、battle_id 事实源、保存失败语义，再谈播放端

优先级：高

##### 9.40 审计日志格式看似 JSON 行，但 `details` 没有做转义，严格来说不是稳定可解析格式

代码事实：
- [include/app/audit_log.h:38](/D:/Program/boost/include/app/audit_log.h:38) `entry` 通过字符串拼接生成 JSON 行
- [include/app/audit_log.h:39](/D:/Program/boost/include/app/audit_log.h:39) `details` 直接塞进 `"details":"..."`
- [src/game/login/login_service.cpp:69](/D:/Program/boost/src/game/login/login_service.cpp:69) 登录失败审计会拼接 `user=`、`reason=`
- [src/game/gateway/gateway_service.cpp:86](/D:/Program/boost/src/game/gateway/gateway_service.cpp:86) 限频审计会直接拼 `remote_endpoint`

不合理点：
- 只要 `details` 内部包含引号、反斜杠、换行等字符，日志就不再是稳定 JSON
- 审计日志当前更像“JSON 外观的字符串日志”，而不是结构化审计事件
- 后续如果想做审计检索、转储、分析，这会很快暴露问题

建议：
- 维护期内至少应把当前审计日志定位为轻量文本审计，而不是严格结构化 JSON 审计
- 如果后续要继续扩审计能力，应先收敛成真正的结构化编码方式

优先级：高

##### 9.41 审计事件模型过薄，只有 `event + details`，缺少统一字段规范

代码事实：
- [include/app/audit_log.h:37](/D:/Program/boost/include/app/audit_log.h:37) 审计只有 `ts`、`event`、`details`
- [src/game/login/login_service.cpp:94](/D:/Program/boost/src/game/login/login_service.cpp:94) 登录成功只记 `user=...`
- [src/game/gateway/gateway_server.cpp:98](/D:/Program/boost/src/game/gateway/gateway_server.cpp:98) 连接拒绝只记 `reason=max_capacity`
- [examples/admin_demo/admin_demo_main.cpp:78](/D:/Program/boost/examples/admin_demo/admin_demo_main.cpp:78) kick/ban/reload 也是自由拼接字符串

不合理点：
- 当前审计模型没有统一要求：
  - actor
  - target
  - source ip
  - request id
  - outcome
  - reason code
- 这会导致不同模块记的审计字段风格完全不同，难以形成统一运行时取证面

建议：
- 在 1.x 维护阶段，至少先定义一份最小审计字段规范
- 即使暂不改实现，也应在文档中明确当前审计仅是“自由文本事件记录”

优先级：中

##### 9.42 `SqlitePlayerStore` 和 `JsonFilePlayerStore` 都存在，但主运行时没有统一 store 选择与注入策略

代码事实：
- [include/game/persistence/sqlite_store.h:18](/D:/Program/boost/include/game/persistence/sqlite_store.h:18) 提供了 `SqlitePlayerStore`
- 示例入口普遍使用 [include/game/persistence/player_store.h:33](/D:/Program/boost/include/game/persistence/player_store.h:33) 的 `JsonFilePlayerStore`
- 配置层当前也没有专门的 player store provider 选择字段

不合理点：
- 持久化后端抽象已经开始出现，但 runtime assembly 并没有把它纳入统一配置和依赖注入模型
- 结果是 persistence backend 虽然存在多个实现，但主链并不真正具备“可选持久化后端”能力

建议：
- 文档中应把 SQLite store 标注为备选实现或预留实现，而不是默认可切换能力
- 若后续要转正，应先把 store 选择策略纳入统一 runtime assembly

优先级：中

##### 9.43 持久化、回放、审计三类横切能力彼此独立存在，但没有统一绑定到业务生命周期节点

代码事实：
- 登录链只接了审计，没有接 player store
- 停服链只接了 player store，没有统一 replay flush
- battle 链声明 replay，却没正式接审计或持久化闭环
- 管理/治理链会写审计，但没有统一与状态持久化联动

不合理点：
- 这说明横切能力当前不是围绕生命周期节点组织的，而是围绕“哪里想记录就写一点、哪里想保存就接一点”
- 长期看会导致：
  - 事件已记但状态未落
  - 状态已落但缺少审计
  - replay 能读不能产
  - shutdown 能保存但运行中不持久

建议：
- 后续维护应按生命周期节点收口：
  - 登录
  - 顶号/迁移
  - 开战
  - 结算
  - 停服
- 先定义这些节点各自应该绑定哪些横切动作

优先级：高

##### 9.44 当前测试几乎没有覆盖 persistence / replay / audit 的真实边界

代码事实：
- 未见针对 `audit_log` 的格式与字段测试
- 未见针对 `JsonFilePlayerStore` / `SqlitePlayerStore` 的主链集成测试
- 未见 replay 生成/加载闭环测试
- 现有 battle demo 和 shutdown 示例主要靠运行说明，不是自动化回归

不合理点：
- 当前这些能力一旦要进入正式维护面，回归风险很高
- 尤其是审计格式、停服保存、回放兼容性这类问题，不靠测试很难长期稳定

建议：
- 如果这条线后续进入整改，应优先补：
  - player store 基本读写与格式测试
  - audit 事件格式测试
  - replay 生成/加载闭环测试
  - shutdown 持久化行为测试

优先级：中

### Persistence / Audit 维护整改路线图

- 适用范围：player store、replay store、audit log 及其与业务/停服流程的接线
- 目标：先把这些横切能力从“散装存在”收口成“按生命周期节点定义的受控行为”

#### 建议执行顺序

##### 第一步：明确三类能力的成熟度与定位

目标：
- 区分 demo 级能力、预留能力和正式维护能力

具体任务：
- 定义 player store 的定位
- 定义 replay 当前是否只是预留能力
- 定义 audit 当前是文本审计还是结构化审计

验收点：
- 文档能明确说明这三类能力各自处于什么成熟度阶段

建议版本：`v1.1.15`

##### 第二步：按生命周期节点收口横切动作

目标：
- 把持久化、回放、审计和业务节点对齐

具体任务：
- 定义登录节点是否落审计/持久化
- 定义 battle 结算节点是否生成 replay / 审计
- 定义 shutdown 节点的最小保存集合

验收点：
- 任一横切能力都能对应到明确生命周期节点，而不是散落在 example 中

建议版本：`v1.1.16`

##### 第三步：冻结存储后端与日志格式的事实源

目标：
- 防止“接口存在”被误读为“能力已转正”

具体任务：
- 标注 JSON file / SQLite store 的实际支持级别
- 标注 audit 日志当前格式保证级别
- 标注 replay 数据格式的稳定性边界

验收点：
- 后端实现和数据格式的成熟度表述与真实接线一致

建议版本：`v1.1.17`

##### 第四步：补横切能力回归测试

目标：
- 让 persistence / replay / audit 进入正式维护回归面

具体任务：
- 补 player store 测试
- 补 audit 格式测试
- 补 replay 闭环测试
- 补 shutdown 持久化测试

验收点：
- 横切能力不再只能靠示例运行验证

建议版本：`v1.2.4`

#### 最小必要动作

1. 明确 player store / replay / audit 的成熟度与定位
2. 按登录、结算、停服三个关键节点定义横切动作
3. 冻结当前数据格式和后端支持级别说明
4. 补持久化、审计、回放的基础回归验证

## 10. `v1.x` 维护总收敛

### 10.1 当前代码实态校准

基于当前仓库结构、`git log`、`README.md`、`CHANGELOG.md` 和已分析代码，当前项目更准确的定位不是“一个边界稳定的 `v1.0.0` 成熟框架”，而是：

- 一个已经具备完整网关演示闭环的 C++20 游戏服务端样板
- 一个在 `develop` 上持续叠加能力的框架原型仓库
- 一个通过 `examples/` 展示能力拼装方式的全栈演示仓库

当前代码实态有三个非常关键的共性判断：

1. 能力暴露速度快于边界收口速度  
   协议增强、battle、admin、watcher、audit、replay、SQLite、多进程入口都已经“出现”，但不少能力仍停留在演示级、预留级或半接线级。

2. examples 的装配事实经常先于 core 的稳定契约  
   很多能力是先在 `examples/*main.cpp` 中拼起来，再被 `README` / `CHANGELOG` 写成“项目能力”。

3. 当前最该做的不是再扩功能，而是统一事实源、成熟度表述和生命周期边界  
   否则后续每一个新改动都会继续放大“文档说有 / 配置里有 / 抽象里有 / 主链不闭环”的问题。

### 10.2 总体问题模型

把前面所有分析收敛后，当前项目的核心问题其实可以压成 5 类：

#### A. 事实源不稳定

- `v1.0.0` 发布语义与 `develop` 当前能力混用
- 协议事实同时存在字符串 body 和 typed message 两套定义
- battle 状态被 `RoomManager` / `BattleManager` 双写
- replay / audit / player store 的成熟度没有统一事实源

#### B. 生命周期不闭环

- `Session` 关闭路径不统一
- room leave / disconnect / empty room / battle cleanup 没统一收口
- watcher 与 shutdown 只是触发器，不是完整 lifecycle manager
- replay 读取有了，生成与保存主链没闭环

#### C. 职责边界膨胀

- `MessageDispatcher` 同时承担协议入口、middleware、线程路由
- `GatewayServer` 同时承担接入、清理、metrics、management 装配
- `LoginService`、`BattleService` 都在承担跨域编排
- `examples/*main.cpp` 承担了过多 runtime assembly 责任

#### D. 成熟度表述过于乐观

- 配置项存在就容易被写成“支持该能力”
- 抽象接口存在就容易被写成“能力已落地”
- example 中手工装配成功就容易被写成“框架正式能力”
- README / CHANGELOG 对一些预留能力的描述明显超前于主链成熟度

#### E. 回归保护不足

- 很多测试验证“能跑通”
- 但较少验证：
  - 单一事实源
  - 生命周期顺序
  - 管理权限边界
  - replay / audit / shutdown 闭环
  - example 与 core 语义一致性

### 10.3 维护总原则

后续 `v1.x` 维护不建议再按“模块兴趣”推进，而应统一遵守下面 4 个原则：

1. 先收口事实源，再改实现  
   文档、协议、状态、成熟度表述不统一时，不要先扩能力。

2. 先收口生命周期，再做增强  
   close / reload / shutdown / cleanup / replay-save 这类链路不闭环时，不要先追求 feature completeness。

3. 先收口装配方式，再谈多入口扩展  
   当 example 仍是主要装配事实源时，继续扩更多 demo 只会放大漂移。

4. 先补边界测试，再做结构升级  
   没有边界测试保护时，不适合直接推进 typed protocol、拆服、battle 增强、治理转正。

### 10.4 建议执行批次

结合前面所有路线图，建议把 `v1.x` 维护压成 4 个批次，而不是把 `v1.1.1` 到 `v1.2.4` 视作彼此独立的散点版本。

#### 批次 A：基线与事实源收口

建议对应版本：

- `v1.1.1`
- `v1.1.5`
- `v1.1.9`
- `v1.1.12`
- `v1.1.15`

这一批次的共同目标：

- 校准 `v1.0.0` / `develop` 的能力边界
- 冻结协议、业务、治理、配置、横切能力的成熟度表述
- 明确哪些是正式能力，哪些是预留/演示能力

这一批次必须优先完成，因为：

- 如果事实源不先统一，后面任何实现整改都会反复返工
- 如果成熟度不先校准，维护排期会被文档误导

#### 批次 B：主链生命周期与边界收口

建议对应版本：

- `v1.1.2`
- `v1.1.3`
- `v1.1.4`
- `v1.1.6`
- `v1.1.7`
- `v1.1.8`
- `v1.1.10`
- `v1.1.11`
- `v1.1.13`
- `v1.1.14`
- `v1.1.16`
- `v1.1.17`

这一批次的共同目标：

- 统一 `Session`、room、battle、gateway、shutdown、reload 的生命周期收口
- 统一业务协议事实源和治理入口事实源
- 统一 battle 单一事实源、跨域编排链和横切生命周期节点

这一批次是整个 1.x 维护的主工作量。

#### 批次 C：边界测试与回归面加固

建议对应版本：

- `v1.2.1`
- `v1.2.2`
- `v1.2.3`
- `v1.2.4`

这一批次的共同目标：

- 把协议、业务、治理、装配、持久化五条主线的关键边界都纳入回归
- 让后续增强建立在稳定维护面之上

这一批次不应被提前跳过，否则结构升级阶段会缺乏保护。

#### 批次 D：结构升级决策

建议对应版本：

- `v1.2.0`

这一批次的共同目标：

- 在前面三批完成后，再决定是否正式推进：
  - typed message 接管主链
  - internal bus / backend router 转正
  - battle 回放/观战闭环
  - 更完整的治理与装配抽象

当前不建议把这批内容提前到前面做。

### 10.5 不建议并行推进的改造

下面这些改造不建议在没有依赖收口前并行做：

#### 不建议 1：一边保留字符串协议漂移，一边推进 `message_serializer`

原因：
- 协议事实源未统一时，typed message 只会成为第三套语义来源。

#### 不建议 2：一边保留 room/battle 双写，一边增强 replay / spectator /结算

原因：
- battle 单一事实源未解决前，所有 battle 增强都建立在模糊边界上。

#### 不建议 3：一边让 example 自由装配，一边宣称支持热更新 / 优雅停服 / 多后端持久化

原因：
- runtime assembly 仍散落时，能力成熟度无法稳定。

#### 不建议 4：一边没有 admin 权限模型，一边继续扩管理命令

原因：
- 治理面会先扩暴露面，再补控制面，风险过高。

### 10.6 当前最建议优先做的 12 个维护动作

如果要把整份文档压成一版可执行的近期维护面，我建议优先顺序如下：

1. 修正文档中 `v1.0.0` 与 `develop` 能力边界的混用
2. 冻结当前真实协议表，明确字符串协议仍是 1.x 主契约
3. 统一 `Session` 主动关闭与异常关闭路径
4. 固定协议增强顺序，明确分片当前是否正式启用
5. 前置 ingress 白名单与限频，不再把入口治理放到业务线程池后
6. 明确 `battle_started` 单一事实源，停止 `RoomManager` / `BattleManager` 双写扩散
7. 收敛“重复登录恢复链”和“空房 battle 清理链”
8. 明确 `AdminService` 与 HTTP management 的成熟度和权限边界
9. 给配置字段补“启动生效 / 热更新生效 / 仅预留”三类标记
10. 明确 player store / replay / audit 当前的正式定位
11. 先补业务、治理、装配、横切能力的边界测试
12. 再决定是否推进 typed protocol、internal bus、battle replay 闭环

### 10.7 版本总览建议

从维护排期角度看，当前建议版本可以按下面方式理解：

- `v1.1.1 - v1.1.4`
  协议/网关主链收口

- `v1.1.5 - v1.1.8`
  业务线收口

- `v1.1.9 - v1.1.11`
  治理线收口

- `v1.1.12 - v1.1.14`
  运行时装配线收口

- `v1.1.15 - v1.1.17`
  持久化/审计/回放横切线收口

- `v1.2.0`
  协议与内部结构升级决策点

- `v1.2.1 - v1.2.4`
  各条主线回归面加固

### 10.8 总结判断

当前项目最真实的维护目标，不是“继续证明它已经支持很多能力”，而是把现有能力从下面这种状态：

- 代码里有
- example 里能拼
- 文档里写得很全

收口成下面这种状态：

- 事实源唯一
- 生命周期闭环
- 成熟度表述准确
- 装配方式一致
- 回归边界稳定

在这件事完成之前，不建议把 `v1.x` 的重点继续放在新增能力上。

## 11. 维护任务总表

### 11.1 使用说明

本节把前面所有分析压成可执行任务视图。

字段说明：

- `优先级`
  - `P0`：不先处理会持续放大后续维护成本
  - `P1`：应尽快进入 1.x 主维护面
  - `P2`：在主链收口后推进
- `并行性`
  - `可并行`：可与同批次其他低耦合任务并行
  - `谨慎并行`：可并行，但要先确认事实源或接口约束
  - `不建议并行`：应等待依赖项完成

### 11.2 任务总览表

| ID | 任务 | 优先级 | 建议版本 | 依赖 | 影响模块 | 并行性 |
|---|---|---|---|---|---|---|
| T01 | 校准 `v1.0.0` / `develop` 能力边界与文档表述 | P0 | `v1.1.1` | 无 | `README` `CHANGELOG` `docs/*` | 可并行 |
| T02 | 冻结当前真实协议表，明确 1.x 主协议事实源 | P0 | `v1.1.1` `v1.1.6` | T01 | `protocol` `message_types` `serializer` `login/room/battle service` | 不建议并行 |
| T03 | 统一 `Session` 主动关闭 / 异常关闭收口路径 | P0 | `v1.1.2` | T01 | `Session` `GatewayServer` `SessionManager` `RoomManager` `BattleManager` | 谨慎并行 |
| T04 | 固定协议增强顺序，明确分片当前是否正式启用 | P0 | `v1.1.2` | T02 | `packet_codec` `packet_fragment` `packet_compressor` `Session` `InternalBus` | 不建议并行 |
| T05 | 前置 ingress 鉴权白名单与限频，收口入口治理 | P1 | `v1.1.3` | T03 | `GatewayService` `MessageDispatcher` `GatewayServer` | 谨慎并行 |
| T06 | 明确 `battle_started` 单一事实源，停止 room/battle 双写扩散 | P0 | `v1.1.4` `v1.1.8` | T03 T04 | `RoomManager` `BattleManager` `BattleService` `GatewayServer` | 不建议并行 |
| T07 | 收敛重复登录恢复链 | P1 | `v1.1.7` | T03 T06 | `LoginService` `SessionManager` `RoomManager` `PushService` | 不建议并行 |
| T08 | 收敛空房 battle 清理链 | P1 | `v1.1.7` | T03 T06 | `RoomService` `GatewayServer` `BattleManager` | 不建议并行 |
| T09 | 收紧房间态与战斗态边界，明确 `transfer_session()` 定位 | P1 | `v1.1.8` | T06 T07 T08 | `RoomManager` `RoomService` `BattleManager` | 谨慎并行 |
| T10 | 校准治理入口分层，明确 HTTP / admin 命令成熟度 | P1 | `v1.1.9` | T01 | `AdminService` `HttpManager` `GatewayServer` `docs` | 可并行 |
| T11 | 明确 admin 权限模型与审计最小规则 | P1 | `v1.1.11` | T10 | `AdminService` `GatewayService` `audit_log` | 谨慎并行 |
| T12 | 给配置字段补“启动生效 / 热更新生效 / 仅预留”标记 | P1 | `v1.1.12` | T01 | `config` `ConfigWatcher` `docs` `examples` | 可并行 |
| T13 | 收敛标准启动 / reload / shutdown 顺序 | P1 | `v1.1.13` `v1.1.14` | T03 T12 | `examples/*main.cpp` `ConfigWatcher` `GracefulShutdown` `GatewayServer` | 不建议并行 |
| T14 | 明确 player store / replay / audit 当前定位与成熟度 | P1 | `v1.1.15` | T01 | `persistence` `ReplayPlayer` `audit_log` `docs` | 可并行 |
| T15 | 按登录 / 结算 / 停服节点收口横切动作 | P1 | `v1.1.16` | T07 T08 T13 T14 | `login` `battle` `shutdown` `persistence` `audit` | 不建议并行 |
| T16 | 冻结存储后端和审计/回放数据格式支持级别 | P2 | `v1.1.17` | T14 T15 | `player_store` `sqlite_store` `replay` `audit_log` | 谨慎并行 |
| T17 | 业务边界测试加固 | P1 | `v1.2.1` | T06 T07 T08 T09 | `login` `room` `battle` tests | 谨慎并行 |
| T18 | 治理边界测试加固 | P2 | `v1.2.2` | T10 T11 | `admin` `http management` tests | 可并行 |
| T19 | 生命周期与装配测试加固 | P2 | `v1.2.3` | T12 T13 | `config` `watcher` `shutdown` `examples` tests | 可并行 |
| T20 | 持久化 / 审计 / 回放测试加固 | P2 | `v1.2.4` | T14 T15 T16 | `persistence` `audit` `replay` tests | 可并行 |
| T21 | 评估是否正式推进 typed protocol / internal bus / battle replay 闭环 | P2 | `v1.2.0` | T02 T04 T06 T17 T20 | `protocol` `internal_bus` `battle` | 不建议并行 |

### 11.3 建议执行批次

#### 批次 A：基线校准

包含任务：

- T01
- T02
- T10
- T12
- T14

说明：
- 这批任务的核心不是改复杂实现，而是统一事实源和成熟度表述
- 完成后，后续维护才有稳定参照面

#### 批次 B：主链收口

包含任务：

- T03
- T04
- T05
- T06
- T07
- T08
- T09
- T11
- T13
- T15
- T16

说明：
- 这是 1.x 维护主工作量所在
- 这里面不建议“全开并行”，尤其 battle、shutdown、协议增强链三组任务耦合较深

#### 批次 C：回归加固

包含任务：

- T17
- T18
- T19
- T20

说明：
- 这批任务应建立在前面边界和流程已经冻结的前提上
- 如果边界还在变，不建议过早补大量回归

#### 批次 D：结构升级决策

包含任务：

- T21

说明：
- 这是决策点，不是默认必须做的实施项
- 只有在前面维护收口完成后，才适合讨论 typed protocol、internal bus 转正、battle replay 闭环

### 11.4 近期最小执行集

如果当前只准备投入一轮较小维护，我建议先只做下面 8 个任务：

1. T01：校准版本与能力边界文档
2. T02：冻结真实协议表
3. T03：统一 `Session` 关闭收口
4. T04：固定协议增强顺序，明确分片状态
5. T06：明确 `battle_started` 单一事实源
6. T10：校准治理入口成熟度
7. T12：标记配置字段生效时机
8. T14：明确 player store / replay / audit 定位

这 8 个任务的价值是：

- 不要求立刻做大规模重构
- 但能先把“事实源混乱”和“成熟度误判”这两个最大问题压下去
- 也能为下一轮真实整改建立稳定前提

### 11.5 中期执行集

如果第一轮维护完成，第二轮建议进入下面 7 个任务：

1. T05：入口治理前置
2. T07：重复登录恢复链收口
3. T08：空房 battle 清理链收口
4. T09：房间态 / 战斗态边界收紧
5. T11：admin 权限与审计规则收口
6. T13：标准启动 / reload / shutdown 顺序收口
7. T15：横切动作按生命周期节点收口

这轮结束后，项目的“主链路维护性”会明显改善。

### 11.6 可以明确延后的事项

下面这些事当前可以明确后置，不建议抢到前面：

- 正式切换到 typed message 主链
- 正式推进 internal bus / backend router 转正
- 完整 battle replay 生成与播放闭环
- 完整 spectator 能力闭环
- 更复杂的 admin / management 能力扩展
- 多持久化后端统一配置切换

原因不是这些方向不重要，而是它们都强依赖前面已经识别出的基础边界问题先被收口。

### 11.7 最终建议

对当前仓库最合理的 1.x 维护策略，不是继续“证明功能很多”，而是按任务表先把下面五件事稳定下来：

1. 协议和文档只有一个事实源
2. 会话、房间、战斗、停服的生命周期链闭环
3. gateway / admin / management 的治理边界可解释
4. runtime assembly 不再主要依赖 example 手工拼装
5. persistence / audit / replay 进入明确生命周期节点

只要这五件事收住，后续无论你是继续维护 1.x，还是准备 2.0 演进，代码和文档都会进入一个可控状态。

##### 9.12 房间离开与战斗清理的协调知识散落在 `RoomService` / `GatewayServer` 两处

代码事实：

- [src/game/room/room_service.cpp:118](/D:/Program/boost/src/game/room/room_service.cpp:118) 主动离房且房间人数为 0 时，调用 `battle_manager_.remove_room(...)`
- [src/game/gateway/gateway_server.cpp:152](/D:/Program/boost/src/game/gateway/gateway_server.cpp:152) 会话关闭时先删房间成员，再视人数决定 `battle_manager_.remove_room(...)`

不合理点：

- “房间没人时顺带清理战斗”这个知识不只存在一个地方
- 这属于典型的跨域清理逻辑扩散
- 后续如果加入观战、战斗结算延迟、战斗回放保留期，这种散落清理会越来越难收口

建议：

- 维护期内应先把“空房是否立即清理战斗”定义成统一规则
- 再把清理编排集中到一处，不要继续分散

优先级：高

##### 9.13 `RoomManager` 的 COW 广播快照能力和 `RoomService` 的实际广播路径没有完全统一

代码事实：

- [include/game/room/room_manager.h:81](/D:/Program/boost/include/game/room/room_manager.h:81) 已提供 `broadcast_to_room()` COW 快照接口
- [src/game/room/room_service.cpp:203](/D:/Program/boost/src/game/room/room_service.cpp:203) 实际广播状态时仍自己先取 `room_snapshot()` 再循环成员发送
- `room_demo` 还单独示例了 `broadcast_to_room()` 的使用

不合理点：

- 说明房间广播优化能力和业务主广播路径没有真正收敛到一套实现
- 当前存在：
  - manager 提供的广播帮助能力
  - service 自己实现的广播路径
  两套思路并存

建议：

- 要么统一所有房间广播都走 `RoomManager` 的快照能力
- 要么删除这类“存在但主链不用”的辅助接口，避免持续制造错觉

优先级：中

##### 9.14 房间模块测试覆盖基础流程，但对迁移/广播/战斗耦合边界覆盖不足

代码事实：

- [tests/unit/room_manager_test.cpp](/D:/Program/boost/tests/unit/room_manager_test.cpp) 主要覆盖创建/加入/房主切换/ready
- 当前没看到专门覆盖：
  - `transfer_session()`
  - `room_state` body 构造
  - battle 标记一致性
  - 空房清理战斗
  - 多成员广播排除自身

不合理点：

- 现有测试证明房间基本数据结构可用
- 但不能证明房间模块当前最复杂的边界是稳定的

建议：

- 如果后续进入房间模块整改，应优先补这些“边界测试”，而不是只补 happy path

优先级：中
