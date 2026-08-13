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

Linux 新 clone 的推荐入口会幂等创建开发测试环境、固定 Conan 环境、依赖图和 Debug
build tree。只有显式传入 `--allow-public` 时才允许从 pip/Conan Center 补齐缺失内容：

```bash
python3.12 scripts/dev.py setup --allow-public
.venv/dev/bin/python scripts/dev.py ready
```

空 Conan cache 的首次 `setup` 会下载和构建数 GiB 的锁定依赖；实际耗时主要取决于 Boost
等上游归档的网络吞吐，不能用后续增量运行的分钟级耗时估算。公司网络或公网受限环境应先按
下文配置 `conan/remotes.local.json`，再执行不带 `--allow-public` 的 `setup`。不要通过删除
lockfile、改用全局 Conan 或反复中断下载来缩短首次准备。

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

`setup` 已运行本地诊断所需的准备步骤。单独排查工具链时可运行：
记录的 CMake/CTest；Ubuntu 22.04 的系统 `python3` 可能仍是 3.10，因此不要省略
`python3.12`：

```bash
python3.12 scripts/dev.py doctor
```

不要从 `scripts/` 中逐个猜测命令。先查看按维护领域整理的稳定入口；输出同时标明允许的
运行环境、典型时长、外部副作用和权威操作文档：

```bash
python3.12 scripts/dev.py commands
python3.12 scripts/dev.py commands --domain contributor
python3.12 scripts/dev.py commands --domain release --json
```

`commands` 只展示受支持的 public entrypoint。内部 gate、producer 和 tool 是实现细节；除非
对应 runbook 明确要求，否则新贡献者不应直接把它们加入 workflow 或文档。

开发过程中可查看基于 Git diff 的建议测试层。该结果只用于选择快速反馈，不会让 `ready` 或
CI 跳过完整治理：

```bash
python3.12 scripts/run_tests.py --recommend
```

### 克隆并准备 Conan

普通 Linux 贡献者使用上面的 `dev.py setup`，不需要手工执行本节命令。以下展开命令只用于
排查依赖或配置公司 Conan 镜像。

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

`contributor-debug` preset 与 `dev.py setup` 使用同一 Conan toolchain 和 build tree；依赖已
准备完成后可使用 `cmake --preset contributor-debug`。通用 `default` preset 不承担 Conan
首次安装。

### 运行首次测试

```bash
python3.12 scripts/dev.py test unit \
  --build-dir build/contributor-debug \
  --timeout 300 \
  --verbose
```

### 最快运行一个业务闭环

```bash
python3.12 scripts/dev.py smoke --build-dir build/contributor-debug
```

该命令会增量构建 unit test 和 gateway demo、运行 unit 层，再用 `--script` 执行业务
smoke。`--script` 不监听网络端口，也不要求先启动五个 backend。它会在进程内执行 login、
room、ready、battle input、settlement 等基本交换，适合作为首次运行和 Gateway Runtime
修改后的快速 smoke。真实多进程路径由 `project_v2_multi_process_tests` 和 Docker
Compose 覆盖。

排查 facade 本身时，可直接运行其最终业务命令：
`build/contributor-debug/examples/v2_gateway_demo/v2_gateway_demo --script`。

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
- ownership、review、敏感变更和紧急路径见根目录 `CONTRIBUTING.md` 与 `GOVERNANCE.md`

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

目标为 `main` 的 PR 自动触发 `ci.yml`：PR 路径固定使用 GitHub-hosted
`ubuntu-latest`，单 job 最长 45 分钟，同一 PR 的新提交会取消旧运行。手动
`workflow_dispatch` 继续用于分支诊断，并保留显式 runner 选择；`v*` tag push 只自动
触发 `release.yml`。

| 门禁 | 命令 | 触发方式 |
|---|---|---|
| 本地工具诊断 | `python3.12 scripts/dev.py doctor` | 本地 |
| 本地构建+测试+业务 smoke | `python3.12 scripts/dev.py smoke --build-dir build/contributor-debug` | 本地 |
| 脚本/workflow/文档治理和完整 Python 契约测试 | `.venv/dev/bin/python scripts/dev.py check` | 本地 |
| PR build/test/governance | `linux-build-and-test` | 目标为 `main` 的 PR 自动触发 |
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
| [维护者指南](maintainer-guide.md) | 代码地图、脚本/workflow 治理、变更路径和验证分层 |
| [当前状态](current-state.md) | 已实现能力的权威事实源 |
| [Runner Inventory](runner-inventory.md) | GitHub Actions runner 拓扑单一事实源 |
| [发布治理](release-governance.md) | 可靠性矩阵和发布检查清单 |
| [性能基线](performance-baseline.md) | 性能数据和归档口径 |
| [TLS/mTLS](tls-mtls-runbook.md) | 传输安全配置 |
| [部署运维](deployment/) | 部署、运维、配置 Runbook |
| [贡献指南](../.github/PULL_REQUEST_TEMPLATE.md) | PR 提交清单与要求 |
| [仓库贡献策略](../CONTRIBUTING.md) | review、测试、敏感变更和文档要求 |
| [安全策略](../SECURITY.md) | 非公开漏洞披露与响应边界 |
| [支持策略](../SUPPORT.md) | 支持范围和 Issue 路由 |
| [仓库治理](../GOVERNANCE.md) | ownership、紧急变更和 GitHub 外部设置边界 |
| [提交规范](../.github/COMMIT_CONVENTION.md) | Git 提交消息格式与约定 |

---

## 编码与测试政策

编码风格、文件组织、测试要求和 PR review 规则统一由
[`CONTRIBUTING.md`](../CONTRIBUTING.md) 维护。日常分层测试使用：

```bash
.venv/dev/bin/python scripts/dev.py test unit --build-dir build/contributor-debug --verbose
.venv/dev/bin/python scripts/dev.py test integration --build-dir build/contributor-debug --verbose
.venv/dev/bin/python scripts/dev.py ready
```

新增行为必须附带对应层级的测试；公共协议、SDK ABI、配置或部署边界变更必须同时提供兼容与回滚说明。

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

SDK 版本独立管理（当前 `4.2.1`），支持 C++ / C ABI / Python / C#。

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

如有安全漏洞或敏感信息泄露，不要在公开 Issue 中披露细节；按
[`SECURITY.md`](../SECURITY.md) 使用私密披露入口联系维护者。
