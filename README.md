# BoostGateway

> Historical repository name: `BoostAsioDemo`. The repository path and some compatibility surfaces still keep that name during the transition period.

高性能 C++20 实时服务框架，当前主线版本基线为 `v3.5.0`。项目主链已经收束到 `gateway + login + room + battle + matchmaking + leaderboard` 六服务闭环，并具备 SDK、Redis/Raft、TLS profile、生产候选证据门禁等能力。

## 当前状态

- 当前事实源：`docs/current-state.md`
- 当前主线定位：企业级实时服务框架，而不是继续扩张功能面的 demo 集合
- 当前对外名称：`BoostGateway`
- 当前完成状态：
  - 明文 SDK full-flow 通过
  - backend TLS SDK full-flow 通过
  - SDK package consumer 通过
  - N5 SDK enterprise delivery 通过
  - R0 production candidate evidence 通过

## 快速入口

- 主文档入口：`docs/README.md`
- 项目蓝图规划：`docs/project-blueprint.md`
- legacy/helper 清单：`docs/legacy-helper-inventory.md`
- 服务端部署快速说明：`docs/deployment-quickstart.md`
- 当前事实源：`docs/current-state.md`
- 架构总览：`docs/architecture-overview.md`
- 可靠性矩阵：`docs/reliability-matrix.md`
- 脚本入口索引：`docs/script-inventory.json`
- 性能事实：`docs/performance-baseline.md`
- 发布/验收门禁：`docs/v3-release-checklist.md`

当前 CI/CD 平台选择：

- 普通 branch push / PR 不再自动触发流水线；自动触发只保留特定 release tag，当前约定为 `v*`。
- `.github/workflows/release.yml` 在推送 `v*` tag 时自动执行 release package/publish；`.github/workflows/ci.yml` 也只在 `v*` tag 或手动 dispatch 时运行 Linux Conan 主线验证。
- 性能 smoke、nightly stability、fixed-runner evidence、release/capacity 等入口保留 `workflow_dispatch`，需要时手动触发；具体触发条件以 `.github/workflows/*.yml` 为准。
- `.github/runner-matrix.json` 作为版本化 runner/默认标签配置源，用于记录当前 active runner 选择和 fixed-runner 默认标签；变更 tag 策略或 runner 拓扑时需要同步更新 workflow 与该文件。

## 常用验证入口

```bash
python scripts/verify_release_candidate.py --skip-release-baseline --soak-profile smoke
python scripts/check_current_docs_install.py
python scripts/check_mainline_readiness.py
python scripts/check_script_inventory.py
python scripts/check_legacy_helper_inventory.py
python scripts/check_validation_summary_contract.py
python scripts/check_config_source_layout.py
python scripts/check_p3_p4_release_readiness.py
python scripts/verify_sdk_enterprise_delivery.py --build-dir build/Release --skip-build
python scripts/verify_production_candidate_evidence.py --build-dir build/Release --skip-build
python scripts/check_production_evidence_manifest.py
python scripts/render_production_readiness_report.py
```

Conan PoC 入口：

```bash
export CONAN_HOME=$PWD/.conan2-local
python scripts/generate_conan_lock.py --profile conan/profiles/linux-gcc-x64 --build-type Debug --without-sqlite --allow-public
conan install . --profile:host conan/profiles/linux-gcc-x64 --profile:build conan/profiles/linux-gcc-x64 --lockfile conan/locks/linux-gcc-x64-debug-nogrpc-nosqlite.lock -o "&:with_grpc=False" -o "&:with_sqlite=False" --output-folder=build/conan-debug --build=missing -s build_type=Debug
cmake -S . -B build/linux-ninja-debug-conan -G Ninja -DBOOST_USE_CONAN_DEPS=ON -DENABLE_TESTING=OFF -DCMAKE_BUILD_TYPE=Debug -DCMAKE_TOOLCHAIN_FILE=build/conan-debug/build/Debug/generators/conan_toolchain.cmake
cmake --build build/linux-ninja-debug-conan --parallel
```

说明：

