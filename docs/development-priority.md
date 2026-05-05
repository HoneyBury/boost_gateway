# 开发优先级看板

## 1. 维护规则

- 每完成一项核心任务，同步更新状态
- 每次调整技术方向或优先级顺序，同步更新原因
- 状态约定：`todo` `doing` `done` `blocked`

## 2. 已完成 (P0–P7)

### P0 — 网络层基础

| 编号 | 条目 | 状态 |
|---|---|---|
| 2.1 | `SessionManager / RoomManager / BattleManager` 状态拆分 | done |
| 2.2 | 协议统一化：`request_id + error_code` | done |
| 2.3 | 网关限频中间层 | done |

### P1 — 业务骨架

| 编号 | 条目 | 状态 |
|---|---|---|
| 2.4 | 完整登录链路：token 校验 + 登录上下文 | done |
| 2.5 | 房间系统独立化：创建/加入/房主/准备/广播 | done |
| 2.6 | 战斗系统独立化：上下文/输入路由/状态广播 | done |

### P2 — 工程增强

| 编号 | 条目 | 状态 |
|---|---|---|
| 2.7 | 广播与推送链路：PushService 统一推送 | done |
| 2.8 | 重连恢复与踢线：旧连接替换/会话恢复 | done |
| 2.9 | 配置系统：key=value + JSON 双格式 | done |

### P3 — 可观测性与后端

| 编号 | 条目 | 状态 |
|---|---|---|
| 2.10 | 可观测性：Prometheus + JSON 指标导出 | done |
| 2.11 | 压测体系：6 种场景 + JSON 配置 | done |
| 2.12 | 后端持久化：JsonFileTokenValidator + HTTP 鉴权 | done |

### P4 — 管理端点与部署

| 编号 | 条目 | 状态 |
|---|---|---|
| 2.13 | HTTP 管理端点：/health + /metrics | done |
| 2.14 | 压测场景扩展：broadcast_storm / malicious / battle | done |
| 2.15 | 远程鉴权客户端：HttpTokenValidator | done |
| 2.16 | Docker + CI：Dockerfile / docker-compose / CI 扩展 | done |

### P5 — 基础设施深化

| 编号 | 条目 | 状态 |
|---|---|---|
| 2.17 | 结构化序列化：18 种 message types + binary serializer | done |
| 2.18 | 链路追踪 ID：trace_id 贯穿全链路 | done |
| 2.19 | 协议 flags 字节：压缩/加密标记位 | done |
| 2.20 | Buffer 池 + 对象池：BufferPool / ObjectPool | done |
| 2.21 | 批量发包：Session::send_batch() | done |
| 2.22 | 慢连接检测：写队列积压 > 50% 告警 | done |

### P6 — 生产加固

| 编号 | 条目 | 状态 |
|---|---|---|
| 2.23 | Per-second 速率指标：6 种 _rate gauge | done |
| 2.24 | 零拷贝：BufferPool 集成到 Session 读包路径 | done |
| 2.25 | 崩溃转储：SEH/signal handler + crash report | done |
| 2.26 | 服务拆分路由：ServiceRouter + ServiceId | done |
| 2.27 | 战斗帧同步：frame_number + advance_frame | done |

### P7 — 生产安全

| 编号 | 条目 | 状态 |
|---|---|---|
| 2.28 | 连接限制：max_connections + per_ip_connection_limit | done |
| 2.29 | 线程池拆分：按消息 ID 范围路由到专用池 | done |
| 2.30 | 战斗结算：end_battle() + winner/scores | done |
| 2.31 | Handler 注册工具：HandlerRegistry | done |

---

## 3. 下一阶段候选 (P8+)

### 扩展方向

| 优先级 | 条目 | 说明 |
|---|---|---|
| **P8-1** | **多进程拆分实战** | 当前 `ServiceRouter` 已铺垫路由层。下一步：独立的 `login_server` / `room_server` / `battle_server` 可执行文件，通过内部 TCP 协议互通 |
| **P8-2** | **protobuf / flatbuffers 评估** | 当前协议体是自定义二进制格式。评估引入 protobuf 做跨语言（客户端 C#/Java/JS）协作的成本和收益 |
| **P8-3** | **内部消息总线** | Gateway 与后端服务间的高性能消息传输层。可基于 Boost.Asio 实现内部 RPC 或采用现成方案 |
| **P8-4** | **服务发现与健康检查** | 多进程下的服务注册、心跳保活、故障转移。可利用现有的 `/health` 端点 |
| **P8-5** | **持久化层** | 战斗回放异步落盘、玩家数据存储接口、可选 SQLite/Redis 后端接入 |
| **P8-6** | **战斗回放系统** | 基于已记录的 `InputEvent[]` 实现战斗完整回放 |

### 优化方向

| 优先级 | 条目 | 说明 |
|---|---|---|
| **P8-7** | **广播路径锁优化** | 当前广播路径持锁遍历 session 列表。可改为 RCU 或 copy-on-write 快照减少锁竞争 |
| **P8-8** | **连接绑定 executor** | 将同一连接的所有回调固定到同一 executor，消除 strand 开销 |
| **P8-9** | **写队列反压** | 当写队列超过阈值时主动降速收包，避免内存无限增长 |
| **P8-10** | **协议压缩** | 利用 flags 字节的 `kCompressed` 位，对大包启用 zlib/zstd 压缩 |
| **P8-11** | **大包消息分片** | 超过阈值的大包自动拆分为多个分片传输，避免阻塞小包 |
| **P8-12** | **连接预热与慢启动** | 新连接阶梯式放宽限频阈值，避免误伤正常客户端 |

### 测试与质量

| 优先级 | 条目 | 说明 |
|---|---|---|
| **P8-13** | **混沌测试** | 随机断连、乱序包、重复包、超长延迟场景 |
| **P8-14** | **长稳测试** | 72h 持续压测，监控内存泄漏和性能衰减 |
| **P8-15** | **模糊测试** | 对协议编解码做 fuzz testing |

## 4. 推荐路线

```
短期 (P8):  多进程拆分 → 内部消息总线 → 服务发现
中期 (P9):  广播锁优化 → 写队列反压 → 协议压缩
长期 (P10): 持久化层 → 战斗回放 → 混沌测试
```

## 5. 最近一次更新

- 日期：`2026-05-05`
- 说明：P0–P7 全部完成。项目已具备从网络层到业务层、从可观测性到部署的完整能力。下一阶段聚焦多进程架构拆分和性能优化。
