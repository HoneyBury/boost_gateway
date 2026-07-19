# 更新日志

## v3.5.3 — 高风险部署证据闭环（2026-07-19）

> **范围**：不扩大默认协议或业务能力，补齐长期稳定性、受限 CPU 容量、真实依赖恢复、告警生命周期和专项性能证据。

### 稳定性与恢复

- 2h/8h soak 持续采集 Gateway 与宿主 CPU、RSS、fd 和负载，保留失败轮与确认复测，并校验资源采样覆盖率和最大间隔。
- Redis 演练覆盖运行中停服、持续业务流量、恢复和告警 `inactive -> pending -> firing -> resolved`；Raft 演练覆盖 leader 故障、重新选主和落后 follower 追赶。
- TLS 预发演练记录多轮 off/on 开销与恢复时间，生产证据清单统一绑定候选 SHA、checkout、runner 和 Conan lockfile。

### 性能与治理

- 容量采集支持 1/2/4 CPU affinity 矩阵，并为 matchmaking、leaderboard 和 Redis on/off 输出至少三轮的吞吐、P99、CPU、RSS 与失败率。
- 新增 OTel off/on 固定 runner 对照：每种模式隔离 Gateway/Battle Backend，使用本机回环 OTLP collector 核对 exporter、collector 与后端路由计数，输出 P99、吞吐、Gateway CPU/RSS 的观测差异。
- OTel exporter 提供线程安全的入队、导出批次、失败重试与缓冲计数，并通过 Gateway diagnostics 暴露可审计状态。
- 生产 readiness 明确绑定 workflow checkout SHA；导入旧候选证据会以 provenance 失败阻断最终决策。
- 手动 Release 增加显式 compiler image 维护开关，用于 runner 缓存丢失后的候选预热；tag 发布仍只执行离线消费验证。

### 发布边界

- `v3.5.3` 的不可变 tag 只在新冻结 SHA 的 Release、R0、1/2/4 核矩阵、R4、R5/R6、Raft、2h/8h 与最终 R2/R3 全部完成后创建。
- 1 核结果允许作为明确记录的容量边界，但不得把阈值失败改写为通过；OTel 相对开销只报告实测值，不在采集后补设任意百分比阈值。

## v3.5.2 — 发行包与真实控制面（候选）

> **范围**：不改变默认协议和业务能力，补齐发行包的 clean-environment 验证、SBOM/attestation、Operator kind 和备用 Linux runner 证据。

### 发布工程

- 新增发行包消费验证：在预缓存的 `ubuntu:24.04` 中以 `--pull=never --network=none` 检查全部安装二进制的 ELF/执行权限、运行时依赖和 hello-world 启动。
- Release 生成 SPDX JSON SBOM，并为 tag tarball 生成 build provenance 与 SBOM attestation；tarball 和 SBOM 一并进入 `SHA256SUMS.txt`。
- clean-environment 结果输出结构化 summary，并随候选 artifact 归档。
- Specialized E2E 使用 checksum 固定的 kind `v0.32.0` 与 kubectl `v1.36.1`，Kubernetes node image 固定到 `v1.36.1` digest；runner-local 工具目录可重复验签复用。
- Operator kind smoke 使用宿主 Go cache 构建 manager，并从已准入的 Ubuntu runtime 本地构建 probe workload，避免在证据 job 中依赖 Docker Hub 的 Go/nginx 镜像。
- Operator 真实集群验证覆盖六组件 Ready、gateway 1->2->1 scale、rollout restart/undo、Operator restart、CR delete 与 kind cluster cleanup。

### 候选事实

- Release run `29560450740`（`9945028`）通过完整 build/test/gates、clean Ubuntu package consumer 与 SPDX SBOM，artifact `8399167635` 包含 tarball、SBOM 和结构化 summary。
- Specialized E2E run `29563770679`（`21a4815`）通过真实 kind，summary `overall_pass=true`，artifact `8400330394`。
- 最新 Release run `29564215641`（`18de5ed`）再次通过完整 package/SBOM 链，artifact `8400536405`。
- 增强 R0 run `29564768686`（`d8d8108`）通过 Redis live、runtime HTTP、release baseline、P5/P6 真实 kind 与 N5 SDK，artifact `8400890077` 的顶层和关键子 summary 均为 `overall_pass=true`。
- 修复 R0 `--include-kind` 向 resilience/evidence 子门禁错误透传的问题；子门禁统一接收 `--include-operator-kind`，并新增冻结接线回归测试。

### 分发决策

- `v3.5.x` 正式 SDK 资产保持 C++ CMake package + C ABI shared library；Python/C# 继续是源码 wrapper/example，不承诺 wheel/NuGet 仓库、签名和平台矩阵。
- 独立 debug-symbol 包需要新增 `RelWithDebInfo`/符号拆分与符号服务器策略，不在 patch 线引入，转入下一次 minor 评估。

### 待冻结验证

- 正式 tag push 上验证 build provenance 与 SBOM attestation。
- 第二台 Linux runner 对同一已发布资产执行 checksum、解压和最小启动复验；runner 未在线前不得宣称完成。
- 在 compiler-bearing clean container 中补 CMake SDK consumer；当前已通过 AOI downstream CMake consumer `5/5` 与 clean Ubuntu runtime consumer，但两者不能合并冒充该项。

---

## v3.5.1 — 发布与事实治理（2026-07-17）

> **范围**：不改变默认协议和运行时行为，修复 GitHub 展示、Release notes、发行包布局、许可证、SDK 包元数据和冻结事实文档。

### 修复

