# 当前项目事实源

更新时间：2026-07-21

本文档作为当前进度的入口事实源。版本号以 `CMakeLists.txt` 中的 `boost_gateway VERSION 3.5.3` 为准；提交状态以 `git HEAD` 为准。`v3.5.3` 已在最终 SHA `b9c348b4b58fdeeffa9d82ff87a67ed781a96b78` 发布并完成线上资产独立验签；后续实现不得移动该 tag，维护范围按 `docs/v3.5.x-maintenance-plan.md` 执行。

legacy/helper 迁移边界与 v1 兼容面清单见 `docs/legacy/legacy-helper-inventory.md`。
普通 branch push / PR 不再自动触发流水线；自动触发只保留特定 release tag，当前约定为 `v*`。`.github/workflows/release.yml` 在推送 `v*` tag 时自动执行 release package/publish；`.github/workflows/ci.yml` 仅保留手动 dispatch，用作 GitHub-hosted 主线回归兜底，避免 tag 发布时重复构建。`.github/runner-matrix.json` 作为版本化 runner/默认标签配置源，`scripts/check_workflow_catalog.py` 会阻断 workflow 清单、runner matrix 与 `.github/CI-CD.md` 漂移。性能 smoke/baseline/capacity、bounded stability、fixed-runner evidence、release/capacity 等入口保留 `workflow_dispatch`，具体触发条件以 `.github/workflows/*.yml` 为准。
GitHub 仓库当前 runner inventory 的单一事实源见 `docs/runner-inventory.md`；runner 命名、custom labels、Conan/Docker/R5 准入规则见 `docs/runner-gate-standard.md`。2026-07-21 的 GitHub API 快照显示 AOI 在线且空闲，`myserver` 与 `MyDesktop-Win` 离线；历史 runner 状态不能替代 dispatch 前检查。生产证据仍必须以各 summary 和 artifact 为准。

## 稳定能力

- v1.x：维护期能力已经收束，v1 代码已从仓库移除（`include/game`、`src/game`、老示例、v1 测试）。
- v2.x：当前主线。`ActorSystem`、gateway-only ingress、五个后端服务、`BackendEnvelope`、typed envelope adapter、服务健康检查、TTL/readiness、WriteBehind drain 统计与失败上报已经进入可验收状态。
- R4 契约门禁：`scripts/verify_r4_contract.py` 覆盖通信契约、后端恢复、typed envelope、proto schema、gateway-only ingress 和短架构基线入口。
- 稳定性门禁：`scripts/verify_stability_soak.py` 覆盖 I/O accept policy、WriteBehind drain/failure、backend timeout/recovery 和短架构基线，提供 `smoke`、`short`、`medium` soak profile；`.github/workflows/nightly-stability.yml` 现在是手动 bounded stability 入口，历史 Nightly 名称只保留在文件名中。
- 后端稳定性：`BackendServer` 已支持多会话跟踪与关闭收口；plain TCP `read_frame()` 使用 Boost.Asio non-blocking bounded read，避免 POSIX `select()` 限制。
- RC 总门禁：`scripts/verify_release_candidate.py` 汇总可靠性矩阵、R4 契约、稳定性 soak 和可选 Release baseline，并输出结构化 summary。
- 安全发布门禁：`scripts/check_security_release_gate.py` 检查生产 Login Backend 仅验证外部 RS256 JWT、禁用 dev token/local signing/local identity operations 的证据，以及 admin 审计最小键和 ACL 边界说明。

## 增量能力

