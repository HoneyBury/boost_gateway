# v1.x 网关配置字段成熟度（v1.1.12 / T12）

## 1. 文档定位

- **任务**：落实 **`development-optimization.md` §11 T12**：把 `GatewayAppConfig` 相关字段的 **启动生效 / 热更新生效 / 仅解析（预留）** 写清，避免「配置能写进 JSON」被误读成「进程已按该字段运行」。
- **单一事实源**：字段级表格仍以 **`docs/v1-maturity-matrix.md` §5.1** 为准；本文补充 **口径说明**、**热更新现状**、**示例入口差异**，并与 **`ConfigWatcher` / `GracefulShutdown`**（矩阵 §5.2 / §5.3）对齐。
- **不在本版承诺**：统一 reload 校验 / 回滚 / 去抖 /「改配置自动重建线程池或重绑监听」— 归 **`v1.1.13`–`v1.1.14`（T13）** 与后续工程化。

---

## 2. 列含义（如何读 §5.1 表）

| 列 | 含义 |
|---|---|
| **启动生效** | 进程 **首次** `load_gateway_config` + 构造 `GatewayServer` / `SessionOptions` / 依赖配置的路径上，字段 **被读取并影响行为**。`✅（解析）` 表示仅解析落库，**主链未消费**。 |
| **热更新生效** | `ConfigWatcher` 触发 reload 回调后，**无需重启进程**即可改变运行时行为。当前仓库 **仅有少数字段**为 `✅`，其余标 **`❌（重启）`**。 |
| **主链接入** | 与矩阵全局约定一致：`stable` / `experimental` / `reserved`。 |
| **备注** | 指向代码路径、已知缺口（例如 http 校验超时字段未严格约束 socket）。 |

---

## 3. 热更新现状（与矩阵 §5.2 一致）

- **`ConfigWatcher`**：`stable` 的是「文件 `last_write_time` 变更 → 调回调」；**不是**完整配置热更新框架（无校验失败回滚、无版本协商、无字段级 diff）。
- **当前各 example 回调实际生效字段**：以代码为准 — 多数入口在 reload 时 **仅** `server.set_connection_limits(max_connections, per_ip_connection_limit)`（与矩阵 §5.1 中 **`max_connections` / `per_ip_connection_limit`** 两行一致）。
- **结论**：除上述连接限额相关字段外，**编辑 `config/gateway.json` 并依赖 watcher 并不会让其它字段在运行时生效** — 需要 **重启进程**（或扩展回调，属 T13 范畴）。

---

## 4. `GatewayAppConfig` 字段表

与 **`docs/v1-maturity-matrix.md` §5.1** 同步维护；修订字段时 **先改矩阵 §5.1**，再视需要更新本节叙述。

| 字段 | 启动生效 | 热更新生效 | 主链接入 | 备注 |
|---|---|---|---|---|
| `port` | ✅ | ❌（重启） | `stable` | 主监听端口 |
| `http_management_port` | ✅ | ❌（重启） | `stable` | 0 表示禁用 |
| `io_threads` | ✅ | ❌（重启） | `stable` | |
| `business_threads` | ✅ | ❌（重启） | `stable` | |
| `metrics_log_interval` | ✅ | ❌（重启） | `stable` | |
| `metrics_prometheus_path` | ✅ | ❌（重启） | `stable` | |
| `metrics_json_path` | ✅ | ❌（重启） | `stable` | |
| `auth_provider` | ✅ | ❌（重启） | `stable` | dev / json_file / http |
| `auth_users_path` | ✅ | ❌（重启） | `stable` | json_file 模式必填 |
| `auth_http_endpoint` | ✅ | ❌（重启） | `experimental` | http 模式同步阻塞，见矩阵 §3.1 |
| `auth_http_timeout` | ✅ | ❌（重启） | `experimental` | 字段被读取，但 http 校验器实际不约束 socket 超时 |
| `max_connections` | ✅ | ✅ | `stable` | `examples/echo` 等 reload：`set_connection_limits()` |
| `per_ip_connection_limit` | ✅ | ✅ | `stable` | 同上 |
| `max_guests` | ✅（解析） | ❌ | `reserved` | 字段被解析但**主链未引用** |
| `session_max_packet_size` | ✅ | ❌（重启） | `stable` | |
| `session_max_pending_write_bytes` | ✅ | ❌（重启） | `stable` | |
| `session_heartbeat_check_interval` | ✅ | ❌（重启） | `stable` | |
| `session_heartbeat_timeout` | ✅ | ❌（重启） | `stable` | |
| `tls.*` | ✅（解析） | ❌ | `reserved` | 字段被解析，主链未启用 SSL stream，见矩阵 §4.5 |

> **`✅（解析）`**：配置层能读出该字段，但运行时主链未对该字段做出行为响应；**`reserved`** 字段不应被运维当作可生效配置。

---

## 5. 与 roadmap 的关系

| 版本 | 内容 |
|---|---|
| **`v1.1.12`**（本文） | T12：配置字段成熟度 **单列文档** + 热更新叙事收口 |
| **`v1.1.13`** | T13：**`docs/v1-runtime-lifecycle.md`** + showcase shutdown **`io_context.stop()`** |
| **`v1.1.14`** | T13 后半：**`try_load_gateway_config`**；**`docs/v1-runtime-lifecycle.md` §6–§7**（矩阵 §5.2–§5.3 组件表仍为本索引） |
| **`v1.1.15`** | **T14**：player store / replay / audit 与生命周期节点对照 — **`docs/v1-cross-cutting-capabilities.md`** |
| **`v1.1.16`** | **T15**：横切动作 **应收口** — **`docs/v1-cross-cutting-lifecycle-binding.md`** |
| **`v1.1.17`** | **T16**：横切 **数据格式与后端支持级别** — **`docs/v1-cross-cutting-data-formats.md`** |
| **`v1.2.3`（T19）** | **已完成**：生命周期与装配回归测试（`lifecycle_assembly_test`） |