- GitHub 仓库首页恢复显示根 `README.md`，CI/CD 架构文档迁移到 `.github/CI-CD.md`。
- tag publish 从本文件提取对应版本正文，避免 GitHub Release 只有 compare 链接。
- 包名从 CMake 项目版本派生，tag 必须与项目版本一致；带 `/` 的维护分支可安全执行手动候选打包。
- tag publish 使用 `$RUNNER_TEMP` 下的 run-local 目录，只对唯一发布 tarball 生成 basename 形式的 `SHA256SUMS.txt`，避免 self-hosted workspace 残留文件进入校验清单。
- 发布 tarball 改为真实 gzip 压缩并去除内部 `dist/` 前缀，解压后直接得到单一版本目录。
- SDK Python/C# 包元数据与 native SDK `4.1.0` 对齐，兼容矩阵补充 Gateway `v3.5.x`。
- production hardening gate 的 configure preset 断言与 fixed-runner `release` 默认值对齐，避免旧 `default` 口径误阻断。
- 使用 Operator 的 `go.sum` 作为 `setup-go` cache 依赖路径，消除仓库根缺少 `go.sum` 的 runner 告警。

### 文档与治理

- 新增 `docs/v3.5.x-maintenance-plan.md`，固定 `v3.5.1-v3.5.3` 的维护边界和冻结条件。
- 新增 MIT `LICENSE` 并纳入安装包与治理门禁。
- 回填 `v3.5.0` 最终候选、R0/R4/R5/R6/R2/R3 和正式发布事实。

---

## v3.5.0 — 项目清理与平台收束（2026-07-17）

> **范围**：暂停 Windows 支持，聚焦 Linux/macOS 平台。删除所有 Windows 特有代码和工具，清理 CI/CD 流水线，更新项目版本到 3.5.0。

### 生产候选与依赖治理

- **严格离线 Conan**：fixed-runner workflow 使用版本固定的 runner-local Conan 虚拟环境和 ABI-safe namespace，日常证据任务统一执行 `--no-remote --build=never`；依赖预热与候选验证职责分离。
- **候选证据 provenance**：R0、2h long soak、R4、R5、R6 与最终 R2/R3 必须绑定同一候选 SHA、实际 checkout、runner、构建配置和 Conan lockfile，禁止跨提交拼接发布结论。
- **预生产恢复证据**：R5 使用 runtime-only Docker 镜像和 `docker_pull_policy=never`，R6 归档 TLS 多轮验证；fixed-runner 离线执行环境已经完成准入。
- **gRPC 实验边界**：`BOOST_BUILD_GRPC=ON` 已在 fixed runner 上完成严格离线构建、gRPC/OTel 测试、SDK package consumer 和 N6 决策门禁；继续保持 `experimental_only`，不进入默认生产传输链路。
- **发布收口顺序**：冻结候选后按低成本门禁、2h soak/R4、R5/R6、R2/R3 顺序生成同提交证据；8h overnight soak 作为后续高风险发布补充。
- **TCP frame 读取单飞**：`Session::start()` 改为幂等，header/body 使用显式读取阶段，避免重复 pending read 将包体误判为长度头。
- **长稳/容量证据解耦**：2h summary 完成后立即固化 provenance，并与 capacity/R4 独立导入；后置容量失败不再作废有效的同 SHA 长稳证据。
- **长稳失败归档与确认复测**：long/overnight 基准的每个失败执行会独立保存原始 summary、benchmark JSON、stdout/stderr 和前后主机资源快照；单轮失败立即执行两次同配置确认，只有极低频且两次均恢复的尖峰可标记为 `confirmation_recovered`，同指标 2/3 失败、频繁未确认失败或原有偏差门槛违规仍会阻断。
- **冻结证据接线修复**：R0 Redis live preflight 只传递受支持的 `--require-redis`，runtime HTTP observability producer 从 canonical 脚本位置正确解析仓库根，避免增强候选验证被脚本路径或 CLI 漂移误阻断。

### 删除

- **Windows 源码**：`windows_service.h/cpp`（~500 行 SCM 集成）、`windows_platform_test.cpp`
- **Windows 脚本**：43 个 `.bat` 和 `.ps1` 文件
- **Windows CI**：`windows-ci.yml` workflow
- **Windows Docker**：`docker/gateway-server.Dockerfile`（nanoserver 镜像）

### 简化

- **双平台文件 → POSIX-only**：`process_supervisor`（删除 CreateProcess 路径）、`crash_handler`（删除 SEH 异常过滤器）、`perf_counter`（统一 `steady_clock`）、`highres_timer`（变为空 RAII）、`hot_path`（删除 MSVC 注解）、`audit_log`（统一 `localtime_r`）、`redis_client`（删除 winsock2 include）
- **CMake**：移除 MSVC `/EHsc`、`boost_gateway_stage_runtime_dlls()` 函数、Windows preset（4 个 configure + 4 个 build + 4 个 test）、SDK DLL 命名 workaround
- **CI workflows**：11 个 workflow 全部改为 Linux-only，删除 MSVC 条件分支和 choco install 步骤
- **Runner matrix**：schema v2，所有 runner 指向 Linux，标记 Windows 为 deprecated

### 构建

- `CMakeLists.txt`：版本号 3.4.0 → 3.5.0
- `CMakePresets.json`：仅保留 `default`（Debug）和 `release`（Release）两个 preset

### 后续维护

- 脚本、文档、代码质量和 CI/CD 收束阶段已完成；后续补丁版本范围见 `docs/v3.5.x-maintenance-plan.md`。

### 发布后已知问题

- Linux 资产虽然命名为 `.tar.gz`，实际由 `cmake -E tar cfv` 生成未压缩 tar；内容和已发布 SHA-256 未改变，但严格使用 gzip 解压会失败。`v3.5.1` 改用 `czfv` 生成真实 gzip，并增加 archive layout/format 门禁。