- v3 proto/gRPC：schema 校验、CMake target 和 release checklist 已存在，当前定位为传输契约与构建入口，不作为默认生产链路。
- Redis/Raft/Operator：已通过专项 E2E 形成独立可靠性闭环；默认发布仍保持有界 smoke，固定本机/runner 可显式启用 Redis live 与 Operator kind 验证。
- Release baseline：`scripts/collect_release_baseline.py` 现在聚合 R4 release contract 与 v2 多进程 `echo/battle` 性能采集；默认 `baseline` profile 适合固定机器执行，`capacity` 与 `business-capacity` profile 用于 5K/10K 连接、battle-500 和 SDK full-flow 业务容量专项；`.github/workflows/release.yml` 已提供 `v*` tag / 手动入口，当前也可在 GitHub-hosted `ubuntu-latest` 上做 bounded validation，但 fixed-runner 接入与最终证据口径仍以 `docs/fixed-runner-playbook.md` 为准。
- 2026-07-12：fixed-runner 的 8h overnight soak 已完整通过（28815.787s）。本次 `long-soak-capacity.yml` run `29183833041` 在 `6d537ee` 上只执行 capacity 与 business-capacity（未执行 2h/8h），Conan lockfile 预检、Release 构建和 R4 fixed-runner 聚合门禁均通过。
- 2026-07-12：`GatewayServiceBridge` 已避免在连接池选取时阻塞等待其它同步 RPC 持有的连接锁，并在池满全忙时公平轮询。run `29183833041` 的固定 Linux runner 三轮 capacity `battle-500` P99 为 40/100/150ms、吞吐中位数 6725.07 msg/s；business-capacity P99 为 75/150/150ms、吞吐中位数 6725.26 msg/s。两组均为 500 connected、0 rejected、0 failed，business-capacity 的 3 个 SDK full-flow 客户端也全部通过；`fixed-runner-release-capacity-summary.json` 为 `overall_pass=true`。
- 依赖治理：`BOOST_DEPENDENCY_PROVIDER=conan` 是严格默认入口，依赖完全由 Conan lockfile、CMakeDeps 和 CMakeToolchain 提供；缺包直接阻断，不再隐式 fallback。`fetchcontent` 只保留为开发者显式选择的源码调试模式。
- 2026-07-15：候选 `6f1a2ba` 已在 AOI runner 通过 `preprod-evidence.yml` run `29415968573`。该 run 使用新 Conan namespace `graph-01fd8acf00b862ebd633`、`docker_pull_policy=never` 和 runtime-only 项目镜像，完成 strict offline Conan install、Release 构建、完整 R5 与两轮 R6；R5 的 11 个镜像全部命中、gateway restart 前后 SDK full-flow 与 production snapshot 均通过，artifact `preprod-evidence-29415968573` 的 R5/R6 summary 均为 `overall_pass=true`。首轮 run `29415647897` 因图输入变化生成的新 namespace 为空而失败；在确认 OS/GCC/arch/build-type/Conan/lockfile 身份一致后，由旧 cache 本地复制 seed，并以 `--no-remote --build=never` 10/10 包验收后消除阻断。
- 2026-07-15 workflow 离线收口：`grpc-experimental.yml`、`nightly-stability.yml`、`perf-regression.yml`、`release.yml` 及 fixed-runner evidence 入口统一禁止公网 Conan，并显式使用 `--no-remote --build=never`；gRPC Release lockfile 已纳入仓库，不再在 workflow 内动态联网解析。Candidate、Gate Diagnostics 与 Specialized E2E 默认构建组合统一为 AOI 已预热的 Release namespace。`check_workflow_catalog.py` 会阻断 fixed-runner workflow 重新引入 `--allow-public`、`--build=missing` 或未离线配置的 Conan composite action。GitHub-hosted `ci.yml` 与显式依赖预热入口 `conan-validate.yml` 是仅有例外。
- 2026-07-15 离线 workflow 远端复验：候选 `9c2421d` 的 Bounded Soak run `29420484420`、Release run `29420589850`、Specialized E2E run `29420833603`、Candidate Evidence run `29421017380` 和 Gate Diagnostics run `29421305436` 全部成功，Conan 均命中 AOI 的 Release namespace。gRPC run `29420321189` 在 1 分钟内严格离线失败于新 namespace 缺少 `boost/1.86.0` recipe；后续本地 seed 试验 `29421659838` 证明 legacy cache 缺少 c-ares source，且不可达代理成功阻止其联网。这两次运行证明正常 workflow 没有降级联网或复用不可信二进制。
- 2026-07-16 gRPC runner 修复与复验：AOI runner 的 runs `29416581124`、`29420321189`、`29421659838` 分别暴露离线动态 lockfile 解析、全新 gRPC namespace 缺包和验证 job 混入 cache 迁移的问题。当前 workflow 固定消费仓库内 `linux-gcc-x64-release-grpc-nosqlite.lock`，只允许 `--no-remote --build=never`，默认编译并行度为 2；run `29465329265` 已在 `7c1bd4b` 上使用 `graph-a863bf92d6e235d2f53d` 完成 15/15 包严格离线 install、185/185 构建、17/17 gRPC/OTel 测试、SDK package consumer 5/5 和 N6 decision gate。缓存预热是 runner 准入动作，不再由日常 gRPC 证据 job 动态完成；默认传输结论仍为 `defer_default_transport`。
- 依赖治理补充：仓库已新增 `scripts/generate_conan_lock.py`、`conan/profiles/linux-gcc-x64`；`release.yml` 与 `long-soak-capacity.yml` 已支持把 Ubuntu fixed-runner 与同一份 `conan_lockfile` 关联使用。当前默认主线 Conan 路径是 `with_grpc=False`、`with_sqlite=False`；`sqlite3` 继续保留为可选/实验层。
- Conan 离线事实：AOI runner 的 ABI-safe namespace 已覆盖默认 `nosqlite` lockfile，并通过 `--no-remote --build=never` 验收；新图 key 仍须先预热或从完全相同 ABI/Conan/lockfile 身份的旧 namespace 复制 seed 后重新验收。
- 仓库已新增 `scripts/bootstrap_conan.py` 与 `conan/remotes.example.json`，用于优先准备本地 cache / 内网 remote；公网 `conancenter` 不是默认前提。
- 2026-07-14 已完成 workflow Conan 缓存路径审计：除面向 GitHub-hosted runner、使用 checkout 内目录和 `actions/cache` 的 `ci.yml` 外，所有执行 Conan 的 fixed-runner workflow 通过 `scripts/tools/resolve_runner_cache.py` 使用 `/opt/boost-gateway/conan` 下按 Ubuntu release、GCC、架构、build type、Conan 图及 remote 配置分区的持久 Home；`production-readiness.yml` 只汇聚 artifact，不执行 Conan。每个分区须先按 lockfile 预热，生产证据阶段使用 `--no-remote`，详见 `conan/README.md` 和 `docs/fixed-runner-playbook.md`。
- bootstrap 现已支持 `conan/remotes.local.json` 覆盖、`CONAN_REMOTE_URL` 环境变量注入和 `--no-remote` 离线模式。
- 仓库已新增独立的 `conan-validate.yml` 手动流水线，用于在不扰动默认 CI 的前提下验证 Conan 依赖链；当前已补 `runner` / `conan_profile` / `conan_lockfile` 输入，默认可切到 Linux fixed-runner。
- `conan-validate.yml` 已完成真实 GitHub Actions dispatch，历史事实是 workflow 已被 GitHub 接受并派发；后续 Linux fixed-runner 结果继续按同一入口归档。
- 2026-07-11 已在在线 Linux fixed runner 上完成当前 `develop` 的有界主线回归：`release.yml` run `29142235214`、`conan-validate.yml` run `29142663279`、`nightly-stability.yml` run `29142649741`、`ci.yml` run `29143332897`、`perf-regression.yml` run `29143057517` 和 `perf-commit-check.yml` run `29144040776` 均在 `cb1c853` 上成功。专项 E2E run `29145172304` 在 `e8ea86f` 上成功，生产 resilience run `29145497642` 在 `cbed12d` 上成功，生产 evidence run `29146018657` 在 `ecf624c` 上成功。R0 candidate run `29152333112` 在 `8cadbef` 上成功，R0/P5/P6/N5 的归档 summary 均为 `overall_pass=true`，且 Debug candidate 已按 debug baseline 评估。长稳/容量 run `29146495724` 在 `ea05045` 上失败：其 `long` profile 只记录了 13.952 秒的有界执行，不能作为真实 2h soak 声明；capacity 的 battle-500 P99 为 750ms、超过 500ms 门槛；business-capacity 则因 SDK client 子进程非 UTF-8 输出触发 `UnicodeDecodeError`。这是历史失败记录；真实时长 soak 与刷新后的 capacity/business-capacity 证据已由后续 run 归档，R2/R3 仍需使用这些 artifact 重新生成连续链。
- 2026-07-11 的 long-soak/capacity run `29153158335` 已确认 Conan preflight/build 通过，business-capacity 的 SDK full-flow 三轮通过且不再出现 UTF-8 解码异常；当时 capacity 与 business-capacity 的唯一性能阻断是 battle-500 P99=750ms（门槛 500ms）。该 run 的新持续基准在第二轮 gate 失败后 209 秒提前退出，未形成 2h 事实；后续实现已改为持续满目标时长、汇总失败指标，并上传 P5 与 `v2-stability-soak` 子产物。
- 2026-07-12 的 Release run `29154210374` 通过 Conan/build，失败于 `MultiProcessFixture.BusinessFlowFullCycle`：共享 Redis 中残留的带后缀 SDK 用户污染了固定 `alice`/`bob` leaderboard 断言。`MultiProcessFixture` 现与 SDK fixture 一样强制 `BOOST_DISABLE_REDIS_AUTO_CONNECT=1`，其派生的真实多进程测试使用隔离内存 leaderboard；Redis live 行为仍由专项 E2E 显式覆盖。
- 2026-07-12 的 Release run `29194358366` 已在 `develop` / `c6f1d91` 成功完成，说明此前 release teardown 偶发失败的收口修改当前已具备一条新的远端成功事实；对应前一条失败 run `29190636370` 的单测 teardown 段错误没有再次出现。
- 2026-07-12：独立 `gRPC Experimental` fixed-runner workflow 已形成真实成功事实。首轮 run `29195792943` 在 `main` / `5df1479` 因 `use_existing_workspace=true` 下 runner workspace 不是目标 `GITHUB_SHA` 而失败，随后将该 workflow 默认值收口为 fresh checkout（`0af5c91`），并在 run `29196150703` 上以 `use_existing_workspace=false`、`no_remote=true` 成功完成 `BOOST_BUILD_GRPC=ON` 的固定 runner 验证；该 run 通过了 preflight、gRPC lockfile 生成、Conan install、gRPC build、`GrpcGateway|OtelExporter` 聚焦 CTest、`verify_sdk_package_consumer.py --with-grpc` 和 `check_v3_grpc_poc_decision.py`。该历史缓存事实不能跨 Ubuntu release 复用；当前 workflow 已使用 OS/compiler/arch/build-type 分区，默认生产结论仍保持 `defer_default_transport`。
- 2026-07-12 的 long-soak run `29153927305` 已真实持续 7200.241 秒、完成 1603 次架构基准。仅 `multi_battle_tick_100_entities` 有 4 次（0.25%）超过 1000us，最后一次为 1036.3us。2026-07-13/14 的同候选 run `29262169909`、`29295632367` 均运行满 2 小时，各自在约 1577 次循环中出现一次约 1215us 的同指标尾峰；旧规则因偏差略超 20% 拒绝了失败率仅 0.0634% 的结果。持续 soak 现使用分级瞬时异常策略：标准档要求失败率不超过 1% 且偏差不超过 20%；只有失败率不超过 0.1% 的稀有尾峰可放宽到 25%，超过 25% 仍硬阻断。此统计规则只适用于重复微基准；capacity/business-capacity 继续使用 battle-500 P99 500ms 硬门槛，最新通过事实见 `29260476565`。
- 2026-07-10/11 已在 GitHub-hosted `ubuntu-latest` 上完成整套主线 workflow 回归收口。`.github/workflows/ci.yml` 的 hosted 主线验证确认当前 `develop` 分支在无 self-hosted runner 参与时也能完成 Conan install、Build、CTest、R4 contract、monitoring operability、workflow Python CLI contract gate 和 legacy/helper inventory gate；对应治理补充包括 workflow 内 Python 脚本参数漂移静态门禁（`scripts/check_workflow_python_cli_contracts.py`）、`ci.yml` 上的显式 `sccache` 安装/缓存目录预创建、Conan cache 恢复试点，以及 DemoServer config watcher teardown 收口。进一步的 hosted 调查在 2026-07-10 形成了完整闭环：run `29106845147` 证明 `ci.yml` 旧版 `sccache` key 会因“配置哈希固定 + exact-hit 不再 save”而冻结在旧缓存，即使 Conan cache 已恢复，warm run 仍出现 `compile_requests=184 / cache_hits=0 / cache_misses=184`；提交 `28bda13` 将 `ci.yml` 调整为“配置哈希前缀 restore + commit exact key save”后，run `29108671173` 已在 exact-hit 同一 `sccache` key 的情况下达到 `compile_requests=184 / cache_hits=184 / cache_misses=0`，总时长从上一轮的 24m34s 收敛到 18m31s。随后，`perf-regression.yml` 在 run `29112908106` 上以 fallback restore 老 `release` key 的方式获得 `compile_requests=202 / cache_hits=199 / cache_misses=3` 并保存自己的 workflow exact key；`perf-commit-check.yml` 在 run `29112908805` 暴露出 `workflow_dispatch` 下 PR comment 假设错误后，通过提交 `8127391` 修正为仅在 `pull_request` 事件评论，重跑 `29113691995` 已通过；`nightly-stability.yml` 在 run `29112908489` 暴露 `verify_stability_soak.py` 默认 120s build timeout 对 hosted Debug 构建过紧后，同样由提交 `8127391` 显式提升 timeout，重跑 `29113691508` 已通过；`release.yml` 首轮 run `29112907891` 暴露测试阶段缺少可观测性，提交 `f365125` 增补 `List tests` 与 `ctest --progress --output-on-failure | tee` 日志后，重跑 `29114301198` 已完成 release gate、baseline、打包全链路验证。当前 GitHub-hosted `ci.yml` / `release.yml` / `perf-commit-check.yml` / `nightly-stability.yml` / `perf-regression.yml` 的 Conan + `sccache` + hosted fallback 链路都已具备真实事实源，剩余重点已经从“workflow 自身可用性排查”转为 fixed-runner inventory、标签治理和 production evidence。
- Conan 主线严格解析 `fmt`、`spdlog`、`nlohmann_json`、`hiredis`、`boost::headers`、`OpenSSL` 和测试所需 GTest；`protobuf/grpc/sqlite3` 仍属实验或可选层。
- SDK 构建与安装默认消费 Conan target；源码调试模式仍兼容 FetchContent 的头文件布局，但不会被自动选择。
- `project_v3` 已去掉对 `hiredis_SOURCE_DIR` 的显式 include 假设，统一依赖 `hiredis` target。
- helper/generated contract 收口补充：全部 5 服务域的 29 个业务 handler 已统一接入 adapter，且 29 个均已具备 `EnvelopeMessageKind` / schema-backed typed request/response 覆盖，包含 login 域补齐后的 `register_account` / `guest_login` 以及 room governance / control-plane 风格消息（`room_list`、`room_detail`、`room_kick`、`room_transfer_owner`、`room_state_push`、`room_battle_finished`）。剩余 raw JSON-only 面已收敛到仅内部 Raft raw JSON RPC。
- P1 性能事实：当前口径和固定 runner 操作以 `docs/performance-baseline.md` 为准。`docs/archive/releases/v3.3.2-p1-performance-stabilization.md` 仅记录旧版本的历史退化，不作为当前状态或部署规格来源。
- 专项 E2E：`scripts/verify_specialized_e2e.py` 聚合 Raft 集群/恢复、Redis 降级与可选 Redis live / Operator kind smoke，作为 Redis/Raft/Operator 独立验收入口；`.github/workflows/specialized-e2e.yml` 提供手动触发入口，固定 runner 接入见 `docs/fixed-runner-playbook.md`。
- P3 数据恢复：`scripts/verify_data_recovery_gate.py` 聚合 replay/result/snapshot、WriteBehind flush/drain、Redis degraded、Raft committed restart replay 和持久化 round trip；Redis live 与 settlement replay 通过显式参数接入固定环境。
- P4 可观测性/限流：`scripts/verify_observability_gate.py` 聚合 rate limit 全局消息类型/IP/user/login/connection、trace/OTel、backend RED metrics、gateway metrics 导出和 audit 事件证据，并接入 RC 总门禁；固定观测 runner 可通过 `--include-otel-collector` 验证 fake collector POST，通过 `--include-runtime-http` 启动真实 `v2_gateway_demo` + SDK full-flow 验证 `/health`、`/ready` 与 `/metrics*`。
- P5 控制面：`scripts/verify_control_plane_gate.py` 聚合 Operator manifest 静态契约、fake-client Go 测试，并接入 RC 总门禁；Go build/module cache、HOME 与 telemetry 统一固定到 workspace 外的 job 临时目录，并可通过 `--go-state-root` / `BOOST_GATEWAY_GO_STATE_ROOT` 覆盖，避免固定 runner checkout 被旧缓存权限阻断；固定 runner 可通过 `--include-envtest` / `--include-kind` 验证 envtest、kind status/components 和样例 CR 删除路径。
- P5 长稳/故障/回滚：`scripts/verify_production_resilience_gate.py` 聚合固定 runner 预检、stability soak、data recovery、Redis/Raft/Operator specialized E2E，并可显式追加 Redis live、Operator kind、runtime HTTP observability、release/capacity baseline；默认入口保持有界，summary 写入 `runtime/validation/production-resilience-summary.json`。
- P6 生产证据聚合：`scripts/verify_production_evidence_gate.py` 将 stability soak、P3 data recovery、Redis/Raft/Operator specialized E2E、生产候选完整性审核与可选 release/capacity baseline 聚合为一个固定 runner 入口；默认模式保持有界，长稳、Redis live、Operator kind、settlement replay、capacity baseline 通过显式参数启用。本机 P6 收束验证已覆盖 Release 构建、Redis live、Operator kind 和 3 轮 Release baseline，交付记录见 `docs/archive/releases/v3.3.2-p6-production-evidence.md`。
- P2 固定 runner 证据：`.github/workflows/production-gates.yml` 已统一承载 P5 resilience 与 P6 production evidence 诊断入口，支持 JSON runner 输入、preflight summary 归档、Redis/kind 真实依赖、runtime HTTP observability 和 release/capacity baseline；独立 `.github/workflows/production-candidate-evidence.yml` 生成 R0，`.github/workflows/preprod-evidence.yml` 生成 R5/R6。`.github/workflows/production-readiness.yml` 分别导入 R0、真实 2h soak、当前 capacity/R4 和 R5/R6 artifact，并在同一 workspace 生成 R2/R3；capacity-only batch 不能替代真实 2h soak。
- R0 候选 workflow 的 stability baseline profile 必须与实际构建配置一致：`Debug` 构建使用 debug 阈值，`Release` 构建使用 release 阈值。2026-07-11 已修复此前 Debug workflow 错传 release profile 导致的错误性能阻断；真正的 release/capacity 声明仍由显式 Release baseline 和 R4 固定 runner 证据负责。
- N0 固定 runner 常态化：`release.yml`、`specialized-e2e.yml` 已补齐 JSON runner、preflight summary 归档和统一 Step Summary 渲染；`check_fixed_runner_environment.py`、`render_validation_summary.py` 与各聚合 gate summary 已统一到 `summary_version=2`、`overall_pass`、`failed_category`、`environment`、`artifacts` 契约。本地收束证据见 `runtime/validation/n0-release-baseline-preflight-summary.json`、`runtime/validation/n0-specialized-preflight-summary.json`、`runtime/validation/n0-specialized-raft-ha-summary.json`。
- N1 性能证据索引：`docs/performance-baseline.md` 已补 baseline / capacity / bounded soak / long soak / business-flow perf / business-capacity / docker snapshot 统一归档口径，`verify_stability_soak.py` 已支持 `long` / `overnight` profile。R2 fixed-runner manifest 直接验证 `long-soak-2h-summary.json`，capacity/R4 由独立 run ID 导入；两者必须绑定同一候选 SHA，但后置容量失败不再反向作废已经通过的 2h summary。
- N2 监控 SLO：Prometheus alerts 已新增 `BoostGatewayHighRouteLatency`、`BoostGatewayBusinessFlowFailure`，Grafana dashboard 已新增 route latency 与 business-flow success 面板；`docs/deployment/production-operations-runbook.md` 已明确 SLI/SLO 口径和告警响应流程。`check_monitoring_operability.py` 已统一输出 `summary_version=2`、`overall_pass`、`environment`、`artifacts`；本地收束验证见 `runtime/validation/monitoring-operability-summary.json`、`runtime/validation/n2-monitoring-operability-summary.json` 与 `runtime/validation/n2-observability-summary.json`。2026-05-24 已再次刷新 `n2-monitoring-operability-summary.json`，当前为 `PASS`。
- N3 部署恢复/回滚：`scripts/check_production_recovery_gate.py` 已补默认有界静态门禁，覆盖 Docker Compose、Kubernetes rollout/rollback、Redis volume/PVC、RTO/RPO、SDK full-flow 恢复验证和运维记录模板，并接入 `scripts/verify_production_resilience_gate.py` 默认步骤；`check_deploy_operability.py` 与 `run_cloud_production_closure.py` 已统一到 `summary_version=2` fixed-runner 契约。`docs/production/production-recovery-drill-record-template.json` 与 `scripts/check_recovery_drill_record.py` 已将真实演练记录固化为可校验 JSON。当前 macOS + OrbStack Docker 环境已形成本机预演证据；2026-05-24 已再次刷新 `runtime/validation/n3-deploy-operability-summary.json` 与 `runtime/validation/preprod-recovery-drill-summary.json`，当前均为 `PASS`，云端固定 runner / K8s 继续按同一 summary 契约持续归档。
- N4 传输安全与配置治理：`scripts/check_transport_config_governance.py` 已聚合 TLS/mTLS profile 边界和配置漂移检查；backend 服务端 opt-in TLS listener、五个 backend 入口配置接入、Docker/K8s/Helm Secret/volume profile、backend TLS request/response 实测和本机 TLS profile SDK full-flow 已补齐。默认生产结论仍是 plain TCP，TLS transport 上线需要固定 runner / 预发多轮演练、证书轮换和性能损耗额外证据。
- N5 SDK 企业交付：`scripts/verify_sdk_enterprise_delivery.py` 已聚合 SDK distribution、package consumer、in-process business-flow、真实 gateway full-flow 和 backend TLS profile 下的真实 gateway full-flow；`sdk/docs/compatibility.md` 已补 C++/C ABI/Python/C# 客户端兼容矩阵，`sdk/docs/README.md` 已补生产客户端接入清单和 plain TCP / backend TLS profile 的客户端边界。2026-05-24 最后一轮已修复 SDK full-flow 动态端口、package consumer Debug/NOCONFIG 映射、business-flow 进程组 timeout 与 fixture 动态端口/teardown 收束，`runtime/validation/n5-sdk-enterprise-delivery-summary.json` 当前为 `PASS`。
- N6 gRPC/proto 取舍：`scripts/check_v3_grpc_poc_decision.py` 已补 v3 proto/gRPC PoC 决策门禁，验证 schema/transport contract、CMake target、TCP baseline 对照和 ADR 边界；当前结论是 generated gRPC 保留实验，不进入默认生产链路。
- N6 gRPC/proto 取舍补充：`tests/perf/grpc_vs_tcp_perf_test.cpp` 已不再是 placeholder，当前已基于真实 TCP backend request 与 gRPC `RequestLogin` RPC 生成 benchmark 数据；`gateway.proto` 与 `GatewayGrpcServer` 当前已覆盖 login/logout/health，以及 room/match/leaderboard/battle 的基础 RPC，`GrpcGatewayAdapter` 也已从 allow-all stub 收口到 `GatewayServiceBridge` 驱动的真实 backend 路由。2026-07-12 本机 Conan `grpc/1.67.1 + protobuf/5.27.0` 开启构建后，gRPC E2E 已形成 8 条事实链：基础 adapter E2E 验证 Room create/join、Match join/leave、Leaderboard submit/rank/top 和缺失房间的 `NOT_FOUND` 映射；实验 `boost_gateway::sdk_grpc` / `GrpcClient` 验证 Login、Room、Battle create/input/state/finish、Leaderboard submit/rank 与 Battle 结束后的标准失败状态；`StreamBattleState` 现为可取消的 server stream，`max_updates=0` 持续订阅时服务端将间隔夹紧到 100-5000ms，CQ 以独立 request/write/timer/done tag 回收，SDK callback 返回 `false` 会取消流，E2E 已验证两次正常/取消流均回收为 active=0、completed=1、cancelled=1，并验证 Battle 路由的 request/success/latency metrics；`GatewayGrpcServer::SecurityOptions` 已支持 trusted principal resolver + `Authorizer` RBAC，E2E 已验证缺失 principal `UNAUTHENTICATED`、observer 写路径 `PERMISSION_DENIED`、Battle stream 拒绝时不泄漏 active stream metrics；`GrpcServer` 已支持 `SslServerCredentials`，`GrpcClient` 已支持 `connect_secure()` 与 CA / client cert 配置，`scripts/gen_certs.py --include-client` 可生成临时 client cert，E2E 已验证 TLS 全流程和 mTLS 下“缺 client cert 失败 / 带 client cert 成功”两条路径；`GrpcGatewayAdapter` 现可直连 OTLP HTTP collector，gRPC E2E 已验证 `/v1/traces` 收到 `route.login_request` span；实验 `boost_gateway::sdk_grpc`、`project_proto` 和 `gateway.pb.h/gateway.grpc.pb.h` 已进入仓库内 CMake install/export 与 `find_package()` consumer 契约，`scripts/verify_sdk_package_consumer.py --with-grpc` 会显式校验该路径。2026-07-12 本机 Release 微基准（同进程 mock login backend，不是固定 runner 容量结论）在并发 1/10/100/1000 均零失败，gRPC P99 为 61us/1068us/4767us/42742us。生成入口只编译自包含的 `gateway.proto`，避免与 typed-envelope 领域 proto 的同名消息重复链接。仓库已新增独立 `.github/workflows/grpc-experimental.yml`，并已在 fixed-runner run `29196150703`（`main` / `0af5c91`）成功复用 `${{ github.workspace }}/../.conan2-local` 完成 `BOOST_BUILD_GRPC=ON` 的 Conan/build/ctest/package-consumer/decision-boundary 证据；因此当前剩余结论只是不把 gRPC 升格为默认生产链路，而不再缺 fixed-runner 入口事实。
- `v3.5.0` 最终冻结事实：annotated tag `v3.5.0` 固定在 `eed73cca8011a993d825b7d5e62a9cc6351dfdc0`。R0 run `29508284109`、2h/capacity/R4 run `29509769283`、R5/R6 run `29509972609` 与 readiness run `29544170962` 均绑定该候选；R3 为 `overall_pass=true`、`final_production_ready=true`。tag release run `29544261478` 的 package/publish 成功，线上 tarball SHA-256 为 `4affb1f624302b936da9bbdd460f8c2b04adc455ace3bb103a24cef5bfbc85c4`，修正后的 `SHA256SUMS.txt` 已经全新下载独立验签。
- `v3.5.1` 发布与事实治理：annotated tag 固定在 `d7ecb1ae075112d692c73bcc0d25b9ad554ed544`。AOI Release package run `29551112356` 和增强 R0 run `29551445037` 先完成候选验证；tag package/publish run `29551782341` 随后成功。线上 `boost-gateway-v3.5.1-linux-x64.tar.gz` 为真实 gzip、只有单一版本顶层目录并包含 README/CHANGELOG/LICENSE，全新下载后 `SHA256SUMS.txt` 验签通过，SHA-256 为 `0872a6040d62f1bac0972e531ab211104bd273bd18923b006bdbd56b68b2c71e`。本 patch 未改变运行时代码、默认协议或部署配置，因此没有把 `v3.5.0` 的 2h/R4/R5/R6 summary 伪装为新 SHA 证据。
- `v3.5.2` 发行包阶段证据：AOI Release run `29560450740` 绑定候选 `9945028d51dbf44dcfcf30a0d5a3d03a653a1140`，通过严格离线 Conan、完整 build/test/gates、真实 gzip layout、clean `ubuntu:24.04` package consumer（`network=none`、`pull=never`）和 SPDX JSON SBOM；artifact `8399167635` 包含 tarball、SBOM 与 `release-package-consumer-summary.json`，artifact ZIP 大小 21170227 bytes、SHA-256 为 `0fe6bb809fbe2bc4b0a3f069fcac35a309fa957aea4b1e11dcb2d486dbe61eae`。该手动 dispatch 按条件不生成 tag attestation，只作为最终 tag 之前的阶段证据。
- `v3.5.2` Operator 真实集群阶段证据：Specialized E2E run `29563770679` 绑定候选 `21a4815f`，AOI 使用 checksum 固定的 kind `v0.32.0`、kubectl `v1.36.1` 和 `kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5`，在严格离线 Conan preflight 后完成六组件 Ready、gateway 1->2->1 scale、rollout restart/undo、Operator restart、CR delete 与 kind cluster cleanup。artifact `8400330394` 中 `specialized-e2e-summary.json` 为 `summary_version=2`、`overall_pass=true`，Operator kind step 用时 33.927 秒。此前 runs `29561481625`、`29561854728`、`29562512251`、`29562931024`、`29563357565` 分别暴露 Docker Hub reset、legacy builder、无条件 registry HEAD、nginx pull 和 readiness probe 缺口，均保留为失败诊断，不计入成功证据。
- `v3.5.2` 最新 AOI 候选刷新：Release run `29564215641` 在 `18de5edbe1e37dfdf5c2d10e746664c6084cf3ed` 上再次通过完整 build/test/gates、clean Ubuntu consumer 与 SPDX SBOM，artifact `8400536405` 大小 21176305 bytes；增强 R0 首轮 `29564217856` 暴露顶层 `--include-kind` 被错误传给只接受 `--include-operator-kind` 的两个子门禁，修复后 run `29564768686` 在 `d8d8108446eee5b7927e01cdd7caf1204faaa4e6` 上成功。下载 artifact `8400890077` 核验，顶层 R0、production resilience、production evidence、P5 kind、P6 specialized summary 均为 `summary_version=2`、`overall_pass=true`；顶层 provenance 记录 candidate/git checkout 完全匹配、AOI/Release/lockfile SHA-256 `cd92f4c0cc579a066cf15cda76b4ffc63ade521bf5477ba6749c6a8187dc97d1`，scope 明确包含 Redis live、runtime HTTP、release baseline 与 kind。
- `v3.5.2` 最终冻结与发布：annotated tag 指向 `a0c6d051d09129a5330326c533bc65e4067b025d`。`myserver` Release `29587996645` / artifact `8409986789` 与增强 R0 `29588720453` / artifact `8410441198` 在同一 SHA 上通过；tag package/publish/两类 attestation run 为 `29589708378` attempt 2。线上 tarball/SPDX digest 分别为 `3142ffe7578e457e7d6fba63a6a00c3366874252b9f56894e9e8f9c7a31e047b` 和 `21a9fc4f0580da6785d0e5183ce9da78417e6aec9b74d8f6f77c5d7212656a33`。AOI 独立线上资产验证 run `29591469812` / artifact `8411254103` 为 `overall_pass=true`；`myserver` 也独立完成 checksum、clean runtime/CMake consumer 与 provenance/SPDX 验签。
- `v3.5.2` SDK/符号分发边界：正式安装面是 C++ CMake package、C ABI shared library 与 Linux x64 tarball/SBOM；Python `pyproject.toml` 和 C# `.csproj` 继续作为轻量源码 wrapper/example，不承诺 wheel/NuGet 仓库分发。当前 Release 只构建 `Release`，没有 `RelWithDebInfo`、符号拆分或符号服务器契约，独立 debug-symbol 包延期到下一次 minor。compiler-bearing Ubuntu 24.04/GCC 13/CMake 3.28/Ninja 1.11 镜像中的 `find_package/configure/build/run` 已由最终 Release 和线上资产复验通过。
- R0 生产候选证据聚合：`scripts/verify_production_candidate_evidence.py` 已聚合 fixed-runner preflight、P5 production resilience、P6 production evidence、N5 SDK enterprise delivery，并可显式追加 N4 TLS full-flow 与 N6 gRPC PoC decision；summary 写入 `runtime/validation/r0-production-candidate-evidence-summary.json`。最终 `v3.5.0` run `29508284109` 在 `eed73cc` 上通过 Redis live、runtime HTTP、release baseline、SDK 和生产 resilience/evidence 聚合；AOI 缺少 kind/kubectl，因此真实 Operator kind 不属于本次通过项。
- R1 TLS 上线前置证据：`scripts/verify_tls_production_readiness.py` 已覆盖 TLS profile full-flow、server CA 校验、证书轮换 full-flow、CA 不匹配 expected failure 诊断和 plain/TLS 单次业务闭环耗时对比；默认生产仍是 plain TCP，R1 只作为启用 backend TLS profile 前的前置证据。
- R2 生产候选证据 Manifest：`docs/production/production-candidate-evidence-manifest.json` 与 `scripts/check_production_evidence_manifest.py` 已将 R0/R1 本机有界证据、固定 runner 2h long-soak、R4 capacity、预发恢复演练和 TLS 预发多轮证据统一成可校验 manifest。`--require-fixed-runner` 会独立验证 2h 与 R4/R5/R6 summary 的成功状态、时效和同候选 provenance。
- 2026-07-16 run `29467142891` 诊断：候选 `4e15342` 的 2h soak 完成 7202.242 秒和 1624 轮持续基准，常规 capacity 也通过；后续 business-capacity 第一轮 `echo-1000-30s` 中 `bench_user_71` 因服务端把包体前四字节 `0x0107d100` 误作长度头而 EOF，999/1000 连接触发严格零失败门禁。`Session` 现以幂等 `start()` 和 Idle/Header/Body 单飞状态阻止重复 pending read；2h summary 在长稳步骤完成后立即写入 provenance，并与 capacity/R4 独立导入。
- 2026-07-16 run `29478926992` 诊断：候选 `80cc5cf` 使用 AOI runner-local Conan 与固定 lockfile 正常完成离线预检、构建和 7204.569 秒持续基准；1627 轮中只有一轮 `multi_battle_tick_100_entities.p99` 达到 2141.51us，失败率 0.0615%，但偏差 114.151% 超过稀有尾峰 25% 硬限制，因此该 2h summary 正确保持失败。持续基准现会把每个失败执行归档到 `runtime/perf/v2-stability-soak/failures/`，保存原始结果和前后 load/memory/CPU frequency/thermal 快照；long/overnight 对失败轮立即追加两次同配置确认，2/3 同指标失败仍按原偏差门槛阻断，只有两次确认均恢复且总原始失败率不超过 0.1% 时标记 `confirmation_recovered`。这不构成已通过的 2h 候选事实，仍需在新提交上重跑。
- 2026-07-16 run `29494894953` 已在当前 `main` 候选 `480d5fd` 上形成新的 2h 成功事实。AOI runner 严格离线 Conan 预检、Release 构建和 P5 resilience 聚合均通过；持续架构基准运行 7201.245 秒、完成 1616 轮，`failure_events`、`violating_checks` 和 `failed_checks` 均为空，`long-soak-2h-summary.json` 与顶层 summary 均为 `overall_pass=true`。provenance 记录 `candidate_revision=git_commit=480d5fd...`、`revision_matches_checkout=true`，lockfile 为 `linux-gcc-x64-release-nogrpc-nosqlite.lock`（SHA-256 `cd92f4c0...97d1`）。同 SHA 的前序 run `29489191125` 在前 13 步成功后于 long-soak 步骤异常终止，GitHub API 中该步骤仍为 `in_progress` 且没有 log/artifact，不能归类为应用门禁失败。成功 run 明确关闭 capacity/business-capacity，R4 步骤被跳过；因此它只解除 `480d5fd` 的 2h long-soak 缺口，不替代同 SHA 的 R0、R4、R5/R6 或 R2/R3。
- R3 生产 Readiness Report：`scripts/render_production_readiness_report.py` 已将 R2 manifest、R0 aggregate 和 R1 TLS readiness 汇总为 Markdown 报告与机器 summary；报告明确区分 bounded local evidence 与 final production readiness。`v3.5.0` readiness run `29544170962` 导入同一 `eed73cc` 的四类 artifact，bounded/fixed R2 均通过，最终 `final_production_ready=true`。
- 2026-07-13：R2/R3 证据来源契约已收紧。R0、真实 2h soak、R4、R5、R6 核心 summary 现在统一写入 `provenance`，包含候选提交、实际 checkout、workflow/run、runner、构建配置和 Conan lockfile SHA-256；R2 会拒绝缺失时间、缺失 provenance、checkout 不匹配和跨候选提交的组合。R4 同时拒绝引用其它提交生成的 capacity/business-capacity summary。`production-readiness.yml` 会分别生成 bounded/fixed 两份 R2，最终模式要求两者同时通过。现有不同提交上的成功 run 继续作为历史能力事实，但不能再拼接成最终 R2/R3；下一轮必须先冻结一个候选 SHA，再在同一 SHA 上刷新 R0、2h、R4、R5、R6。
- `v3.5.0` 同 SHA 证据刷新已完成：`29508284109` 提供 R0，`29509769283` 提供 7202.597 秒/1624 轮零失败的 2h summary 以及 capacity/business-capacity/R4，`29509972609` 提供真实 Docker Compose gateway restart R5 和两轮 R6，`29544170962` 汇聚并生成最终 R2/R3。此前不同 SHA 的成功或失败 run 只保留为历史诊断，不参与最终结论。
- R4 固定 Runner Release / Capacity 证据：`scripts/verify_fixed_runner_release_capacity.py` 已将 release baseline、capacity profile 和 business-capacity profile 汇总成 `runtime/validation/fixed-runner-release-capacity-summary.json`，并校验 capacity/business-capacity 的 preset、必需 case 与最小 repetitions，用于解除 R2/R3 中 `fixed_runner_release_capacity` 阻断；最终投产仍建议在固定低噪声性能机器上刷新该 summary。
- R5 预发恢复 / 回滚演练证据：`scripts/verify_preprod_recovery_drill.py` 已将 N3 recovery gate、Docker Compose gateway restart、SDK full-flow、Docker production snapshot 和 recovery drill record validator 串成 `runtime/validation/preprod-recovery-drill-summary.json` producer。最终 run `29509972609` 在 `eed73cc` 上使用 `docker_pull_policy=never` 和本地 11/11 镜像完成真实 Compose gateway restart，恢复前后 SDK full-flow 与 Prometheus/Grafana snapshot 全部通过。
- R6 TLS 预发多轮证据：`scripts/verify_tls_preprod_multi_run.py` 已多轮聚合 R1 TLS readiness，覆盖 TLS full-flow、证书轮换、CA mismatch expected failure 和 plain-vs-TLS overhead ratio，输出 `runtime/validation/tls-preprod-multi-run-summary.json`。最终 run `29509972609` 的两轮结果均通过，plain-vs-TLS overhead ratio 为 `1.037`、`1.026`，provenance 与 `eed73cc` 一致。
- 生产认证边界：生产 `auth.mode=external-jwt` 已收口为外部身份提供方的 RS256 JWT 验证方，强制 public key、issuer、audience 与 `exp`，拒绝对称密钥、私钥、本地注册、guest 登录和 refresh token 签发。开发模式仍保留演示身份能力；其进程内存账户状态不再保存任何 credential hash，不能作为生产账户库。JWKS/多 `kid` 在线轮换、持久化账户体系和可撤销 refresh token 仍是外部身份提供方集成的后续工作。
- 脚本与配置治理：`docs/script-inventory.json` 已将顶层脚本划分为 public entrypoint、aggregate gate、producer、tool、platform wrapper 和 legacy；`scripts/check_script_inventory.py`、`scripts/check_validation_summary_contract.py`、`scripts/check_config_source_layout.py` 已用于阻断脚本索引、summary v2 契约和 `env/` 配置事实源漂移。后续如需物理移动脚本，必须先保留顶层 shim 并更新 inventory / reliability matrix。
- 默认主线测试面为 `tests/v2`、SDK 和对应 gate。
- 其中 `admin_service` 已明确留在 legacy-v1 / demo-only 面，不进入默认 gate，也不作为当前 v2 生产控制面承诺。后续如需评估新的 v2 控制面，参考 `docs/legacy/v2-control-plane-preplan.md`。
- P3 监控运维：Prometheus 已加载 `env/monitoring/prometheus-alerts.yml`，Grafana dashboard 已对齐当前 gateway `/metrics` 真实指标，`scripts/check_monitoring_operability.py` 会阻断后端 HTTP scrape、旧指标名和 runbook 漂移；运维流程见 `docs/deployment/production-operations-runbook.md`。
- P4 SDK 企业级封装：C++ SDK heartbeat 已实作，disconnect callback 可由 heartbeat failure 触发；C ABI 暴露 heartbeat 控制，Python/C# wrapper 增加 native 版本校验和加载/分配诊断；SDK business-flow 与 full-flow client 验证覆盖 login、room、ready、battle、push、reconnect、heartbeat。
- H0-H5 生产候选硬化：`scripts/check_production_hardening_gate.py` 聚合手动 fixed-runner 生产 gate 入口、长稳/容量/K8s/观测/SDK 企业接入证据；当前没有 weekly schedule，P5/P6 通过 `.github/workflows/production-gates.yml` 选择 `gate=p5-resilience|p6-evidence` 执行。
- 生产性能快照：`scripts/collect_docker_production_perf_snapshot.py` 已补齐 OrbStack / Docker Compose 生产栈运行态采样入口，覆盖 gateway readiness/diagnostics、Prometheus targets、Grafana health 和容器 CPU/RSS/PID/IO 快照；本机实测 `overall_pass=true`，产物见 `runtime/perf/docker-production-snapshot/`。
- 生产业务闭环接入：`docs/production-business-closure-plan.md` 已完成 P0-P8 收束。默认代码主链支持 gateway 同时接入 login / room / battle / matchmaking / leaderboard，Compose、Kubernetes 和当前 systemd 默认入口均按五后端装配；其中 leaderboard 可选 Redis 持久化，未配置 Redis 时保持内存降级。P0-P2 打通 SDK matchmaking/leaderboard、full-flow 和 battle settlement 自动写榜；P3-P4 将新业务路径纳入性能/监控/快照，并完成 Redis/Raft HA profile；P5-P8 补齐 OTel/trace、TLS 边界、K8s/Operator full-flow 入口和 v3 proto/gRPC ADR。聚合验证入口为 `scripts/verify_p5_p8_business_closure.py`。
- P0-P7 框架现代化与坦克大战 demo：已按 `docs/realtime-framework-modernization-plan.md` 完成 P0-P7 全部 checkpoint。P0 目录与文档结构固化；P1 identity 注册协议与错误码完成；P2 房间大厅支持 list/detail/kick/transfer；P3 实时实例运行时（`v2::realtime::InstanceRuntime`）实现 tick-based 游戏循环；P4 坦克大战仿真（`TankWorld` 20×15 网格）含运动/碰撞/子弹/得分；P5 settlement 与 leaderboard 数据结构就绪；P6 resume/reconnect 支持；P7 回归门禁与验证脚本覆盖 642 测试 + 8 个 checkpoint。demo 全部位于 `demo/games/tank_battle/`，默认不参与生产构建（`BOOST_BUILD_TANK_DEMO=OFF`）。
- R4/R5 ECS 管线增强与 TankBattlePlugin：`InstancePlugin` SPI 正式化（8 虚方法 + noexcept 契约 + try-catch 错误隔离），`TankBattlePlugin` 完整实现（move/attack/shoot/finish）位于 `src/v2/battle/`。这些能力当前定位为框架/demonstration/plugin 侧扩展，不属于默认生产 battle 主链；默认 battle 后端仍以 `BattleInstancePlugin` 为当前部署事实。新增 ECS 系统：`ProjectileSystem`（弹道飞行/AoE/DoT）、`BattleLifecycleSystem`（自动状态机 kCreated→kRunning→kFinished，空闲超时 300 帧，离线超时 60 帧）、`BattleReplaySystem`（逐帧快照录制）、`AoiSystem`（ECS 集成 AOI + SpatialGrid）。新增组件：`ProjectileComponent`、`DamageOverlayComponent`、`BattleReplayFrameRecord`。数据持久化层：`CachedBattleDataStore`（LRU + WriteBehind）接入 demo_server，`JsonFileBattleDataStore` 文件落地。远程 Actor 通信：`RemoteActorRef::tell()` 跨节点消息投递。gRPC 网关：`GatewayGrpcServer`（login/logout/health）保留为 `BOOST_BUILD_GRPC` 条件编译下的实验能力，不进入默认生产链路。62+ 新增测试覆盖 lifecycle/replay/AOI/spatial grid/file store/tank battle plugin/projectile system。
- N1 性能刻度（perf scaling）：`demo/games/tank_battle/tests/perf_test.cpp` 新增 500 实例 × 50  ticks 基准规格（保守阈值 100 TPS），覆盖 2/20/100/500 四级并发刻度用于 CI 性能回归。
- N5 SDK Python 示例：`demo/games/tank_battle/client_sdk_adapter/python_demo.py` 作为坦克大战 Python SDK demo，走通 connect→login→room→battle→move→finish→leaderboard→disconnect 全生命周期，输出 JSON 摘要并支持 `--n5-demo` 集成到 `verify_tank_battle_demo.py` 验证脚本。

