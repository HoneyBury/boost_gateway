# BoostGateway Client SDK

面向 BoostGateway 游戏服务器的 C++ 客户端 SDK。封装了 TCP 连接管理、协议编解码、请求/响应关联和服务端推送处理。

> **当前版本: v4.1.0** | **分发状态: C++ 静态库 + C ABI 动态库 + Python/C# 轻量封装** | [路线与当前状态](roadmap.md)

版本兼容与语言封装边界见 [SDK 与 Gateway 兼容矩阵](compatibility.md)。

## 快速开始

```cpp
#include "boost_gateway/sdk/client.h"

boost_gateway::sdk::SdkClient client;

// 1. 连接
client.connect("127.0.0.1", 9201, std::chrono::seconds(5));

// 2. 登录
auto login = client.login("player1", "token:player1");
if (!login.ok) { /* 处理登录失败 */ }

// 3. 注册推送回调
client.on_push([](const auto& push) {
    // 处理服务端推送: kSessionKickedPush, kBattleStatePush 等
});
client.on_disconnect([] {
    // 处理 heartbeat 失败或意外断开后的重连/降级
});
client.start_heartbeat(std::chrono::seconds(15));

// 4. 游戏操作
auto room = client.create_room("room_001");
auto battle = client.start_battle("room_001");
client.send_battle_input("move:100,200");

// 5. 断开
client.disconnect();
```

## API 参考

### 连接管理

| 方法 | 说明 |
|------|------|
| `connect(host, port, timeout)` | 连接到网关服务器 |
| `disconnect()` | 断开连接 |
| `is_connected()` | 检查连接状态 |
| `start_heartbeat(interval)` | 启动自动 heartbeat，建议登录成功后调用 |
| `stop_heartbeat()` | 停止自动 heartbeat，`disconnect()` 会自动停止 |

### 认证

| 方法 | 返回 | 说明 |
|------|------|------|
| `login(user_id, token, timeout)` | `LoginResult` | 登录认证 |

### 房间

| 方法 | 返回 | 说明 |
|------|------|------|
| `create_room(room_id, timeout)` | `RoomResult` | 创建房间 |
| `join_room(room_id, timeout)` | `RoomResult` | 加入房间 |
| `leave_room(room_id, timeout)` | `RoomResult` | 离开房间 |
| `set_ready(ready, timeout)` | `RoomResult` | 设置准备状态 |

### 战斗

| 方法 | 返回 | 说明 |
|------|------|------|
| `start_battle(room_id, timeout)` | `BattleStartResult` | 开始战斗 |
| `send_battle_input(input_data, timeout)` | `BattleInputResult` | 发送战斗输入 |

### 事件回调

| 方法 | 说明 |
|------|------|
| `on_push(PushCallback)` | 服务端推送回调 |
| `on_disconnect(DisconnectCallback)` | 意外断开回调 |

### 线程模型与生命周期

- `SdkClient` 是同步 API。业务请求会持有内部 I/O 锁，避免 heartbeat 与业务请求并发读写同一个 TCP socket。
- `on_push` 在执行同步请求或 heartbeat 读取到服务端 push 时触发；回调内不要长期阻塞，也不要递归调用同一个 `SdkClient` 的同步请求。
- `on_disconnect` 目前由自动 heartbeat 检测到连接失败时触发；主动 `disconnect()` 不触发该回调。
- `disconnect()` 会停止 heartbeat、关闭 socket，并可重复调用。
- `connect()` 会先清理旧连接，再建立新 TCP 连接；reconnect 建议流程是 `disconnect()` → `connect()` → `login()`。
- 超时、未连接、发送失败和无效响应会写入 result 的 `error_code` / `error_message`。

### Heartbeat / Reconnect / Push

```cpp
client.on_disconnect([&] {
    // 建议在业务线程或调度器里执行重连，不要在回调内阻塞太久。
});
client.start_heartbeat(std::chrono::seconds(15));

// reconnect
client.disconnect();
client.connect("127.0.0.1", 9201);
client.login("player1", "token:player1");
```

SDK 会识别并分发以下 push：`kSessionKickedPush`、`kSessionResumedPush`、`kRoomStatePush`、`kBattleStatePush`、`kBattleInputPush`。

### 结果类型