---

## v3.4.0 — P0 性能优化 + R7 模块收束（2026-05-23）

> **范围**：P0 性能优化轮次（连接池扩容、战斗路由线程卸载、断路器线程安全、Windows 高精度定时器）与 R7 模块收束（持久化层编译接入、内存架构 ECS 集成、诊断/鉴权注入），并将版本口径统一到 3.4.0。

### 性能

- **后端连接池扩容**: `GatewayServiceBridge` 默认池 1→4，压测覆盖 8（echo P99 从 100ms→1ms）
- **战斗路由线程卸载**: `Runtime` 默认工作线程 0→4（battle P99 从 750ms→10ms）
- **CircuitBreaker 线程安全**: 添加 mutex 保护，消除数据竞争
- **Windows 高精度定时器**: `HighResTimer` RAII 封装 `timeBeginPeriod(1)`，消除 15.6ms 休眠粒度

### 模块收束（R7）

- **P0 持久化层**: `persistence/*.cpp` 编译入 `project_v2`，`ReplayStorage` 接入 `BattleBackendService`
- **P1 内存架构**: `BumpArena` + `ObjectPool<EntityHandle>` 注入 `SimpleWorld` ECS 实体管理
- **P1+P2 诊断**: `DiagnosticsManager`/`HealthCheck` 注入 gateway `Runtime`，`diag_wrap` 覆盖后端 handler
- **P1+P2 鉴权**: `Authorizer` RBAC 集成 gateway 消息分发路径

### 文档与构建

- `CMakeLists.txt`：顶层 `project(boost_gateway VERSION ...)` 更新到 `3.4.0`
- `README.md`、`docs/README.md`、`docs/v2-roadmap.md`、`docs/v2-enterprise-roadmap.md`：版本口径统一到 v3.4.0
- `CHANGELOG.md`：添加 v3.4.0 版本记录

### 测试

- Unit tests: **772 通过 / 63 跳过（Redis 依赖）/ 0 失败**（Release 构建）
- 后端正向延迟从 ~30ms 降至 ~2.5ms，echo 吞吐最高 17,846/s，battle 吞吐 1,424/s

## v3.3.2 — 版本与交付面收束（2026-05-16）

> **范围**：启动 `v3.x` 生产就绪阶段的第一批收口工作，先统一版本口径、安装目标和发布清单，不扩展新的业务能力。

### 代码与构建

- `CMakeLists.txt`：顶层 `project(boost_gateway VERSION ...)` 更新到 `3.3.2`，与当前主线 `v3.3.x` 文档口径对齐。
- `install(TARGETS ...)`：补齐 `v2_match_backend`、`v2_leaderboard_backend`，使 README 中列出的 backend 入口与实际安装产物一致。
- `install(FILES ...)`：安装 `docs/v3-production-readiness-plan.md` 与 `docs/v3-release-checklist.md`。

### 文档

- `README.md`：明确当前主线已进入 **v3.x 生产就绪加强阶段**，增加对应版本行与文档入口。
- **新增** `docs/v3-production-readiness-plan.md`：定义 12 周生产就绪收口计划，覆盖性能数据闭环、架构实测、Actor 多核线程边界、通信契约、控制面和发布门槛。
- **新增** `docs/v3-release-checklist.md`：定义 v3.x 阶段的版本口径、安装产物、配置脚本、控制面入口和发布阻断条件。
- **新增** `scripts/collect_v2_perf_baseline.py`：跨平台 v2 多进程基线采集入口，统一启动 backend/gateway、运行压测、抓取 diagnostics 和进程资源快照、落盘 `runtime/perf/<timestamp>/`。
- `scripts/collect_v2_perf_baseline.ps1`：调整为 Windows 包装器，调用同一份 Python 主逻辑，避免脚本双份漂移。
- `docs/README.md`、`docs/v2-enterprise-roadmap.md`：把 v3.x 生产就绪规划与发布清单纳入当前主线文档索引。
- `docs/performance-baseline.md`：将 Python 跨平台脚本纳入标准采集入口。

### 测试

- 本次为 R0 文档与构建面收口，未新增运行时行为改动。
- 未执行构建与测试，下一步进入性能基线与架构实测闭环。

## v1.2.5 — CI / Docker / 发布链路稳定性修复（2026-05-08）

> **范围**：不扩展 `v1.x` 业务能力，不进入 `v2.0.0` 开发；仅补齐稳定发布所需的 CI、Docker 与测试发现可靠性问题。

### 代码

- 暂无业务主链结构变更。

### 文档

- `README.md`、`docs/README.md`、`docs/release-process.md`：统一当前版本口径到 `v1.2.5`。
- **新增** `docs/releases/v1.2.5.md`：记录稳定性补丁版定位。
- **新增** `docs/v2-startup-checklist.md`：整理从当前 `develop` 启动 `v2.0` 的清单。

### 发布工程

- CI：稳定 Windows runner 与 GoogleTest discovery timeout。
- Docker：修正依赖下载与 Boost 压缩包 locale 处理。
- `CMakeLists.txt`：版本升至 `1.2.5`，安装包携带 `docs/releases/v1.2.5.md`。

### 测试

- 本版以既有回归面为基线，重点提升 CI 环境稳定性。

## v1.2.4 — 生命周期/横切测试闭环 + T21 决策记录（2026-05-07）

> **范围**：完成 `T18`–`T20` 测试收口，并记录 `T21` 结构升级决策。**不进入 `v2.0.0` 开发**；当前维护分支仅补齐治理、生命周期装配、持久化/审计/回放的回归护栏。