## P0 性能优化轮次（2026-05-23）

Release 构建 + 5 后端拓扑下完成 P0 收束，4 项性能修复 + 基线验证：

### 修复项
- **后端连接池实验**: `gateway_service_bridge.cpp` 生产默认已回收为 1；多连接池只保留为显式压测/实验参数，不能作为默认投产路径
- **战斗路由线程卸载**: `runtime.cpp` 默认工作线程 0→4
- **CircuitBreaker 线程安全**: `circuit_breaker.h/.cpp` 添加 mutex 保护
- **高精度定时器**: `v2::platform::HighResTimer` RAII 封装，消除粗粒度休眠

### 基线结果（Release, 3 轮）
| 场景 | 阈值 | 优化前 | 优化后 |
|------|------|--------|--------|
| echo-100 | P99 ≤ 50ms | 100ms ❌ | **1ms** ✅ |
| echo-1000 | P99 ≤ 50ms | 150ms ❌ | **5ms** ✅ |
| battle-20 | P99 ≤ 100ms | 750ms ❌ | **10ms** ✅ |
| battle-100 | P99 ≤ 250ms | 5000ms ❌ | **200ms** ✅ |

后端正向延迟从 ~30ms 降至 ~2.5ms，echo 吞吐最高 17,846/s，battle 吞吐 1,424/s。详见性能基线文档。

