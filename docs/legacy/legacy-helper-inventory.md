# Legacy / Helper Inventory

更新时间：2026-07-09

本文档记录当前仓库仍保留的 legacy 兼容面、helper 迁移层和默认主线之外的过渡入口。它不是未来规划文档，而是当前事实清单；未来规划仍以 `docs/project-blueprint.md` 为准。

## 作用范围

- 说明哪些兼容层仍然存在，为什么存在，以及它们是否属于默认生产主线。
- 约束新增功能不得继续扩张 legacy raw JSON 或 v1 example surface。
- 为 `scripts/gates/governance/check_legacy_helper_inventory.py` 提供可校验的事实源。

## 总体规则

1. 默认主线仍是 `SDK + TCP gateway + BackendEnvelope + typed envelope helper + 五后端 + Redis`。
2. `legacy raw JSON` 只允许作为兼容测试和迁移窗口保留，不得承载新增主功能。
3. `generated proto` / `generated protobuf / gRPC stub` 已经存在生成入口，但还不是默认唯一传输路径。
4. v1 风格 legacy 模块（`include/game`、`src/game`、老示例、v1 测试）已从仓库移除。
5. 新增 legacy/helper surface 时，必须同时更新本文档、相关测试和治理脚本。

## Helper 兼容层

| 项目 | 当前状态 | 默认主线角色 | 证据 |
| --- | --- | --- | --- |
| `BackendEnvelope` | 当前跨服务默认外层契约 | 默认主线的一部分 | `include/v2/service/backend_envelope.h`, `src/v2/gateway/gateway_service_bridge.cpp` |
| typed envelope helper | 已覆盖 `login/room/battle/match/leaderboard` | 默认主线的一部分 | `include/v2/service/envelope_adapter.h`, `src/v2/service/envelope_adapter.cpp`, `proto/README.md` |
| `legacy raw JSON` | 兼容窗口仍在，带 deprecation notice | compatibility-only，不得扩展 | `include/v2/service/envelope_adapter.h`, `tests/v2/unit/service_boundary_test.cpp` |
| generated proto | 已有 schema 和生成入口 | migration layer，不是默认 transport | `proto/v3/*.proto`, `scripts/tools/generate_proto_cpp.py`, `proto/README.md` |
| generated protobuf / gRPC stub | Raft protobuf runtime 已进入默认内部依赖；外部 generated gRPC 仍属实验能力 | raft_internal_default / grpc_experimental_only | `proto/README.md`, `proto/v3/raft.proto`, `scripts/gates/governance/check_v3_grpc_poc_decision.py`, `src/v2/grpc/` |

## 服务级迁移状态

| 服务域 | 当前 handler 路径 | typed envelope | legacy raw JSON | generated proto 备注 |
| --- | --- | --- | --- | --- |
| login | `src/v2/login/login_backend_service.cpp` | 已统一接入 adapter；`register_account` / `login_request` / `guest_login` / `token_validate` / `session_bind` / `session_close` / `token_refresh` 均已具备 schema-backed typed request/response | compatibility-only 仅保留 legacy raw JSON 兼容窗口语义，不再新增 raw JSON-only 主业务 handler | `register_account` / `guest_login` 已进入 `EnvelopeMessageKind` / `proto/v3/login.proto` |
| room | `src/v2/room/room_backend_service.cpp` | 已接入，含所有 11 个 handler | N/A — 全部 handler 已接入 typed envelope | schema 已存在，未替换默认 transport |
| battle | `src/v2/battle/battle_backend_service.cpp` | 已接入，含 battle create/input/state/finish/replay_load | compatibility-only 现主要收缩到回放/状态附带 JSON 结构，不再新增 raw JSON-only 主业务 handler | schema 已存在，未替换默认 transport |
| matchmaking | `src/v2/matchmaking/matchmaking_service.cpp` | 已接入 | compatibility-only | schema 已存在，未替换默认 transport |
| leaderboard | `src/v2/leaderboard/leaderboard_service.cpp` | 已接入 | compatibility-only | schema 已存在，未替换默认 transport |

当前覆盖事实：

- 全部 5 服务域共有 29 个业务 handler 已统一接入 `decode_handler_payload()` / `wrap_typed_response_if_needed()` adapter 路径。
- 其中 29 个 handler 已具备 `EnvelopeMessageKind` / schema-backed typed contract。
- login 域的 `register_account` 与 `guest_login` 已进入 `proto/v3/login.proto` 与 `include/v3/proto/envelope_codec.h`。

## 服务级 handler coverage matrix

