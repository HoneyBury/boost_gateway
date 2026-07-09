# 项目蓝图规划

更新时间：2026-07-08

本文档用于指导 BoostGateway 后续 6 个月以上的开发、维护和取舍。当前实现事实仍以 `docs/current-state.md` 为准；本文档在该事实基线上定义未来规划、差距和验收门禁。若本文档与 `current-state.md` 对”已经实现”的判断冲突，以 `current-state.md` 和可执行验证脚本结果为准；若涉及未来方向，以本文档为优先规划依据。当前 1-3 个月的主线执行顺序与收口动作，已单独整理到 `docs/mainline-execution-plan.md`。

## 一句话定位

将 BoostGateway 从”高工程化 C++ 实时服务 demo 仓库”收敛为”企业级、高性能、跨平台实时服务框架”。坦克大战等业务只作为 `demo/games/` 下的验证样例，不能反向污染 gateway、login、room、battle、matchmaking、leaderboard、SDK 或协议公共层。

建议后续对外名称收敛为：

- 项目/产品名：`BoostGateway`
- CMake project：`boost_gateway`
- 描述：`Enterprise-grade C++20 realtime service framework`

`BoostAsioDemo` 已作为历史仓库名进入迁移期，当前仓库和远端已逐步切到 `boost_gateway` / `BoostGateway` 命名。README、安装包、文档标题、release artifact 和默认二进制命名应保持企业级框架定位。

## 当前事实基线

以下判断来自 README、`docs/current-state.md`、`proto/README.md`、CMake、GitHub Actions 和当前源码结构。

