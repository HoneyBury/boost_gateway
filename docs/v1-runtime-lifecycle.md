# v1.x 标准运行时装配与生命周期顺序（v1.1.13 / T13）

## 1. 文档定位

- **任务**：落实 **`development-optimization.md`**「Runtime Assembly」路线图**第二步**：用**同一份清单**描述各 showcase / 集成样例入口的 **启动**、**ConfigWatcher reload**、**SIGINT/SIGTERM shutdown** 顺序，降低 `examples/*main.cpp` 之间的语义漂移。
- **范围**：以 **`examples/echo/server_main.cpp`** 为**参考骨架**；其它入口允许缺省子步骤（例如无 `ConfigWatcher`），但**不应**在已有步骤上与清单**反向**（除非文档明示为实验例外）。
- **`v1.1.14`（T13 后半）**：reload **成功/失败**语义、shutdown **最小保证动作**、metrics / persistence 与 session stop 的进一步收口 — 见 **`docs/v1-maturity-matrix.md` §5.2–§5.3** 与矩阵 §10。

---

## 2. 标准启动顺序（bootstrap）

下列顺序适用于「单进程、`GatewayServer` + `MessageDispatcher` + 业务线程池」类入口：

1. **日志 / 崩溃处理**：`logging::init`、`crash::install_crash_handler`（若启用）。
2. **加载配置**：`load_gateway_config(path)`；CLI 覆盖监听端口等（若有）。
3. **`SessionOptions`**：由配置填充 `max_packet_size`、`max_pending_write_bytes`、心跳字段等。
4. **运行时内核**：`io_context`、`thread_pool(business_threads)`、`MessageDispatcher`。
5. **领域状态**：`SessionManager`、`RoomManager`、`BattleManager`（按需）、`GatewayMetrics`、`PushService` 等；设置 **查询回调**（如 `set_battle_active_query`）。
6. **鉴权**：按 `auth_provider` 构造 `TokenValidator`（失败则进程退出 —— 与 echo 一致）。
7. **Service 注册**：`GatewayService`、`LoginService`、`RoomService`、`BattleService`、demo handler、`AdminService`（若启用）等 **`register_handlers(dispatcher)`**。
8. **`GatewayServer` 构造**：传入 `io_context`、`dispatcher`、managers、`port`、`http_management_port`、`SessionOptions`、`metrics_log_interval`（及可选 metrics 导出路径）。
9. **`set_connection_limits(max_connections, per_ip_connection_limit)`**。
10. **`server.start()`**：监听、HTTP L2（若端口非 0）、metrics 定时器、`do_accept`。
11. **可选持久化后端构造**（如 `JsonFilePlayerStore`）— 须在 shutdown 前可用。
12. **`ConfigWatcher`**：构造并 **`start()`**（若启用热更新）。
13. **`GracefulShutdown`**：构造并 **`start()`**，回调内逻辑见 **§4**。
14. **IO worker 线程**：`io_threads` 个线程执行 **`io_context.run()`**。
15. **`business_pool.join()`**（主线程在 IO 线程全部退出后汇合业务池）。

> **缺省项**：`room_demo` / `battle_demo` 等若无 **§12–§13**，视为「裁剪版骨架」，但仍应遵守 **§8–§10** 之前的核心顺序。

---

## 3. 标准 reload 顺序（`ConfigWatcher` 回调）

当前 **`ConfigWatcher`** 仅是 **文件 `last_write_time` 变更触发器**（矩阵 §5.2），**不**承诺校验、回滚或去抖。

**Showcase 最小契约**（与 **`docs/v1-config-maturity.md` §3** 一致）：

1. `load_gateway_config` 已在 **`ConfigWatcher` 内部**完成，回调收到 **`GatewayAppConfig new_cfg`**。
2. 回调内 **至少**应 **`server.set_connection_limits(new_cfg.max_connections, new_cfg.per_ip_connection_limit)`** —— `echo_server`、`login_demo`、`admin_demo` 均为此模式。
3. **禁止**在文档或运维手册中暗示「改任意 JSON 字段都会在 reload 回调中生效」— 以 **`v1-config-maturity.md` §4** 表格为准。

---

## 4. 标准 shutdown 顺序（`GracefulShutdown` 回调）

**目标**：信号触发后，尽快 **停止配置轮询**、**持久化（若有）**、**关闭接入与会话**，并 **`io_context.stop()`** 使 **`run()`** 退出 —— 否则 IO 线程会因 **定时器 / `signal_set`** 等未完成的工作一直阻塞。

推荐顺序（**showcase 对齐实现**）：

1. **`watcher.stop()`** —— 取消 reload 定时器（若 §12 已启用）。
2. **审计 / 业务钩子**：如 `AUDIT_LOG("shutdown", …)`。
3. **可选持久化**：遍历会话写 `PlayerStore` 等（与具体 demo 一致）。
4. **`server.stop()`** —— `acceptor.close`、取消 metrics 定时器、停 HTTP、`Session::stop` 全员。
5. **`io_context.stop()`** —— 唤醒所有 **`io_context.run()`**，使 **§14** worker **join** 返回。

主线程收尾（信号路径之外仍可能走到进程退出）：

6. **`business_pool.join()`**。
7. **`watcher.stop()`**（幂等：若 §1 已调用，再次 `cancel` 无害）。

> **`GracefulShutdown` 本体**仍只是 **SIGINT/SIGTERM → 回调**（矩阵 §5.3）；**不**等同于完整进程级生命周期框架 — **`v1.1.14`** 再继续细化「最小保证动作」与失败语义。

---

## 5. 入口对照（维护用）

| 入口 | `ConfigWatcher` | `GracefulShutdown` | shutdown 内 `io.stop()` | 备注 |
|------|-----------------|-------------------|-------------------------|------|
| `examples/echo/server_main.cpp` | ✅ | ✅ | ✅（**v1.1.13**） | 推荐参考 |
| `examples/login_demo/login_demo_main.cpp` | ✅ | ✅ | ✅（**v1.1.13**） | |
| `examples/admin_demo/admin_demo_main.cpp` | ✅ | ✅ | ✅（**v1.1.13**） | |
| `examples/room_demo` / `battle_demo` | ❌ | ❌ | ❌ | 裁剪骨架；停服依赖进程退出 |

---

## 6. 与 roadmap 的关系

| 版本 | 内容 |
|------|------|
| **`v1.1.13`**（本文） | T13：**清单文档** + showcase shutdown **`io_context.stop()`** 对齐 |
| **`v1.1.14`** | T13 后半：**受控流程**（reload 成败、shutdown 最小保证等） |
