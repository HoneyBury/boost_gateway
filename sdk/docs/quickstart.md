# SDK 快速入门

## 5 分钟示例

```cpp
#include "boost_gateway/sdk/client.h"
#include <iostream>

int main() {
    boost_gateway::sdk::SdkClient client;

    // 1. 连接
    if (!client.connect("127.0.0.1", 9201)) {
        std::cerr << "Connection failed\n";
        return 1;
    }

    // 2. 登录
    auto login = client.login("player1", "token:player1");
    if (!login.ok) {
        std::cerr << "Login failed: " << login.error_message << "\n";
        return 1;
    }
    std::cout << "Logged in as " << login.user_id << "\n";

    // 3. Echo 测试
    auto echo = client.echo("hello");
    std::cout << "Echo: " << echo.echo_body << "\n";

    // 4. 断开
    client.disconnect();
    return 0;
}
```

## 编译

```bash
# 假设 SDK 位于项目根目录的 sdk/ 目录下
cmake -B build
cmake --build build --target sdk_echo_client
./build/sdk/examples/sdk_echo_client 127.0.0.1 9201
```

## 前提

- 需要运行中的 BoostGateway 服务器 (gateway on :9201)
- C++20 编译器
- CMake 3.21+