### 稳定性
- Unit tests: **772 通过 / 63 跳过（Redis 依赖）/ 0 失败**（Release 构建）
- Capacity baseline: P99 尾部无明显退化

## 保留边界

- 2h/8h soak、10K capacity、1/2/4 核和业务专项已进入固定 runner 阻断证据链；Redis/Raft 恢复、Redis 告警生命周期和 TLS 对照已形成真实专项证据。OTel off/on 采集器已实现 exporter/collector/后端路由计数对账，仍需与其他高风险产物在最终冻结 SHA 上刷新；生产容量上限声明、更完整 Operator rollback/probe E2E、更完整角色化 RBAC、外部 OTel collector 长稳和 generated gRPC transport PoC 仍属于后续专项。默认生产主链仍是 SDK + TCP gateway + BackendEnvelope + 五后端 + Redis。
- 主线定位为企业级高性能实时服务框架。坦克大战和后续游戏/实时系统样例必须放在 `demo/games/` 作为业务验证 demo，不能把碰撞、地图、胜负、得分公式等业务规则写入 gateway、login、room、leaderboard 或公共 SDK。当前边界以 `docs/architecture-overview.md`、`docs/project-blueprint.md` 和 `demo/games/README.md` 为准；`docs/archive/plans/realtime-framework-*.md` 只保留历史决策过程。
- 默认 CI/release workflow 使用有界 smoke 门禁，避免长时间占用终端或 runner。