- 当前仓库提供 `conanfile.py` 形式的 Conan 2 PoC。
- 推荐把 `CONAN_HOME` 指到仓库内目录（例如 `.conan2-local`），避免受用户主目录权限影响。
- `python scripts/bootstrap_conan.py` 会优先准备仓库内 Conan home，并按 `conan/remotes.example.json` 配置 remote。
- 如果存在 `conan/remotes.local.json`，它会覆盖示例 remote 配置，适合内网落地。
- 也可以通过环境变量注入内网 remote，例如 `CONAN_REMOTE_URL`、`CONAN_REMOTE_NAME`。
- 默认策略是先禁用公网 `conancenter`，优先本地 cache/内网 remote；只有显式传 `--allow-public` 才会启用公网 remote。
- 如需完全离线，只走本地 cache，可使用 `python scripts/bootstrap_conan.py --no-remote`。
- `BOOST_USE_CONAN_DEPS=ON` 时优先使用 Conan 生成的 `CMakeDeps/CMakeToolchain` 结果。
- 当前依赖分层已经收敛为：
  - Conan-first：`fmt`、`spdlog`、`nlohmann_json`、`hiredis`、`boost::headers`
  - 双轨保守：`OpenSSL`
  - 实验保留：`protobuf`、`grpc`、`sqlite3`
- 当前已打通的 Conan 主线路径以 `with_grpc=False`、`with_sqlite=False` 为默认 lockfile 口径；`sqlite3` 保留为可选/实验层，不阻塞主线 Conan install。
- 当前已落仓 Linux 的 `nosqlite` lockfile：
  - `conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock`
  - `conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock`
- CMake 会统一汇总 Conan 探测缺失项；只要关键包不齐，就自动回退到 `FetchContent/third_party`。
- 如果 Conan 依赖未准备好或未启用，项目仍回退到现有 `FetchContent/third_party` 路径。
- SDK 安装打包现在会同时兼容 Conan 和 fallback 两种 `nlohmann_json` 头文件来源，不再假定只能来自 `nlohmann_json_SOURCE_DIR`。
- `project_v3` 也不再显式依赖 `hiredis_SOURCE_DIR`；Conan 与 fallback 都统一走 `hiredis` target 暴露的头文件路径。
- Linux `nosqlite` lockfile 已验证可真实生成，lockfile-based `conan install` 能进入解图、取包与缺失包构建阶段；真正在 Ubuntu fixed-runner 上的实跑结论应以 CI 结果为准。

独立 Conan 流水线：

- `.github/workflows/conan-validate.yml`
- 仅手动触发
- 支持 `Debug/Release`
- 支持 `--allow-public`
- 支持 `--no-remote`
- 支持显式传入 `runner`、`conan_profile` 与 `conan_lockfile`
- 默认 `use_existing_workspace=true`；在 Linux self-hosted runner 上可复用本地仓库工作区，绕过不稳定的远端 checkout
- 可选打开 `ENABLE_TESTING`
- 已在 Linux self-hosted runner 上完成真实 dispatch

## 运行入口

推荐优先阅读 `docs/deployment-quickstart.md`。本机 Docker/OrbStack 联调：

```bash
docker compose -f env/docker/docker-compose.yml build
docker compose -f env/docker/docker-compose.yml up -d
curl http://127.0.0.1:9080/health
```

客户端连接 gateway：`127.0.0.1:9201`。

本机二进制开发入口：

```bash
# gateway
build/Release/examples/v2_gateway_demo/v2_gateway_demo

# login backend
build/Release/examples/v2_login_backend/v2_login_backend

# room backend
build/Release/examples/v2_room_backend/v2_room_backend

# battle backend
build/Release/examples/v2_battle_backend/v2_battle_backend

# matchmaking backend
build/Release/examples/v2_match_backend/v2_match_backend

# leaderboard backend
build/Release/examples/v2_leaderboard_backend/v2_leaderboard_backend
```

Legacy 兼容入口说明：

- `echo_server` 已降级为 legacy bridge，仅在 `-DBOOST_BUILD_V1_LEGACY_EXAMPLES=ON` 时显式构建。
- v1 `login_server` / `room_server` / `battle_server` / `gateway_pressure` 和 `*_demo` showcase 也已降级为 legacy，仅在 `-DBOOST_BUILD_V1_LEGACY_EXAMPLES=ON` 时显式构建。
- `src/game` / `project_game` 与根级 `tests/unit` / `tests/integration` 也已视为 legacy-v1 surface；如需兼容排查，分别显式打开 `-DBOOST_BUILD_V1_LEGACY_CORE=ON` 与 `-DBOOST_BUILD_V1_LEGACY_TESTS=ON`。
- `tank_battle_demo` 与 `realtime_echo_plugin` 继续保持默认关闭，仅作为 demo/plugin 样例。

## 文档策略

- `docs/` 顶层只保留当前仍维护的主文档
- 历史版本、阶段计划、交付记录和旧 runbook 已迁入 `docs/archive/`
- 如果顶层文档和归档文档冲突，以 `docs/current-state.md` 为准

## 许可证

MIT
