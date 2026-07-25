# 开发者入门指南

欢迎参与 BoostGateway 项目开发。本文档给出从新克隆到首次构建、测试、运行和 IDE
调试的完整路径。命令默认面向 Linux x64 本地开发；固定 runner、发布证据和其它平台的
严格离线流程见 [`conan/README.md`](../conan/README.md)。

## 前置要求

- **OS**: Ubuntu 22.04+ 或 macOS 14+
- **CMake**: 3.21+
- **Ninja**: 默认 generator
- **Linux 编译器**: GCC 13；仓库 profile 显式使用 `/usr/bin/gcc-13` 和
  `/usr/bin/g++-13`
- **Python**: 3.12；Conan venv helper 会拒绝其它 Python major.minor
- **Conan**: 2.8.1；默认构建必需，必须使用隔离 venv，不使用全局浮动版本
- **Git**: 2.30+

可选依赖：
- **Redis**: 用于集群相关功能测试
- **Docker / OrbStack**: 用于容器化部署测试

首次配置前可检查关键工具：

```bash
gcc-13 --version
g++-13 --version
cmake --version
ninja --version
python3.12 --version
git --version
```

## 快速开始

### 克隆并准备 Conan

```bash
git clone <repo-url> boost_gateway
cd boost_gateway

python3.12 scripts/tools/ensure_conan_venv.py --conan-version 2.8.1
source .venv/conan-2.8.1/bin/activate

export CONAN_HOME="$PWD/.conan2-local"
python3 scripts/bootstrap_conan.py \
  --conan-home "$CONAN_HOME" \
  --disable-example-internal \
  --allow-public

conan install . \
  --profile:host conan/profiles/linux-gcc-x64 \
  --profile:build conan/profiles/linux-gcc-x64 \
  --lockfile conan/locks/linux-gcc-x64-debug-nogrpc-nosqlite.lock \
  -o "&:with_grpc=False" \
  -o "&:with_raft_protobuf=True" \
  -o "&:with_sqlite=False" \
  --output-folder=build/conan-debug \
  --build=missing \
  -s build_type=Debug
```

`--allow-public` 只用于允许新开发环境从 Conan Center 填充缺失包。使用公司镜像时，
在被 `.gitignore` 忽略的 `conan/remotes.local.json` 中配置实际 remote，然后去掉
`--allow-public`。已经准入并预热的 fixed runner 必须改用 `--no-remote` 和
`--build=never`，不要把开发机的联网流程当作发布证据。

### 配置和构建 Debug

```bash
cmake -S . -B build/contributor-debug -G Ninja \
  -DBOOST_DEPENDENCY_PROVIDER=conan \
  -DENABLE_TESTING=ON \
  -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_TOOLCHAIN_FILE="$PWD/build/conan-debug/build/Debug/generators/conan_toolchain.cmake"

cmake --build build/contributor-debug --parallel \
  --target project_v2_unit_tests v2_gateway_demo
```

默认 Conan 图为 `with_grpc=False`、`with_raft_protobuf=True`、`with_sqlite=False`。
gRPC 和 SQLite 是独立实验/可选构建面，不应在首次开发构建中开启。CMake 的 Conan
模式是严格模式：toolchain 或任何锁定依赖缺失时会直接失败，不会回退到 FetchContent。

仓库的 `CMakePresets.json` 当前没有注入 Conan toolchain，因此新克隆不要直接以
`cmake --preset default` 代替上述首次配置命令。

### 运行首次测试

```bash
python3 scripts/run_tests.py unit \
  --build-dir build/contributor-debug \
  --timeout 300 \
  --verbose
```

### 最快运行一个业务闭环

```bash
./build/contributor-debug/examples/v2_gateway_demo/v2_gateway_demo --script
```

`--script` 不监听网络端口，也不要求先启动五个 backend。它会在进程内执行 login、
room、ready、battle input、settlement 等基本交换，适合作为首次运行和 Gateway Runtime
修改后的快速 smoke。真实多进程路径由 `project_v2_multi_process_tests` 和 Docker
Compose 覆盖。

