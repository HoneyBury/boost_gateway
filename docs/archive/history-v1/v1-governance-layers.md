# v1.x 治理入口分层与成熟度冻结（v1.1.9 / v1.1.10 / T10）

## 文档定位

本文对应 **`development-optimization.md`**「Gateway / Admin / Management 维护整改路线图」**第一步：先划清治理入口分层**（**`v1.1.9`** / **T10**），以及 **第二步：冻结治理能力成熟度与接线方式**（**`v1.1.10`**）。

- **分层划定（v1.1.9）**：用文字 + 表格固定 **L0–L3** 与职责，并指向代码位置；**不新增**鉴权、不修改行为。
- **成熟度冻结（v1.1.10）**：见 **§6** — 对齐文档、README、`runtime-playbook`、showcase **示例源码注释**，**禁止**将「示例已接线」「消息号已实现」推导为正式、可依赖的治理能力。
- **权限与审计约束**：**`v1.1.11`**（T11）。
- **治理边界测试**：**`v1.2.2`**（T18，已在当前维护分支完成）。

单项能力仍以 **`../history-v1/v1-maturity-matrix.md` §4** 为准；本文负责「这些能力分属哪一层、谁可以碰」。

---

## 1. 分层总览

| 层 | 载体 | 典型职责 | 副作用 |
|---|------|----------|--------|
| **L0 — 客户端 TCP ingress** | `Session` → `MessageDispatcher::ingress` | 登录前白名单、连接维基础限频、心跳直回 | 可 **拒绝包**（不进入业务池）；可 **回包**（`kErrorResponse` / `kHeartbeatResponse`） |
| **L1 — 业务消息处理** | `MessageDispatcher` → 各 `*Service` handler | 登录 / 房间 / 战斗等业务 | 依业务定义 |
| **L2 — HTTP 管理面（独立端口）** | `net::HttpManager` | **只读**健康桩、指标导出 | **无**业务状态写入；**无**鉴权（矩阵 §4.1） |
| **L3 — 二进制「管理」消息（可选）** | `AdminService` + 消息号 5001–5005 | 示例里 kick / ban / reload / status | **有**副作用；**demo-only**、**无权限模型**（矩阵 §4.2） |
| **装配与进程级** | `GatewayServer` | Accept、连接数/IP 限制、`Session` 生命周期回调、**启动** L2、周期 metrics 日志/落盘 | 断线时房间成员移除 + `clear_battle_if_room_empty` 等（见 `v1-cross-domain-flows.md`） |

**不属于「治理控制面」但易混淆**：

- **`GatewayService`**：实现 **L0** 策略（`register_ingress_middleware`），**不是**完整「运维 API」。
- **`InternalBus`**：`dispatch(nullptr, …)` **跳过** ingress（见 `message_dispatcher` 文档与矩阵 §2.4），不可与客户端入口混谈。

---

## 2. L2：HTTP 路径按「观测 / 健康」拆开

| 方法 + 路径 | 归类 | 当前语义（代码：`src/net/http_manager.cpp`） |
|-------------|------|-----------------------------------------------|
| `GET /health` | **存活探测（liveness stub）** | 固定 `{"status":"ok"}`；**不**反映进程/业务真实健康；`HealthProvider` 仅在头文件声明，**主链未接线** |
| `GET /metrics` | **观测导出（Prometheus）** | 依赖 `GatewayServer` 注入的 `metrics_provider_` |
| `GET /metrics/json` | **观测导出（JSON）** | 同上 |
| 其它 | — | `404` |

**准入期望（文档约束，非代码）**：仅 **内网 / 受信网络** 暴露 `http_management_port`；**不得**将当前 HTTP 面当作已认证管理 API。

---

## 3. L3：二进制 Admin 的正式定位

- **默认 `GatewayServer` 主链**：**不注册** `AdminService` handlers。
- **接线位置**：`examples/admin_demo`、`examples/login_demo` 等**手工** `register_handlers`。
- **风险**：任意已连接 TCP 客户端只要能发对应 **message_id** 即可触发回调；**无**「谁能调用」模型。

**维护规则**：新增「有副作用的运维能力」时，须先在本分层模型中声明所属层（L2 / L3 / 未来独立控制面），**禁止**默认塞进主链而不经 T10/T11 评审。

---

## 4. 与 roadmap 后续版本的关系

| 版本 | 内容 |
|------|------|
| `v1.1.10` | 冻结治理能力成熟度与接线方式（文档与示例表述；**§6**） |
| `v1.1.11` | T11：**`../history-v1/v1-admin-audit-rules.md`** — 调用前提契约 + `admin_invoke` **边界审计**（**无**运行时 ACL） |
| `v1.2.2` | T18：治理边界测试加固 |

---

## 5. 相关代码索引

| 组件 | 路径 |
|------|------|
| Ingress 中间件 | `src/game/gateway/gateway_service.cpp` |
| HTTP 管理 | `include/net/http_manager.h`，`src/net/http_manager.cpp` |
| 二进制 Admin | `include/game/gateway/admin_service.h`，`src/game/gateway/admin_service.cpp` |
| 网关注册与 HTTP 启动 | `src/game/gateway/gateway_server.cpp` |

---

## 6. 治理能力成熟度冻结（`v1.1.10`）

对应 **`development-optimization.md`** 路线图**第二步**验收点：**文档与示例不再暗示**未正式收口的治理能力已经可稳定依赖。下列表述在 **v1.1.10** 起视为 **维护期约束**（与 `../history-v1/v1-maturity-matrix.md` §4 一致；冲突时以矩阵为准）。

### 6.1 必须写清的事实（不得用语义偷换）

| 对象 | 正式结论 | 禁止 / 需避免的暗示 |
|------|----------|---------------------|
| **`AdminService`（5001–5005）** | **demo-only**；**默认主链不注册**；仅 `examples/admin_demo`、`examples/login_demo` 等**手工** `register_handlers`；**无**调用方身份模型 | 「管理面已就绪」「生产级运维 API」「已鉴权的管理指令」 |
| **`net::HttpManager`（L2）** | **只读**观测导出（`/metrics` / `/metrics/json` **stable**）+ **`GET /health` liveness stub**（**experimental**，固定 `{"status":"ok"}`；**不接**真实 `HealthProvider`） | 「HTTP 管理面 = 完整控制面」「/health = 就绪/业务健康」「已认证运维 HTTP API」 |
| **`GatewayServer`（装配）** | Accept、连接/IP 限额、`Session` 生命周期回调、`http_management_port > 0` 时拉起 **L2**、周期 metrics — **不包含**二进制 admin、**不包含**内置权限审计 | 「网关自带完整治理能力」「开箱即用的运维后端」 |

### 6.2 文档与源码落点（本版本已校对）

- `README.md`、`../runbooks/runtime-playbook.md`：**HTTP** 用词与 `/health` 语义与上表对齐。
- `examples/admin_demo/admin_demo_main.cpp`、`examples/login_demo/login_demo_main.cpp`：注释与日志用词与 **§6.1** 对齐。
- **`include/game/gateway/admin_service.h`**：类前注释标明 **demo-only** 与本文引用。

**`v1.1.11`（T11）** 起：L3 二进制 admin 的调用前提与 **`admin_invoke`** 必备键见 **`../history-v1/v1-admin-audit-rules.md`**。权限运行时强制、失败细分响应、结构化审计后端等仍属 **`reserved`**，由 **`v1.2.2`（T18）** 等与该文后续章节承接。
