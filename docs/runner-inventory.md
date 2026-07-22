# GitHub Actions Runner Inventory

更新时间：2026-07-22（macOS ARM64 + Linux ARM64 runner 与 persistent Conan admission）

本文档作为仓库 Actions runner 拓扑的单一事实源。`current-state.md` 与 `fixed-runner-playbook.md` 只引用这里的结论，不再各自维护 runner 在线状态描述。
runner 命名、custom labels、Conan/Docker/R5 准入规则见
`docs/runner-gate-standard.md`。

## 验证来源

- 仓库：`HoneyBury/boost_gateway`
- 验证时间：2026-07-22 14:28 UTC
- 验证命令：`gh api repos/HoneyBury/boost_gateway/actions/runners`

## 当前快照

| Runner | OS | 状态 | Busy | 版本 | Labels |
|---|---|---|---|---|---|
| `aoi-omen-gaming-laptop-16-am0xxx` | Linux | `online` | `false` | `2.335.1` | `self-hosted`, `X64`, `Linux`, `node-aoi-omen-gaming-laptop-16-am0xxx` |
| `HoneyBurydeMacBook-Pro` | macOS | `online` | `false` | `2.335.1` | `self-hosted`, `macOS`, `ARM64`, `node-honeybury-macbook-pro`, `sdk-osx-arm64`, `jwks-macos-arm64`, `macos-arm64-candidate` |
| `HoneyBury-M4-Linux-ARM64` | Linux | `online` | `false` | `2.336.0` | `self-hosted`, `Linux`, `ARM64`, `node-honeybury-m4-linux-arm64`, `ubuntu-2404`, `conan-gcc13-release`, `conan-gcc13-debug`, `conan-gcc13-grpc`, `preprod-r5-honeybury-m4-linux-arm64`, `preprod-r5`, `sdk-linux-arm64`, `debug-symbols-linux-arm64`, `jwks-linux-arm64` |
| `MyDesktop-Win` | Windows | `offline` | `false` | `2.334.0` | `self-hosted`, `Windows`, `X64` |
| `myserver` | Linux | `offline` | `false` | `2.335.1` | `self-hosted`, `X64`, `Linux`, `preprod-r5`, `preprod-r5-myserver` |

## 当前结论

- Linux runner `aoi-omen-gaming-laptop-16-am0xxx` 已在线，并匹配 `["self-hosted","Linux","X64"]`。
- macOS runner `HoneyBurydeMacBook-Pro` 已在线，并具备唯一 label `node-honeybury-macbook-pro`。run `29927622379` 已形成原生 R5、bounded baseline、package 与 dSYM exact-SHA artifact；runs `29925779628`、`29928355843` 分别完成原生 JWKS 与 osx-arm64 SDK evidence。
- Linux ARM64 runner `HoneyBury-M4-Linux-ARM64` 已在线，并匹配唯一 label `node-honeybury-m4-linux-arm64`。Release run `29906228268`、Debug run `29907949804`、gRPC run `29908827298` 和原生 `linux/arm64` R5/R6 run `29909904605` 已完成 G0-G5 准入；runner 已获得机器专属及共享 `preprod-r5` labels。
- AOI 已安装并验签 kind `v0.32.0` 与 kubectl `v1.36.1`，固定 Kubernetes `v1.36.1` node digest 已进入本地 cache；run `29563770679` 的真实 Operator kind summary 为 `overall_pass=true`。
- 默认指向 Linux fixed-runner 的 workflow 可以开始实际执行；是否形成生产证据仍取决于各 workflow 的 preflight、summary 和 artifact，而不只是 job 被派发。
- `myserver` 当前离线且正在重新配置；历史 G2/G3/R5 事实不能代替新环境准入。
- Windows runner `MyDesktop-Win` 当前离线，且已经退出 Linux/macOS 双平台维护范围。

GitHub API 在 2026-07-22 14:28 UTC 确认 AOI Linux、Mac 原生 runner 与 Mac-hosted
Linux ARM64 runner 在线且 `busy=false`；Windows 与 `myserver` 离线。
R5 机器专属复验必须使用目标机器的 unique custom label；不能用共享 label 代替
当前候选的 G2/G3 cache 准入。

## 2026-07-15 本机核验

