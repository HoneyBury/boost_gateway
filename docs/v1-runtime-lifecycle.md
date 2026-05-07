# v1.x 标准运行时装配与生命周期顺序（v1.1.13–v1.1.14 / T13）

## 1. 文档定位

- **任务**：落实 **`development-optimization.md`**「Runtime Assembly」路线图 **第二步 + 第三步（文档化）**：用**同一份清单**描述 **启动**、**reload**、**shutdown**；并写明 **成功/失败**与 **最小保证**（**§6–§7**）。
- **范围**：以 **`examples/echo/server_main.cpp`** 为**参考骨架**；其它入口允许缺省子步骤（例如无 `ConfigWatcher`），但**不应**在已有步骤上与清单**反向**（除非文档明示为实验例外）。
- **`v1.1.13`**：**步骤顺序** + showcase **`io_context.stop()`**（§2–§5；§5 为入口对照）。
- **`v1.1.14`**：**受控语义** — **`ConfigWatcher`** 仅在 **`try_load_gateway_config` 成功**时调用回调（**§6**）；shutdown **最小保证**与「仍为预留的能力」分界（**§7**）。
- **横切动作**：**应收口规范**（节点 **N1–N7**）见 **`docs/v1-cross-cutting-lifecycle-binding.md`**（**v1.1.16** / T15）；**当前接线事实**见 **`docs/v1-cross-cutting-capabilities.md`**（**v1.1.15** / T14）；**数据格式与支持级别**见 **`docs/v1-cross-cutting-data-formats.md`**（**v1.1.17** / T16）。

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

当前 **`ConfigWatcher`** 仍是 **文件 `last_write_time` 变更触发器**（矩阵 §5.2），**不**承诺去抖、并发保护或「坏配置回滚到上一份合法快照」。

**Showcase 最小契约**（与 **`docs/v1-config-maturity.md` §3** 一致）：

1.  **`ConfigWatcher`** 内部使用 **`try_load_gateway_config(path)`**（**`v1.1.14`**）：**仅当**磁盘文件 **可读且解析成功**时才有 **`GatewayAppConfig new_cfg`**；失败则 **WARN**、**不调用** reload 回调（避免历史上 **`load_gateway_config` 失败仍塞默认配置**误触发 **`set_connection_limits`**）。
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

> **`GracefulShutdown` 本体**仍只是 **SIGINT/SIGTERM → 回调**（矩阵 §5.3）；完整进程级编排器仍 **reserved**。

---

## 5. 入口对照（维护用）

| 入口 | `ConfigWatcher` | `GracefulShutdown` | shutdown 内 `io.stop()` | 备注 |
|------|-----------------|-------------------|-------------------------|------|
| `examples/echo/server_main.cpp` | ✅ | ✅ | ✅（**v1.1.13**） | 推荐参考 |
| `examples/login_demo/login_demo_main.cpp` | ✅ | ✅ | ✅（**v1.1.13**） | |
| `examples/admin_demo/admin_demo_main.cpp` | ✅ | ✅ | ✅（**v1.1.13**） | |
| `examples/room_demo` / `battle_demo` | ❌ | ❌ | ❌ | 裁剪骨架；停服依赖进程退出 |

---

## 6. 受控 reload 语义（`v1.1.14`）

| 概念 | 现行语义 |
|------|----------|
| **触发** | `mtime` 较 **`last_write_`** 变大 → 先 **推进水印** `last_write_ = current`，再尝试加载（避免对同一文件版本重复触发）。 |
| **成功** | **`try_load_gateway_config`** 返回 **`optional` 有值** → 调用 **`on_reload_(cfg)`**；showcase 在此应用连接限额等。 |
| **失败** | **`nullopt`**（文件瞬时不可读、JSON 解析异常、`ConfigStore::load` 失败等）→ **仅日志**，**不调用**回调；进程内 **`GatewayServer` / 会话状态**保持 **上一次成功 reload 或启动时**的配置衍生行为。 |
| **仍非正式热更新框架** | 无 **reload 事务**、无 **活跃配置版本号**、无 **字段级 diff** — 矩阵 §5.2 **reserved** 项照旧。 |

---

## 7. Shutdown 最小保证 vs 仍预留（`v1.1.14`）

下列针对 **带 `GracefulShutdown` + `ConfigWatcher` + `GatewayServer` 的 showcase**（**§5** 表中 ✅ 行），描述「文档承诺的最小集合」与「仍未统一收口」的分界。

### 7.1 showcase 最小保证（SIGINT/SIGTERM 路径）

在 **`GracefulShutdown` 回调**内，**预期**依次发生：

1. **`watcher.stop()`** —— 停止下一轮 reload 轮询。
2. **[可选] `AUDIT_LOG("shutdown", …)`**。
3. **[可选] 演示型持久化**（如 `JsonFilePlayerStore::save`）— **best-effort**，失败未必打断后续步骤（由各 demo 自行决定）。
4. **`server.stop()`** —— **`acceptor.close`**；**取消**周期 metrics 定时器；**停止** `HttpManager`；对 **`SessionManager::all_sessions()`** 逐个 **`Session::stop()`**（进而触发房间清理 / `clear_battle_if_room_empty` 等既有闭包）。
5. **`io_context.stop()`** —— 所有 **`io_context.run()`** 退出，主线程得以 **`join` IO worker**。

主线程末尾：**`business_pool.join()`**；**`watcher.stop()`** 幂等。

### 7.2 仍为 reserved / 未承诺

| 项 | 说明 |
|----|------|
| **最终 metrics 文件落盘 / flush 顺序** | `GatewayServer::stop()` **取消** `metrics_timer_`，**不**保证周期任务已在停服前再跑一次；与文件导出路径相关的「最后一次快照」**无统一保证**。 |
| **业务线程池排空顺序** | **`business_pool.join()`** 等待已投递任务跑完，但 **与** `Session::stop` **的交叉顺序**依赖 Asio post 与 handler 实现，**无**框架级「先停接包再 join 池」的硬约束。 |
| **第二路信号 / 重入** | 未定义二次 SIGINT 行为；应假定 **仅处理一次** 干净路径。 |
| **无 `GracefulShutdown` 的 demo** | **`room_demo` / `battle_demo`** 等 — **不适用** §7.1。 |

---

## 8. 与 roadmap 的关系

| 版本 | 内容 |
|------|------|
| **`v1.1.13`** | T13：**清单** + showcase **`io_context.stop()`** |
| **`v1.1.14`**（§6–§7） | T13 后半：**reload 成败语义**（**`try_load_gateway_config`**）+ shutdown **最小保证 / 预留分界** |
| **`v1.2.3`（T19）** | **已完成**：生命周期与装配自动化回归（`lifecycle_assembly_test`） |