### 代码

- `tests/unit/admin_service_test.cpp`：校验 admin callback 调用与 `admin_invoke` 最小审计键。
- `tests/integration/http_management_test.cpp`：校验 `/health` 固定返回、未知路径 `404`、非 `GET` 请求 `405`。
- `tests/integration/gateway_integration_test.cpp`：校验默认装配不注册 admin，以及 `GatewayServer::stop()` 的连接/房间态收口。
- `tests/unit/lifecycle_assembly_test.cpp`：覆盖 `ConfigWatcher` reload 成败语义与停用后的回调抑制。
- `tests/unit/persistence_replay_audit_test.cpp`：覆盖 `JsonFilePlayerStore`、`.replay` 读写、`ReplayPlayer` 与 `AUDIT_LOG` 行格式。

### 文档

- **新增** `docs/v1-structure-upgrade-decision.md`：记录 `T21` 决策，明确 `typed protocol` / `internal bus` / `battle replay` **不**在当前维护分支转正。
- `docs/README.md`、`docs/development-priority.md`：同步 `T18`–`T21` 完成状态与约束。
- **新增** `docs/release-process.md`、`docs/releases/v1.2.4.md`：固定 `develop -> main` 合并、三平台 CI/CD、tag release 与归档说明。

### 发布工程

- `.github/workflows/ci.yml`：改为 Linux / macOS `default` + Windows `windows-msvc-debug` 的三平台矩阵。
- `.github/workflows/release.yml`：改为 tag 触发的三平台构建 / 测试 / install / 打包 / GitHub Release 上传流程。
- `CMakeLists.txt`、`CMakePresets.json`：版本升至 `1.2.4`，补 `install()` 规则与 release test preset。

### 测试

- 新增治理、生命周期装配、持久化/审计/回放回归面。

---

## v1.2.1 — 业务边界测试加固（T17）(2026-05-07)

> **范围**：**`BattleManager` / `RoomManager` 单元测试** + **`gateway_integration_test`** 集成路径；覆盖房主开战、全员就绪、单人房间 **`not_enough_players`**、未开战 **`battle_input`**；辅助函数 **`read_until_message`** 跳过 **`kRoomStatePush`** 与就绪响应的交错到达。

### 代码

- `tests/unit/battle_manager_test.cpp`、`tests/unit/room_manager_test.cpp`、`tests/integration/gateway_integration_test.cpp`。

### 文档

- 矩阵 §8 / §10；`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`CHANGELOG.md`、`development-log.md`。

### 测试

- `ctest`：**81/81**。

---

## v1.1.17 — 横切数据格式与后端支持级别（T16）(2026-05-06)

> **范围**：**文档**。新增 **`docs/v1-cross-cutting-data-formats.md`**：`JsonFilePlayerStore` / **`HAS_SQLITE`** `SqlitePlayerStore`、**`.replay` 载荷**、**`ReplayPlayer`** JSON 读侧契约、**`AUDIT_LOG`** 行模板与 **格式脆弱性**；与矩阵 §6 / §4.4 交叉引用。

### 文档

- **`docs/v1-cross-cutting-data-formats.md`**；`docs/v1-maturity-matrix.md` §6 引言、§4.4、§10；**`docs/v1-runtime-lifecycle.md`** §1；`docs/README.md`、`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`v1-config-maturity.md` §5、`v1-cross-cutting-capabilities.md` §5、`v1-cross-cutting-lifecycle-binding.md` §6、`CHANGELOG.md`、`development-log.md`。

### 测试

- **无代码变更**；`ctest` **68/68**。

---

## v1.1.16 — 横切动作生命周期绑定规范（T15）(2026-05-06)

> **范围**：**文档**。新增 **`docs/v1-cross-cutting-lifecycle-binding.md`**：节点 **N1–N7** × 审计 / player 持久化 / battle replay **应收口规范矩阵**；showcase 自检清单；与 **T14 事实**文档交叉引用。

### 文档

- **`docs/v1-cross-cutting-lifecycle-binding.md`**；**`docs/v1-cross-cutting-capabilities.md`** §1 / §5；**`docs/v1-runtime-lifecycle.md`** §1 指针；`docs/v1-maturity-matrix.md` §6 引言、§10；`docs/README.md`、`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`v1-config-maturity.md` §5、`CHANGELOG.md`、`development-log.md`。

### 测试

- **无代码变更**；`ctest` **68/68**。

---

## v1.1.15 — 横切能力定位：持久化 / 回放 / 审计（T14）(2026-05-06)

> **范围**：**文档**。新增 **`docs/v1-cross-cutting-capabilities.md`**：三类横切能力与业务生命周期节点的 **当前接线事实矩阵**（不等同于 T15「收口」）；矩阵 **§6**、**§4.4** 增加指针。

### 文档

- **`docs/v1-cross-cutting-capabilities.md`**；`docs/v1-maturity-matrix.md` §6 引言、§4.4、§10；`docs/v1-config-maturity.md` §5；`docs/README.md`、`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`CHANGELOG.md`、`development-log.md`。

### 测试

- **无代码变更**；`ctest` **68/68**（与 **v1.1.14** 二进制一致，冒烟确认）。

---

## v1.1.14 — 受控 reload / shutdown 语义（T13 后半）(2026-05-06)

> **范围**：**`ConfigWatcher`** 仅在 **`try_load_gateway_config`** 成功时调用 reload 回调（失败则 WARN、**不**回调，避免默认配置误触 **`set_connection_limits`**）。**`docs/v1-runtime-lifecycle.md`**：**§6** reload 成败语义；**§7** shutdown 最小保证与仍为 **reserved** 的分界。