| Runner | OS / toolchain | 本机状态 | 结论 |
|---|---|---|---|
| `myserver` | Ubuntu 24.04 x64, kernel 6.8, GCC 13.3, pinned venv Conan 2.8.1, Docker 29.5, Compose 5.1 | GitHub API 当前 `online`；唯一 label `preprod-r5-myserver` 与历史共享 label 仍保留 | 最终 `v3.5.2` SHA 的 strict Conan、Release `29587996645`、R0 `29588720453`、tag publish 与独立线上资产验收均通过。 |
| `aoi-omen-gaming-laptop-16-am0xxx` | Ubuntu 22.04 x64, GCC 13.4, Conan 2.8.1 | GitHub API `online`；唯一 label `node-aoi-omen-gaming-laptop-16-am0xxx` | `/opt/boost-gateway`、新图 namespace 和 runtime-only Docker cache 已预热；run `29415968573` 完成 strict offline Conan、R5 和 R6，结论 success。 |

本机核验不能替代预发 runner artifact。当前 AOI 使用机器唯一 label 定向执行；
共享 `preprod-r5` 能力池只应包含在线且通过 G0-G3 的 runner。

## 2026-07-22 macOS ARM64 Runner 准入

| Runner | 原生工具链 | Persistent Conan | Docker/R5 |
|---|---|---|---|
| `HoneyBurydeMacBook-Pro` | macOS 26.5.2 ARM64、Apple Clang 21、CMake 4.2.1、Ninja 1.13.2、Python 3.12、sccache 0.16.0、.NET 8、Syft 1.49 | runner tool cache 下 Conan 2.8.1 namespace `graph-27de4eada077b868e6b4`，13 个锁定包 `--no-remote --build=never` 通过；独立 SDK package venv 固定 setuptools 83.0.0/wheel 0.47.0；sccache 使用 OrbStack-safe `127.0.0.1:4227` | run `29927622379` 完成原生 R5/package/dSYM，run `29925779628` 完成 JWKS，run `29928355843` 完成 osx-arm64 SDK |

Mac 原生生产候选与 Linux Docker R5 是两条证据线。`macos-arm64.yml` 使用 Mach-O
server 进程执行 gateway restart 前后完整 SDK flow；Mac-hosted Linux ARM64 runner
则只消费 ARM64 ELF 和 `linux/arm64` image。两条证据不能互相替代。

Mac SDK packaging 使用 runner tool cache 下独立的 `tools/sdk-package-py3.12`，固定
setuptools 83.0.0 与 wheel 0.47.0。JWKS run `29925779628` 在
`19b1a67d439dc3c82cf18eb2990b02a38c05131c` 完成真实 HTTPS drill 10/10 和 summary
contract 6/6，artifact ID `8531893301`。当前候选 SHA
`a355fb7500ad259ae8921db04effbe325483400f` 上，run `29927622379` 完成全量 CTest、
原生 R5、bounded performance/stability、package 与 dSYM 160/160，artifact ID
`8532883869`；SDK run `29928355843` 完成 osx-arm64 package 23/23、full-flow 15/15，
artifact ID `8532949790`。

## 2026-07-22 Mac-hosted Linux ARM64 Runner 准入

| Runner | 原生工具链 | Persistent Conan | 当前 Gate |
|---|---|---|---|
| `HoneyBury-M4-Linux-ARM64` | OrbStack Ubuntu 24.04.4 `aarch64`、GCC 13.3、CMake 3.28、Ninja 1.11、Python 3.12、Docker/Compose 29.1/2.40、Go 1.22、.NET 8、Syft 1.49 | `/opt/boost-gateway` 下 Conan 2.8.1 Release/Debug/gRPC namespaces；三类图均完成 `--no-remote --build=never` | G0-G5 已通过；完整平台 baseline/soak/capacity 仍待完成 |

VM 名为 `boost-linux-arm64`，runner 由
`actions.runner.HoneyBury-boost_gateway.HoneyBury-M4-Linux-ARM64.service` 管理，
systemd 状态为 enabled/active。OrbStack 已启用登录启动，VM 重启后 runner 服务与
原有 ARM64 容器均完成恢复验证。runner 具备唯一 identity label
`node-honeybury-m4-linux-arm64`。G3-G5 完成后已添加
`preprod-r5-honeybury-m4-linux-arm64` 与 `preprod-r5`。