### Docker 方式启动

Docker runtime staging 面向完整 Release 二进制，不复用上面的 Debug 输出。先生成
Release Conan toolchain 并构建全部六个服务：

```bash
conan install . \
  --profile:host conan/profiles/linux-gcc-x64 \
  --profile:build conan/profiles/linux-gcc-x64 \
  --lockfile conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock \
  -o "&:with_grpc=False" \
  -o "&:with_raft_protobuf=True" \
  -o "&:with_sqlite=False" \
  --output-folder=build/conan-release \
  --build=missing \
  -s build_type=Release

cmake -S . -B build/release -G Ninja \
  -DBOOST_DEPENDENCY_PROVIDER=conan \
  -DENABLE_TESTING=ON \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_TOOLCHAIN_FILE="$PWD/build/conan-release/build/Release/generators/conan_toolchain.cmake"
cmake --build build/release --parallel

python3 scripts/tools/prepare_docker_runtime_context.py \
  --build-dir build/release \
  --allow-dirty
docker compose -f env/docker/docker-compose.yml build
docker compose -f env/docker/docker-compose.yml up -d
curl http://127.0.0.1:9080/health
```

staging helper 会检查六个 ELF、动态依赖和 lockfile。开发工作树通常是 dirty，因此本地
镜像使用 `--allow-dirty`；发布证据不得使用该参数。

## CLion 配置

CLion 需要消费已经由 `conan install` 生成的 toolchain。先在终端完成本页的 Debug
Conan install，再配置 IDE：

1. 在 `Settings | Build, Execution, Deployment | Toolchains` 中选择 Ninja，C 编译器
   设为 `/usr/bin/gcc-13`，C++ 编译器设为 `/usr/bin/g++-13`。
2. 在 `Settings | Build, Execution, Deployment | CMake` 新建 `Debug-Conan` profile，
   build directory 使用 `build/clion-debug`，不要复用命令行 build 目录。
3. CMake options 设置为：

```text
-DBOOST_DEPENDENCY_PROVIDER=conan
-DENABLE_TESTING=ON
-DCMAKE_BUILD_TYPE=Debug
-DCMAKE_TOOLCHAIN_FILE=/absolute/path/to/boost_gateway/build/conan-debug/build/Debug/generators/conan_toolchain.cmake
```

4. Reload CMake 后先构建 `project_v2_unit_tests`。项目已设置
   `CMAKE_EXPORT_COMPILE_COMMANDS=ON`，CLion 会从这个 CMake profile 获得完整索引。
5. 新建 `v2_gateway_demo` Run Configuration，program arguments 使用 `--script`，
   working directory 设置为仓库根目录。

调试 backend 时也必须把 working directory 设为仓库根目录，因为默认配置路径是
`config/environments/local/<service>.json`。也可以显式传入
`--config config/environments/local/<service>.json`。Debug 和 Release 应始终使用不同
的 Conan output folder 与 CMake build directory。

## 项目结构

```
boost_gateway/
├── include/              # 公共头文件
│   ├── v2/               # 主线 v2 框架头文件
│   │   ├── actor/        # Actor 模型
│   │   ├── gateway/      # 网关核心
│   │   ├── service/      # 服务层（路由、熔断、协议）
│   │   ├── battle/       # 战斗实例
│   │   ├── ecs/          # ECS 组件系统
│   │   └── ...
│   ├── v3/               # 协议演进层（proto、集群、持久化）
│   ├── app/              # 应用层（进程管理、日志、配置）
│   └── net/              # 底层网络（packet、session）
├── src/                  # 源文件（结构同 include/）
├── sdk/                  # 客户端 SDK（C++/C ABI/Python/C#）
├── examples/             # 服务入口（gateway、5 个 backend）
├── tests/                # 测试
│   ├── v2/unit/          # v2 单元测试
│   ├── v2/integration/   # v2 集成测试
│   └── perf/             # 性能测试
├── scripts/              # 验证脚本和工具
│   ├── gates/            # 门禁脚本（按类别分子目录）
│   ├── producers/        # 数据采集脚本
│   └── tools/            # 工具脚本
├── config/               # 运行时 JSON 配置
├── env/                  # 生产环境配置（Docker/K8s/监控）
├── proto/                # Protobuf schema
├── docs/                 # 文档
└── cmake/                # CMake 模块
```