| 服务域 | typed request decode | typed response wrap | raw JSON compatibility-only scope |
| --- | --- | --- | --- |
| login | `register_account`, `login_request`, `guest_login`, `token_validate`, `session_bind`, `session_close`, `token_refresh` | `register_account`, `login_request`, `guest_login`, `token_validate`, `session_bind`, `session_close`, `token_refresh` | 全部 7 个 handler 已具备 schema-backed typed contract；legacy raw JSON 仅保留兼容窗口语义 |
| room | `room_create`, `room_join`, `room_ready`, `room_leave`, `room_start_battle`, `room_list`, `room_detail`, `room_kick`, `room_transfer_owner`, `room_state_push`, `room_battle_finished` | `room_create`, `room_join`, `room_ready`, `room_leave`, `room_start_battle`, `room_list`, `room_detail`, `room_kick`, `room_transfer_owner`, `room_state_push`, `room_battle_finished` | N/A — 所有 11 个 handler 均已接入 typed envelope |
| battle | `battle_create`, `battle_input`, `battle_state`, `battle_finish`, `replay_load` | `battle_create`, `battle_input`, `battle_state`, `battle_finish`, `replay_load` | 无新增 raw JSON-only 主业务路径；保留 snapshot/replay payload JSON 结构作为实现细节 |
| matchmaking | `match_join`, `match_leave`, `match_status` | `match_join`, `match_leave`, `match_status` | 内部 Raft RPC 已双读 legacy JSON/protobuf v1；核心 writer 仍保持 legacy JSON |
| leaderboard | `leaderboard_submit`, `leaderboard_top`, `leaderboard_rank` | `leaderboard_submit`, `leaderboard_top`, `leaderboard_rank` | 内部 Raft RPC 已双读 legacy JSON/protobuf v1；核心 writer 仍保持 legacy JSON |

当前治理要求：
- 新增业务 handler 必须至少接入 `decode_handler_payload()`
- 新增 typed request handler 必须同时接入 `wrap_typed_response_if_needed()`
- 不得新增新的 raw JSON-only 业务消息类型；仅允许在上表列出的兼容窗口内继续保留既有路径
- 后续 raw JSON 退场的主剩余面已收敛到内部 Raft legacy JSON writer；reader 已支持 protobuf v1

退场推进顺序：

1. ✅ room governance / control-plane 风格消息已全部接入 typed envelope，共 6 个 handler（`room_list`, `room_detail`, `room_kick`, `room_transfer_owner`, `room_state_push`, `room_battle_finished`）。
2. 内部 Raft legacy JSON writer 在 v3.6 兼容窗口继续保留；切换 protobuf writer 前必须有等价集群、恢复和滚动回退测试。
3. 默认 full-flow 新增检查点必须优先走 schema-first/typed envelope，不得以 legacy raw JSON 作为新功能入口。
4. 当五个服务域的剩余兼容窗口都有 typed/generated 替代和 full-flow 证据后，再评估默认禁用 legacy raw JSON 输入。

## Legacy 构建面

| 入口/模块 | 当前状态 | 默认状态 | 备注 |
| --- | --- | --- | --- |
| ~~`project_game` / `include/game` / `src/game`~~ | ~~v1 风格单进程旧主链~~ | ~~已移除~~ | ✅ 已完成退场 |
| ~~`tests/unit` / `tests/integration`~~ | ~~根级 v1-root 测试面~~ | ~~已移除~~ | ✅ 已完成退场 |
| ~~`examples/login` / `room` / `battle` / `pressure`~~ | ~~v1 独立入口~~ | ~~已移除~~ | ✅ 已完成退场 |
| ~~`examples/*_demo` / `echo_server` / `admin_demo`~~ | ~~showcase 入口~~ | ~~已移除~~ | ✅ 已完成退场 |
| `demo/games/tank_battle/` | 业务 demo | `BOOST_BUILD_TANK_DEMO=OFF` | 不属于默认生产主线 |
| `examples/realtime_echo_plugin` | demo/plugin 样例 | `BOOST_BUILD_ECHO_PLUGIN_DEMO=OFF` | 不属于默认生产主线 |

## 禁止新增的行为

- 不得新增仅支持 `legacy raw JSON` 的 handler 或 payload。
- 不得把 demo 业务规则写回 `gateway`、公共 runtime、公共 SDK 或公共协议层。
- 不得把当前 `AdminService` 以原样迁移方式接入默认 v2 主链。

## 进入默认主线前的条件

- helper 或 proto 迁移必须有对应的 schema、typed contract 测试和 full-flow 证据。
- legacy raw JSON 真正退场前，五个服务域都必须完成 generated/typed contract 覆盖。

## 治理入口

```bash
python scripts/gates/governance/check_legacy_helper_inventory.py
python scripts/check_mainline_readiness.py
python scripts/gates/governance/check_script_inventory.py
```

Conan 依赖治理入口：

- `conan/README.md`
- `conan/profiles/linux-gcc-x64`