Mac host 固定设置 `machines.forward_ports=false`，防止 VM 的
9080/9201/9202/9302-9305 自动映射到 Mac 并污染原生 macOS workflow。修改该配置后
必须执行 `orbctl stop && orbctl start`；恢复后同时验证 Linux runner online、VM 内
Redis `PING`、ARM64 Docker/kind image，以及 Mac 上上述端口在任务空闲时无监听。

SDK packaging 工具使用独立持久 venv
`/opt/boost-gateway/tools/sdk-package-py3.12`，固定 setuptools 83.0.0、wheel 0.47.0
与 auditwheel 6.7.0，不污染 Conan venv。run `29926003937` 在
`19b1a67d439dc3c82cf18eb2990b02a38c05131c` 完成 Linux ARM64 JWKS 10/10 和
summary contract 6/6，artifact ID `8532055003`。同平台 SDK run `29926636641`
在 `a355fb7500ad259ae8921db04effbe325483400f` 完成 package 25/25、full-flow 15/15、
SBOM/checksum，artifact ID `8532281062`。debug-symbol run `29926847088` 在同 SHA
完成 14 ELF pair、独立验证 116/116 与 crash probe 12/12，artifact ID
`8532586136`。

首次 run `29905435922` 暴露 `gha-setup-ninja@v5` 下载 x86_64 Linux 二进制的问题。
提交 `fec858d13e5366ab46668cb759b8ec6a87169926` 让 Linux ARM64 fixed-runner workflow
使用准入的系统原生 CMake/Ninja/Python 后，联网预热 run `29905671975` 成功；同 SHA
严格离线 run `29906228268` 使用 ARM64 profile/lockfile、`--build=never` 和关闭 remote
完成 install、Conan-provider configure 与 unit-test target build。artifact
`conan-validate-29906228268` 的 ID 为 `8523977573`。

Debug namespace 由联网 seed run `29907427580` 初始化；同 SHA 的 strict-offline run
`29907949804` 使用 Debug profile/lockfile、`--no-remote --build=never` 完成 configure
与 unit-test target build。artifact `conan-validate-29907949804` 的 ID 为
`8524649296`。gRPC 独立 namespace 使用 ARM64 gRPC/no-sqlite lockfile 初始化后，
strict-offline run `29908827298` 在同一 SHA 完成项目 gRPC targets、focused tests、
installed SDK consumer 与 PoC boundary，artifact `grpc-experimental-29908827298`
的 ID 为 `8525038829`。

最终原生 Linux ARM64 预发 run `29909904605` 在
`9485993b92f0d8e06fe675eae89c47280a7f46d2` 以 `docker_pull_policy=never` 验收
6 个候选构建镜像和 5 个 registry 镜像。11/11 镜像均为 `linux/arm64`，missing、
wrong-platform、stale-build 均为 0；R5 的 23 个步骤、恢复记录 33/33 通过，Redis
退化/恢复包含在内；R6 两轮均通过，开销比为 1.044 和 1.028。artifact
`preprod-evidence-29909904605` 的 ID 为 `8525559864`。Release、Debug/gRPC 与 R5
是 runner 准入能力证据，分别绑定上述 exact SHA；不得拼接成最终 frozen-candidate
发布结论。

当前 Mac 的本地执行策略为仅保留原生 ARM64：已删除 4 个未被容器引用的 amd64
Docker images，将 `ubuntu:24.04` 替换为 `linux/arm64`，镜像清点结果为 0 个 amd64；
OrbStack Rosetta 转译也已关闭。远端 Linux x64 runner 及其缓存不在本机清理范围内，
仍继续承担 linux-x64 证据。

`myserver` 在 2026-07-15 对候选 `18abba26aba2c0fe3d7d59e399e84887f9279bab`
完成 G0-G5。主线 graph `3f740f23045ffb63d9a3` 在发现旧 seed 缺失 recipe export 后
已从批准 remote 干净重建；实验 gRPC graph `3d1aa0fa82959e8f09ad` 也已显式预热。
两者均已关闭全部 remote，并分别以 10/10、15/15 包完成离线验收。run `29428322350`
使用机器唯一 label 和 parallelism 2 完成 strict Conan、Release 全目标构建、真实 R5
gateway restart recovery drill 及两轮 R6；artifact `preprod-evidence-29428322350` 的
R5/R6 summary 均为 `overall_pass=true`。根分区在完整 run 后仍保留约 29GB 可用空间。