## R7 模块收束（2026-05-23）

基于”模块已实现但未接入生产主流程“的分析，对以下模块进行了收束，将 demo/unit-test-only 的功能接入实际项目路径：

### P0 持久化层 — 编译接入生产构建

- `persistence/writebehind.cpp`、`persistence/replay_storage.cpp`、`persistence/storage_engine_sqlite.cpp`、`persistence/player_data.cpp` 已加入 `project_v2` 静态库构建
- `BOOST_BUILD_SQLITE` CMake 选项：启用后自动检测 sqlite3 并定义 `HAS_SQLITE`
- `ReplayStorage` 已接入 `BattleBackendService`：战斗结算时自动保存 replay
- 接入点：`examples/v2_battle_backend/main.cpp` 通过 `set_replay_storage_dir()` 配置 replay 存储目录

### P1 内存架构 — ECS Entity 存储集成

- `BumpArena` 注入 `SimpleWorld`：`create_entity()` 优先从 arena 分配 `EntityStorage`，arena 不可用时回退到堆分配
- `ObjectPool<EntityHandle>` 接入实体销毁路径：`destroy_entity()` 将 handle 回收到池中
- Entity 生命周期现在完全由 arena/pool 管理，不再依赖单 `generations_` map

### P1+P2 诊断 & 鉴权 — Gateway Runtime 集成