### 代码

- `include/app/config.h`、`src/app/config.cpp`：`try_load_gateway_config`、`fill_gateway_from_store`；`load_gateway_config` 失败日志文案（*not found or invalid*）。
- `include/app/config_watcher.h`：`check_and_reload` 使用 **`try_load_gateway_config`**。
- `tests/unit/config_test.cpp`：缺失文件 / 坏 JSON 时 **`nullopt`** 用例。

### 文档

- **`docs/v1-runtime-lifecycle.md`**（§5–§8 顺序与交叉引用）；矩阵 §5 / §10；`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`v1-config-maturity.md` §6、`docs/README.md`、`development-log.md`。

### 测试

- `ctest`：**68/68**。

---

## v1.1.13 — 标准运行时装配 / shutdown 顺序（T13）(2026-05-06)

> **范围**：**`docs/v1-runtime-lifecycle.md`**（启动 / reload / shutdown 清单）；**`examples/echo`**、`login_demo`、`admin_demo` 在 **`GracefulShutdown`** 回调中 **`watcher.stop()` + `server.stop()` + `io_context.stop()`**，避免信号停服后 IO 线程无法 **`join`**。

### 代码

- `examples/echo/server_main.cpp`、`examples/login_demo/login_demo_main.cpp`、`examples/admin_demo/admin_demo_main.cpp`。

### 文档

- **`docs/v1-runtime-lifecycle.md`**；矩阵 §5 / §10；`docs/README.md`、`v1-config-maturity.md`、`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`development-log.md`。

### 测试

- `ctest`：**66/66**。

---

## v1.1.12 — 配置字段成熟度（T12）+ v2 设计文档入库 (2026-05-06)

> **范围**：**文档**。新增 **`docs/v1-config-maturity.md`**（`GatewayAppConfig` 字段表 + 热更新/`ConfigWatcher` 叙事）；**`docs/v1-maturity-matrix.md` §5.1** 改为指向该文；节拍与 playbook 指针同步。**`docs/v2-design.md`** 纳入仓库（v2 草案，**不**代表已进入 v2 实施）。

### 文档

- **`docs/v1-config-maturity.md`**；矩阵 §5.1 / §10；`docs/README.md`、`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`development-log.md`。
- **`docs/v2-design.md`**（既有草案文本，本次首次跟踪）。

### 测试

- `ctest`：**66/66**。

---

## v1.1.11 — 二进制 Admin 最小规则（T11）(2026-05-06)

> **范围**：**`docs/v1-admin-audit-rules.md`**（调用前提 / 动作语义 / `admin_invoke` 审计键）；`AdminService::register_handlers` 迁入 **`admin_service.cpp`** 并在每条 admin 请求上写 **`AUDIT_LOG(admin_invoke, …)`**。**不引入**令牌/角色 ACL，**不改变** `kAdminResponse` 成功/失败细分策略。

### 代码

- `src/game/gateway/admin_service.cpp`（新建）、`include/game/gateway/admin_service.h`、`src/game/gateway/CMakeLists.txt`。
- `examples/admin_demo`：移除与边界重复的 kick/ban `AUDIT_LOG`。

### 文档

- **新增** **`docs/v1-admin-audit-rules.md`**；矩阵 §4.2 / §4.4 / §9 / §10；`docs/README.md`、`development-priority.md`、`development-log.md`、`runtime-playbook.md`、`v1-governance-layers.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、根 `README.md`。

### 测试

- `ctest`：**66/66**。

---

## v1.1.10 — 治理成熟度冻结（文档 + 示例用语）(2026-05-06)

> **范围**：落实 **`development-optimization.md`** 路线图**第二步**验收点：不再让「消息号存在 / 示例已接线」被误读为 **正式、可依赖**的治理能力；**不改变** TCP、HTTP、admin 的默认行为。

### 文档

- **`docs/v1-governance-layers.md`**：新增 **§6**（Admin / `HttpManager` / `GatewayServer` 装配的**禁止暗示**与事实表）；文首版本范围更新为 **v1.1.9–v1.1.10**。
- `docs/v1-maturity-matrix.md` §4 引言与 §10 版本表；`development-priority.md`；`docs/README.md`；`v1-string-protocol.md` / `v1-cross-domain-flows.md` 后续版本指针；`runtime-playbook.md`（含 **HTTP 观测端点**小节标题与 **§6** 引用）；根 `README.md`。

### 示例与头文件注释

- `examples/admin_demo/admin_demo_main.cpp`、`examples/login_demo/login_demo_main.cpp`：注释与日志用语与 **§6** 对齐。
- `include/game/gateway/admin_service.h`：标明 **demo-only** 与 **§6** 引用。

### 测试

- `ctest`：**66/66**。

---

## v1.1.9 — 治理入口分层（T10）(2026-05-06)

> **范围**：仅文档。**不改变** TCP ingress、`HttpManager`、二进制 `AdminService` 的默认接线或行为。

### 文档

- **`docs/v1-governance-layers.md`**：L0（`GatewayService` ingress）/ L1（业务 handler）/ L2（`HttpManager`）/ L3（`AdminService`）职责与边界；`/health` stub 与 `GatewayServer` 装配说明；与 v1.1.10 / v1.1.11 / v1.2.2（T18）的归属。
- `docs/README.md`、`v1-maturity-matrix.md` §4、`development-priority.md`（T10 **done**）同步。

### 测试

- `ctest`：**66/66**。

---

## v1.1.8 — 房间/战斗边界收紧（T09 + T06②）(2026-05-06)

> **范围**：**不改变**对外 body 形状；增强 **Room 侧身份缓存** 与 **房战状态文档**。战斗仍以 `user_id` 为主键（与 `Session` 解耦）。