## 最近验证

2026-07-11 在 `develop` 上完成的手动 workflow 验证：

| Workflow | Run | 提交 | 结论 |
|---|---:|---|---|
| `release.yml` | `29142235214` | `cb1c853` | success |
| `conan-validate.yml` | `29142663279` | `cb1c853` | success |
| `nightly-stability.yml` | `29142649741` | `cb1c853` | success |
| `ci.yml` | `29143332897` | `cb1c853` | success |
| `perf-regression.yml` | `29143057517` | `cb1c853` | success |
| `perf-commit-check.yml` | `29144040776` | `cb1c853` | success |
| `specialized-e2e.yml` | `29145172304` | `e8ea86f` | success |
| `production-resilience.yml` | `29145497642` | `cbed12d` | success |
| `production-evidence.yml` | `29146018657` | `ecf624c` | success |
| `production-candidate-evidence.yml` | `29152333112` | `8cadbef` | success: R0/P5/P6/N5 summaries all passed |
| `long-soak-capacity.yml` | `29146495724` | `ea05045` | failure: long profile recorded 13.952s, not 2h; battle-500 P99=750ms; business-capacity UTF-8 decode failure |
| `long-soak-capacity.yml` | `29153158335` | `f5516be` | failure: Conan and SDK business flow passed; second sustained baseline gate failed after 209s; battle-500 P99=750ms persists |

2026-07-12 在 `main` 上补充完成的 gRPC fixed-runner 实验验证：

| Workflow | Run | 提交 | 结论 |
|---|---:|---|---|
| `grpc-experimental.yml` | `29195792943` | `5df1479` | failure: `use_existing_workspace=true` 时 runner workspace HEAD 与 `GITHUB_SHA` 不一致，命中 preflight 保护 |
| `grpc-experimental.yml` | `29196150703` | `0af5c91` | success: `use_existing_workspace=false` + `no_remote=true`；当时 runner 预置 Conan 缓存可完成 `BOOST_BUILD_GRPC=ON`、SDK consumer 与 decision-boundary 验证。当前缓存策略已按 Ubuntu release/GCC/arch/build type 分区。 |
| `preprod-evidence.yml` | `29345674702` | `f6e0e57` | failure on `aoi-omen-gaming-laptop-16-am0xxx`: `/opt/boost-gateway` missing or not writable, so persistent cache resolution failed before Conan/Configure/R5. R6 build-dir failures are consequential, not independent TLS evidence. |
| `preprod-evidence.yml` | `29415647897` | `6f1a2ba` | failure on AOI runner: candidate graph key selected a new empty Conan namespace; strict offline install correctly failed at `boost/1.86.0`. |
| `preprod-evidence.yml` | `29415968573` | `6f1a2ba` | success on AOI runner: same-ABI cache seed accepted by `--no-remote --build=never`; strict Conan build, complete R5 `never` drill, two R6 runs and artifact upload passed. |
| `preprod-evidence.yml` | `29428322350` | `18abba2` | success on `myserver`: clean nosqlite cache, pinned Conan venv, Release build, complete R5 `never` drill, two R6 runs and artifact upload passed. |

2026-07-15 在 `develop` / `9c2421d` 上完成 strict-offline workflow 复验：

