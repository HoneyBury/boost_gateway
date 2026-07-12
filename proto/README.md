# v3 Protocol Buffers

本目录定义 `v3` 服务间通信协议，并保留从仓库内 typed helper 迁移到
generated protobuf / gRPC stub 的正式入口。

## 文件结构

```text
proto/v3/
├── common.proto
├── login.proto
├── room.proto
├── battle.proto
├── match.proto
└── leaderboard.proto
```

- `common.proto`: `ServiceEnvelope`、路由元数据、跨服务公共字段
- `login.proto`: 登录与 token 校验请求/响应
- `room.proto`: 房间生命周期消息
- `battle.proto`: 战斗输入、状态推送、结束消息
- `match.proto`: 匹配请求与结果
- `leaderboard.proto`: 提交积分与排行榜查询

## 当前状态

当前主线同时存在两层协议能力：

1. `include/v3/proto/envelope_codec.h`
   用于当前主链 typed helper 兼容层，已经接入
   `login/room/battle/match/leaderboard`
2. `proto/v3/*.proto`
   作为正式 schema 源，已接入生成入口和 CMake helper target

这意味着当前仓库已经具备：

- typed envelope helper 的运行时兼容能力
- generated protobuf / gRPC stub 的生成入口；CMake 当前以自包含的 `gateway.proto` 为 canonical schema，避免与 typed-envelope 领域 proto 的同名消息重复链接

但默认运行路径仍以 helper 兼容层为主，generated stub 还没有完全替代现有桥接实现。

## 生成方式

直接运行脚本：

```bash
python scripts/generate_proto_cpp.py
```

Windows 包装入口：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/generate_proto_cpp.ps1
```

CMake helper target：

```bash
cmake --build <build-dir> --target generate_v3_proto_cpp
```

生成逻辑：

- 总是生成 protobuf C++ stub 到 `src/v3/proto/`
- 如果环境中存在 `grpc_cpp_plugin`，额外生成 gRPC C++ stub
- 如果缺少 `grpc_cpp_plugin`，脚本会保留 protobuf 生成并明确提示跳过 gRPC

## 设计约束

1. `ServiceEnvelope` 继续作为跨服务统一外层契约
2. 字段编号不重用，只追加
3. helper 层与 generated schema 必须保持 kind / domain / payload 语义一致
4. legacy raw payload 兼容窗口必须通过测试明确，而不是隐式长期保留

## 消息流

```text
Gateway -> ServiceEnvelope -> Login Backend
Gateway -> ServiceEnvelope -> Room Backend
Gateway -> ServiceEnvelope -> Battle Backend
Gateway -> ServiceEnvelope -> Match Backend
Gateway -> ServiceEnvelope -> Leaderboard Backend
```

## 兼容说明

当前实际兼容状态：

- `login/room/battle/match/leaderboard` 后端都接受 wrapped envelope payload
- helper 层仍允许与 legacy raw JSON 共存
- generated protobuf / gRPC 已经有生成入口，但还不是默认唯一传输路径

下一步目标不是再发明第三套协议，而是把现有 helper 契约稳定迁移到 generated stub。

## 退场边界

当前不得把 generated gRPC 作为默认生产 transport。继续推进 PoC 前必须补齐以下证据：

- 非登录路径的 gRPC vs TCP benchmark，至少覆盖 room、battle、match、leaderboard 的基础 RPC。
- SDK-integrated full-flow，不只验证单个 gRPC RPC。
- 持续订阅的 streaming/push、TLS、RBAC、observability 的独立 profile 证据；Battle 已有可取消、限速的 server stream，但尚无 TLS/RBAC/OTel 或外部指标导出 profile。
- Ubuntu fixed-runner 上 `BOOST_BUILD_GRPC=ON` 的构建和测试 summary。

在这些证据完成前，生产默认链路继续保持 `SDK + TCP gateway + BackendEnvelope + typed helper + 五后端`，决策状态保持 `defer_default_transport`。