- `DiagnosticsManager` / `HealthCheck` 通过 forward declaration + `unique_ptr` 注入 `Runtime`，避免循环依赖
- `DemoServer` 已将 `BackendMetrics` 和 `ServiceRegistry` 数据源接入诊断
- `LoginBackendService` / `RoomBackendService` 全部 handler 使用 `diag_wrap` 包装（try-catch + SPDLOG）
- `Authorizer` RBAC 接入消息分发：`Runtime::handle()` 中 `is_session_allowed()` 在 JWT 验证后执行角色化权限检查
- `set_session_role()` / `is_session_allowed()` 接口已暴露，Player 默认角色新增 match（6001/6004/6006）和 leaderboard（7001/7003/7005）消息 ID

### 未解决问题

（当前无活跃阻塞项）

## 当前阶段结论

生产稳定化、交付闭环、生产业务闭环接入、N0-N6 生产数据沉淀与风险燃尽，以及 R0-R6 生产候选实证阶段已经完成当前有界收束。当前主线具备生产候选所需的默认有界 gate、固定 runner 入口、部署/运维/SDK 文档、监控告警静态校验、P5 resilience gate、P6 production evidence gate、P5-P8 business closure gate、N3 recovery gate、N4 transport/config governance gate、N5 SDK enterprise delivery gate、N6 gRPC PoC decision gate、R0 production candidate evidence gate、R1 TLS production readiness gate、R2 evidence manifest gate、R3 readiness report、R4 fixed-runner release/capacity gate、R5 preprod recovery drill gate、R6 TLS preprod multi-run gate 和生产候选完整性审核。