## 开发工作流

### 分支策略

- `main`: 稳定分支，所有发布从 main 打 tag
- 新功能/修复：从 main 创建 feature branch，完成后 PR 合入

### 代码风格

项目使用 `.clang-format` 统一代码格式。提交前请运行：

```bash
clang-format -i <changed-files>
```

### 测试要求

- 新增代码必须有对应单元测试
- 修改公共接口需要更新集成测试
- 性能敏感路径需要 smoke test 覆盖

### CI 门禁

当前默认 CI 触发方式已经收敛：`ci.yml` 仅通过手动 `workflow_dispatch` 运行；`v*` tag push 只自动触发 `release.yml`，避免发布时重复构建。开发分支日常验证以本地命令和手动 GitHub-hosted dispatch 为主。

| 门禁 | 命令 | 触发方式 |
|---|---|---|
| 本地构建+测试 | `cmake --build build/contributor-debug --parallel && python3 scripts/run_tests.py unit --build-dir build/contributor-debug --verbose` | 本地 |
| GitHub-hosted 主线回归 | `gh workflow run ci.yml --ref main -f runner='"ubuntu-latest"'` | 手动 |
| 本地 RC 总门禁 | `python3 scripts/verify_release_candidate.py --skip-release-baseline --soak-profile smoke` | 本地/手动 |
| Release 构建与发布门禁 | `.github/workflows/release.yml` | `v*` tag 自动或手动 |

## 常见任务

### 添加新的消息类型

1. 在 `proto/v3/` 中定义 proto schema
2. 在 `include/v2/service/` 中添加 typed envelope
3. 在后端 handler 中实现处理逻辑
4. 在 `tests/v2/unit/service_boundary_test.cpp` 中添加测试

### 添加新的后端服务

1. 在 `include/v2/<service>/` 创建头文件
2. 在 `src/v2/<service>/` 创建实现
3. 在 `examples/v2_<service>_backend/` 创建入口
4. 在 `config/` 中添加配置文件
5. 更新 `CMakeLists.txt` 和 CI workflow

### 运行性能测试

```bash
# 快速 smoke（30s）
python3 scripts/producers/collect_v2_perf_baseline.py --run-preset smoke

# 完整 baseline（需要固定性能机器）
python3 scripts/producers/collect_release_baseline.py --perf-preset baseline --perf-repetitions 3 \
  --cpu-set 0-1 --loadgen-cpu-set 4-7 --loadgen-io-threads 4

# Matchmaking/Leaderboard 并发专项
python3 scripts/producers/collect_release_baseline.py --perf-preset business-capacity \
  --business-operation-scenario matchmaking \
  --business-operation-scenario leaderboard \
  --business-operation-clients 16 --business-operation-iterations 10
```

## 文档入口

| 文档 | 用途 |
|---|---|
| [架构总览](architecture-overview.md) | 组件、数据流、部署模型 |
| [当前状态](current-state.md) | 已实现能力的权威事实源 |
| [Runner Inventory](runner-inventory.md) | GitHub Actions runner 拓扑单一事实源 |
| [发布治理](release-governance.md) | 可靠性矩阵和发布检查清单 |
| [性能基线](performance-baseline.md) | 性能数据和归档口径 |
| [TLS/mTLS](tls-mtls-runbook.md) | 传输安全配置 |
| [部署运维](deployment/) | 部署、运维、配置 Runbook |
| [贡献指南](../.github/PULL_REQUEST_TEMPLATE.md) | PR 提交清单与要求 |
| [提交规范](../.github/COMMIT_CONVENTION.md) | Git 提交消息格式与约定 |

---

## 编码规范

### 文件与目录组织

