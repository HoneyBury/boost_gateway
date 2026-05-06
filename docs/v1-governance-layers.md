# v1.x 治理入口分层（v1.1.9 / T10）

## 文档定位

本文对应 **`development-optimization.md`**「Gateway / Admin / Management 维护整改路线图」**第一步：先划清治理入口分层**，维护任务表 **T10** 在 **`v1.1.9`** 的交付物。

- **本版（v1.1.9）**：用文字 + 表格固定**分层与职责**，并指向代码位置；**不新增**鉴权、不修改行为。
- **成熟度冻结**：**`v1.1.10`**（见 `development-priority.md`）。
- **权限与审计约束**：**`v1.1.11`**（T11）。
- **治理边界测试**：**`v1.2.2`**（T18）。

单项能力仍以 **`docs/v1-maturity-matrix.md` §4** 为准；本文负责「这些能力分属哪一层、谁可以碰」。

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
| `v1.1.10` | 冻结治理能力成熟度与接线方式（文档与示例表述） |
| `v1.1.11` | T11：admin 权限与审计最小规则 |
| `v1.2.2` | T18：治理边界测试加固 |

---

## 5. 相关代码索引

| 组件 | 路径 |
|------|------|
| Ingress 中间件 | `src/game/gateway/gateway_service.cpp` |
| HTTP 管理 | `include/net/http_manager.h`，`src/net/http_manager.cpp` |
| 二进制 Admin | `include/game/gateway/admin_service.h` |
| 网关注册与 HTTP 启动 | `src/game/gateway/gateway_server.cpp` |