P0-P7 框架现代化已在 `main` 分支提交，commit 范围 `7bb4898..5a43edd`。

当前默认可执行入口：

1. 本地/PR 快速：`python3 scripts/verify_release_candidate.py --skip-release-baseline --soak-profile smoke`
2. 坦克大战 demo 验证：`python3 demo/games/tank_battle/scripts/verify_tank_battle_demo.py --build-dir build`
3. P5 resilience：`python3 scripts/verify_production_resilience_gate.py --build-dir build/default --skip-build`
3. P6 production evidence：`python3 scripts/verify_production_evidence_gate.py --build-dir build/default --skip-build`
4. P5-P8 business closure：`python3 scripts/verify_p5_p8_business_closure.py --build-dir build/default --skip-build`
5. N4 transport/config governance：`python3 scripts/check_transport_config_governance.py`
6. N5 SDK enterprise delivery：`python3 scripts/verify_sdk_enterprise_delivery.py --build-dir build/default --skip-build`
7. N6 gRPC PoC decision：`python3 scripts/check_v3_grpc_poc_decision.py --build-dir build/default`
8. R0 production candidate evidence：`python3 scripts/verify_production_candidate_evidence.py --build-dir build/default --skip-build`
9. R1 TLS production readiness：`python3 scripts/verify_tls_production_readiness.py --build-dir build/default --skip-build`
10. R2 production evidence manifest：`python3 scripts/check_production_evidence_manifest.py`
11. R3 production readiness report：`python3 scripts/render_production_readiness_report.py`
12. R4 fixed-runner release/capacity evidence：`python3 scripts/verify_fixed_runner_release_capacity.py`
13. R5 preprod recovery drill：`python3 scripts/verify_preprod_recovery_drill.py --build-dir build/default`
14. R6 TLS preprod multi-run：`python3 scripts/verify_tls_preprod_multi_run.py --build-dir build/default --skip-build`
15. 生产候选审核：`python3 scripts/check_production_candidate_audit.py`