```cpp
struct LoginResult { bool ok; int32_t error_code; string error_message; string user_id; string display_name; };
struct RoomResult { bool ok; int32_t error_code; string error_message; string room_id; int member_count; };
struct BattleStartResult { bool ok; int32_t error_code; string error_message; string battle_id; };
struct BattleInputResult { bool ok; int32_t error_code; string error_message; uint64_t input_seq; };
struct MatchResult { bool ok; int32_t error_code; string error_message; string response_body; };
struct LeaderboardSubmitResult { bool ok; int32_t error_code; string error_message; string response_body; };
struct LeaderboardQueryResult { bool ok; int32_t error_code; string error_message; string response_body; };
struct EchoResult { bool ok; string echo_body; };
```

### SDK 错误码

| 错误码 | 说明 |
|--------|------|
| `kOk` (0) | 成功 |
| `kNotConnected` (-1) | 未连接 |
| `kTimeout` (-2) | 超时 |
| `kSendFailed` (-3) | 发送失败 |
| `kReadFailed` (-4) | 读取失败 |
| `kInvalidResponse` (-5) | 无效响应 |
| `kMalformedBody` (-6) | 消息体格式错误 |

## 协议消息号

SDK 自动处理以下协议消息的编解码:

| 消息号 | 常量 | 方向 |
|--------|------|------|
| 1 | kHeartbeatRequest | C→S |
| 1001 | kEchoRequest | C→S |
| 2001 | kLoginRequest | C→S |
| 3001 | kRoomCreateRequest | C→S |
| 3003 | kRoomJoinRequest | C→S |
| 3005 | kRoomLeaveRequest | C→S |
| 3007 | kRoomReadyRequest | C→S |
| 4001 | kBattleStartRequest | C→S |
| 4003 | kBattleInputRequest | C→S |
| 6001 | kMatchJoinRequest | C→S |
| 6004 | kMatchLeaveRequest | C→S |
| 6006 | kMatchStatusRequest | C→S |
| 7001 | kLeaderboardSubmitRequest | C→S |
| 7003 | kLeaderboardTopRequest | C→S |
| 7005 | kLeaderboardRankRequest | C→S |

## 编译链接

```cmake
# 在客户端项目的 CMakeLists.txt 中:
add_subdirectory(path/to/sdk)
target_link_libraries(your_app PRIVATE boost_gateway_sdk)
```

安装后的消费方式：

```cmake
find_package(boost_gateway_sdk CONFIG REQUIRED)
target_link_libraries(your_app PRIVATE boost_gateway::sdk)
```

C API 动态库会随 SDK 一起安装，用于 Python `ctypes` 与 C# `DllImport` 绑定。C ABI 入口包含 `gsdk_version()`，用于运行时校验 native library 与语言封装版本是否匹配。

Python wrapper 会优先读取 `BOOST_GATEWAY_SDK_LIBRARY` 指定的 native library 路径，加载失败时会列出尝试过的路径和底层错误。Python/C# wrapper 都会校验 native SDK 主版本号，避免语言封装与动态库版本错配。

生产接入示例：

- C++：`sdk/examples/full_flow_client/main.cpp`
- Python：`sdk/examples/python_full_flow.py`
- C#：`sdk/examples/csharp_full_flow/Program.cs`

分发验证入口：

```bash
python3 scripts/check_sdk_distribution.py --build-dir build/default
python3 scripts/verify_sdk_package_consumer.py --build-dir build/default
python3 scripts/verify_sdk_business_flow.py --build-dir build/default
python3 scripts/verify_sdk_full_flow_client.py --build-dir build/default
```

SDK 依赖:
- Boost.Asio (TCP 网络, 头文件)
- nlohmann_json (JSON, 头文件)

## 完整业务流程示例

```cpp
sdk::SdkClient alice, bob;

// === Alice 创建房间 ===
alice.connect("127.0.0.1", 9201);
alice.login("alice", "token:alice");
alice.create_room("battle_room");
alice.set_ready(true);

// === Bob 加入房间 ===
bob.connect("127.0.0.1", 9201);
bob.login("bob", "token:bob");
bob.join_room("battle_room");
bob.set_ready(true);

// === 开始战斗 ===
alice.start_battle("battle_room");

// === 战斗输入 ===
alice.send_battle_input("move:10,20");
bob.send_battle_input("move:30,40");

// === 结束 ===
alice.send_battle_input("finish:surrender");
alice.disconnect();
bob.disconnect();
```

## 与 Gateway 版本兼容性

| SDK 版本 | Gateway 版本 |
|---------|-------------|
| v4.1.0 | v3.3.x |
| v2.4.0 | v2.0.0+ |