- 公共头文件放 `include/v2/`（框架主线）或 `include/v3/`（协议演进层）
- 实现文件放 `src/v2/`，目录结构镜像 `include/v2/`
- 每个 `.h` 对应一个 `.cpp`（模板或 header-only 除外）
- 测试文件命名 `*_test.cpp`，放在 `tests/v2/unit/` 或 `tests/v2/integration/`

### 命名约定

| 种类 | 风格 | 示例 |
|------|------|------|
| 文件名 | `snake_case` | `backend_metrics.h`, `gateway_service_bridge.cpp` |
| 类 / 结构体 / 枚举 | `PascalCase` | `SessionManager`, `DecodedHandlerPayload` |
| 函数 / 方法 | `snake_case` | `decode_handler_payload()`, `record_latency()` |
| 变量 / 参数 | `snake_case` | `connection_pool_size`, `max_pending` |
| 宏 / 常量 | `UPPER_SNAKE` | `V2_RATE_LIMIT_CONNECTION`, `BOOST_DEPENDENCY_PROVIDER` |
| 命名空间 | `snake_case` | `v2::gateway`, `v3::cluster` |

### 代码风格

- 使用 `.clang-format`（LLVM 风格，4 空格，100 列限制）统一格式：
  ```bash
  clang-format -i <changed-files>
  ```
- 禁止 `using namespace std;` 或 `using namespace boost;`
- 头文件使用前向声明而非直接 `#include`，仅在需要完整类型时包含
- 公共 API 优先使用 typed contract（`DecodedHandlerPayload`、`TypedEnvelope`），不扩展 raw JSON
- 禁止把业务规则写入 `include/v2/` 或 `src/v2/` 公共框架层；业务 demo 放在 `demo/games/`

### C++ 标准

- 项目使用 **C++20**，可使用的特性包括 `std::span`、`concepts`、`std::midpoint`、三路比较运算符
- 新代码应优先使用标准库设施而非 Boost 对应组件（Boost 1.86 作为后备）

## 测试政策

### 测试分层

| 层级 | 位置 | 执行时间 | 触发方式 |
|------|------|---------|---------|
| **单元测试** | `tests/v2/unit/` | 秒级 | 本地 / 手动主 CI |
| **集成测试** | `tests/v2/integration/` | 分钟级 | 本地 / 手动主 CI |
| **E2E 多进程** | `tests/v2/integration/` (multi_process) | 分钟级 | 手动主 CI / 专项 workflow |
| **性能 Smoke** | `tests/perf/` (可选构建) | 30s | `perf-regression.yml` 手动 `perf_preset=smoke` |
| **模糊测试** | `tests/fuzz/` (可选构建) | 自定义 | 手动 |
| **安全测试** | `tests/security/` (可选构建) | 分钟级 | 手动 |

### 运行命令

```bash
# 全部测试
python3 scripts/run_tests.py all --build-dir build/contributor-debug --verbose

# 分层运行
python3 scripts/run_tests.py unit --build-dir build/contributor-debug --verbose
python3 scripts/run_tests.py integration --build-dir build/contributor-debug --verbose
python3 scripts/run_tests.py e2e --build-dir build/contributor-debug --verbose

# 仅 SDK 测试
python3 scripts/run_tests.py sdk --build-dir build/contributor-debug --verbose

# Release 模式（性能相关）
cmake --build build/release --parallel
python3 scripts/run_tests.py all --build-dir build/release --verbose
```

### 测试要求

- **新增代码必须附带单元测试**，覆盖核心逻辑分支
- **修改公共接口**（handler 签名、消息格式、配置结构）必须更新集成测试
- **性能敏感路径**需要 smoke 覆盖（通过 `collect_v2_perf_baseline.py --run-preset smoke`）
- 测试命名：测试套件使用 `PascalCase`（`HealthCheckTest`），用例使用 `snake_case`（`all_pass_when_backends_healthy`）
- 框架使用 Google Test + `gtest_discover_tests()` CMake 集成

### 统一测试入口

`scripts/run_tests.py` 封装了 ctest 的分层过滤：

