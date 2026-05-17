# v1.x 二进制 Admin 最小权限与审计规则（v1.1.11 / T11）

## 1. 文档定位

- **任务**：对齐 **`development-optimization.md`** 路线图**第三步**：在进入「可被依赖」的运维面之前，先把 **二进制 admin（L3）** 的调用前提与 **审计最小键**说清楚。
- **范围**：`AdminService` 默认启用最小 ACL；集成方必须配置共享密钥、可信 peer，或在 demo-only 入口显式关闭 ACL。本文仍**不包含**细分业务失败响应（回调失败仍由集成方自定）。
- **实现落点**：`AdminService::register_handlers`（`src/game/gateway/admin_service.cpp`）在每条 admin handler **入口**写 **`AUDIT_LOG("admin_invoke", …)`**。集成方 **仍可在回调里**追加业务语义审计（勿与本文 **必备键**语义冲突）。
- **冲突处理**：成熟度子状态以 **`docs/v1-maturity-matrix.md` §4** 为准；本文是 **契约 + SHOULD**。

---

## 2. 调用前提（谁「可以」使用该能力）

下列为 **运维/文档契约**，而非代码强制（**运行时 ACL**：`reserved`）。

| 条件 | 说明 |
|------|------|
| **注册显式化** | 仅在为 `examples/admin_demo` / `examples/login_demo` 等 showcase **手工**调用 `AdminService::register_handlers` 时出现；**默认 `GatewayServer` 不注册**（见 **`docs/v1-governance-layers.md`** §L3）。 |
| **网络信任域** | 仅允许在 **内网 / 专线 / VPN** 等对端身份已保证的环境暴露业务 TCP；**不得在公网直连**且无额外控制面时使用 L3 admin。 |
| **调用方会话** | 当前实现不要求业务登录态，但默认 ACL 会拒绝未携带共享密钥且不在可信 peer 前缀内的调用。 |
| **推荐接入方式（SHOULD）** | 运维客户端经 **跳板 / 堡垒**、或 **Sidecar admin 连接器**专线；或由 **单独的 HTTP+mTLS / gRPC** 控制面改写为内部 `dispatch`，并配置 `AdminService::AccessControl`。 |

---

## 3. 动作语义与副作用（当前实现）

| `message_id` | 语义 | Body 惯例 | 回调 / 副作用 | `kAdminResponse` body |
|--------------|------|-----------|----------------|------------------------|
| `5003` `kAdminServerStatus` | 快照查询 | （忽略） | 调用 `on_status_`，无会话状态改写 | **回调返回值** JSON 字符串；若无回调则为 `"{}"` |
| `5004` `kAdminReloadConfig` | 触发重载（示例钩子） | 透传写入审计 `payload_excerpt`；语义由集成方自定 | `on_reload_` | 固定 **`reload_ok`** |
| `5001` `kAdminKickPlayer` | 踢人（示例钩子） | 惯例为 `user_id` 明文 | `on_kick_(body)`；由集成方断开会话 | 固定 **`kick_ok`**（即使未找到目标也返回，集成方自定是否 no-op） |
| `5002` `kAdminBanIp` | 封禁（示例钩子） | 惯例为目标 IP | `on_ban_(body, 3600)`，`3600` 为**字面常量**秒 | 固定 **`ban_ok`** |

**失败细分（当前刻意不承诺）**：handler **不区分**回调是否执行成功、`body` 是否合法；不向调用方返回结构化 `error_code`。ACL 拒绝路径会写 `admin_denied`，有 session 时返回固定 `admin_denied` body。

---

## 4. 审计：`admin_invoke` 必备键（v1.1.11）

在 **每条** admin handler 入口处写入：

- **`event`**：固定 **`admin_invoke`**（与既有 `kick`/`ban`/`config_reload` 等事件名共存允许）。
- **`details` 字符串**（`AUDIT_LOG` 第二参）**必须**含下列 **键=值** 子串（顺序不限，空格分隔）：

| 键 | 含义 |
|----|------|
| `layer=L3_admin` | 表明来自二进制 admin 路径 |
| `action=<name>` | `server_status` \| `reload_config` \| `kick_player` \| `ban_ip` |
| `outcome=accepted` | 表示 **已进入 handler 且将按当前语义执行**（非业务结果） |
| `actor_endpoint=<peer>` | `Session::remote_endpoint()`；无会话时为 `none` |
| `request_id=<u32>` | 包内 `request_id` |
| `trace_id=<u64>` | 分发上下文 `trace_id` |
| `payload_excerpt=<text>` | **除** `server_status` 外，对 `body` 前 64 字符做 **引号/控制符剥除** 后的摘录；`body` 为空可省略或 `payload_excerpt=` 空 |

> **JSON 脆弱性**：`AUDIT_LOG` 整体仍是矩阵 §4.4 所称「近似 JSON 行」，**≠**结构化日志后端；编排层 **不得**假定 `details` 内无偶发破坏 JSON 的字符（`endpoint` 等）。

### 4.2 ACL 拒绝审计

ACL 拒绝时写入：

- **`event`**：固定 **`admin_denied`**。
- **`details`** 至少包含 `layer=L3_admin`、`action=<name>`、`outcome=denied`、`actor_endpoint=<peer>`、`request_id=<u32>`、`trace_id=<u64>`。

### 4.1 示例行（节选）

```
{"ts":"…","event":"admin_invoke","details":"layer=L3_admin action=kick_player outcome=accepted actor_endpoint=127.0.0.1:12345 request_id=7 trace_id=0 payload_excerpt=u1"}
```

---

## 5. 与其它审计事件的关系（SHOULD）

- **`login_success` / `login_failure`**、`connection_rejected` 等保持不变。
- Showcase 若在 **回调内**再给 `kick`/`ban`**补记**一行：建议 **增补**字段（例如 `effects=kicked_sessions:1`），**勿重复**本条 `admin_invoke` 必备键语义（避免三套口径）。

---

## 6. roadmap 接续

| 版本 | 内容 |
|------|------|
| **`v1.1.11`**（本文） | 文档契约 + **`admin_invoke`** 边界审计 |
| **`v1.2.2`（T18）** | **已完成**：治理边界回归已覆盖默认装配不注册 admin、`admin_invoke` 最小审计键、HTTP 固定行为 |
| **`v3.3.x/P2`** | **已完成**：`AdminService` 默认 ACL、共享密钥前缀、可信 peer 前缀、`admin_denied` 审计；demo-only 入口需显式关闭 ACL |
