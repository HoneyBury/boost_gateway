# SDK 与 Gateway 兼容矩阵

更新时间：2026-07-12

本文档记录当前客户端 SDK 与 BoostGateway 服务端的生产接入口径。默认事实源以 SDK native 版本、C ABI、语言封装和真实 gateway full-flow gate 为准。

## 当前兼容线

| Gateway 版本 | SDK native 版本 | C++ package | C ABI | Python wrapper | C# wrapper | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| `v3.3.2` | `v4.1.0` | `find_package(boost_gateway_sdk 4.1.0 CONFIG REQUIRED)` | `gsdk_version()` 主版本 `4.x` | 校验 native 主版本 `4.x` | 校验 native 主版本 `4.x` | stable |

## 客户端兼容矩阵

| 客户端形态 | 安装/加载入口 | 运行时版本校验 | Full-flow 示例 | 当前支持边界 |
| --- | --- | --- | --- | --- |
| C++ | CMake install 后 `boost_gateway::sdk` | 编译期 `BOOST_GATEWAY_SDK_VERSION` | `sdk/examples/full_flow_client/main.cpp` | 默认同步 TCP API，调用方负责线程归属和对象生命周期 |
| C ABI | `boost_gateway_sdk_dll` 动态库 | `gsdk_version()` | 由 Python/C# wrapper 与 C ABI 单测覆盖 | ABI 边界捕获异常，错误通过返回值和 response text 传递 |
| Python | `BOOST_GATEWAY_SDK_LIBRARY` 或平台默认库名 | `assert_compatible_version()` 要求 native `4.x` | `sdk/examples/python_full_flow.py` | 薄绑定，不内置包管理发布；加载失败会列出尝试路径 |
| C# | `DllImport` native library | `AssertCompatibleNativeVersion()` 要求 native `4.x` | `sdk/examples/csharp_full_flow/Program.cs` | 薄绑定，不替代 NuGet 包；native allocation failure 会抛出明确异常 |

## 运行时校验

- C ABI 暴露 `gsdk_version()`，语言封装必须在创建 client 前校验 native 主版本。
- Python wrapper 支持 `BOOST_GATEWAY_SDK_LIBRARY` 指定 native library 路径；加载失败时输出尝试路径和底层错误。
- C# wrapper 在 native client allocation 失败时抛出明确异常。
- SDK full-flow gate 必须覆盖 login、echo、room、ready、battle、push、reconnect、heartbeat 和 disconnect callback。
- N4/N5 起还必须保留 backend TLS profile full-flow 证据；客户端仍连接 plain TCP gateway，TLS profile 发生在 gateway->backend。

## N5 企业交付门禁

客户端交付前运行：

```bash
python3 scripts/verify_sdk_enterprise_delivery.py --build-dir build/default --skip-build
```

该门禁聚合：

- `scripts/check_sdk_distribution.py`：SDK 版本、CMake package、C ABI、Python/C# wrapper、native 加载诊断。
- `scripts/verify_sdk_package_consumer.py`：临时安装 SDK，并让外部 CMake consumer `find_package()`、链接和运行；`--with-grpc` 还会校验实验 `boost_gateway::sdk_grpc`、生成的 `gateway.pb.h/gateway.grpc.pb.h` 与 package config 的 gRPC 依赖导出。
- `scripts/verify_sdk_business_flow.py`：in-process gateway 业务闭环，覆盖 heartbeat、reconnect、push、disconnect callback。
- `scripts/verify_sdk_full_flow_client.py`：真实 `v2_gateway_demo` + 五后端 + `sdk_full_flow_client`，覆盖最接近客户端接入的生产链路。
- `scripts/verify_sdk_full_flow_client.py --backend-tls`：同一 SDK 客户端 API，在服务端 backend TLS profile 下跑通真实 full-flow。

## 兼容边界

- 默认 SDK 面向现有 TCP wire protocol。仓库内 `BOOST_BUILD_GRPC=ON` 时提供实验性 `boost_gateway::sdk_grpc` / `GrpcClient`，已验证 unary Login、Room、Battle、Leaderboard、有限帧 `stream_battle_state()` 及回调取消的 `subscribe_battle_state()`；服务端将订阅间隔夹紧到 100-5000ms。该 target 现在已进入仓库内 CMake install/export 与 package consumer 契约，也已具备 TLS/mTLS、RBAC 和 OTLP collector E2E 事实；但仍不承诺跨语言封装、默认启用或外部 wire 稳定性。
- backend TLS profile 不改变 SDK 外部 API；证书加载和 mTLS 策略由服务端部署治理，客户端 SDK 只感知 gateway 连接和业务结果。
- `on_disconnect` 当前由 heartbeat failure 触发；主动 `disconnect()` 不触发该回调。
- `on_push` 回调在同步请求或 heartbeat 读到 push 时触发，回调内不应阻塞或递归调用同一个 client 的同步 API。
- 兼容升级默认策略：Gateway patch/minor 版本保持 SDK `4.x` 主版本兼容；破坏性协议变化必须提升 SDK 主版本并更新本矩阵。
- Python/C# 当前是轻量 wrapper，不承诺正式包仓库分发；进入正式客户端发布前需要补齐 wheel/NuGet 签名、平台矩阵和 CI 安装验证。

## 示例入口

- C++：`sdk/examples/full_flow_client/main.cpp`
- Python：`sdk/examples/python_full_flow.py`
- C#：`sdk/examples/csharp_full_flow/Program.cs`

## 客户端接入建议

- 登录成功后启动 heartbeat，断线回调里不要直接递归调用同一个 client 的同步 API；建议交给客户端自己的调度线程执行重连。
- 主动退出使用 `disconnect()`，它是幂等操作且不会触发意外断开回调。
- 生产日志至少记录 SDK native version、gateway host/port、player id、room id、request step、error code、retry count 和 reconnect attempt。
- 同一个 `SdkClient` 不建议多线程并行业务请求；如需并发，按玩家会话或业务 lane 拆分 client。
