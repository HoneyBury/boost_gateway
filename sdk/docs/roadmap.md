# BoostGateway SDK 路线与当前状态

> 本文档已从早期“企业级重构规划”收口为“规划 + 当前事实”。

## 1. 当前 SDK 状态

SDK 当前并不是一个完全独立于服务端演进的最终产品，但已经明显超过最初的单连接草案：

- `sdk/include/boost_gateway/sdk/client.h`
- `sdk/include/boost_gateway/sdk/protocol/*`
- `sdk/include/boost_gateway/sdk/transport/*`
- `sdk/src/transport/tcp_transport.cpp`
- `sdk/src/transport/connection_pool.cpp`

当前已具备：

- `Client`
- `protocol::codec`
- `transport::transport`
- `transport::tcp_transport`
- `transport::connection_pool`

当前仍然是“持续收口中”：

- 真正的异步 API 分层还不完整
- generated protobuf/gRPC client 已作为 `BOOST_BUILD_GRPC=ON` 的仓库内实验 target 接入；SDK 多服务 E2E 已覆盖 Login、Room、Battle、Leaderboard，以及回调取消的 Battle state 订阅
- 文档与示例仍需要继续整理

## 2. 当前目标不再是“从零重构”

原始规划里的很多目标已经部分落地，因此当前 SDK 路线更适合表述为：

1. 保持当前 C++ SDK 可用与可测
2. 跟随主线 typed envelope / proto transport 演进
3. 补全独立分发、安装、文档与跨语言封装质量

## 3. 当前重点

### 3.0 P3 分发与 ABI 收束

- 统一 SDK CMake/package/C API/Python/C# 版本为 `v4.1.0`
- 安装 C API 动态库，供 Python `ctypes` 与 C# `DllImport` 使用
- 增加 SDK 分发门禁，校验版本、导出符号、安装目标、语言封装覆盖面和可选构建产物

### 3.1 协议层

- 当前 SDK 仍主要面向现有 v1/v2 协议链
- 后续应与 `proto/v3/*` 和 `include/v3/proto/envelope_codec.h` 对齐

### 3.2 传输层

- `TcpTransport` 和 `ConnectionPool` 已存在
- 后续应补充更明确的 async / reconnect / timeout 行为文档

### 3.3 构建与分发

- `sdk/CMakeLists.txt` 已具备安装/导出基础
- 后续可继续收口 `find_package`、版本文件和示例消费路径

## 4. 下一步建议

1. 让 SDK 协议层跟 typed `ServiceEnvelope` helper 保持一致
2. 补 SDK 独立文档（README / quickstart / API reference）
3. 在 streaming、TLS/RBAC、observability 和独立安装包契约完成后，再评估是否对外暴露 generated protobuf/gRPC 客户端接口