```bash
# 运行当前 build directory 中的全部测试
python3 scripts/run_tests.py all --build-dir build/contributor-debug

# 按层级运行
python3 scripts/run_tests.py unit --build-dir build/contributor-debug
python3 scripts/run_tests.py integration --build-dir build/contributor-debug
python3 scripts/run_tests.py e2e --build-dir build/contributor-debug
python3 scripts/run_tests.py sdk --build-dir build/contributor-debug

# 可选构建层（需要 CMake 选项）
python3 scripts/run_tests.py perf          # 性能基准测试
python3 scripts/run_tests.py fuzz          # 模糊测试
python3 scripts/run_tests.py security      # 安全测试

# 选项
python3 scripts/run_tests.py unit --timeout 120        # 超时控制
python3 scripts/run_tests.py unit --parallel 4         # 并行数
python3 scripts/run_tests.py --list                    # 列出可用层级
```

内部通过 CTest label（`-L unit`）过滤，与 `gtest_discover_tests(PROPERTIES LABELS ...)` 配合。

### 验证矩阵

各 CI 场景执行的测试层：

| CI 场景 | 触发方式 | 执行的测试层 |
|---------|---------|-------------|
| 主 CI（`ci.yml`） | 手动 `workflow_dispatch` | unit + integration + e2e |
| 性能门禁 | `workflow_dispatch` | `perf-regression.yml`，`perf_preset=smoke|baseline|capacity` |
| Release 构建 | `v*` tag push / 手动 | unit + integration + e2e + 所选性能 profile |
| Bounded stability | 手动 | `nightly-stability.yml`，smoke/short/medium soak |
| 固定 Runner 容量 | 手动 | e2e + perf capacity |
| 本地开发 | `python3 scripts/run_tests.py <layer> --build-dir <dir>` | 按改动选择 unit / integration / e2e / sdk；`all` 会运行已注册的全部测试 |

### 测试层级选择指南

新增测试文件时按以下规则选择层级：

- **单元测试**（`tests/v2/unit/`）: 纯逻辑、无外部依赖（数据库、网络、文件系统）。单文件单职责，命名 `*_test.cpp`。秒级完成。
- **集成测试**（`tests/v2/integration/`）: 涉及服务间通信、配置加载、进程启动/停止。依赖 `project_v2` 框架对象。分钟级。
- **E2E 多进程**（`tests/v2/integration/`，`project_v2_multi_process_tests` 目标）: 启动真实 OS 进程做全链路验证。需要编译好的 service binary。分钟级。
- **SDK 测试**（`sdk/tests/`）: 分 unit（纯 SDK 逻辑）和 business flow（与 server 交互）。按 `-L sdk` 过滤。
- **性能/模糊/安全**（可选构建）: 不影响默认构建路径，通过 CMake option 单独开启。

## Benchmark 政策

| 预设 | 用途 | 时长 | 触发 |
|------|------|------|------|
| `smoke` | 手动性能冒烟 | ~30s | `perf-regression.yml` |
| `baseline` | Release 准入 | ~30min | 手动 `release.yml` + `perf-preset=baseline` |
| `capacity` | 容量基线 | ~60min | 固定 runner / 手动 |
| `business-capacity` | 业务闭环容量 | ~60min | 固定 runner / 手动 |

```bash
# 手动 smoke（仓库当前没有 PR 自动性能 workflow）
python3 scripts/producers/collect_v2_perf_baseline.py --run-preset smoke

# 完整基线
python3 scripts/producers/collect_release_baseline.py --perf-preset baseline --perf-repetitions 3 \
  --cpu-set 0-1 --loadgen-cpu-set 4-7 --loadgen-io-threads 4
```

CPU 隔离仅支持 Linux：`--cpu-set` 只约束 Gateway 和后端服务，`--loadgen-cpu-set` 约束采集器、pressure 和进程内业务客户端；两者必须显式不重叠，指定 CPU 必须属于 runner 的当前 allowed set。性能门禁见 `config/perf/v2_arch_baseline_gates.json`。

## 协议开发指南

添加新的消息类型的完整路径：