### 核心变更

- `RoomManager::RoomMember::member_user_id` + `set_member_user_id`；**`RoomService`** 在 create/join 成功后写入当前登录 `user_id`。
- **`RoomService::build_room_state_body`**、**`BattleService` 开战 `player_ids`**：**优先** `member_user_id`，空则回退 `login_context_of`（兼容未走 `RoomService` 的装配）。
- **`transfer_session`**：注释明确**战斗中允许**迁移；单测 **`TransferSessionPreservesMemberUserId`**。

### 文档

- **`docs/v1-room-battle-boundary.md`**（状态表、`transfer_session` 契约、`end_battle` 与房间关系）；导航与 `v1-string-protocol.md` / `v1-cross-domain-flows.md` / 矩阵 §3.2 同步。

### 测试

- `ctest`：**66/66**。

---

## v1.1.7 — 跨域编排收口（T07 / T08）(2026-05-06)

> **范围**：**不修改**对外冻结的字符串协议（`docs/v1-string-protocol.md`）。将「顶号房间恢复」与「空房即清战斗」收口为**可查文档 + 单一策略函数**，避免 `GatewayServer` 与 `RoomService` 各写一份 battle 清理条件。

### T07 重复登录恢复链

- 新增 `include/game/login/login_recovery.h`、`src/game/login/login_recovery.cpp`：`transfer_room_for_duplicate_login`、`build_login_room_notify_paths`。
- `LoginService` 仅编排 push/响应，顶号房间迁移与 `login_ok`/`session_resumed` 拼装迁入上述模块。

### T08 空房 battle 清理链

- 新增 `include/game/room/room_battle_lifecycle.h`、`src/game/room/room_battle_lifecycle.cpp`：`clear_battle_if_room_empty`。
- `GatewayServer` 断线回调与 `RoomService::leave_room` 成功路径均调用该函数（替代分散的 `member_count==0` + `remove_room`）。

### 文档

- `docs/v1-cross-domain-flows.md`，`docs/README.md`、`development-priority.md`、`v1-maturity-matrix.md`、`runtime-playbook.md` 指针更新。

### 测试

- `RoomBattleLifecycleTest.ClearBattleWhenRoomBecomesEmptyAfterLeaves`
- `ctest`：65/65。

---

## v1.1.6 — 业务协议冻结（T02 后半）(2026-05-06)

> **范围**：`development-optimization.md`「第二步」——冻结 login / room / battle **字符串 body** 事实源；消除 battle 业务错误误用 **auth** 错误码；**对外 body 文本不变**（仍为 `player_not_in_battle`），**wire 上 `error_code` 变更**（兼容旧客户端需自知）。

### 协议与错误码

- 新增 `net::protocol::ErrorCode::kPlayerNotInBattle` = **3004**，`to_string` → **`player_not_in_battle`**。
- `BattleService`：`SubmitInputResult::kPlayerNotInBattle` 回复 **`kPlayerNotInBattle`**，不再使用 **`kAuthRequired`**。

### 文档

- **`docs/v1-string-protocol.md`** — 消息号、body 形态、`ErrorCode` 表、与 `net::msg` 分叉说明。
- **`docs/runtime-playbook.md`、`docs/v1-maturity-matrix.md`、`docs/README.md`、`development-priority.md`**：`v1.1.6` 交叉引用与矩阵更新。

### 测试

- `BattleManagerTest.SubmitInputUnknownPlayerReturnsNotInBattle`
- `ctest`：64/64 通过（相对 `v1.1.5` +1 单元测）。

---

## v1.1.5 — 业务事实源校准（叙事文档）(2026-05-06)

> **范围**：维护期 **`development-optimization.md`**「第一步」的**文档验收**——能明确回答登录 vs 恢复、席位建模、battle 与房间关系、`battle_started` SSOT。**无代码与协议变更**，运行时行为与 **`v1.1.4`** 一致。

### 新增文档

- `docs/v1-business-fact-source.md` — login / room / battle 三条线的职责边界与四个核心问答（与 `docs/v1-maturity-matrix.md` §3 互补）。

### 导航更新

- `docs/README.md`：`v1.x` 维护期文档列表增加本条链接。
- `docs/v1-maturity-matrix.md` §「业务层」首部增加对本文件的指引。
- `docs/development-priority.md`、`docs/runtime-playbook.md` §10：`v1.1.5` 收尾表述。

---

## v1.1.4 — `battle_started` 单一事实源（T06 第一阶段）(2026-05-06)

> **范围**：以 `BattleManager` 为房间内「是否在战斗中」的**唯一持久状态**；移除 `RoomManager` 内部的 `battle_started` 双写及 `BattleService` 成功后对 `RoomManager::mark_battle_started` 的回填。**不改变**对外字符串协议（如 `battle_started:{room}:{n}`、`session_resumed:…:battle=1`）。
>
> 对应 `docs/development-optimization.md` §11 任务表 **T06**（第一阶段）。

### 行为与设计

- `RoomManager`：删除 `RoomState::battle_started`、`mark_battle_started()`、以及仅靠房间局部存储的 `battle_started(room_id)`；`RoomSnapshot::battle_started`、`join_room`/`set_ready` 的 `kRoomInBattle` 分支改为调用可选的 **`set_battle_active_query(std::function<bool(const std::string&)>)`**，由各网关装配点在启动时绑定为：`[&battle_mgr](auto& id) { return battle_mgr.battle_started(id); }`。
- `BattleManager::battle_started(room_id)` / `active_battles_`**仍是唯一事实源**（与 `BattleService::start_battle` 成功写入一致）。
- `BattleService`：起战成功后**不再**调用 `room_manager_.mark_battle_started`。
- 未绑定 `set_battle_active_query` 的程序（纯房间演示）：房间侧战斗中视图恒视为未开战（与原行为在无战斗装配时等价）。

