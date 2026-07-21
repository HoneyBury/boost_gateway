# 开发者入门指南

欢迎参与 BoostGateway 项目开发。本文档帮助你在 30 分钟内完成环境搭建和首次构建。

## 前置要求

- **OS**: Ubuntu 22.04+ 或 macOS 14+（Linux 和 macOS 共享 POSIX API）
- **CMake**: 3.21+
- **Ninja**: 推荐作为默认 generator
- **编译器**: GCC 11+ 或 Clang 15+（需要 C++20 支持）
- **Python**: 3.10+（用于验证脚本）
- **Git**: 2.30+

可选依赖：
- **Conan 2.8.1**: 默认构建必需；使用隔离 venv 和仓库 lockfile
- **Redis**: 用于集群相关功能测试
- **Docker / OrbStack**: 用于容器化部署测试

## 快速开始

### 克隆和构建

```bash
git clone <repo-url> boost_gateway
cd boost_gateway

# 先按 conan/README.md 完成 lockfile 驱动的 conan install，然后严格配置
cmake -S . -B build/release -G Ninja -DBOOST_DEPENDENCY_PROVIDER=conan \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_TOOLCHAIN_FILE=build/conan-release/build/Release/generators/conan_toolchain.cmake
cmake --build build/release --parallel
```

### 运行测试

```bash
ctest --preset release --timeout 300
```

### 运行网关 Demo

```bash
# 启动 gateway（需要先启动 5 个后端服务，或使用单进程模式）
./build/release/examples/v2_gateway_demo/v2_gateway_demo --port 9201
```

### Docker 方式启动

```bash
python3 scripts/tools/prepare_docker_runtime_context.py --build-dir build/release
docker compose -f env/docker/docker-compose.yml build
docker compose -f env/docker/docker-compose.yml up -d
curl http://127.0.0.1:9080/health
```

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
| 本地构建+测试 | `cmake --preset default && ctest --preset default --timeout 300` | 本地 |
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
python3 scripts/collect_v2_perf_baseline.py --run-preset smoke

# 完整 baseline（需要固定性能机器）
python3 scripts/collect_release_baseline.py --perf-preset baseline --perf-repetitions 3 \
  --cpu-set 0-1 --loadgen-cpu-set 4-7 --loadgen-io-threads 4

# Matchmaking/Leaderboard 并发专项
python3 scripts/collect_release_baseline.py --perf-preset business-capacity \
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
ctest --preset default --timeout 300

# 分层运行
ctest --preset default -R project_v2_unit_tests
ctest --preset default -R project_v2_integration_tests
ctest --preset default -R project_v2_multi_process_tests

# 仅 SDK 测试
ctest --preset default -R sdk

# Release 模式（性能相关）
cmake --build --preset release
ctest --preset release
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
# 运行全部测试（等同 ctest --preset default）
python3 scripts/run_tests.py

# 按层级运行
python3 scripts/run_tests.py unit          # 单元测试
python3 scripts/run_tests.py integration   # 集成测试
python3 scripts/run_tests.py e2e           # 多进程 E2E
python3 scripts/run_tests.py sdk           # SDK 测试

# 可选构建层（需要 CMake 选项）
python3 scripts/run_tests.py perf          # 性能基准测试
python3 scripts/run_tests.py fuzz          # 模糊测试
python3 scripts/run_tests.py security      # 安全测试

# 选项
python3 scripts/run_tests.py unit --preset release     # Release 模式
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
| 本地开发 | `python3 scripts/run_tests.py` | unit + integration（默认） |

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
python3 scripts/collect_v2_perf_baseline.py --run-preset smoke

# 完整基线
python3 scripts/collect_release_baseline.py --perf-preset baseline --perf-repetitions 3 \
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

SDK 版本独立管理（当前 `4.1.0`），支持 C++ / C ABI / Python / C#。

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
| **G3 Legacy Raw JSON 收束** | 全部 5 服务域 29 个业务 handler 已统一接入 adapter，且 29 个均已具备 schema-backed typed contract；raw JSON 仅保留在内部 Raft RPC | `include/v2/service/envelope_adapter.h`, `docs/legacy/legacy-helper-inventory.md` |
| **G4 Conan 依赖治理** | `BOOST_DEPENDENCY_PROVIDER=conan` 严格默认；lockfile/profile/workflow 已落地；缺包直接失败 | `conan/README.md`, `conanfile.py`, `conan/locks/` |
| **G5 CI 构建缓存** | 4 个主要 build/perf workflow 启用 sccache；每次运行归档构建耗时与缓存命中率至 `runtime/perf/build-times/` | `.github/workflows/ci.yml`, `docs/performance-baseline.md` |
| **G8 v1 遗留模块退场** | v1 game/examples/tests 已移除，CMake 选项已清理 | ✅ 已完成 |