| 领域 | 当前事实 | 证据 |
| --- | --- | --- |
| 项目定位 | README 已明确主线是企业级实时服务框架，不再扩张 demo 集合 | `README.md`, `docs/current-state.md` |
| 默认生产链路 | 默认仍是 SDK + TCP gateway + `BackendEnvelope` + 五后端 + Redis，可选 TLS profile | `docs/current-state.md`, `docs/release-governance.md` |
| 服务闭环 | `gateway + login + room + battle + matchmaking + leaderboard` 已作为主线闭环 | `src/v2/`, `examples/v2_*`, `README.md` |
| 协议演进 | v3 proto schema、CMake target、schema check 和 gRPC PoC gate 已存在；gRPC gateway 当前已覆盖 login/logout/health 以及 room/match/leaderboard/battle 的基础 RPC，并开始通过 `GrpcGatewayAdapter` 落到真实 `GatewayServiceBridge` 路由，但 generated gRPC 仍是实验能力，不进入默认生产链路 | `proto/README.md`, `proto/CMakeLists.txt`, `src/v2/CMakeLists.txt`, `src/v2/grpc/`, `scripts/check_v3_grpc_poc_decision.py` |
| helper/legacy 状态 | typed envelope helper 已接入主线，主业务 typed 覆盖已完成全部 5 服务域的 31 个 handler（含 room governance / control-plane 风格消息）；legacy raw JSON 兼容窗口仍存在但已收缩到仅内部 Raft RPC | `include/v2/service/envelope_adapter.h`, `tests/v2/unit/service_boundary_test.cpp`, `docs/legacy-helper-inventory.md`, `proto/README.md` |
| CI 平台 | 主 CI 已包含 Ubuntu、macOS matrix，并使用 Ninja/CMake preset | `.github/workflows/ci.yml`, `CMakePresets.json` |
| 性能门禁 | perf label 触发 per-commit smoke；release baseline、capacity、long soak 已有 workflow 或固定 runner 入口 | `.github/workflows/perf-commit-check.yml`, `.github/workflows/release.yml`, `.github/workflows/long-soak-capacity.yml` |
| 依赖管理 | Conan 2 `nosqlite` lockfile/profile 路径已经落仓，`BOOST_USE_CONAN_DEPS=ON` 是默认值，自动回退到 FetchContent/third_party；`release.yml` 已接入 Conan lockfile 预检步骤；Ubuntu fixed-runner 实跑结果可提升为默认推荐依据 | `conanfile.py`, `conan/README.md`, `conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock`, `.github/workflows/conan-validate.yml`, `.github/workflows/release.yml`, `.github/workflows/long-soak-capacity.yml`, `.github/workflows/production-evidence.yml` |
| 编译缓存 | 主流程已启用 `sccache` + `actions/cache`，每次 CI 运行归档 build-time.json + sccache-stats.json | `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `.github/workflows/perf-commit-check.yml` |
| 近期代码趋势 | 最近提交集中在 battle tick/projectile、room lifecycle、SDK API、部署文档和 Docker 构建修复 | `git log --oneline -n 8` |

## 已知冲突与未实现项

这些项目必须在后续开发中被显式处理，不能靠文档措辞掩盖。

| 编号 | 问题 | 当前事实 | 规划要求 |
| --- | --- | --- | --- |
| G1 | 项目命名仍带 demo 色彩 | 根 README 标题已改为 `BoostGateway`，CMake 描述已收敛为企业级框架定位 | 本地目录和远端仓库已进入迁移期，文档引用已完成统一 |
| G2 | gRPC/proto 尚未成为默认主链 | `BOOST_BUILD_GRPC=OFF`，gRPC Gateway 虽已覆盖 Room/Battle/Match/Leaderboard 的基础 RPC，但仍缺 streaming/push、SDK-integrated full-flow、TLS、RBAC 和 observability 证据 | 中期完成 generated proto/gRPC full-flow 和性能对照，再决定是否进入默认链路 |
| G3 | helper/legacy 兼容层仍在主链 | `BackendEnvelope` 与 typed helper 是当前实际运行路径；主业务 typed 覆盖已完成全部 5 个服务域的 31 个 handler，包括 room governance / control-plane 风格消息；仅内部 Raft RPC 仍保留 raw JSON 路径 | room governance / control-plane 风格消息的 typed 收束已完成；内部 Raft RPC 继续作为内部 RPC 边界保留，待等价集群/恢复测试就绪后迁移；其余路径可推进 `legacy raw JSON` 兼容窗口收缩 |
| G4 | 依赖治理正在从 fallback 迁向 Conan lockfile | `BOOST_USE_CONAN_DEPS=ON` 已是默认值；Conan profile/lock 入口、Linux `nosqlite` lockfile、fixed-runner workflow 预检均已落地；`release.yml` 已接入 Conan lockfile preflight；仍需 Ubuntu fixed-runner 真实 summary 作为默认推荐依据 | 短期优先完成 Ubuntu fixed-runner lockfile install、release baseline、long-soak/capacity 和 production evidence；通过后把 Conan `nosqlite` 提升为主线唯一推荐路径 |
| G5 | 编译加速尚未系统化收口 | sccache 已在 5/10 工作流中启用，剩余 5 个辅助工作流无需缓存；每次 CI 运行已结构化归档构建耗时与 sccache 统计至 `runtime/perf/build-times/` | build-time 基线基础设施已完成，待 CI 实际运行积累数据后可视需要扩大 sccache 覆盖 |
| G6 | 平台结论仍需固定 runner 沉淀 | CI 有 Ubuntu/macOS，但生产容量、long soak、TLS overhead 仍依赖固定 runner 后续刷新 | 中长期将固定 runner 结果纳入 release 准入和 readiness report |
| G6.5 | 自动 CI 平台矩阵需要和当前在线 runner 一致 | 开发者可能只开启 1-2 台 runner，如果 workflow 固定全平台会导致无意义排队 | 短期引入仓库内版本化 runner matrix，按当前活跃机器提交配置 |
| G7 | 测试分层命名和执行策略仍偏脚本聚合 | 已有大量 gate，但 unit/integration/e2e/perf/nightly/capacity 的开发者入口仍需要更清晰 | 长期形成开发者指南和贡献者验证矩阵 |
| G8 | v1 风格遗留模块已完成退场 | `include/game`、`src/game`、老 `examples/*_demo`、`login_server/room_server/battle_server/gateway_pressure` 已从仓库移除；`BOOST_BUILD_V1_LEGACY_*` 选项已清理 | ✅ 已移除 |

## 总体原则

1. 事实优先：任何生产能力声明都必须绑定代码、脚本、workflow 或 runtime summary。
2. 默认链路保守：实验协议、demo plugin、TLS profile 和固定 runner 专项不得未经门禁进入默认生产路径。
3. 框架边界稳定：公共框架层只承载连接、路由、协议、运行时、观测、配置、持久化和 SDK 能力；具体业务规则留在 demo/plugin。
4. CI 快速反馈：PR 默认门禁保持有界，长稳、容量和高成本基准放到夜间、手动或固定 runner。
5. 可迁移优先：协议、依赖、平台支持和文档结构的每一步迁移都要保留兼容窗口、回滚策略和弃用检查。
6. 收束优先：当前阶段不做功能扩张；新增代码必须服务于稳定性、可维护性、测试替代、文档事实源或旧模块退场。

## 模块收束与旧技术遗留清理

当前项目已经形成 v2/v3 主线，但仓库里仍保留 v1 风格单进程游戏服务、旧示例入口、兼容 helper、实验 gRPC 和部分 legacy 脚本。后续 1-2 个版本的重点应是“固化已有模块并缩小默认维护面”，而不是继续扩张功能面。

### 模块处置矩阵

| 模块/目录 | 当前状态 | 处置策略 | 退场或固化条件 |
| --- | --- | --- | --- |
| `src/v2/`, `include/v2/` | 当前主线，承载 gateway/backend/service/runtime/realtime/persistence/observability | 固化为默认维护核心；禁止把 demo 业务规则写入公共层 | RC、SDK full-flow、R0/R2/R3、v2 unit/integration/multi-process 持续通过 |
| `src/v3/`, `include/v3/`, `proto/v3/` | v3 schema、Redis/Raft/OTel/proto helper 与生成入口 | 保留为协议/集群/持久化演进层；gRPC 继续实验化 | generated proto/gRPC full-flow、性能对照和 readiness gate 通过后再提升默认级别 |
| `sdk/` | SDK 企业交付主线，覆盖 C++/C ABI/Python/C# 轻封装 | 固化 ABI/API 边界、兼容矩阵和 full-flow gate | SDK 新 API 必须绑定协议 schema、测试和兼容说明 |
| `env/`, `deploy/`, `operator/` | 生产配置、Docker/K8s/monitoring/operator 事实源 | 保留并继续治理漂移 | `check_config_source_layout.py`、生产证据 gate 和 operator gate 持续通过 |
| ~~`include/game/`, `src/game/`, `project_game`~~ | ~~v1 风格单进程~~ | ~~已移除~~ | ✅ 已完成退场 |
| ~~老示例：`examples/login`, `room`, `battle`, `login_demo`, `room_demo`, `battle_demo`, `admin_demo`, `pressure`~~ | ~~v1 旧示例~~ | ~~已移除~~ | ✅ 已完成退场 |
| `examples/echo` | 仅保留 `echo_client`（纯 `project_net` 依赖）；`echo_server` 已移除 | 清理后保留为独立 echo 示例 | 不再承担 v1/v2 桥接角色 |
| `include/net/`, `src/net/`, `project_net` | 底层 packet/session/http 管理能力，仍被 v1、v2、安全/模糊测试和 SDK 周边使用 | 拆分低层稳定能力与 v1 路由草案；保留 packet/session，冻结 `InternalBus`/`ServiceRouter` 等非主线能力 | v2 主链不再引用的草案类进入 legacy 清单，删除前保留编译期检查 |
| `src/v2/service/envelope_adapter.*` | typed envelope 与 legacy raw JSON 兼容层 | 短期保留但冻结 raw JSON；新增服务不得扩展 raw payload | 主业务 typed contract 已覆盖全部 5 服务域（含 room governance / control-plane 风格消息）；剩余 raw JSON 面已收敛到内部 Raft RPC；兼容窗口可安全冻结 |
| `src/v2/grpc/`, `tests/v2/unit/gateway_grpc_test.cpp`, `tests/perf/grpc_vs_tcp_perf_test.cpp` | gRPC PoC，默认 `BOOST_BUILD_GRPC=OFF`；benchmark 已改为真实 TCP login backend vs gRPC `RequestLogin` I/O，但 full-flow 仍远未完成 | 保留为实验区，不进入默认主线；下一步应补 full-flow 而不是扩大宣传面 | gRPC 覆盖 Room/Battle/Match/Leaderboard/streaming/SDK/观测/限流/RBAC/TLS 后再升级 |
| `demo/games/tank_battle/`, `examples/realtime_echo_plugin` | 业务/demo/plugin 验证，不属于默认生产主链 | 保留为可选 demo，默认 OFF；禁止反向污染框架层 | demo gate 只验证 SPI 和业务样例，不作为生产能力宣传依据 |
| `scripts/p4_validate.py` 和 legacy wrapper | script inventory 已标注 legacy | 不新增引用；能被新 gate 覆盖后删除或移入 archive | `check_script_inventory.py` 确认无 public/workflow 引用 |
| `third_party/` 与 FetchContent 混合依赖 | 依赖来源目前统一为 Conan-first（`BOOST_USE_CONAN_DEPS=ON` 默认开启），自动 fallback 到 FetchContent/third_party | 中期迁移到纯 Conan lockfile 驱动；保留离线镜像策略 | lockfile/profile 和 dependency cache 稳定后，清理冗余 third_party 源 |

### 收束顺序

1. 先冻结：为老 examples、raw JSON、gRPC PoC、legacy scripts 写清楚”不再新增能力”的边界。
2. 再替代：用 v2 unit/integration/multi-process、SDK full-flow、production evidence gate 替代旧测试依赖。
3. 再降级：把老 examples 和 legacy 测试移到显式 CMake option，默认构建只保留 v2/v3/SDK 主线。
4. 再移除：当文档、CI、release install 和 runtime summary 都不再引用旧入口后，删除旧代码或迁入 archive/reference。

当前进度：
- ✅ v1 `include/game`、`src/game`、`project_game` 已从仓库移除，对应 CMake option 已清理
- ✅ 老 examples（`login`, `room`, `battle`, `pressure`, `*_demo`）已移除
- ✅ `echo_server` 已移除（`echo_client` 保留）
- ✅ `tests/unit`, `tests/integration`, `tests/chaos` 已移除
- ✅ G3 typed envelope 收束：全部 5 服务域 31 个业务 handler 已接入 typed request/response（含 room governance / control-plane 风格消息），仅内部 Raft RPC 保留 raw JSON 路径
- ✅ G4 Conan 依赖治理：`BOOST_USE_CONAN_DEPS=ON` 默认开启，lockfile/profile/引导工具已落地；工作流（conan-validate/release/long-soak-capacity/production-evidence）已接入锁文件预检；docs/conan/README.md 和 governance gate 已更新；待 Ubuntu fixed-runner 真实 summary 完成后可提升为唯一推荐路径
- ✅ G5 sccache 构建时间基线：5/10 工作流已启用 sccache（另 5 个辅助工作流无需缓存）；每次 CI 运行结构化归档构建耗时与 sccache 统计至 `runtime/perf/build-times/`

### 默认构建/安装面目标

短期目标不是立刻删除旧代码，而是让默认构建和 release 包只表达当前主线：

- 默认构建：`project_app`、必要的 `project_net` 低层能力、`project_v3`、`project_v2`、SDK、`v2_gateway_demo`、五个 v2 backend、`v2_gateway_pressure`、主线测试。
- 可选构建：tank battle demo、realtime echo plugin、gRPC PoC、perf/capacity tests。
- 默认安装：README、current-state、project-blueprint、runbook、v2 可执行入口、SDK 产物和生产配置。

### 删除前门禁

任何旧模块删除前必须满足：

- 没有 workflow、public script、README、顶层 docs 或 install 清单引用该入口。
- 有 v2/v3/SDK 等价测试覆盖原来承担的核心风险。
- `check_current_docs_install.py`、`check_mainline_readiness.py`、`check_script_inventory.py` 和相关 release gate 通过。
- 删除行为在 release note 中说明兼容影响和替代入口。

## 短期规划：偿还技术债务，巩固基础

周期：1-2 个月。其中 CI 构建加速可在 1-2 周内先落地。

### S1 命名与定位收敛

目标：项目对外表达从 demo 仓库切换为企业级实时服务框架。

任务：

- 将 README 主标题和项目描述调整为 `BoostGateway` / `Enterprise-grade C++20 realtime service framework`。
- 将 CMake `project()` 描述、安装文档、release artifact 描述和部署文档中的 demo 表述统一为框架表述。
- 保留 `BoostAsioDemo` 历史说明，避免破坏仓库路径和历史引用。当前本地目录和远端仓库名已进入迁移期。
- 增加命名迁移说明：哪些名字立即切换，哪些二进制/路径保持兼容。

验收：

- `README.md`、`docs/README.md`、`docs/current-state.md`、`CMakeLists.txt` 对项目定位一致。
- `scripts/check_current_docs_install.py` 通过。
- release 包安装文档包含新的蓝图文档和命名说明。

### S2 legacy/helper 债务盘点与退场计划

目标：把 `legacy raw JSON`、typed helper 和 generated proto 的边界从“代码里隐含”变成“文档和测试可验证”。

任务：

- 建立 `legacy/helper` 使用清单，覆盖 `include/v2/service/envelope_adapter.h`、`src/v2/service/`、后端服务 handler、SDK、测试和 proto schema。
- 为 legacy raw payload 定义弃用窗口：新增功能不得再扩展 raw JSON 入口，现有 raw 只允许为兼容测试保留。
- 将 `legacy_raw_json_deprecation_notice()` 的测试从提示升级为迁移准入依据。
- 对每个服务列出 typed envelope 到 generated proto 的迁移状态。
- 第一批与第二批主业务 typed contract 迁移完成后，把剩余 raw JSON 面明确收敛到内部 Raft RPC。

验收：

- 新增或更新可靠性矩阵条目，明确 legacy/helper 退场证据。
- `proto/README.md` 与蓝图一致说明 helper 只是迁移层，不是长期协议方向。
- 新增检查脚本或文档清单可阻断新增未登记 legacy 入口。

### S2.5 默认维护面收缩 ✅ 已完成

目标：在不破坏当前验证能力的前提下，把旧 `project_game` / v1 examples 从”默认主线”降级为”显式 legacy 兼容面”，最终从仓库移除。

已完成的工作：
- ✅ v1 `include/game`、`src/game`、`project_game` 已移除
- ✅ 老 examples（`login`, `room`, `battle`, `pressure`, `*_demo`）已移除
- ✅ CMake `BOOST_BUILD_V1_LEGACY_*` 选项已清理
- ✅ v1 `tests/unit`, `tests/integration`, `tests/chaos` 已移除
- ✅ `echo_server`（shadow-bridge）已移除
- ✅ 文档、治理脚本已更新

验收：
- 默认构建和安装包只暴露当前 v2/v3/SDK 主线能力。
- 顶层文档不再把 v1 风格二进制作为运行入口推荐。

### S3 CI 构建加速与基础流水线治理 ✅ 已完成

目标：降低 CI 等待时间，同时不牺牲当前 Ubuntu/macOS 覆盖。

已完成的工作：
- ✅ 保持 Ninja 作为默认 generator；当前 `CMakePresets.json` 已满足，后续新增 preset 必须默认 Ninja。
- ✅ 在 `ci.yml`、`perf-commit-check.yml`、`release.yml` 以及 `nightly-stability.yml`、`perf-regression.yml` 共 5 个 workflow 中引入 sccache。
- ✅ Windows sccache 已随 Windows 支持暂停而移除。
- ✅ 使用 `actions/cache@v4` 按 OS、CMake preset、依赖锁 hash 分 key 缓存 sccache 目录。
- ✅ 每次 CI 运行结构化归档构建耗时（`build-time.json`）与 sccache 统计（`sccache-stats.json`）至 `runtime/perf/build-times/`。

验收：

- ✅ PR CI 的 build step 明确显示 compiler launcher 生效。
- ✅ Ubuntu build cache 命中可观测（`sccache-stats.json` 归档）。
- ✅ `docs/performance-baseline.md` 已记录构建时间基线。

### S4 性能测试分层进入标准流水线

目标：把关键性能反馈接入 CI/CD，但避免把长任务放进 PR 默认路径。

任务：

- 保留 PR 默认 smoke gate。
- `perf` label 继续触发 per-commit performance smoke。
- release baseline 继续走手动/定时 workflow，并要求 artifact 归档。
- capacity、business-capacity、2h/8h soak 只在固定 runner 或夜间任务执行。

验收：

- `docs/release-governance.md` 的分层门禁与 workflow 实际触发条件一致。
- release readiness report 能区分 bounded local evidence 和 fixed-runner evidence。

## 中期规划：突破平台限制，完成协议演进

周期：3-6 个月。

### M1 平台兼容性常态化

目标：把 Linux/macOS 从”能编译”推进到”能稳定运行主线门禁”。

任务：

- 维持 Ubuntu、macOS 双平台 CI matrix，并补齐失败分类和 artifact。
- 将 Linux Ubuntu 固定 runner 作为生产候选容量事实源，优先沉淀 10K echo、battle-500、business-capacity 和 long/overnight soak。
- 梳理平台特定实现：plain TCP bounded read、POSIX process helper、Docker/kind/operator 依赖。
- 对 macOS ARM64 建立构建和 smoke 验证，不提前承诺容量上限。

验收：

- `docs/performance-baseline.md` 拆分 Ubuntu fixed-runner baseline 和 macOS smoke 状态。
- R2/R3 readiness 能以 Ubuntu fixed-runner summary 作为投产判断输入。

### M2 依赖管理迁移到 Conan

目标：让 Boost、OpenSSL、fmt、spdlog、nlohmann_json、gtest、gRPC/protobuf、hiredis/sqlite 等依赖可复现、可缓存、可升级。

任务：

- 已选定 Conan 作为依赖管理方案，`conanfile.py` 和 `BOOST_USE_CONAN_DEPS=ON` 已落地，`release.yml` 已接入 Conan lockfile preflight。
- 仓库内 `conan/profiles/`、`scripts/generate_conan_lock.py`、`conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock` 与 fixed-runner 文档入口已完备；`conan-validate.yml`、`long-soak-capacity.yml`、`production-evidence.yml` 使用 lockfile 预检。
- 当前已完成第一轮规则收敛：`fmt/spdlog/nlohmann_json/hiredis/boost::headers` 按 Conan-first 治理，`OpenSSL` 暂时保持双轨保守；当前默认 Conan 主线路径使用 `with_sqlite=False`，`protobuf/grpc/sqlite3` 继续停留在实验或可选层。
- 迁移 CMake 依赖发现逻辑，减少 `third_party/` 和临时系统探测路径。
- CI 使用 dependency cache，cache key 包含 lockfile hash。
- 保留离线/内网构建说明，避免完全依赖公网下载。

验收：

- 新 clone 可按文档完成配置、构建、测试。
- CI 不再因系统预装依赖差异产生不稳定结果。
- 依赖版本升级可以通过单独 PR 和 release note 审核。

### M3 Proto/gRPC 协议栈完整迁移

目标：从 helper 兼容层迁移到 generated proto/gRPC 或 generated proto backed transport，并形成可替换默认链路的证据。

任务：

- 补齐 gRPC Gateway 能力：Room Create/Join/Leave/Ready/StartBattle，Battle Input/Finish/FramePush(streaming)，Match Join/Leave/Status，Leaderboard Submit/Top/Rank，Session Kick/Resume。
- 为每个服务建立 proto contract test、generated stub compile test、TCP 对照测试和 full-flow 测试。
- 建立 gRPC vs TCP 性能基准；当前 `tests/perf/grpc_vs_tcp_perf_test.cpp` 已升级为真实 TCP login backend 与 gRPC `RequestLogin` 的 I/O benchmark，下一步需要把 benchmark 从 login-only 扩展到更多非登录路径。
- 明确迁移策略：先 generated proto 作为 payload schema，再评估 gRPC 作为外部或内部 transport。
- 在完成 full-flow、性能、观测、限流、RBAC、TLS 证据前，gRPC 不进入默认生产链路。

验收：

- `BOOST_BUILD_GRPC=ON` 在 Ubuntu fixed runner 上稳定构建和测试。
- gRPC full-flow 与 TCP full-flow 的行为差异有测试覆盖。
- proto/gRPC readiness gate 能被 R0/R2/R3 evidence 消费。

### M4 移除旧 helper 兼容层

目标：在协议迁移证据充分后，收束 legacy raw JSON 和非标准 helper 层。

任务：

- 将 legacy raw payload 从默认路径移除，只保留兼容测试 fixture 或 migration shim。
- 后端 handler 使用 generated schema 或统一 typed contract，不再新增 ad hoc JSON payload。
- SDK 协议层与服务端 schema 保持同源生成或有严格差异检查。

验收：

- `legacy_raw_json_deprecation_notice()` 不再对应默认运行路径。
- 新增消息类型必须先进入 proto/schema，再进入服务实现和 SDK。
- 兼容层删除不会破坏 full-flow、SDK 和生产候选 gate。

## 长期规划：构建生态，提升影响力

周期：6 个月以上。

### L1 完整开发者指南与贡献路径

目标：让外部开发者能理解、构建、测试、扩展和贡献框架。

任务：

- 编写 Developer Guide，覆盖架构、目录、构建、测试分层、协议新增、服务新增、SDK 扩展、demo/plugin 开发。
- 建立贡献入口：issue template、PR checklist、coding style、test policy、benchmark policy、security disclosure。
- 把 `docs/archive/` 与当前文档的边界说明写清楚，避免历史计划被误用为当前事实。

验收：

- 新贡献者可按文档新增一个简单实时服务或 demo plugin。
- PR checklist 能要求对应的 unit/integration/e2e/perf/doc 更新。

### L2 通用实时服务框架化

目标：把项目从游戏服务器样例进一步抽象到通用实时系统。

任务：

- 固化业务 plugin SPI，支持游戏、协作、IoT、实时推送、状态同步等场景。
- 将 demo 场景保持在 `demo/games/` 或后续 `demo/realtime/`，框架层不写业务规则。
- SDK 提供稳定同步 API、异步事件、重连、push、heartbeat、版本诊断和多语言分发策略。

验收：

- 至少两个非同构 demo 使用同一框架能力，不复制 gateway/backend 核心逻辑。
- SDK 文档明确支持矩阵、 ABI/API 兼容策略和包分发状态。

### L3 跨平台与高性能运行生态

目标：成为真正跨平台的高性能 C++ 实时服务框架。

任务：

- 扩展平台矩阵：Ubuntu LTS、macOS x64/ARM64（Windows 支持暂停，待条件成熟后评估恢复）。
- 评估高性能 runner：Blacksmith、namespace 固定 runner、自托管 bare metal 或云裸金属。
- 建立资源曲线：CPU core scaling、RSS/fd growth、TLS overhead、OTel overhead、Redis on/off、multi-node recovery。
- 夜间或周度生成性能趋势报告，避免只保留单点结果。

验收：

- release readiness report 包含跨平台 smoke、Ubuntu fixed-runner capacity、长稳趋势和已知风险。
- 性能退化可以在合并前或夜间任务中被明确归因。

## 工程效率路线

| 阶段 | 目标 | 关键动作 | 验收 |
| --- | --- | --- | --- |
| 1-2 周 | 立即降低 CI 成本 | 启用 sccache；确认所有默认 preset 使用 Ninja；记录 build time baseline | CI cache 命中可见，构建耗时有对比 |
| 1-2 月 | 依赖和流水线标准化 | 选择 vcpkg/Conan；加入 dependency cache；CI matrix 并行维护 | 新环境可复现构建，依赖升级可审计 |
| 3 月以上 | 根本性性能和反馈提升 | 固定 runner/高性能 runner；分层测试策略；夜间容量和趋势报告 | PR 快速、release 有证据、容量结论可追溯 |

## 测试分层策略

| 层级 | 默认触发 | 内容 | 目标 |
| --- | --- | --- | --- |
| Unit | PR / 本地 | 纯逻辑、协议 codec、数据结构、service boundary | 秒级到分钟级反馈 |
| Integration | PR / nightly | gateway/backend、service bus、SDK full-flow、数据恢复 | 验证主链行为 |
| E2E / specialized | 手动 / nightly / fixed runner | Redis live、Raft HA、Operator kind、Docker/K8s | 验证真实依赖 |
| Perf smoke | `perf` label / 手动 | 30s smoke preset | 捕捉明显回归 |
| Release baseline | 手动 / 定时 | baseline 三轮、artifact 归档 | release 性能准入 |
| Capacity / soak | fixed runner / 夜间 | 10K、battle-500、2h/8h、business-capacity | 生产容量和长稳事实 |

## 近期提交解读

当前 HEAD 附近的提交说明主线正在从“生产候选证据收束”进入“业务 demo 与实时运行时增强”：

- `50d82cf` 对 battle instance tick 做 query-driven throttle，说明实时实例调度仍在优化。
- `23800b4` 修复 room lifecycle 和 projectile movement，说明房间/战斗状态一致性仍是近期重点。
- `6b8f99e` 增加 realtime tank battle projectiles，属于 demo/plugin 侧能力，不应扩大到框架主链。
- `19e56a8` 增加部署 quickstart，说明文档正在从交付记录转向可运行入口。
- `84e7bfc` 增加 SDK registration、room admin、replay API，说明 SDK 表面积正在扩大，需要更严格的协议和兼容治理。

因此，下一阶段不应继续无边界扩张 demo 功能，而应优先做命名收敛、协议标准化、legacy/helper 退场、CI 加速和固定 runner 证据沉淀。

## 决策门禁

以下门禁用于判断规划是否可以推进到下一阶段。

| 决策 | 可以推进的条件 | 不可推进的信号 |
| --- | --- | --- |
| 更名为 BoostGateway | README/CMake/install/docs/release 描述一致，历史兼容说明清楚 | 二进制、文档、包名各说各话 |
| gRPC 进入默认链路 | full-flow、SDK、观测、限流、RBAC、TLS、性能对照均通过 | 只完成 proto 生成或少量 RPC |
| 移除 legacy raw JSON | 所有服务有 generated/typed contract 覆盖（已完成全部 31 个业务 handler），兼容窗口结束，有迁移说明 | 仍有默认 handler 依赖 raw payload（内部 Raft RPC 尚未迁移） |
| 固定 runner 结果用于生产容量声明 | summary_version=2 artifact 齐全，多轮稳定，环境可复现 | 只有本机短样本或单轮结果 |
| 引入高性能 runner | sccache、Ninja、依赖 cache、CI 并行已完成且仍不满足反馈目标 | 基础流水线尚未优化就增加维护成本 |

## 下一步执行顺序

1. 完成项目命名和描述收敛，保留兼容说明。
2. 固化 helper/raw JSON 当前事实：主业务 typed contract 已覆盖全部 5 服务域的 31 个 handler（含 room governance / control-plane 风格消息），仅内部 Raft RPC 保留 raw JSON 路径。
3. 将 gRPC 从 login-only benchmark 推进到更多非登录路径的 full-flow contract/test 覆盖，但继续保持 `defer_default_transport`。
4. 在条件允许时于 Ubuntu fixed runner 上刷新 Conan install、release/capacity/long-soak 真实证据。
5. 继续做 CI/build cache 量化与开发者入口治理。
6. 编写 Developer Guide 和贡献规则，把测试分层策略固化为 PR checklist。