### 装配点

已在 `examples/*/…_main.cpp`、`tests/integration/*`（`gateway_integration_test`、`http_management_test`）、`examples/echo/server_main.cpp` 等同时具备 `RoomManager`+`BattleManager` 的入口加上述 wiring。

### 测试

- 单元：`RoomManagerTest.JoinAndReadyRejectedWhenBattleManagerMarksRoomInBattle`
- `ctest`：63/63 通过。

### T06 后续（仍属 roadmap，非本版必选）

- 与 **`v1.1.8`/T09**：`transfer_session` 在战斗中语义、空房与 `BattleManager.remove_room` 清理顺序等仍可继续收口。

---

## v1.1.3 — 入口治理前置 (2026-05-06)

> **范围**：`MessageDispatcher` 增加 **ingress** 中间件层，在投递到业务线程池**之前**同步执行；`GatewayService` 的白名单与限频迁至该层。**不修改业务协议、不改变白名单消息号集合、不触碰配置结构与治理 HTTP 分层**。
>
> 对应 `docs/development-optimization.md` §11 任务表的 **T05**。

### 行为变更

- 新增 `net::MessageDispatcher::register_ingress_middleware` / `ingress_middleware_count()`。
- 客户端连接的 `dispatch(session, …)`：`session != nullptr` 时先顺序执行 ingress，再 `asio::post` 到默认或按号段指定的业务线程池。被白名单拒绝或限频拒绝的请求**不会再占用业务 worker 队列槽位**。
- `dispatch(nullptr, …)`（实验性内部总线路径，`InternalBus`）**跳过** ingress，避免会话级策略误作用于无 `Session` 的链路；沿用 post-pool 的 `register_middleware` 链（网关默认不注册该链）。
- 保留 `register_middleware` / `middleware_count()` 表示 **post-pool** 链，供兼容与未来扩展。

### 测试与验证

- 单元：`MessageDispatcherTest.{IngressMiddlewareRunsSynchronouslyBeforeBusinessPool, IngressSkippedWhenSessionIsNull_InternalBusStyle}`，`ServiceRegistrationTest` 断言 ingress=2、middleware=0。
- `ctest`：62/62 通过。

### 兼容性

- 对已登录客户端、合法未登录业务流程（heartbeat / login / echo）无协议层差异；被拒时的错误响应（`auth_required`、`rate_limited`）保持不变。

---

## v1.1.2 — 主链生命周期与协议增强收口 (2026-05-06)

> **范围**：主链代码层面收敛 `Session` 关闭路径与协议增强标志位语义，**不引入新功能、不修改业务协议、不触碰配置/治理结构**。
>
> 对应 `docs/development-optimization.md` §11 任务表中的 T03 / T04。

### 主链行为修正

- **统一 `Session` 关闭路径（T03）**：`Session::stop()` 不再直接 `socket_.shutdown/close`，改为经由 `strand_` 上的 `handle_close(asio::error::operation_aborted)` 收口。这意味着主动关闭与心跳超时 / 网络异常 / 写队列溢出 / 包非法等异常关闭走同一条单事实源路径，`close_handler_` 一定会被触发且仅触发一次。
  - **顺带修复**：v1.0.0 中 `LoginService` 顶号踢线时调用 `replaced_session->stop()` 实际上绕过了 `close_handler_`，导致 `SessionManager` / `GatewayMetrics::on_session_closed()` / `active_connection_count_` 没有针对被踢号执行清理。本版本随 T03 一并修复。
- **协议增强顺序与压缩标志位语义（T04）**：
  - 出站固定为 `serialize -> compress (only when zlib available) -> encode`；
  - 入站固定为 `decode -> decompress (only when zlib available) -> dispatch`；
  - 新增 `net::packet::is_compression_available()`（编译期常量，绑定 `HAS_ZLIB`）。**仅当其为真时**才允许设置 `packet::flags::kCompressed`，避免无 zlib 的 build 用 fallback 长度前缀透传冒充压缩造成跨 build 语义错乱。
  - 当对端把 `kCompressed` 发到一个 *没有压缩后端* 的 build 上，服务端直接 `invalid_argument` 关闭连接，而不是错误地走 fallback decompress。
  - 分片标志位（`kFragment*`）仍为 `reserved`，主链不分片不组帧，与 `docs/v1-maturity-matrix.md` §2.3 一致。

### 测试

- 新增 `tests/unit/session_close_test.cpp`（4 用例，覆盖 stop 收口 / 幂等 / aborted 语义 / 无 close_handler 安全性）
- `tests/unit/compressor_test.cpp` 新增 `IsCompressionAvailableMatchesBuildBackend`
- 新增集成回归 `tests/integration/gateway_integration_test.cpp::CompressedFlagWithoutBackendIsRejected`
- `ctest`：60/60 通过（v1.1.1 基线 54 + 本版本新增 6）

### 兼容性

- 二进制协议 wire format 不变
- 业务消息号 / `LoginService` / `RoomService` / `BattleService` / 配置字段不变
- 没有压缩后端的客户端（含 v1.0.0 默认 build）继续工作；只有"伪造 `kCompressed`"的对端会被严格拒绝

---

## v1.1.1 — 基线校准 (2026-05-06)

> **范围**：纯文档基线校准，**不涉及主链协议、业务、运行时行为变更**。
>
> 对应 `docs/development-optimization.md` §11 任务表中的 T01 / T02 / T10 / T12 / T14。

