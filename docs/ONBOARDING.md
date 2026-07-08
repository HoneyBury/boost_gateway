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
- **Conan 2**: 用于依赖管理（非必须，项目有 FetchContent fallback）
- **Redis**: 用于集群相关功能测试
- **Docker / OrbStack**: 用于容器化部署测试

## 快速开始

### 克隆和构建

```bash
git clone <repo-url> boost_gateway
cd boost_gateway

# Debug 构建（默认）
cmake --preset default
cmake --build --preset default --parallel

# Release 构建
cmake --preset release
cmake --build --preset release --parallel
```

### 运行测试

```bash
ctest --preset default --timeout 300
```

### 运行网关 Demo

```bash
# 启动 gateway（需要先启动 5 个后端服务，或使用单进程模式）
./build/default/examples/v2_gateway_demo/v2_gateway_demo --port 9201
```

### Docker 方式启动

```bash
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

PR 合入前需要通过以下门禁：

| 门禁 | 命令 | 触发方式 |
|---|---|---|
| 构建+测试 | `cmake --preset default && ctest --preset default` | 自动 |
| RC 总门禁 | `python3 scripts/verify_release_candidate.py --skip-release-baseline --soak-profile smoke` | v* tag |

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
python3 scripts/collect_v2_perf_baseline.py --preset smoke

# 完整 baseline（需要固定性能机器）
python3 scripts/collect_release_baseline.py --perf-preset baseline --perf-repetitions 3
```

## 文档入口

| 文档 | 用途 |
|---|---|
| [架构总览](architecture-overview.md) | 组件、数据流、部署模型 |
| [当前状态](current-state.md) | 已实现能力的权威事实源 |
| [发布治理](release-governance.md) | 可靠性矩阵和发布检查清单 |
| [性能基线](performance-baseline.md) | 性能数据和归档口径 |
| [TLS/mTLS](tls-mtls-runbook.md) | 传输安全配置 |
| [部署运维](deployment/) | 部署、运维、配置 Runbook |