1. **定义 proto schema**（可选，长期方向）: 在 `proto/v3/` 中添加 `.proto` 定义
2. **添加 typed envelope**（当前主线方式）: 在 `include/v2/service/` 中定义请求/响应类型，添加 `HandlerPayloadEncoding` 枚举
3. **实现后端 handler**: 在对应 `src/v2/<service>/<service>_backend_service.cpp` 中使用 `decode_handler_payload<T>()` 和 `wrap_typed_response_if_needed()`
4. **添加测试**: 在 `tests/v2/unit/service_boundary_test.cpp` 中验证 typed encode/decode

**规则**:
- 新消息必须走 typed / proto，不得扩展 legacy raw JSON
- raw JSON 路径已收缩到内部 Raft RPC，不适用于业务消息
- 参考现有 handler 实现（如 `room_create`、`match_join`）作为模板

## SDK 扩展指南

SDK 版本独立管理（当前 `4.2.0`），支持 C++ / C ABI / Python / C#。

### 新增 API 的路径

1. **C++ API** → `sdk/include/boost_gateway/sdk/client.h` + `sdk/src/client.cpp`
2. **C ABI** → `sdk/include/boost_gateway/sdk/c_api.h` + `sdk/src/c_api.cpp`
3. **Python 绑定** → `sdk/python/`
4. **C# 绑定** → `sdk/csharp/SdkClient.cs`
5. **测试** → `sdk/tests/unit/` + `sdk/tests/sdk_integration_test.cpp`
6. **示例** → `sdk/examples/`

### 规则

- 新增 SDK API 必须绑定协议 schema 和测试
- SDK 保持向后 ABI 兼容（不删除或重命名公开符号）
- Python/C# 绑定通过 C ABI 间接调用，避免直接依赖 C++ ABI

## Demo / Plugin 开发指南

Demo（如 `demo/games/tank_battle/`）用于在框架能力之上验证业务逻辑，不构建在默认主线路径中。

### 规则

- 新 demo 必须通过 `BOOST_BUILD_*_DEMO=ON` 可选项控制构建
- Demo 代码不得修改 `include/v2/`、`src/v2/`、`sdk/` 框架层（bug 修复除外）
- 框架提供 SPI 和运行时 hook，demo 通过 SPI 扩展行为
- 参考 `demo/games/tank_battle/` 作为最小 demo 骨架

## 安全披露

### 提交前检查

- 不提交 `.env`、`credentials.json`、`*.key`、`*.pem`（测试证书例外）等敏感文件
- 不硬编码生产地址、令牌或密钥
- CI 日志中不输出连接字符串或凭据

### 报告安全问题

如有安全漏洞或敏感信息泄露，通过 GitHub Issues 报告（不公开细节）或联系仓库维护者。本项目暂无公开的 CVE 编号分配流程。

---

## 已完成的治理工作

这些是该项目当前的治理基础设施，新开发者应了解：

| 领域 | 状态 | 关键文件 |
|------|------|---------|
| **G3 Legacy Raw JSON 收束** | 全部 5 服务域 29 个业务 handler 已统一接入 adapter，且 29 个均已具备 schema-backed typed contract；内部 Raft 已严格双读 JSON/protobuf，legacy JSON 仅作为 v3.6 writer 兼容路径保留 | `include/v2/service/envelope_adapter.h`, `proto/v3/raft.proto`, `docs/legacy/legacy-helper-inventory.md` |
| **G4 Conan 依赖治理** | `BOOST_DEPENDENCY_PROVIDER=conan` 严格默认；lockfile/profile/workflow 已落地；缺包直接失败 | `conan/README.md`, `conanfile.py`, `conan/locks/` |
| **G5 CI 构建缓存** | 4 个主要 build/perf workflow 启用 sccache；每次运行归档构建耗时与缓存命中率至 `runtime/perf/build-times/` | `.github/workflows/ci.yml`, `docs/performance-baseline.md` |
| **G8 v1 遗留模块退场** | v1 game/examples/tests 已移除，CMake 选项已清理 | ✅ 已完成 |
