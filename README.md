# BoostGateway

BoostGateway 是一个面向实时业务的企业级 C++20 服务框架。默认主链由 TCP Gateway、
Login、Room、Battle、Matchmaking、Leaderboard 和多语言 SDK 组成，并提供 Redis/Raft、
TLS、观测、容器部署和生产证据门禁。

最新发布版本是 [v3.6.2](https://github.com/HoneyBury/boost_gateway/releases/tag/v3.6.2)，
包含 Linux x64、Linux ARM64、macOS ARM64 runtime，SDK 4.2.0，以及对应的符号、SBOM、
checksum 和 provenance。仓库历史名称为 `BoostAsioDemo`，部分兼容路径仍保留旧名称。

## 当前状态

| 领域 | 当前结论 |
|---|---|
| 默认传输 | SDK + TCP Gateway + `BackendEnvelope`；gRPC 仍为实验能力 |
| 默认依赖 | Conan 2.8.1、仓库 profile/lockfile、CMake `conan` provider |
| 支持平台 | Linux x64、Linux ARM64、macOS ARM64；平台证据不可互换 |
| 发布状态 | v3.6.2 三平台 runtime、SDK 和符号资产已发布并完成独立复验 |
| 当前主线 | Ubuntu 24.04 x64 单节点自动部署、观测、备份恢复和 30 天不可变验证 |

权威事实和当前边界见 [当前项目事实源](docs/current-state.md)，当前执行顺序见
[主线执行计划](docs/mainline-execution-plan.md)。

## 快速开始

新贡献者从 [开发者入门指南](docs/ONBOARDING.md) 开始。该文档包含 Python 3.12、
GCC 13、Conan 2.8.1、Debug/Release 构建、CLion、测试和 Docker 的完整配置。

完成 Conan 和 Debug 配置后，最短验证路径是：

```bash
cmake --build build/contributor-debug --parallel \
  --target project_v2_unit_tests v2_gateway_demo
python3.12 scripts/run_tests.py unit \
  --build-dir build/contributor-debug --verbose
build/contributor-debug/examples/v2_gateway_demo/v2_gateway_demo --script
```

`--script` 在进程内执行 login、room、battle 和 settlement smoke，不需要先启动五个
backend。完整六服务环境见
[部署快速入门](docs/deployment/deployment-quickstart.md)。

## 服务拓扑

| 组件 | 默认本地端口 | 入口 |
|---|---:|---|
| Gateway TCP | 9201 | `v2_gateway_demo` |
| Gateway management HTTP | 9080 | `/health`、`/metrics` |
| Login | 9202 | `v2_login_backend` |
| Room | 9302 | `v2_room_backend` |
| Battle | 9303 | `v2_battle_backend` |
| Matchmaking | 9304 | `v2_match_backend` |
| Leaderboard | 9305 | `v2_leaderboard_backend` |

详细的数据流、模块边界和部署模型见
[架构总览](docs/architecture-overview.md)。

## 常用验证

```bash
python3.12 scripts/gates/governance/check_current_docs_install.py
python3.12 scripts/check_mainline_readiness.py
python3.12 scripts/gates/governance/check_script_inventory.py
python3.12 scripts/gates/governance/check_config_source_layout.py
python3.12 scripts/verify_release_candidate.py \
  --skip-release-baseline --soak-profile smoke
```

容量、长稳、TLS、恢复和发布证据需要对应 fixed runner 或外部依赖，不能用一次本地
smoke 代替。完整门禁矩阵见 [发布治理](docs/release-governance.md)。

## 文档入口

| 文档 | 用途 |
|---|---|
| [文档索引](docs/README.md) | 当前维护文档、历史归档和事实优先级 |
| [开发者入门](docs/ONBOARDING.md) | 构建、测试、CLion、代码规范和贡献流程 |
| [当前状态](docs/current-state.md) | 已实现能力、默认边界和当前工作 |
| [架构总览](docs/architecture-overview.md) | 组件、数据流、端口和部署模型 |
| [主线执行计划](docs/mainline-execution-plan.md) | v3.6.2 发布后的企业运营主线 |
| [性能基线](docs/performance-baseline.md) | 性能事实、测量口径和声明边界 |
| [固定 Runner 手册](docs/fixed-runner-playbook.md) | Conan/Docker cache 和生产证据操作 |

历史版本计划和已结束交付记录位于
[docs/archive](docs/archive/README.md)，不作为当前实施依据。

## CI 与发布

- 普通 branch push/PR 不自动触发主流水线；`ci.yml` 通过手动 dispatch 执行。
- `v*` tag push 自动触发 `release.yml`。
- 性能、长稳、生产证据和平台专项 workflow 均按需手动触发。
- runner 选择以 `.github/runner-matrix.json` 和
  [Runner Inventory](docs/runner-inventory.md) 为准。

## 许可证

[MIT](LICENSE)