## 下一阶段优先级

后续长期开发以 `docs/project-blueprint.md` 为规划依据。本文档继续作为”已经实现/当前默认链路”的事实源；`docs/fixed-runner-playbook.md`、`docs/release-governance.md` 和各 runtime summary 继续作为生产候选证据事实源。
当前 1-3 个月的实际主线执行顺序，见 `docs/mainline-execution-plan.md`。

当前默认有界收束已经完成；长稳 2h/8h 已有固定 runner 连续证据，10K 容量必须按新的真实 lifecycle 契约重新采集，TLS/OTel 对照继续按各自候选 SHA 和专项边界归档。真实 gRPC transport profile 继续作为后续专项持续沉淀。业务验证型后续工作必须继续遵守“框架与业务隔离”：demo 只放在 `demo/games/` 或后续 demo 目录，不能把坦克大战等具体业务规则写入公共框架主链。

下一阶段执行优先级概括为：

1. 当前主线：`v3.5.3` 的 Release、R0、R4、R5/R6、Raft、2h/8h、R2/R3、tag publish 和线上资产验证已在 `b9c348b4` 闭环。发布后分支 `codex/v36-capacity-evidence` 已将 service/load generator CPU 隔离、逐轮相邻资源快照和 quiescence 纳入硬门禁；AOI run `29742852766` 验证了两侧隔离及实际发出流量的 P99/吞吐，但仍使用旧客户端 lifecycle，不能证明命名中的 echo-5K/10K 已达到目标稳态。
2. 候选 `375910f3` 的 AOI runs `29790072882` / `29791850363` / `29793036782` 保留了有效的 affinity、资源快照和已发出流量诊断，但旧 pressure client 会按计划强制结束尚未启动或尚未认证的客户端，且旧 summary 用推导值代替真实 TCP/auth/active lifecycle。它们不能证明 5K/10K 客户端达到稳态，也不能支持“1 CPU 满足 10K”或 1/2/4 CPU 扩展结论；加固后的聚合器会按缺少 `case_manifest` 和真实 lifecycle fail closed。
3. 当前 `codex/v36-capacity-evidence` 候选已实现真实客户端 lifecycle、稳态计时、结构化失败、饱和曲线、1/2/4 CPU 与 `io_cores` 单变量聚合，以及 battle offload/SDK 生命周期修复；本地回归通过后仍须在 AOI fixed runner 刷新完整数据。该分支尚未合入 `main`，不得把实现状态写成主线发布事实。
4. 已闭环的实验项：generated proto/gRPC 已由 fixed-runner run `29465329265` 完成严格离线构建、测试、安装包 consumer 与 N6 决策证据；默认生产传输结论继续保持 `defer_default_transport`，本轮不再扩展或升格 gRPC。
5. 当前证据工程：长稳/生产 resilience 编排器已增加取消信号、Linux parent-death/PID bridge、分层进程组回收和原子部分失败 summary。AOI 取消探针 run `29795945950` 的顶层、P5 与 stability 三层均记录 `interrupted=true` / `SIGTERM`，保留 38.766 秒、9 轮和 3 个资源样本，且 job cleanup 无 orphan process；线上资产复验已增加 standalone SBOM 与已验证 SPDX predicate 的结构绑定。checkpoint 只用于审计，不允许把多个中断段累计成 2h/8h 通过。
6. 长期：generated proto/gRPC 从当前 unary SDK 多服务 E2E 扩展到 streaming transport profile、跨语言/版本化 SDK 分发契约，以及 Developer Guide、贡献路径、通用实时服务 plugin 生态、macOS ARM64 和固定/高性能 runner 趋势化容量报告。

当前命名与默认维护面状态：

- 对外产品/框架名称按 `BoostGateway` 收敛。
- 仓库历史名 `BoostAsioDemo` 暂时保留，用于兼容历史引用与路径。