| Workflow | Run | 结论 |
|---|---:|---|
| `nightly-stability.yml` | `29420484420` | success: Release namespace 本地命中，bounded smoke 全链路 58 秒完成。 |
| `release.yml` | `29420589850` | success: strict Conan、build/test/gates/baseline/package 全部通过。 |
| `specialized-e2e.yml` | `29420833603` | success: Release Conan preflight 与专项 E2E 通过。 |
| `production-candidate-evidence.yml` | `29421017380` | success: R0 Release 构建与聚合通过。 |
| `production-gates.yml` | `29421305436` | success: P6 evidence 诊断入口通过。 |
| `grpc-experimental.yml` | `29416581124` | failure: strict offline runtime lockfile generation could not resolve the gRPC graph. |
| `grpc-experimental.yml` | `29420321189` | failure: 新 GCC 13 gRPC namespace `graph-a863bf92d6e235d2f53d` 缺少 recipe，严格离线快速阻断且未联网。 |
| `grpc-experimental.yml` | `29421659838` | failure: legacy cache 缺少 c-ares source；不可达代理阻止下载。旧 GCC 11 实际构建包不得冒充当前 GCC 13 cache。 |
| AOI local admission | 2026-07-16 | success: `graph-a863bf92d6e235d2f53d` passed 15/15 package offline install, followed by a 185/185 build, 17/17 focused tests, SDK consumer 5/5 and the N6 decision gate. A new GitHub run artifact remains pending. |

`v3.5.0` 的最终 AOI 证据已经在同一候选 `eed73cc` 上闭环：R0 `29508284109`、2h/capacity/R4 `29509769283`、R5/R6 `29509972609`、R2/R3 `29544170962`、tag package/publish `29544261478` 均成功。AOI 仍缺少 kind/kubectl，因此 Operator 真实集群专项保留给 `v3.5.2`；静态 manifest、fake-client/unit gate 已通过。

`v3.5.1` 发布治理候选 `d7ecb1a` 也已由 AOI 完成 Release package `29551112356`、增强 R0 `29551445037` 和 tag package/publish `29551782341`。该 patch 没有运行时代码变化；真实 gzip/layout/LICENSE、完整 Release notes、Redis live、runtime HTTP、release baseline 与 SDK/生产聚合均通过。

`v3.5.2` 阶段验证中，AOI Release run `29560450740`（`9945028`）已完成 clean Ubuntu package consumer 和 SPDX SBOM；Specialized E2E run `29563770679`（`21a4815`）已完成真实 kind Ready/scale/restart/rollback/delete/cleanup，artifact `8400330394` 的 summary 为 `overall_pass=true`。GitHub API 同期仍显示 `myserver` 离线，因此第二 Linux runner 的同资产复验没有完成，不能由 AOI 结果代替。

后续刷新中，Release run `29564215641`（`18de5ed`）再次通过 clean package/SBOM；增强 R0 run `29564768686`（`d8d8108`）在同一 AOI 上通过 Redis live、runtime HTTP、release baseline、P5/P6 两轮真实 kind 与 N5 SDK，artifact `8400890077` 的顶层 provenance 与 checkout/runner/lockfile 一致。2026-07-17 `myserver` 恢复在线并对 `2b36333` 完成 G2，但本机 workflow artifact 尚未生成，因此仍不构成第二机器发行证据。

最终 `v3.5.2` tag 固定在 `a0c6d051d09129a5330326c533bc65e4067b025d`。`myserver` Release run `29587996645` / artifact `8409986789`、增强 R0 run `29588720453` / artifact `8410441198` 与 tag package/publish run `29589708378` attempt 2 全部成功。AOI 发布后独立下载验收 run `29591469812` / artifact `8411254103` 记录同一 tarball/SPDX digest、offline runtime consumer 和两类 attestation 均通过；`myserver` 也独立验收同一线上资产。

## 受影响的默认 fixed-runner workflow

- `release.yml`
- `perf-regression.yml`
- `nightly-stability.yml`
- `conan-validate.yml`
- `specialized-e2e.yml`
- `production-gates.yml`
- `production-candidate-evidence.yml`
- `preprod-evidence.yml`
- `long-soak-capacity.yml`

实验性 fixed-runner workflow：

- `grpc-experimental.yml`

## 说明

- `ci.yml` 当前默认走 GitHub-hosted `ubuntu-latest` fallback；其余 workflow 可使用在线 Linux fixed runner。
- `grpc-experimental.yml` 的当前事实是：默认使用 fresh checkout，并无条件执行 `--no-remote --build=never`；对应 Ubuntu release/GCC/arch/build-type/gRPC lockfile 分区必须预先完成准入，不得跨 Ubuntu release 或实际编译器版本复用二进制包。
- 上表的 bounded 成功 run 不替代 `specialized-e2e.yml`、`production-gates.yml`、`preprod-evidence.yml` 与 `long-soak-capacity.yml` 的固定 runner 生产证据。
