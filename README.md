# C++ Scaffold

This repository provides a minimal modern C++ starter project with:

- CMake and CMake Presets
- Third-party dependencies via `FetchContent`
- `Boost.Asio` for asynchronous TCP networking
- `fmt` for formatting
- `spdlog` for logging
- `GoogleTest` for unit tests
- `clang-format` and `.editorconfig`
- GitHub Actions for CI and release artifacts

## 第三方依赖管理

这个项目在 [cmake/Dependencies.cmake](/D:/Program/boost/cmake/Dependencies.cmake:1) 中统一使用 CMake `FetchContent` 管理第三方库。

- `FetchContent_Declare(...)` 用来声明依赖来源和版本。
- `FetchContent_MakeAvailable(...)` 会在 CMake configure 阶段下载源码，并把依赖暴露成当前工程可链接的 CMake target。
- For `Boost.Asio`, this project intentionally uses a lighter pattern:
  这里故意使用更轻量的方式：
  `FetchContent_Populate(...)` 下载官方 Boost 发布包，然后在
  [cmake/Dependencies.cmake](/D:/Program/boost/cmake/Dependencies.cmake:1) 中创建一个名为 `project_boost_asio` 的 `INTERFACE`
  target，只导出 Boost 头文件路径和编译宏。

这个项目中的依赖作用域如下：

- `hello_lib` links `fmt::fmt` and `spdlog::spdlog` as `PUBLIC`
  这表示 `hello_world`、`echo_server` 和 `echo_client` 会自动继承所需的头文件路径和链接设置。
- `echo_server` and `echo_client` link `project_boost_asio` as `PRIVATE`
  这表示 Boost 只对这两个可执行目标的编译生效，不会继续传播到其他 target。
- `hello_tests` links `GTest::gtest_main` as `PRIVATE`
  这样测试依赖就不会污染生产目标。

从工程角度看，常见的 CMake 作用域含义是：

- `PRIVATE`：只有当前 target 自己编译时需要这个依赖
- `PUBLIC`：当前 target 自己需要，而且所有依赖它的 target 也需要
- `INTERFACE`：当前 target 自己不直接使用，但依赖它的 target 需要

这个项目里的构建产物范围也比较清晰：

- `hello_lib` 会产出一个可复用静态库，供多个可执行文件复用
- `hello_world`、`echo_server`、`echo_client` 会产出可执行文件
- 测试二进制只会在 `ENABLE_TESTING=ON` 时构建
- 所有生成物都放在 `build/` 目录下，源码和构建产物相互隔离

## Logging

The scaffold includes a baseline `spdlog` setup with:

- colored console output
- file logging under `logs/`
- a shared log pattern
- level selection by build type
- convenience macros such as `LOG_INFO(...)` and `LOG_ERROR(...)`

Main entry points:

- `app::logging::init("app_name")`
- `LOG_TRACE(...)`
- `LOG_DEBUG(...)`
- `LOG_INFO(...)`
- `LOG_WARN(...)`
- `LOG_ERROR(...)`
- `LOG_CRITICAL(...)`

## Requirements

- CMake 3.21+
- A C++20 compiler
- Ninja or another supported CMake generator

## Build

```bash
cmake --preset default
cmake --build --preset default
ctest --preset default
```

On Windows with Visual Studio 2022 installed:

```bash
cmake --preset windows-msvc-debug
cmake --build --preset windows-msvc-debug
ctest --preset windows-msvc-debug
```

## Demo

```bash
./build/default/hello_world
```

Expected output:

```text
[2026-05-05 08:39:44.502] [hello_world] [info] Hello, World!
```

## Boost.Asio Echo 示例

构建：

```bash
cmake --preset windows-msvc-debug
cmake --build --preset windows-msvc-debug
```

启动服务端：

```bash
./build/windows-msvc-debug/Debug/echo_server.exe 9000
```

在另一个终端启动客户端：

```bash
./build/windows-msvc-debug/Debug/echo_client.exe 127.0.0.1 9000 "hello from client"
```

预期客户端输出：

```text
[info] Connected to 127.0.0.1:9000
[info] Sending: hello from client
[info] Received echo: hello from client
```

## Asio 运行模型

- `io_context.run()` 会进入事件循环，等待异步操作完成，并执行对应的 handler。
- 某个 handler 运行在哪个线程上，取决于当前是哪一个线程在调用这个 `io_context` 的 `run()`。
- 如果只有一个线程调用 `run()`，那所有 handler 都会在这一条线程里执行。
- 如果多个线程同时对同一个 `io_context` 调用 `run()`，那么 handler 可能运行在这些线程中的任意一个上，除非你用 `strand` 做串行化保护。