### 文档新增

- 新增 `docs/v1-maturity-matrix.md` — `v1.x` 维护期能力成熟度**单一事实源**。覆盖：网络/协议、业务、治理、配置、持久化、可观测性、工程能力，每项均标注 `stable` / `experimental` / `reserved` / `demo-only`。

### 文档修正（消除"代码事实 / 文档承诺"不一致）

- `README.md`：
  - 新增"版本基线说明"区分 `v1.0.0` 发布版与 `develop` 维护期
  - 全量补成熟度标记，纠正以下过度承诺：
    - 自动分片传输（实际 reserved，主链未接入）
    - TLS 加密（实际 reserved，`GatewayServer` 主链未启用 SSL stream）
    - Token 生命周期失效（实际 experimental，`SessionManager` 不存储过期时间，运行时不主动失效）
    - 登录防爆破（实际 reserved，`LoginService` 未调用 `RateLimiter`）
    - 游客账号（实际 reserved，`max_guests` 主链未引用）
    - 完整热更新（实际 experimental，仅 `max_connections` / `per_ip_connection_limit` 真正应用）
    - 完整管理面 / 管理命令 5001-5005（实际 demo-only，无权限校验）
    - 多进程拆服架构（实际为按模块拆出的独立 demo 入口）
  - 测试规模与压测场景数量的表述按代码事实重新表达（实际 `PressureScenario` 9 个枚举值；ctest 用例以 `ctest -N` 为准）
- `docs/README.md`：重组文档导航，按"v1.x 维护期 / v2.0.0 路线"分组，明确"v2.0.0 在 v1.2.0 决策点前不进入开发"

### 维护版本节奏（来自 `development-optimization.md` §11）

| 版本 | 主题 |
|---|---|
| `v1.1.1` | 基线校准（**本版**） |
| `v1.1.2` | 会话与协议收口（T03 / T04） |
| `v1.1.3` | 入口收敛（T05） |
| `v1.1.4` | 状态边界收敛（T06） |
| `v1.1.5 - v1.1.8` | 业务线收口 |
| `v1.1.9 - v1.1.11` | 治理线收口 |
| `v1.1.12 - v1.1.14` | 运行时装配线收口 |
| `v1.1.15 - v1.1.17` | 持久化/审计/回放横切线收口 |
| `v1.2.0` | 协议与内部结构升级决策点 |
| `v1.2.1 - v1.2.4` | 各主线回归面加固 |

### 兼容性

- 协议、API、配置、运行时行为**完全不变**
- 所有现有测试用例保持通过

---

## v1.0.0 (2026-05-05)

### 核心架构
- 二进制协议：长度前缀 + 消息号 + 请求序号 + 错误码 + 标记位
- Session：异步 TCP + 心跳 + 限频 + 最大包长校验 + 反压保护
- MessageDispatcher：消息注册 + 中间件链 + 按消息范围线程池路由
- SessionManager：认证状态 + 重复登录处理 + 会话迁移
- RoomManager：创建/加入/离开/准备 + 房主机制 + COW 广播快照
- BattleManager：起战斗/结束 + 帧同步（advance_frame）+ 输入历史 + 观战

### 业务服务
- LoginService：三种鉴权模式（dev/json_file/http），Token TTL 24h，顶号踢线
- RoomService：房间生命周期 + 状态广播 + 准备追踪
- BattleService：战斗启动 + 输入路由 + 帧同步 + 结算
- PushService：统一成功/错误/推送响应
- GatewayService：鉴权白名单 + 限频中间件
- AdminService：踢人/封禁/状态/重载管理指令
- MatchmakingService：队列匹配 + ELO 分差控制

### 可观测性
- 10 种累计计数器 + 6 种每秒速率仪表盘
- Prometheus 文本 + JSON 双格式导出
- HTTP 管理端点：/health /metrics /metrics/json
- 请求链路追踪 ID（Session → Dispatcher → Handler）
- 审计日志：登录成功/失败、限频触发、连接拒绝、配置重载
- 崩溃转储：Windows SEH + POSIX 信号
- 日志采样宏：LOG_INFO_SAMPLED / LOG_DEBUG_SAMPLED

### 性能优化
- BufferPool / ObjectPool 复用分配
- 大包自动压缩（>512B）+ 分片传输（>8KB）
- 批量发包（send_batch）+ COW 广播快照
- 零拷贝读包路径 + 写队列反压
- 慢连接检测（积压 > 50% 告警）
- 连接预热（线性提升至全速）

### 安全能力
- Token 生命周期管理（expires_at + TTL）
- 连接限制（总量 + 单 IP）
- 多维限频（连接/用户/消息类型）
- 登录防暴力破解（IP + 用户维度）
- 游客账号（受限权限 + 降速限制）
- TLS 配置（证书 + 私钥 + SSL 上下文）

### 工程能力
- CMake Presets + FetchContent + 本地 third_party 内网构建
- Docker 多阶段构建 + docker-compose + GitHub Actions CI
- 54 个测试（34 单元 + 8 集成 + 7 模糊 + 5 其他）
- 8 种压测场景（echo/invalid_token/slow_echo/broadcast_storm/malicious/battle/chaos/stability）
- 6 个可执行文件

> **维护期补注（自 `v1.1.1` 起，详见 `docs/v1-maturity-matrix.md`）**：
> 以上 v1.0.0 描述中关于"自动分片"、"TLS 上下文已接入主链"、"登录防爆破"、"游客账号"、"完整管理指令"、"完整 Token 生命周期失效"等条目，主链实际为预留或半接入状态，请以 v1.1.1 起的 `docs/v1-maturity-matrix.md` 为准。
