# v2 Control Plane Preplan

更新时间：2026-05-30

本文档不是当前主线承诺，也不是当前生产事实源。
它只用于记录一个“如果后续确实需要，再如何做”的 v2 控制面预开发规划。

当前状态结论：

- `AdminService` 继续保留在 `legacy-v1` / demo-only 面。
- 当前默认主线不提供二进制 admin 命令控制面。
- 当前正式治理入口仍是：
  - gateway HTTP management: `/health` `/ready` `/metrics*`
  - 配置治理与 reload runbook
  - fixed-runner evidence / production evidence / readiness report
  - Operator / control-plane gate

## 为什么现在不做

基于当前项目进度，主线仍优先：

1. 固定 runner 容量/长稳事实沉淀
2. Conan / lockfile 依赖治理收口
3. helper/raw JSON 兼容层退场
4. generated proto/gRPC 完整迁移证据

现有 `AdminService` 直接迁入主线的问题：

- 依赖旧二进制管理消息号 `5001-5004`
- ACL 仅有 `shared_secret` / `trusted_peer_prefixes`
- 装配方式是 demo 手工注册，不是默认运行时治理模型
- 与当前 `v2` 的 JWT role / `Authorizer` / fixed-runner / HTTP management / Operator 证据链不一致

因此，如果后续要做，也应当做“v2 控制面”，而不是迁移当前 `AdminService`。

## 启动条件

只有在同时满足以下条件时，才建议立项：

1. 业务或运维明确提出需要“在线干预能力”，且现有 HTTP metrics / runbook / Operator 不足
2. 需要统一处理 session / room / battle / gateway admission 的运维动作
3. 可以接受新增一条受鉴权、受审计、受版本治理的控制面 API
4. 固定 runner 与 release evidence 已经稳定，团队有余量做新增面

## 目标边界

如果启动，v2 控制面应满足：

- 不复用旧 `5001-5004` 二进制消息
- 不复用 `AdminService` 的 demo ACL
- 鉴权直接复用 JWT role / `Authorizer`
- 审计统一走 `AUDIT_LOG`
- 动作结果可通过 summary / metrics / runbook 观测
- 必须有 idempotency、权限边界、失败语义和回滚说明

## 建议形态

优先顺序：

1. HTTP control plane
2. 内部 service/control RPC
3. gRPC control API

不建议：

- 在默认 gateway ingress 上继续追加旧二进制 admin 消息
- 把控制面和业务协议共用同一套客户端消息号

## 预开发 API 清单

这些不是当前承诺，只是候选最小集。

### 1. Gateway Admission

- `POST /control/gateway/admission/mode`
  - 作用：切换 `normal` / `drain` / `reject_new`
- `GET /control/gateway/admission/status`
  - 作用：查看当前 admission 模式、开始时间、操作者

### 2. Session Control

- `POST /control/sessions/kick`
  - 参数：`session_id` 或 `user_id`
  - 作用：踢单个在线会话
- `POST /control/sessions/disconnect-all`
  - 参数：`user_id`
  - 作用：踢同一用户的全部会话
- `GET /control/sessions/{session_id}`
  - 作用：查看 session 到 user/room/battle 的绑定关系

### 3. Room Moderation

- `POST /control/rooms/{room_id}/kick`
  - 参数：`target_user_id`
  - 作用：房间层踢人
- `POST /control/rooms/{room_id}/transfer-owner`
  - 参数：`target_user_id`
  - 作用：转移 owner
- `POST /control/rooms/{room_id}/close`
  - 作用：关闭房间并清理状态
- `GET /control/rooms/{room_id}`
  - 作用：返回 room 成员、ready、battle 绑定、最后活动时间

### 4. Battle Control

- `POST /control/battles/{battle_id}/finish`
  - 参数：`reason`
  - 作用：强制结束 battle
- `POST /control/battles/{battle_id}/pause`
  - 作用：仅当后续运行时支持 pause/resume 时开放
- `POST /control/battles/{battle_id}/resume`
  - 作用：仅当后续运行时支持 pause/resume 时开放
- `GET /control/battles/{battle_id}`
  - 作用：查看 battle lifecycle、participants、frame、archive 状态

### 5. Config / Reload

- `POST /control/config/reload`
  - 作用：触发受控 reload
  - 要求：先校验、后原子替换、失败保留旧配置
- `GET /control/config/version`
  - 作用：查看当前 config version、最近 reload 结果

### 6. Safety / Ban

- `POST /control/denylist/ip`
  - 参数：`ip`, `ttl_seconds`, `reason`
- `DELETE /control/denylist/ip`
  - 参数：`ip`
- `POST /control/denylist/user`
  - 参数：`user_id`, `ttl_seconds`, `reason`
- `DELETE /control/denylist/user`
  - 参数：`user_id`

### 7. Read-only Diagnostics

- `GET /control/runtime/summary`
  - 作用：返回对 `/metrics/diagnostics/json` 的控制面封装视图
- `GET /control/audit/query`
  - 参数：`event`, `since`, `limit`
  - 作用：受控读取审计事件

## 鉴权与审计要求

如果实施，必须满足：

- 只允许 `admin` role 调用
- 每个写操作记录：
  - `actor`
  - `role`
  - `request_id`
  - `trace_id`
  - `action`
  - `target`
  - `outcome`
  - `reason`
- 每个写操作必须定义成功、拒绝、目标不存在、状态冲突四类结果

## 最小测试清单

如果实施，至少要有：

- unit: authz / schema / idempotency / audit field coverage
- integration: gateway runtime + session/room/battle 状态变更
- e2e: fixed-runner 上的 admission drain / config reload / battle finish
- security: 非 admin role 拒绝、过期 token 拒绝、重复请求幂等

## 明确不做

在未立项前，不做以下行为：

- 不在 `v2` 默认主线注册旧 admin message handlers
- 不新增新的 `5001-5004` 风格二进制管理命令
- 不把 demo `AdminService` 包装成“当前主线控制面”
