# 固定 Runner 执行手册

更新时间：2026-07-22（macOS ARM64 + Mac-hosted Linux ARM64 persistent admission）

本文档用于把 P1 的固定机器任务从“人工约定”收束为可执行入口。默认 CI/release 仍使用有界 smoke；以下任务只在固定 runner 或手动 workflow 上执行。

P2 生产证据 runner 的详细配置、workflow 输入和归档标准见本文档后续章节。

容量、长稳和 release/capacity 必须按 `linux-x64`、`linux-arm64`、`macos-arm64` 分别归档。当前只有 Ubuntu x64 已形成完整历史事实；macOS 与 Linux ARM64 在各自平台门禁闭环后可形成生产结论，但不能继承 Linux x64 阈值或证据。2026-07-11 已在同一 Linux runner 上完成 production resilience `29145497642`、production evidence `29146018657` 和 R0 candidate `29152333112`；历史长稳/容量批次 `29146495724` 已失败，必须以其 artifact 而不是 workflow 名称判断证据强度。最新 capacity 闭环见 `29183833041`。

GitHub-hosted `ubuntu-latest` 仍可作为主线有界回归兜底，但不是 fixed-runner 证据替代物。2026-07-11，在线 Linux runner 已在 `cb1c853` 上成功执行 release、Conan validation、nightly stability、CI 和 perf regression 的 bounded 验证；release baseline、capacity、production evidence 和 long soak 仍必须在同一类 fixed runner 上归档完整 summary。

GitHub 仓库 Actions runner inventory 的单一事实源见 `docs/runner-inventory.md`。截至 2026-07-15，`aoi-omen-gaming-laptop-16-am0xxx` 在线并已通过机器唯一 label 完成 R5/R6；`MyDesktop-Win` 与 `myserver` 离线。是否形成生产证据仍以 workflow artifact 为准。

## 2026-07-22 v3.6 pre-freeze Linux x64 capability evidence

AOI runner `aoi-omen-gaming-laptop-16-am0xxx` is an online Ubuntu 22.04 x86_64
host with glibc 2.35, GCC 13.4, CMake 3.28, Ninja, Docker, Compose and the admitted
Python 3.12 / Conan 2.8.1 environment. The strict-offline Release graph used
`conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock`, whose SHA-256 is
`8edf407134fef2cd8b58b1f24b8012c578812fa4373130d989c27b9dde96f88c`.

The following runs are pre-freeze capability evidence and remain bound to their
own revisions:

- Preproduction run [`29900090220`](https://github.com/HoneyBury/boost_gateway/actions/runs/29900090220)
  at `b319be4b8294450eb83f9a95c8680ea747fa2d88` passed strict-offline Conan,
  `linux/amd64` image admission with `pull=never`, real R5 gateway recovery and
  two R6 TLS iterations. Artifact `preprod-evidence-29900090220` has ID
  `8521767799`; gateway restart RTO was 0.462 seconds and every archived summary
  reports `overall_pass=true` with matching runner, run and revision provenance.
- Release run [`29902388234`](https://github.com/HoneyBury/boost_gateway/actions/runs/29902388234)
  at `76715ba53326e825052f5f496465e5862960a64d` passed the full build/CTest,
  Raft specialized/data-recovery/real mixed-binary gates, bounded release gates,
  clean package and CMake consumers, SPDX checks and Raft evidence binding. The
  Linux package artifact `boost-gateway-linux-x64` has ID `8522600423`; Raft and
  performance evidence artifacts have IDs `8522556773` and `8522586045`.
- R0 run [`29902403738`](https://github.com/HoneyBury/boost_gateway/actions/runs/29902403738)
  at the same `76715ba53326e825052f5f496465e5862960a64d` passed Redis live, runtime HTTP,
  two kind lifecycles, P5/P6, release baseline and SDK enterprise gates. Artifact
  `production-candidate-evidence-29902403738` has ID `8522927830` and records
  `overall_pass=true`, the exact checkout revision and the lockfile digest above.
- Because `jwks-rotation.yml` is not registered on the default branch, its Linux
  path was reproduced locally at `76715ba53326e825052f5f496465e5862960a64d`
  instead of being presented as a workflow artifact. Strict-offline Conan passed,
  focused CTest passed 53/53, the security release gate passed, and the real HTTPS
  rotation drill passed 10/10 outer plus 16/16 probe checks. It observed HTTP
  status counts `200:3`, `503:2`, `302:1`; the summary contract passed 5/5 and
  confirmed that no token, PEM, private key or JWK material was persisted.

These results do not form one final candidate set: the R5/R6 run and the later
Release/R0/JWKS checks have different exact SHAs, repository changes continued
after both revisions, and the project version on `main` remains `3.5.3`. The
default branch also does not yet register `jwks-rotation.yml`,
`sdk-distribution.yml` or `debug-symbols.yml`, so the local JWKS result, SDK checks
inside R0 and Release consumers do not substitute for their dedicated immutable
artifacts. The Linux ARM64 runner added later on the same day does not retroactively
change these x64 results. Final v3.6 claims remain blocked on workflow registration,
a frozen revision, exact-SHA refresh and the remaining Linux ARM64 gates.

## Mac-hosted Linux ARM64 runner

当前 Mac 只承载两个原生 ARM64 执行边界：`HoneyBurydeMacBook-Pro` 负责 macOS
Mach-O，OrbStack VM `boost-linux-arm64` 内的 `HoneyBury-M4-Linux-ARM64` 负责 Linux
ARM64 ELF/OCI。linux-x64 必须继续投递到远端 X64 runner，不在本机执行或仿真。

VM 的固定配置如下：

- Ubuntu 24.04.4 `aarch64`，12 vCPU、12 GiB 内存；GCC 13.3、CMake 3.28、Ninja 1.11、Python 3.12。
- Docker 29.1、Compose 2.40、Go 1.22、.NET 8、sccache 0.7.7、Syft 1.49。
- runner `2.336.0` 位于 `/opt/boost-gateway/actions-runner`，systemd 服务 enabled；唯一 label 为 `node-honeybury-m4-linux-arm64`。
- Conan venv、cache 和 sccache 分别位于 `/opt/boost-gateway/tools/conan-2.8.1-py3.12`、`/opt/boost-gateway/conan`、`/opt/boost-gateway/sccache`。
- OrbStack `app.start_at_login=true`、`rosetta=false`；Mac Docker image inventory 只允许 ARM64。

日常状态和恢复命令：

```bash
orbctl start
orbctl start boost-linux-arm64
orbctl run -m boost-linux-arm64 uname -m
orbctl run -m boost-linux-arm64 \
  systemctl status actions.runner.HoneyBury-boost_gateway.HoneyBury-M4-Linux-ARM64.service
gh api repos/HoneyBury/boost_gateway/actions/runners \
  --jq '.runners[] | select(.name == "HoneyBury-M4-Linux-ARM64")'
```

Linux ARM64 workflow 使用 runner 自带的原生 CMake/Ninja/Python。提交
`fec858d13e5366ab46668cb759b8ec6a87169926` 对 Linux ARM64 跳过只提供 x86_64
二进制的 setup action；x64 和 GitHub-hosted runner 保持原行为。首次联网预热 run
`29905671975` 成功后，同 SHA strict-offline run `29906228268` 以 ARM64 Release
lockfile 完成 `--no-remote --build=never`、Conan-provider configure 和 unit-test
target build。Release namespace 为：

```text
/opt/boost-gateway/conan/ubuntu-24.04-gcc13.3.0-arm64-release/conan-2.8.1-graph-aa67f82068f3051dd848
```

Debug 在线 seed run `29907427580` 后，strict-offline run `29907949804` 在
`c2504f6e9b28c068f886412d70e1d5167ddfd1cf` 通过 Debug install/configure/unit-test
target，artifact ID `8524649296`。gRPC graph 以 ARM64 gRPC/no-sqlite lockfile 独立
预热后，strict-offline run `29908827298` 在同 SHA 通过项目 targets、focused tests、
installed SDK consumer 与 PoC boundary，artifact ID `8525038829`。对应 namespaces：

```text
/opt/boost-gateway/conan/ubuntu-24.04-gcc13.3.0-arm64-debug/conan-2.8.1-graph-fbd8f0c9e0d2928cd474
/opt/boost-gateway/conan/ubuntu-24.04-gcc13.3.0-arm64-release/conan-2.8.1-graph-de9cd1cd6781b37783a6
```

run `29909904605` 在 `9485993b92f0d8e06fe675eae89c47280a7f46d2` 使用 Release
ARM64 lockfile、`linux/arm64`、`pull=never` 完成 11-image offline preflight、包含
Redis 恢复的 R5 和两轮 R6。preflight 为 11/11 ARM64、0 missing、0 wrong-platform、
0 stale-build；R5 23/23 步骤与 recovery record 33/33 通过；R6 overhead ratios 为
1.044、1.028。artifact `preprod-evidence-29909904605` 的 ID 为 `8525559864`。
该 runner 已完成 G0-G5，并获得 `preprod-r5-honeybury-m4-linux-arm64` 与
`preprod-r5`。平台级 baseline/soak/capacity 和最终 frozen-SHA artifact 仍是独立任务。

Ubuntu fixed-runner 必须同时固化仓库内 Conan profile / lockfile，避免“同一台固定机器”仍依赖宿主预装库漂移。`conan-validate.yml`、`release.yml`、`long-soak-capacity.yml` 与 `production-gates.yml` 默认使用 Linux `nosqlite` lockfile；新增 `grpc-experimental.yml` 会在同一 Conan home 上使用 `with_grpc=True`、`with_sqlite=False` 的独立 lockfile/依赖图。`release.yml` 必须在正式门禁前执行 lockfile-based `conan install` 预检，`long-soak-capacity.yml` 与 `production-gates.yml` 还必须执行 `project_v2` 构建预检。本地治理入口为 `python3 scripts/check_conan_lockfile_workflows.py`、`python3 scripts/check_fixed_runner_evidence_plan.py` 和 `python3 scripts/check_workflow_catalog.py`。2026-07-12 已在 `main` / `0af5c91` 通过 run `29196150703` 完成这条 gRPC 实验 fixed-runner 事实链。

### 新机器的 Conan 缓存初始化（必须执行）

固定 runner 的 Conan Home 与 sccache 必须位于持久卷 `/opt/boost-gateway`，而不是 checkout 或共享的 `.conan2-local`。每个 workflow 在 Conan 安装后先运行 `scripts/tools/resolve_runner_cache.py`，生成包含 Ubuntu release、实际 GCC 版本、架构、build type、Conan 版本、`conanfile.py`、profile、remote 配置和 lockfile SHA-256 的身份。典型路径为：

```text
/opt/boost-gateway/conan/ubuntu-22.04-gcc13.2.0-x64-release/conan-2.8.1-graph-<key>
/opt/boost-gateway/sccache/ubuntu-22.04-gcc13.2.0-x64-release
```

这项隔离是强制的：Ubuntu 24.04 制作的 Conan 二进制包不得供 Ubuntu 22.04 使用，即使二者均为 `linux/amd64`。Docker 镜像不同，镜像携带用户态，可在满足 Docker Engine、内核、cgroup 和端口能力的 22.04 x64 runner 上运行。

runner 注册用户需先拥有持久目录：

```bash
sudo install -d -o "$USER" -g "$(id -gn)" \
  /opt/boost-gateway/conan /opt/boost-gateway/sccache /opt/boost-gateway/tools
```

所有开发和 runner 都使用 Conan `2.8.1` 隔离 virtual environment。开发 checkout
使用 `.venv/conan-2.8.1`；runner 使用 `/opt/boost-gateway/tools/conan-2.8.1-py3.12`。
禁止使用系统 Python 或全局 Conan 进行 lockfile、预热和证据任务。新 runner 只允许
在显式预热阶段访问已批准的 Conan remote。每种 runner OS、GCC、架构和 build type
都必须分别预热；成功后，生产证据 workflow 使用 `--no-remote`：

此规则同样覆盖 GitHub-hosted CI、release 和全部 fixed-runner workflow。workflow
必须调用 `ensure_conan_venv.py`（或使用已审计的 `setup-cpp-conan` composite action）
并把 venv `bin` 写入 `GITHUB_PATH`；禁止 `command -v conan` 回退和
`conan>=2.0,<2.9` 等浮动安装范围。helper 默认验收 Python 3.12 与 Conan `2.8.1`。
有持久 runner cache 的 workflow 还必须显式调用
`bootstrap_conan.py --conan-home "$CONAN_HOME"`，不得依赖 checkout-local 默认值。

```bash
conan_venv=/opt/boost-gateway/tools/conan-2.8.1-py3.12
python3.12 scripts/tools/ensure_conan_venv.py --venv "$conan_venv" --conan-version 2.8.1
export PATH="$conan_venv/bin:$PATH"
cache_env="$(mktemp)"
python3 scripts/tools/resolve_runner_cache.py --build-type Release \
  --profile conan/profiles/linux-gcc-x64 \
  --lockfile conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock \
  --github-env "$cache_env" --summary-path runtime/validation/runner-cache-identity.json
set -a; . "$cache_env"; set +a
python3 scripts/bootstrap_conan.py --conan-home "$CONAN_HOME" --allow-public --disable-example-internal
conan install . --profile:host conan/profiles/linux-gcc-x64 --profile:build conan/profiles/linux-gcc-x64 \
  --lockfile conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock \
  -o "&:with_grpc=False" -o "&:with_raft_protobuf=True" -o "&:with_sqlite=False" --output-folder=build/conan-release --build=missing -s build_type=Release
python3.12 scripts/tools/ensure_conan_venv.py --venv "$conan_venv" --conan-version 2.8.1 --offline
python3 scripts/bootstrap_conan.py --conan-home "$CONAN_HOME" --no-remote
conan install . --profile:host conan/profiles/linux-gcc-x64 --profile:build conan/profiles/linux-gcc-x64 \
  --lockfile conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock \
  -o "&:with_grpc=False" -o "&:with_raft_protobuf=True" -o "&:with_sqlite=False" --output-folder=build/conan-release-offline --build=never --no-remote -s build_type=Release
```

### Conan 图哈希变化后的离线迁移

`resolve_runner_cache.py` 会对 `conanfile.py`、profile、lockfile、remote 配置和
remote override 计算图身份。即使只修改 `conanfile.py` 中的 CMakeToolchain 变量，
新候选也会得到新的 `graph-<key>` 目录；这属于隔离策略，不是自动复用失败。
workflow dispatch 前必须从候选 checkout 解析准确的 `CONAN_HOME`，不能凭历史路径
猜测。

若新目录为空，但旧目录与新目录具有完全相同的 Ubuntu release、GCC 完整版本、
架构、build type 和 Conan 版本，并且继续使用同一 lockfile，可用旧 cache 做一次
本地 seed。禁止跨平台 namespace 复制，也禁止使用硬链接共享 SQLite/LRU 元数据：

```bash
old_conan_home="/opt/boost-gateway/conan/<same-platform>/conan-2.8.1-graph-<old-key>"
new_conan_home="$CONAN_HOME"
test -n "$new_conan_home"
cp -a "$old_conan_home/." "$new_conan_home/"

CONAN_HOME="$new_conan_home" conan install . \
  --profile:host conan/profiles/linux-gcc-x64 \
  --profile:build conan/profiles/linux-gcc-x64 \
  --lockfile conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock \
  -o "&:with_grpc=False" -o "&:with_raft_protobuf=True" -o "&:with_sqlite=False" \
  --output-folder=build/conan-cache-acceptance \
  --build=never --no-remote -s build_type=Release
```

只有最后一条命令成功，才可认为新 namespace 已预热。Conan 仍会按 lockfile 的
recipe revision 和 package ID 选择包；复制本身不是有效证据。若依赖、options、
lockfile 或 ABI 身份已变化，应从批准的 mirror 正式预热，不能强行复用旧二进制。

`ci.yml` 是有意保留的例外：它运行在 GitHub-hosted runner 上，使用 checkout 内 `.conan2-local` 与 `actions/cache`；`production-readiness.yml` 只下载并汇聚已有 artifact，不执行 Conan。
`conan-validate.yml` 是显式的 cache 预热/依赖验证入口，可由操作者选择已批准 remote；其他 fixed-runner workflow 必须使用 runner 持久 namespace，并以 `--no-remote --build=never` 消费已预热二进制。缺包时应快速失败并先完成 namespace 预热，不能在证据或稳定性 workflow 中临时联网下载或现场构建依赖。

gRPC 使用独立的 `conan/locks/linux-gcc-x64-release-grpc-nosqlite.lock` 和 `grpc-nosqlite` 图 namespace。dispatch `grpc-experimental.yml` 前，必须在目标 runner 上对 workflow 解析出的 `CONAN_HOME` 完成一次 `with_grpc=True`、`with_sqlite=False`、`--no-remote --build=never` 验收。recipe/source 导入或二进制构建属于 runner 准入维护，不能放进 gRPC 证据 job；否则一次失败运行可能已经改变持久 cache，却无法形成可解释的验证结果。

### macOS ARM64 Runner 的持久 Conan 准入

macOS self-hosted runner 使用 `$RUNNER_TOOL_CACHE/boost-gateway`，不使用 checkout
内的 `.conan2-*`。该目录位于 runner 的持久 tool cache，`actions/checkout` 清理候选
工作区时不会删除它。Conan venv 和 graph namespace 分别为：

```text
$RUNNER_TOOL_CACHE/boost-gateway/tools/conan-2.8.1-py3.12
$RUNNER_TOOL_CACHE/boost-gateway/conan/macos-<release>-apple-clang<actual>-arm64-release/conan-2.8.1-graph-<key>
```

`resolve_runner_cache.py` 在 macOS 上把系统版本、`/usr/bin/clang` 的 Apple Clang
实际版本、profile compiler compatibility version、ARM64、build type、Conan 版本、
lockfile 和 graph 输入一起纳入身份。profile 中的 `compiler.version=17` 是 Conan
兼容设置；本机 Apple Clang 21 仍必须单独记录，不能只用 profile 值命名缓存。

当前 `HoneyBurydeMacBook-Pro` 的准入事实为 macOS 26.5.2、Apple Clang 21.0.0、
ARM64、Conan 2.8.1，namespace
`macos-26.5.2-apple-clang21.0.0-arm64-release/conan-2.8.1-graph-27de4eada077b868e6b4`。
同 ABI 的开发 cache seed 后，锁定图的 13 个 requirements 已通过
`--no-remote --build=never` 验收。workflow 只消费该 namespace；预热、复制或联网
修复必须在 workflow 外进行，且复制后仍须重新执行严格离线验收。

合并后的预冻结能力 run `29902706777` 在 exact SHA
`3af898e4d3a09002ea8f7a3742a214e11a7a6d98` 再次通过上述严格离线 namespace、
两轮全量 CTest、原生 R5、有界 performance/stability smoke、常规 package、
UUID-bound dSYM pair、SPDX SBOM 和 checksum。artifact 名为
`macos-arm64-3af898e4d3a09002ea8f7a3742a214e11a7a6d98`；该 run 证明入口可用，
不把一次 smoke 解释为三次 baseline、2h/8h 长稳或 capacity 结论，也不把 ad-hoc
签名的 dSYM 候选解释为 notarized release asset。

手工复验时使用 runner tool cache 的实际物理路径；不要把个人绝对路径写进
workflow：

```bash
runner_cache_root="$HOME/actions-runner/_work/_tool/boost-gateway"
runner_conan_venv="$runner_cache_root/tools/conan-2.8.1-py3.12"
export PATH="$runner_conan_venv/bin:$PATH"

python3.12 scripts/tools/resolve_runner_cache.py \
  --build-type Release \
  --profile conan/profiles/macos-apple-clang-arm64 \
  --lockfile conan/locks/macos-apple-clang-arm64-release-nogrpc-nosqlite.lock \
  --cache-root "$runner_cache_root" \
  --github-env /tmp/boost-gateway-macos-cache.env \
  --summary-path runtime/validation/macos-runner-cache-summary.json

set -a; . /tmp/boost-gateway-macos-cache.env; set +a
python3 scripts/bootstrap_conan.py --conan-home "$CONAN_HOME" --no-remote
python3 scripts/tools/verify_conan_offline_install.py \
  --profile conan/profiles/macos-apple-clang-arm64 \
  --lockfile conan/locks/macos-apple-clang-arm64-release-nogrpc-nosqlite.lock \
  --output-folder build/conan-macos-runner-admission \
  --build-type Release \
  --conan-executable "$runner_conan_venv/bin/conan" \
  --candidate-revision "$(git rev-parse HEAD)" \
  --summary-path runtime/validation/macos-runner-conan-offline-summary.json
```

### macOS Runner 与 Docker/R5 的边界

macOS 与 Linux 的大部分 POSIX 源码可以共用，但 Docker for Mac/OrbStack 运行的是
Linux VM。Mac 原生构建产出 Mach-O；`prepare_docker_runtime_context.py` 明确要求
ELF 和 `ldd`，Docker bundle 只支持 `linux/amd64`、`linux/arm64`。因此
`macos-arm64.yml` 使用原生进程模式执行 R5，不把 Mach-O 放进 Linux image。

Mac runner 的两种运行边界如下：

1. macOS 原生 R5 由 `macos-arm64.yml` 启动 Mach-O gateway/backends，重启 gateway，
   并在前后执行完整 native SDK flow。这是 macOS 生产候选证据。
2. 当前 Mac-hosted Linux VM 只接受同一候选 SHA 的不可变 `linux/arm64` Compose
   image bundle，并记录 daemon、VM architecture、Compose SHA、image ID 和 RepoDigest。
3. Mac-hosted Linux container 结果属于所选 Linux OCI 平台的宿主兼容性证据，不能
   替代 macOS 原生 R5，也不能替代 Linux runner 的内核、cgroup、容量和性能事实。
4. Linux ARM64 profile、lockfile、Release/Debug/gRPC Conan cache、11-image preflight
   与完整 R5/R6 workflow artifact 已完成 G0-G5；最终发布仍要求 frozen SHA 上刷新。

当前 Mac 的 OrbStack VM 用于原生 Linux ARM64 验证，但本地 image cache 本身不构成
Linux R5 准入。macOS R5 是否通过只看原生 workflow summary。初始化阶段已从 Docker
Hub 拉取 `linux/arm64` 的 `ubuntu:24.04`；证据 job 仍应使用 `pull=never` 和同 SHA
bundle，不能在运行中更新浮动 tag。Mac 本地已删除所有 amd64 images 并关闭 OrbStack
Rosetta，后续不得在该 host 上调度或仿真 linux-x64。

当前 Compose 的 registry tags 不因增加 Mac runner 而升级。Mac-hosted R5 必须与
Linux R5 使用同一份 bundle manifest、image ID 和 RepoDigest；版本升级属于独立的
供应链变更，需要在 Linux 原生 runner 重新构建、全量 R5/R6 和跨宿主复验后再落地。
项目的 6 个 build-backed images 则必须在每个最终候选 SHA 重建，不能复用旧
`boost-gateway-v332-*` image 仅因为 tag 名称未变化。可选 `host-observability`
profile 中的 cAdvisor 即使提供 ARM64 manifest，也依赖 Linux host `/sys`、
`/var/lib/docker` 和 privileged mount，不纳入 Mac R5 第一阶段。

### 新机器的 Docker 镜像预热（R5 必须执行）

R5 Docker Compose recovery drill 依赖 runner 的 Docker daemon 已缓存 `env/docker/docker-compose.yml` 展开的全部镜像。新机器首次接入、Docker 数据目录清理或 registry mirror 变更后，先在 runner 上成功执行一次：

```bash
docker compose -f /path/to/boost_gateway/env/docker/docker-compose.yml pull
docker pull ubuntu:24.04
python3 scripts/tools/prepare_docker_runtime_context.py --build-dir build/release
docker compose -f /path/to/boost_gateway/env/docker/docker-compose.yml build
python3 scripts/verify_preprod_recovery_drill.py \
  --mode docker-compose \
  --image-preflight-only \
  --docker-pull-policy never
```

项目的 6 个镜像只复制 `runtime/docker-rootfs` 中经过 ELF/动态库检查的 Conan Release 产物，Dockerfile 不执行 CMake、Conan 或包下载。镜像缓存由 Docker daemon 管理，不属于 `${GITHUB_WORKSPACE}` 或 Conan home。R5 会从 `docker compose config --format json` 动态获取当前 6 个构建镜像和 5 个 registry 镜像，并在 `r5-docker-image-preflight-summary.json` 记录 image ID、RepoDigest 和缺失清单。每个 build-backed 镜像还必须通过内嵌 manifest 校验：候选 SHA、干净 worktree、Conan lockfile 摘要和容器内实际入口二进制 SHA 必须一致。`docker_pull_policy=never` 是 fixed-runner R5 默认值，完全禁止远端访问；workflow 在 Compose build 前会先要求 `ubuntu:24.04` 基础镜像已缓存，缺失时快速失败而不是隐式联网。`missing` 和 `always` 仅用于明确标注为诊断的联网检查。缺少本项目构建镜像时不会尝试从 registry 猜测拉取，而是要求先 staging、Compose build 或导入已校验 bundle。

若新 runner 访问 `registry-1.docker.io:443` 失败，应通过受信任 mirror 完成首次预热；预热完成后可使用 `never` 离线执行。R5 不能用 `bounded-local` 结果替代预发恢复演练。

### 从受信任工作站离线传入 Docker 缓存

当 runner 不能访问 Docker Hub、但受信任工作站能够访问时，在**同一候选提交**的工作站 checkout 中创建 bundle。该工具从 Compose JSON 动态发现镜像，拉取 registry 镜像、构建全部项目镜像，并按参数导出 `linux/amd64` 或 `linux/arm64`。它会同时保存 Compose SHA-256、压缩包 SHA-256、每个镜像的 ID/标签/平台；不要只传 tar 包而遗漏相邻 manifest。

```bash
python3 scripts/tools/r5_docker_cache_bundle.py export \
  --target-platform linux/amd64 \
  --bundle runtime/r5-docker-cache/r5-docker-images-linux-amd64.tar.gz
scp runtime/r5-docker-cache/r5-docker-images-linux-amd64.tar.gz* runner:/var/tmp/
```

在 Linux runner 的相同候选 checkout 中导入和校验。导入需要 Docker daemon 可写；bundle 在解压和 `docker load` 期间会额外占用约一份未压缩镜像空间。

```bash
python3 scripts/tools/r5_docker_cache_bundle.py import \
  --target-platform linux/amd64 \
  --bundle /var/tmp/r5-docker-images-linux-amd64.tar.gz
python3 scripts/verify_preprod_recovery_drill.py \
  --mode docker-compose --docker-target-platform linux/amd64 \
  --image-preflight-only --docker-pull-policy never
```

第二条命令通过后，再以 `docker_pull_policy=never` 手动 dispatch `preprod-evidence.yml`。bundle 是临时跨机器运输方式，不替代配置受信任 registry mirror；工具会拒绝 Compose 文件或候选提交不匹配的 bundle，二者改变时必须重新导出。

### 2026-07-15 AOI Runner R5/R6 远端证据

候选 `6f1a2baeb440dcb0c0ff8180675d5559cdfa959a` 已在
`aoi-omen-gaming-laptop-16-am0xxx` 上完成 `preprod-evidence.yml`。首次 run
`29415647897` 正确解析到新 namespace
`ubuntu-22.04-gcc13.4.0-x64-release/conan-2.8.1-graph-01fd8acf00b862ebd633`，
但该目录为空，strict offline install 在 `boost/1.86.0` 处失败。旧 namespace
`graph-b2e78859939dbf15cef9` 与其 ABI/Conan/lockfile 身份一致，因此按上一节复制
seed，并以 `--no-remote --build=never` 验收 10/10 包后重新 dispatch。

最终 run `29415968573` 用机器唯一 label
`node-aoi-omen-gaming-laptop-16-am0xxx` 定向执行，8m27s 完成并为 `success`：strict
Conan install、Configure、Release 全目标构建、R5 和两轮 R6 全部通过。R5 artifact
记录 `docker_pull_policy=never`、11 个必需镜像、0 个缺失镜像，并通过 gateway
restart 前后两轮 SDK full-flow、Prometheus targets、Docker production snapshot、
recovery record 校验和 cleanup。artifact 名为
`preprod-evidence-29415968573`，R5/R6 summary 均记录同一候选 SHA 且
`overall_pass=true`。

这次实跑同时确认项目镜像的 runtime-only 边界：先由严格 Conan Release 构建生成
6 个 ELF，`prepare_docker_runtime_context.py` 校验动态库和摘要，Dockerfile 只复制
`runtime/docker-rootfs` 与配置，不在镜像中运行 CMake、Conan、`apt-get` 或依赖下载。

### 新 Ubuntu runner 的 GitHub R5 执行清单

这套清单适用于每台新 Ubuntu runner。不要依赖通用
`["self-hosted","Linux","X64"]` labels 来选择目标机器：若有多台匹配，GitHub
会自行调度。先为要执行 R5 的机器添加唯一 custom label，再在 dispatch 中要求它。

```bash
# 在管理员已认证的机器执行；先查看 runner ID 和现有 labels。
gh api repos/HoneyBury/boost_gateway/actions/runners \
  --jq '.runners[] | {id,name,status,busy,labels:[.labels[].name]}'

# 示例：将本机 myserver 作为唯一 R5 runner。
runner_id="$(gh api repos/HoneyBury/boost_gateway/actions/runners \
  --jq '.runners[] | select(.name == "myserver") | .id')"
gh api --method POST \
  "repos/HoneyBury/boost_gateway/actions/runners/${runner_id}/labels" \
  -f 'labels[]=preprod-r5-myserver'
```

1. 同步候选 checkout，并确认工作区干净：

```bash
git fetch origin --prune
git checkout --detach <candidate-sha>
test -z "$(git status --porcelain)"
test "$(git rev-parse HEAD)" = "$(git rev-parse <candidate-sha>)"
```

2. 预留磁盘并创建 persistent cache root：

```bash
docker builder prune -af
df -h /
sudo install -d -o "$USER" -g "$(id -gn)" \
  /opt/boost-gateway/conan /opt/boost-gateway/sccache /opt/boost-gateway/tools
```

构建或 bundle import 前至少保留 25GB 可用空间；`docker builder prune` 不会
删除已标记的 Compose 镜像。

3. 按本机 OS/compiler/arch/build-type 分区预热 Conan，并立即离线复验：

```bash
conan_venv=/opt/boost-gateway/tools/conan-2.8.1-py3.12
python3.12 scripts/tools/ensure_conan_venv.py --venv "$conan_venv" --conan-version 2.8.1
export PATH="$conan_venv/bin:$PATH"
cache_env="$(mktemp)"
python3 scripts/tools/resolve_runner_cache.py --build-type Release \
  --profile conan/profiles/linux-gcc-x64 \
  --lockfile conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock \
  --github-env "$cache_env" \
  --summary-path runtime/validation/runner-cache-identity.json
set -a; . "$cache_env"; set +a
python3 scripts/bootstrap_conan.py --conan-home "$CONAN_HOME" --allow-public --disable-example-internal
conan install . --output-folder=build/conan-preprod-warm --build=missing \
  -s build_type=Release --profile:host conan/profiles/linux-gcc-x64 \
  --profile:build conan/profiles/linux-gcc-x64 \
  --lockfile conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock \
  -o "&:with_grpc=False" -o "&:with_raft_protobuf=True" -o "&:with_sqlite=False"
python3.12 scripts/tools/ensure_conan_venv.py --venv "$conan_venv" --conan-version 2.8.1 --offline
python3 scripts/bootstrap_conan.py --conan-home "$CONAN_HOME" --no-remote
conan install . --output-folder=build/conan-preprod-offline-check --build=never --no-remote \
  -s build_type=Release --profile:host conan/profiles/linux-gcc-x64 \
  --profile:build conan/profiles/linux-gcc-x64 \
  --lockfile conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock \
  -o "&:with_grpc=False" -o "&:with_raft_protobuf=True" -o "&:with_sqlite=False"
```

4. 预热或 import Docker images，然后严格检查离线 cache：

```bash
docker compose -f env/docker/docker-compose.yml pull
docker compose -f env/docker/docker-compose.yml build
python3 scripts/verify_preprod_recovery_drill.py --mode docker-compose \
  --image-preflight-only --docker-pull-policy never
```

离线 runner 改用同一干净候选 checkout 导出的
`r5_docker_cache_bundle.py export` / `import`，不允许以 dirty checkout 导出。

5. 使用唯一 label dispatch workflow，并保持 R5 离线：

```bash
gh workflow run preprod-evidence.yml --repo HoneyBury/boost_gateway --ref <candidate-sha> \
  -f 'runner=["self-hosted","Linux","X64","preprod-r5-myserver"]' \
  -f configure_preset=release -f build_dir=build/release -f configuration=Release \
  -f build_parallelism=2 \
  -f recovery_mode=docker-compose -f recovery_timeout_seconds=300 \
  -f include_redis_recovery=true \
  -f verify_redis_alert_transition=true \
  -f redis_alert_firing_timeout_seconds=240 \
  -f docker_pull_policy=never -f docker_pull_attempts=1 \
  -f tls_runs=2 -f tls_timeout_seconds=240 \
  -f conan_profile=conan/profiles/linux-gcc-x64 \
  -f conan_lockfile=conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock
```

6. 监控并验证 artifact：

```bash
gh run list --repo HoneyBury/boost_gateway --workflow preprod-evidence.yml \
  --commit <candidate-sha> --limit 1
gh run watch <RUN_ID> --repo HoneyBury/boost_gateway --exit-status
gh run download <RUN_ID> --repo HoneyBury/boost_gateway \
  --name "preprod-evidence-<RUN_ID>" --dir runtime/github-preprod-evidence
jq '{overall_pass,scope,provenance}' \
  runtime/github-preprod-evidence/preprod-recovery-drill-summary.json
```

要求 `overall_pass=true`、`scope.real_docker_compose_drill=true`、
`scope.docker_pull_policy="never"`，且 `provenance.candidate_revision` 等于
dispatch 的完整候选 SHA。

2026-07-14 的第一次 workflow dispatch `29345674702` 被调度到
`aoi-omen-gaming-laptop-16-am0xxx`，并在 `Resolve persistent runner caches`
失败：该 runner 还没有可写的 `/opt/boost-gateway`。先在该机器执行步骤 2-4
再重试；随后 R6 的 `build/release` 不存在是这个首要失败导致 Configure/Build
被跳过的连带结果，不能单独归因为 TLS 回归。

当前验证状态（2026-07-17）：`v3.5.0` 候选 `eed73cc` 已由 AOI 完成 R0 `29508284109`、2h/capacity/R4 `29509769283`、R5/R6 `29509972609` 和最终 R2/R3 `29544170962`，全部 provenance 匹配且通过。后续 `v3.5.x` 候选继续使用唯一 `node-aoi-omen-gaming-laptop-16-am0xxx` label 定向刷新：

1. 使用 `--image-preflight-only --docker-pull-policy never` 确认全部镜像已预热；缺失时通过 mirror 补齐。
2. 手动运行 `preprod-evidence.yml`，保持 `recovery_mode=docker-compose`、`docker_pull_policy=never` 与 `tls_runs=2`，且整个 job 必须成功。`missing` 或 `always` 的联网诊断结果不能解除 R5 预发阻断。
3. `v3.5.0` 的真实 2h 和最终 R2/R3 已闭环；新 patch 若改变运行时代码，必须在新 SHA 上刷新 R0、2h、R4、R5/R6。仅发布元数据变更也必须生成自身 package/bounded/R0 证据，不得把旧 summary 改写成新 SHA。

首次 Conan 预热与严格离线复验必须使用本章前面的固定 `conan-2.8.1` venv、
runner OS 分区的 `CONAN_HOME`，以及 `--build=missing` 后紧跟
`--no-remote --build=never` 的两阶段命令。不要再执行无 `--conan-home` 的
`bootstrap_conan.py`，也不要使用 PATH 中的全局 Conan。

## Ubuntu Fixed-Runner 第一批执行矩阵

当前 1-3 个月主线的第一批真实证据按以下顺序刷新。它们不能用本机 smoke 或 `--allow-missing` 结果替代。

| 顺序 | Workflow | 关键输入 | 必须归档的 summary |
| --- | --- | --- | --- |
| 1 | `conan-validate.yml` | `runner=["self-hosted","Linux","X64"]`、`conan_lockfile=conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock`、`with_sqlite=false` | Conan install/build artifact；失败时以 Conan step 日志为准 |
| 1.5 | `grpc-experimental.yml` | `runner=["self-hosted","Linux","X64"]`、`build_type=Release`、空 `conan_lockfile`（现场生成 grpc+nosqlite lockfile）或显式 grpc lockfile | run `29196150703`（`main` / `0af5c91`）已通过：`runtime/validation/grpc-fixed-runner-preflight-summary.json`、`runtime/validation/grpc-sdk-package-consumer-summary.json`、`runtime/validation/grpc-fixed-runner-decision-summary.json` 全部归档；用于独立验证 `BOOST_BUILD_GRPC=ON`，不替代默认主线 `with_grpc=False` 证据 |
| 2 | `release.yml` (baseline) | `perf_preset=baseline`、`perf_repetitions=3`，Conan lockfile preflight 固定执行 | `runtime/validation/release-baseline-summary.json`、`runtime/perf/release-baseline/summary.json` |
| 3 | `long-soak-capacity.yml` | capacity: `run_2h_soak=false`、`run_8h_soak=false`、`run_capacity=true`、`run_business_capacity=true`、`perf_repetitions=3` | `29183833041`（`6d537ee`）已通过：Conan 预检、Release 构建、capacity、business-capacity 和 R4 聚合均为 `overall_pass=true`。capacity 的 battle-500 三轮 P99=40/100/150ms，business-capacity 为 75/150/150ms，均为 0 rejected/failed；3 个 SDK full-flow 客户端通过。该 run 未执行 2h/8h，长稳事实仍分别以真实 7200/28800 秒 run 归档 |
| 4 | `production-gates.yml` | `gate=p6-evidence`，`conan_lockfile=conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock`，按 runner 能力显式打开 Redis/kind/observability | 旧 `29146018657` 的 `production-evidence-summary.json` 为历史通过事实；新入口需在同一候选 SHA 上刷新 |
| 5 | `production-candidate-evidence.yml` | 独立运行 R0 aggregate，避免在 P6 job 后重复执行门禁；stability baseline profile 随 `configuration` 对齐（Debug=`debug`，Release=`release`） | `29152333112`（`8cadbef`）已通过，`runtime/validation/r0-production-candidate-evidence-summary.json` 及 R0/P5/P6/N5 子 summary 均归档 |
| 6 | `preprod-evidence.yml` | `recovery_mode=docker-compose`、`tls_runs=2`、Release + Conan lockfile | `runtime/validation/preprod-recovery-drill-summary.json`、`r5-preprod-recovery-drill-record.json`、`monitoring-operability-summary.json`、`runtime/validation/tls-preprod-multi-run-summary.json` |
| 7 | `production-readiness.yml` | R0、真实 2h soak、当前 capacity/R4、R5/R6 各自的 run ID，跨 workflow 下载 artifact 后统一执行 R2/R3 | `runtime/validation/r2-production-evidence-manifest-fixed-runner-summary.json`、`runtime/validation/r3-production-readiness-report-summary.json` |

通过判据：

- 每个 workflow 的 Conan lockfile install/build 预检通过。
- `release-baseline-summary.json`、`long-soak-capacity-summary.json`、`fixed-runner-release-capacity-summary.json`、`production-evidence-summary.json` 均为 `overall_pass=true`。
- 投产准入检查必须运行不带 `--allow-missing` 的 `python scripts/check_validation_summary_contract.py`，并运行 `python scripts/check_production_evidence_manifest.py --require-fixed-runner`。
- 如 fixed runner 缺 Redis、kind 或外部网络，summary 必须明确失败在 `preflight` 或 Conan remote/cache 阶段，不得把缺失环境解释为业务通过。
- 仓库内 wiring 变更必须先通过 `python scripts/check_fixed_runner_evidence_plan.py`；该脚本只校验 workflow/summary 归档计划，不能替代 fixed-runner 真实执行。

## N0 统一约定

从 N0 开始，固定 runner 相关 summary 统一要求：

- JSON 顶层包含 `summary_version=2`
- 统一包含 `overall_pass`、`passed`、`failed_category`、`failed_step`
- 统一包含 `environment`，至少记录 `platform`、`python`、`host`
- 统一包含 `artifacts`，指向 summary、report 或子 summary 路径
- workflow step summary 统一通过 `scripts/render_validation_summary.py` 渲染，不再只上传 artifact
- R0、long-soak、R4、R5、R6 还必须包含 `provenance`：候选提交、实际 checkout、workflow/run、runner、构建配置、Conan lockfile 与 SHA-256；`revision_matches_checkout` 必须为 `true`
- 用于同一次 R2/R3 最终准入的五类核心证据必须具有完全相同的 `candidate_revision`，不能把不同提交上的成功 artifact 拼接成一个候选结论

失败归因约定：

- `preflight`：runner 环境缺失，例如 Redis、Docker/kind、端口绑定能力、构建目录异常
- `build`：构建失败或目标缺失
- `specialized` / `stability` / `data_recovery` / `observability` / `release_baseline`：业务门禁或专项测试回归
- `configuration`：workflow 输入组合本身非法，例如没有选择任何有效步骤

## 固定 Runner 证据索引

| 能力 | 推荐频率 | 推荐 runner | 关键 summary / 产物 |
| --- | --- | --- | --- |
| Release baseline | 每周 1 次 | `self-hosted,release-baseline` | `runtime/validation/fixed-runner-preflight-summary.json`、`runtime/validation/release-baseline-summary.json`、`runtime/perf/release-baseline/summary.json`、`runtime/perf/release-baseline/report.md` |
| Specialized E2E default | 每周 2 次 | `self-hosted,raft-ha` 或通用 runner | `runtime/validation/fixed-runner-preflight-summary.json`、`runtime/validation/specialized-e2e-summary.json` |
| Redis live / raft-ha | 每周 1 次 | `self-hosted,redis-live` / `self-hosted,raft-ha` | `runtime/validation/specialized-e2e-summary.json` |
| Production gates / P5 | 手动，候选 SHA 冻结后执行 | `self-hosted,production-resilience` 或通用 Linux fixed runner | `runtime/validation/fixed-runner-preflight-summary.json`、`runtime/validation/production-resilience-summary.json` |
| Production gates / P6 | 手动，候选 SHA 冻结后执行 | `self-hosted,production-evidence` 或通用 Linux fixed runner | `runtime/validation/fixed-runner-preflight-summary.json`、`runtime/validation/production-evidence-summary.json` |
| Observability runtime | 每周 1 次 | `self-hosted,observability` | `runtime/validation/observability-gate-summary.json`、`runtime/validation/gateway-observability-runtime-summary.json` |
| P5-P8 business closure | 每周 1 次 | `self-hosted,business-closure` | `runtime/validation/p5-p8-business-closure-summary.json` |
| K8s / Operator kind | 每周 1 次 | `self-hosted,operator-kind` | `runtime/validation/p5-control-plane-kind-summary.json`、`runtime/validation/p7-k8s-full-flow-summary.json` |

## Runner 标签建议

| 用途 | 建议 label | Workflow | 必需能力 |
| --- | --- | --- | --- |
| Ubuntu release/capacity baseline | `self-hosted,linux,x64,release-baseline` | `release.yml` | Ubuntu LTS、稳定 CPU、固定 OS、CMake、Ninja、Python、可绑定本地端口 |
| Release baseline | `self-hosted,release-baseline` | `release.yml` | 稳定 CPU、固定 OS、CMake、Ninja、Python、可绑定本地端口 |
| Redis live | `self-hosted,redis-live` | `specialized-e2e.yml` | Redis `127.0.0.1:6379` 可达，CMake、Ninja、Python；`specialized_profile=redis-live` |
| Raft HA | `self-hosted,raft-ha` | `specialized-e2e.yml` | CMake、Ninja、Python；`specialized_profile=raft-ha` |
| Operator kind | `self-hosted,operator-kind` | `specialized-e2e.yml` | Docker、kind、kubectl、make、CMake、Ninja、Python |
| Observability | `self-hosted,observability` | 手动命令或 release gate | CMake、Ninja、Python、可绑定本地端口；可选 fake OTel collector 与真实 gateway HTTP runtime 测试 |
| Control plane | `self-hosted,operator-kind` | 手动命令或 `specialized-e2e.yml` | Go、Docker、kind、kubectl、make、Python；可选 envtest assets |
| Business closure P5-P8 | `self-hosted,business-closure` | 手动命令 | CMake、Ninja、Python、可绑定本地端口；可选 OTel、kind、K8s 已部署集群 |
| Production gates / P5 | `self-hosted,production-resilience` | `production-gates.yml`, `gate=p5-resilience` | CMake、Ninja、Python、可绑定本地端口；可选 Redis、Docker/kind、Release baseline 固定性能环境、runtime observability |
| Production gates / P6 | `self-hosted,production-evidence` | `production-gates.yml`, `gate=p6-evidence` | CMake、Ninja、Python、可绑定本地端口；可选 Redis、Docker/kind、Release baseline 固定性能环境、runtime observability |
| Experimental gRPC | `self-hosted,observability` 或通用 Linux fixed runner | `grpc-experimental.yml` | CMake、Ninja、Python、可绑定本地端口；同级 Conan cache、gRPC/Protobuf 依赖、fake OTLP collector POST 能力 |
| Cloud production closure | `self-hosted,cloud-production` | 手动命令 | CMake、Ninja、Python、Docker、kubectl、kind、Go、systemd；用于当前云服务器生产环境收束 |

GitHub Actions 手动触发时，`runner` 输入填实际 label。`production-gates.yml` 的 `runner` 输入必须是 JSON：单 runner 使用 `"ubuntu-latest"`，多个 label 使用 `["self-hosted","Linux","X64"]`。

普通 branch push / PR 不再自动触发流水线；自动触发只保留特定 release tag，当前约定为 `v*`。`.github/workflows/release.yml` 在推送 `v*` tag 时自动执行 release package/publish；其它固定 runner、性能、稳定性和专项验证入口保留 `workflow_dispatch`，需要时手动触发。`.github/runner-matrix.json` 是版本化 runner/默认标签配置源，变更 tag 策略或 runner 拓扑时需要同步更新 workflow 与该文件，避免真实触发行为和文档配置漂移。

## Release Baseline

手动触发 `.github/workflows/release.yml`。当前 workflow 的构建目录和配置固定为 `build/release` / `Release`，手动可配输入只有下表这些：

| 输入 | baseline 建议值 | capacity 建议值 |
| --- | --- | --- |
| `runner` | `["self-hosted","Linux","X64"]` | `["self-hosted","Linux","X64"]` |
| `perf_preset` | `baseline` | `capacity` |
| `perf_repetitions` | `3` | `3` |
| `conan_lockfile` | `conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock` | 同 baseline |
| `prepare_cmake_consumer_image` | 仅 runner 镜像缺失时为 `true` | 通常为 `false` |
| `legacy_raft_binary` | runner 上预置的 `v3.5.3` leaderboard backend | 同 baseline |
| `legacy_raft_revision` | `b9c348b4b58fdeeffa9d82ff87a67ed781a96b78` | 同 baseline |
| `legacy_raft_sha256` | 预置 Linux x64 binary 的实际 SHA-256 | 同 baseline |

`prepare_cmake_consumer_image=true` 只用于手动候选运行恢复固定 digest Dockerfile 对应的 compiler image。构建完成后的 consumer 仍强制 `--network=none --pull=never`；正式 tag 触发没有该输入，必须消费候选阶段已经准入的本地镜像，不能在发布时隐式联网预热。

通过标准：

- `runtime/validation/release-baseline-summary.json` 中 `passed=true`。
- `runtime/perf/release-baseline/summary.json` 中 `release_gates.overall_pass=true`。
- Conan validation preflight 中 lockfile-based `conan install` 通过，且后续 Release build/test/gate 全链路通过。
- GitHub Step Summary 显示 R4、业务性能步骤均为 `PASS`。

## Specialized E2E

默认专项 E2E 不要求 Redis/kind，只跑 Raft 与 Redis degraded。P4 之后可以用 `specialized_profile` 明确区分 Redis live、Raft HA 与全专项：

| 场景 | `runner` | `specialized_profile` | `include_redis_live` | `include_operator_kind` |
| --- | --- | --- | --- | --- |
| Raft + Redis degraded | `ubuntu-latest` 或自托管普通 runner | `default` | `false` | `false` |
| Redis live | `["self-hosted","redis-live"]` | `redis-live` | `true` | `false` |
| Raft HA | `["self-hosted","raft-ha"]` | `raft-ha` | `false` | `false` |
| Operator kind | `["self-hosted","operator-kind"]` | `default` | `false` | `true` |
| 全专项 | `["self-hosted","redis-live","operator-kind"]` | `all` | `true` | `true` |

通过标准：

- `runtime/validation/specialized-e2e-summary.json` 中 `passed=true`。
- Redis live 场景必须确认 runner 上 Redis 服务可达。
- Raft HA 场景必须归档 `profile=raft-ha` 的 summary，覆盖 leader election、failover/follower catch-up 和重启恢复 gates。
- Operator kind 场景必须确认 Docker daemon、kind、kubectl、make 可用。

## Observability / P4

默认 release gate 已运行 `scripts/verify_observability_gate.py`，覆盖 rate limit、trace、OTel buffer、backend RED metrics、gateway metrics 与 audit。固定观测 runner 可追加 fake OTel collector POST 验证和真实 gateway HTTP 观测入口验证：

```bash
python scripts/check_fixed_runner_environment.py --profile observability --build-dir build/default
python scripts/verify_observability_gate.py --build-dir build/default --skip-build --include-otel-collector
python scripts/verify_observability_gate.py --build-dir build/default --skip-build --include-runtime-http
python scripts/verify_observability_gate.py --build-dir build/default --skip-build --include-otel-collector --include-runtime-http
```

通过标准：

- `runtime/validation/observability-gate-summary.json` 中 `passed=true`。
- `--include-otel-collector` 场景必须确认 runner 允许测试进程绑定 `127.0.0.1` 随机端口。
- `--include-runtime-http` 场景会启动真实 `v2_gateway_demo`，用 SDK full-flow 产生业务流量，并验证 `/health`、`/ready`、`/metrics`、`/metrics/json`、`/metrics/diagnostics/json`；子 summary 位于 `runtime/validation/gateway-observability-runtime-summary.json`。
- 如需验证真实 collector，运行 `examples/v2_gateway_demo` 时设置 `OTEL_EXPORT_ENDPOINT=http://<collector>/v1/traces`；默认 P4 gate 不依赖真实外部 collector。

## Experimental gRPC / N6

独立手动触发 `.github/workflows/grpc-experimental.yml`。该 workflow 不会改变默认主线 `with_grpc=False` 的 Conan 图，只用于在同一 OS/compiler/arch/build-type 分区的 Conan Home 内验证实验 `BOOST_BUILD_GRPC=ON`：

- 预检：`check_fixed_runner_environment.py --profile observability`
- Conan：`with_grpc=True`、`with_sqlite=False`
- 构建：`project_proto`、`boost_gateway_sdk_grpc`、`project_v2_*tests`、`sdk_tests`
- 测试：`ctest -R "GrpcGateway|OtelExporter"`
- 包契约：`python scripts/verify_sdk_package_consumer.py --with-grpc`
- 决策边界：`python scripts/check_v3_grpc_poc_decision.py`

当前固定事实：

- 首轮 run `29195792943`（`main` / `5df1479`）因 `use_existing_workspace=true` 下 workspace 不是目标 `GITHUB_SHA` 而失败。
- 之后已将 workflow 默认值收口为 `use_existing_workspace=false`。
- 成功 run `29196150703`（`main` / `0af5c91`）使用当时的 `no_remote=true` 输入，证明旧 runner cache 曾覆盖实验 gRPC 依赖图；当前 workflow 已移除联网切换并无条件使用 `--no-remote --build=never`，该历史缓存路径不能跨 Ubuntu release 或实际 GCC 版本复用。
- run `29420321189`（`develop` / `9c2421d`）在新 GCC 13 gRPC namespace 缺少 recipe 时于 1 分钟内严格离线失败；run `29421659838` 进一步确认 legacy cache 缺少 c-ares source。当前阻断只能通过同 ABI 的批准 cache bundle/mirror 预热解除，不能在实验 workflow 中临时联网或复用旧 GCC 11 构建包。

通过标准：

- `runtime/validation/grpc-fixed-runner-preflight-summary.json` 中 `passed=true`
- `runtime/validation/grpc-sdk-package-consumer-summary.json` 中 `with_grpc=true` 且 `passed=true`
- `runtime/validation/grpc-fixed-runner-decision-summary.json` 中 `passed=true`
- 该 workflow 的成功只说明实验 gRPC 入口在 fixed-runner 上可复现；默认生产链仍保持 `defer_default_transport`

## Business Closure / P5-P8

P5-P8 剩余 profile 的聚合入口：

```bash
python scripts/verify_p5_p8_business_closure.py --build-dir build/default --skip-build
python scripts/verify_p5_p8_business_closure.py --build-dir build/default --skip-build --include-otel-collector --include-runtime-http
python scripts/verify_p5_p8_business_closure.py --build-dir build/default --skip-build --include-operator-kind --include-k8s-full-flow
```

通过标准：

- 默认聚合 summary `runtime/validation/p5-p8-business-closure-summary.json` 中 `passed=true`。
- `--include-otel-collector` 需要 runner 允许测试进程绑定 loopback 随机端口。
- `--include-runtime-http` 会启动真实 gateway HTTP 入口并产生 SDK 业务流量。
- `--include-operator-kind` 需要 Docker/kind/kubectl/make。
- `--include-k8s-full-flow` 要求目标 Kubernetes 集群已经部署 gateway 与五后端，并允许 `kubectl port-forward svc/gateway`。

## Control Plane / P5

默认 release gate 已运行 `scripts/verify_control_plane_gate.py`，只依赖 Operator manifest 静态契约和 Go fake-client/unit tests，不要求 Docker 或 kind。固定控制面 runner 可追加：

```bash
python scripts/check_operator_manifests.py --summary-path runtime/validation/operator-manifests-summary.json
python scripts/check_fixed_runner_environment.py --profile control-plane --build-dir build/default --require-kind
python scripts/verify_control_plane_gate.py --include-kind
python scripts/verify_control_plane_gate.py --include-envtest --include-kind
```

本机收束 P5 时，如 Redis、Docker/kind、Go、kubectl 已配置完成，推荐先跑预检再跑专项聚合：

```bash
python scripts/check_fixed_runner_environment.py --profile specialized-e2e --build-dir build/default --require-redis
python scripts/check_fixed_runner_environment.py --profile control-plane --build-dir build/default --require-kind
python scripts/verify_specialized_e2e.py --build-dir build/default --skip-build --profile all --summary-path runtime/validation/dev-p5-specialized-e2e-summary.json --operator-timeout-seconds 1200
python scripts/verify_control_plane_gate.py --include-kind --summary-path runtime/validation/dev-p5-control-plane-kind-summary.json --kind-timeout-seconds 1200
```

通过标准：

- `runtime/validation/control-plane-gate-summary.json` 中 `passed=true`。
- 默认门禁会额外写出 `runtime/validation/operator-manifests-summary.json`，要求 CRD/status schema、RBAC、manager probes 和 sample 六组件静态契约通过。
- 控制面 gate 会固定使用仓库内 `runtime/go-cache`，并在执行 kind/envtest 前先做 preflight；缺少 Docker/kind 访问权限或 `KUBEBUILDER_ASSETS` 时，summary 应显示 `failed_category=preflight` 和可执行的失败原因。
- 本机收束 summary `runtime/validation/dev-p5-specialized-e2e-summary.json` 中 `passed=true`，且 `include_redis_live=true`、`include_operator_kind=true`。
- `--include-kind` 场景必须断言 sample `BoostGatewayCluster` 的 `Ready=True`、`Progressing=False`、`Degraded=False`、`TLSReady=False`，六个 `status.components[]` 均存在且可用，并验证 sample CR 删除完成。
- `--include-envtest` 场景要求 runner 已准备 controller-runtime envtest assets，例如 `KUBEBUILDER_ASSETS`。

## Production Resilience / P5

P5 长稳、故障注入与回滚演练使用 `scripts/verify_production_resilience_gate.py` 作为统一入口。默认模式保持有界，只跑固定 runner 预检、bounded stability soak、data recovery 和 Redis/Raft/Operator failure-path 专项；真实 Redis、kind、runtime HTTP、release/capacity baseline 必须显式启用。

手动触发 `.github/workflows/production-gates.yml` 并选择 `gate=p5-resilience`。`runner` 输入必须是 JSON：单 runner 使用 `"ubuntu-latest"`，多个 label 使用 `["self-hosted","production-resilience"]` 或 `["self-hosted","Linux","X64"]`。

推荐本机或固定 runner 命令：

```bash
python scripts/check_fixed_runner_environment.py --profile production-resilience --build-dir build/default
python scripts/verify_production_resilience_gate.py --build-dir build/default --skip-build --summary-path runtime/validation/dev-p5-production-resilience-summary.json
python scripts/verify_production_resilience_gate.py --build-dir build/default --skip-build --soak-profile short --include-redis-live --include-runtime-http --summary-path runtime/validation/dev-p5-production-resilience-live-summary.json
python scripts/verify_production_resilience_gate.py --build-dir build/default --skip-build --include-operator-kind --kind-timeout-seconds 1200 --summary-path runtime/validation/dev-p5-production-resilience-kind-summary.json
```

通过标准：

- `runtime/validation/production-resilience-summary.json` 或指定 summary 中 `passed=true`。
- 子 summary `p5-long-soak-summary.json`、`p5-fault-data-recovery-summary.json`、`p5-specialized-failure-summary.json` 均通过。
- 启用 `--include-redis-live` 时，Redis live persistence/event-store 和 Redis service live gates 必须通过。
- 启用 `--include-operator-kind` 时，Operator kind status smoke 与 control-plane kind gate 必须通过，并覆盖 Ready/Progressing/Degraded/TLSReady、六组件 status 和 sample CR 删除。
- 启用 `--include-runtime-http` 时，真实 gateway HTTP `/health`、`/ready` 与 `/metrics*` 必须通过 runtime observability gate。

## 本地预检

执行长任务前可先跑：

```bash
python scripts/check_fixed_runner_environment.py --profile release-baseline --build-dir build/release
python scripts/check_fixed_runner_environment.py --profile specialized-e2e --build-dir build/default --require-redis
python scripts/check_fixed_runner_environment.py --profile specialized-e2e --build-dir build/default --require-kind
python scripts/check_fixed_runner_environment.py --profile observability --build-dir build/default
python scripts/check_fixed_runner_environment.py --profile control-plane --build-dir build/default --require-kind
python scripts/check_fixed_runner_environment.py --profile production-resilience --build-dir build/default --require-redis --require-kind
python scripts/check_fixed_runner_environment.py --profile cloud-production --build-dir build/release
```

预检只检查工具链和外部服务可达性，不替代实际测试。

## Cloud Production Closure

当前云服务器如果被用作生产环境或生产候选环境，应把它视为固定 runner，而不是继续沿用 macOS / Windows 的开发预演口径。推荐在该主机上执行：

```bash
python scripts/check_fixed_runner_environment.py --profile cloud-production --build-dir build/release
python scripts/run_long_soak_capacity.py --build-dir build/release --configuration Release --skip-build --run-2h-soak
python scripts/run_long_soak_capacity.py --build-dir build/release --configuration Release --skip-build --run-capacity --run-business-capacity --perf-repetitions 3 --run-business-operation-perf --leaderboard-redis-comparison --leaderboard-redis-host 127.0.0.1 --leaderboard-redis-port 6379
python scripts/verify_fixed_runner_release_capacity.py --build-dir build/release --configuration Release
python scripts/run_cloud_production_closure.py --build-dir build/release --configuration Release --include-compose --include-kind --include-production-evidence
```

通过标准：

- `runtime/validation/long-soak-2h-summary.json` 中 `summary_version=2`、`overall_pass=true`、`soak_profile=long`，并包含 `provenance`、`environment` 与 `artifacts`。
- `runtime/validation/fixed-runner-release-capacity-summary.json` 中 `summary_version=2`、`overall_pass=true`；容量失败不会反向否定已经通过的 2h summary，但两者仍必须绑定同一候选 SHA。
- workflow 输入 `leaderboard_redis_comparison=true` 时会使用 run 独占的临时 Redis 容器；R4 必须同时启用 `--require-leaderboard-redis-comparison`，并验证内存-only 与 Redis-primary-with-memory-shadow 各至少三轮、启动日志、前后 PING、隔离 key ZCARD 和零操作失败。
- `runtime/validation/cloud-production-closure-summary.json` 中 `summary_version=2`、`overall_pass=true`，并包含 `environment` 与 `artifacts`。
- 长稳 summary 至少归档 `long-soak-2h-summary.json`；8h soak 可在同一云主机扩展执行并归档 `long-soak-8h-summary.json`。容量 summary 应同时归档 `capacity-baseline-summary.json`、`business-capacity-baseline-summary.json`、`runtime/perf/fixed-runner-capacity/summary.json` 和 `runtime/perf/fixed-runner-business-capacity/summary.json`。
- long/overnight 期间任何失败执行及其两次确认都必须保留 `runtime/perf/v2-stability-soak/failures/pass-*-*/`，其中包含该轮 `summary.json`、原始 benchmark JSON、stdout/stderr 和 `host-resources.json`。聚合器会立即执行两次同配置确认：同指标在三次中至少两次失败视为可复现退化；只有确认均恢复且原始失败率不超过 0.1% 的孤立尖峰可记为 `confirmation_recovered`。不得删除失败目录、确认结果或只上传最后一次成功覆盖后的顶层 summary。
- 云端部署收束必须同时包含 Compose 运行态快照、kind/control-plane 结果和 production evidence 聚合 summary。

N1/N2/N3 建议按以下顺序收集：

1. `python scripts/check_fixed_runner_environment.py --profile cloud-production --build-dir build/release`
2. `python scripts/run_long_soak_capacity.py --build-dir build/release --configuration Release --skip-build --run-2h-soak`
3. `python scripts/run_long_soak_capacity.py --build-dir build/release --configuration Release --skip-build --run-capacity --run-business-capacity --perf-repetitions 3`
4. `python scripts/verify_fixed_runner_release_capacity.py --build-dir build/release --configuration Release`
5. `python scripts/check_monitoring_operability.py --summary-path runtime/validation/n2-monitoring-operability-summary.json`
6. `python scripts/run_cloud_production_closure.py --build-dir build/release --configuration Release --include-compose --include-kind --include-production-evidence`

这样可以把 N1 长稳/容量、N2 监控口径、N3 部署恢复都沉淀到统一的 fixed-runner summary 契约里。

如果当前环境是 macOS + OrbStack Docker，本机更适合作为 `local pre-production rehearsal` 而不是 `cloud-production` profile：

- 可以直接刷新 `python3 scripts/check_monitoring_operability.py --summary-path runtime/validation/n2-monitoring-operability-summary.json`
- 可以直接刷新 `python3 scripts/check_deploy_operability.py --summary-path runtime/validation/n3-deploy-operability-summary.json`
- 可以继续复用 `python3 scripts/verify_preprod_recovery_drill.py --build-dir build/release` 形成 Docker Compose 恢复演练证据

`cloud-production` 预检里的 `systemctl`、真实 kind cluster 和更严格的宿主能力要求，仍保留给 Linux 固定 runner，不强行套用到 OrbStack 本机预演环境。

## P6 Production Evidence

P6 聚合入口用于把固定 runner 上的稳定性、数据恢复、Redis/Raft/Operator、生产候选完整性审核和 release baseline 证据收束到一个 summary。默认命令只跑有界任务：

```bash
python scripts/verify_production_evidence_gate.py --build-dir build/default --skip-build
```

手动触发 `.github/workflows/production-gates.yml` 并选择 `gate=p6-evidence`。`runner` 建议填 `["self-hosted","Linux","X64"]`。如同时启用 Redis live 或 Operator kind，runner 需具备对应服务/工具链。

本机或固定 runner 已具备 Redis + Docker/kind 时：

```bash
python scripts/check_fixed_runner_environment.py --profile production-evidence --build-dir build/default --require-redis --require-kind
python scripts/verify_production_evidence_gate.py --build-dir build/default --skip-build --include-redis-live --include-operator-kind
```

Runtime observability 固定 runner 建议：

```bash
python scripts/verify_observability_gate.py --build-dir build/default --skip-build --include-runtime-http --summary-path runtime/validation/p2-observability-runtime-summary.json
```

Release baseline / capacity 固定机器建议：

```bash
python scripts/verify_production_evidence_gate.py --build-dir build/release --configuration Release --skip-build --soak-profile short --baseline-profile release --include-release-baseline --perf-repetitions 3
python scripts/verify_production_evidence_gate.py --build-dir build/release --configuration Release --skip-build --include-capacity-baseline --perf-repetitions 3 --step-timeout-seconds 1800
```

通过标准：

- `runtime/validation/production-evidence-summary.json` 中 `passed=true`。
- `runtime/validation/fixed-runner-preflight-summary.json` 中 `passed=true`，且 Redis/kind 必需项与 workflow 输入一致。
- 子 summary `p6-stability-soak-summary.json`、`p6-data-recovery-summary.json`、`p6-specialized-e2e-summary.json`、`p6-candidate-audit-summary.json` 均为 `passed=true`。
- 启用 release/capacity baseline 时，`p6-release-baseline-summary.json` 和 `runtime/perf/release-baseline/summary.json` 必须同步归档。
- 启用 runtime observability 时，`p2-observability-runtime-summary.json` 和 `gateway-observability-runtime-summary.json` 必须同步归档。

## Identity JWKS rotation evidence

`jwks-rotation.yml` 必须从与其它 v3.6 候选 workflow 相同的 exact SHA 手动触发。
`platform=linux-x64` 使用 GCC/x86_64 lockfile 并记录 glibc；`platform=macos-arm64`
使用 Apple Clang/ARM64 lockfile 和 `$RUNNER_TOOL_CACHE/boost-gateway` 持久 Conan
namespace。两条路径都要求 OpenSSL CLI、localhost 随机端口绑定和严格离线 Conan
图，并把实际 host/runner identity 写入证据。机器专属复验应同时显式传入平台和
匹配的 runner 标签，例如：

```text
platform=linux-x64 runner=["self-hosted","node-aoi-omen-gaming-laptop-16-am0xxx"]
platform=macos-arm64 runner=["self-hosted","macOS","ARM64"]
```

workflow 会在临时目录生成两组 RSA/RS256 signing key 和一组短期 CA/server
certificate，通过 `SSL_CERT_FILE` 只向当前 probe 建立信任，并启动真实
`https://localhost:<random-port>/.well-known/jwks.json`。临时目录不会上传；artifact
只包含去敏 summary、focused CTest 日志和 strict-offline Conan summary。

通过标准：

- `runtime/validation/jwks-rotation-summary.json` 为 `overall_pass=true`，provenance
  与 checkout SHA、runner、workflow run 和 lockfile SHA-256 完全一致。
- HTTPS server 至少记录三次 `200`、两次受控 `503` 和一次被拒绝的 `302`；C++
  probe 必须实际经过 certificate chain、hostname verification、HTTPS allowlist
  和 no-redirect fetcher。
- `old-only -> old+new -> new-only` 三阶段分别接受正确 token，旧 `kid` 删除后
  fail closed；issuer、audience、HTTP URI 和非 allowlist host 继续被拒绝。
- outage 内 stale grace 允许已加载的新 key，超过 TTL+grace 返回
  `jwks_stale_expired`；无初始 snapshot 的 production Login Backend 启动失败。
- 独立静态 multi-`kid` key ring 回滚仍可验签，summary/artifact 不得包含 token、
  PEM、private key 或 JWK modulus/exponent。

`macos-arm64.yml` 默认额外运行 `perf_preset=smoke`、一次 repetition 和
`soak_profile=smoke`，用于验证原生服务拓扑及证据路径。候选冻结时使用
`perf_preset=baseline`、`perf_repetitions=3`；该 bounded evidence 不等于 2h/8h
长稳、capacity 或 Linux affinity/cgroup 结论。

同一 workflow 默认生成 macOS dSYM 候选。依赖继续使用 Release Conan namespace，
项目编译显式固定 `-O2 -g -DNDEBUG`，避免把不存在的 RelWithDebInfo dependency
configuration 误当成已预热图。`dsym-manifest.json` 对每个 Mach-O 记录 stripped
runtime hash、dSYM DWARF hash、ARM64 UUID 和已验证的 source lookup；UUID 不一致、
缺 compile unit、runtime 未 split、签名不可验证或 hello-world 失败都会阻断。

## Raft Phase B release evidence

Raft Phase B 必须从同一 exact SHA 触发 `release.yml`。runner 必须预置来自完整提交 `b9c348b4b58fdeeffa9d82ff87a67ed781a96b78` 的 `v3.5.3` leaderboard backend，并通过 `legacy_raft_sha256` 或 `LEGACY_RAFT_SHA256` 固定其平台摘要。该 workflow 在签名之前依次生成严格离线 Conan、`raft-ha`、data recovery、真实三进程 mixed-binary、clean package consumer 和 SBOM semantic summary，并由 `scripts/verify_raft_release_evidence.py` 拒绝跨 SHA、跨 workflow run、跨 runner、lockfile digest 漂移或旧制品摘要不符。

通过标准：

- `runtime/validation/raft-release-evidence-summary.json` 为 `overall_pass=true`。
- mixed-version protocol-profile 测试出现在 specialized summary 的 `matched_tests` 与实际执行计数中。
- mixed-binary summary 必须完成十三阶段双周期 `v0 -> v1 -> v0 -> v1 -> v0`，每阶段三节点读回一致、提交索引推进且 schema 轨迹符合门禁；六个回滚动作都必须携带 v1 备份与 downgrade 审计记录，第二周期三节点必须使用不同的内容寻址 history sidecar。
- Conan summary 固定 `--no-remote` / `--build=never`、`with_raft_protobuf=True`、`with_grpc=False`。
- SBOM 同时包含 `protobuf`、`abseil`，且不包含 `grpc`。
- legacy/candidate binary SHA-256 必须不同，legacy SHA-256 必须与 runner 预置值相同；同进程 protocol-profile E2E 不替代该事实。

完整操作边界见 `docs/deployment/raft-schema-migration-runbook.md`。

## R2/R3 cross-workflow aggregation

R0、真实 2h soak、当前 capacity/R4 与 R5/R6 在独立 workflow 中产生 summary，不能直接在各自的干净 workspace 运行最终 manifest。开始这一轮前先冻结候选提交，并确保四个 workflow 都从该完整 SHA dispatch。使用 `production-readiness.yml` 传入四类已完成 run ID，将 artifact 汇聚到同一 workspace，再分别运行 bounded/fixed 两份 R2 和最终 R3 readiness report。R2 直接验证 `long-soak-2h-summary.json` 的 `soak_profile=long`、成功状态、时效和 provenance，并独立验证 capacity run 的 R4 summary；capacity-only batch 不能替代 2h soak，capacity 失败也不会使已通过的 2h summary 失效：

```bash
gh workflow run production-readiness.yml --ref <candidate-sha> \
  -f runner='"ubuntu-latest"' \
  -f production_candidate_run_id=<production-candidate-run-id> \
  -f long_soak_run_id=<2h-long-soak-run-id> \
  -f capacity_run_id=<capacity-r4-run-id> \
  -f preprod_evidence_run_id=<r5-r6-run-id> \
  -f require_fixed_runner=true
```

该 workflow 会以 R3 `final_production_ready` 作为最终 job 结论；该值只有在 bounded/fixed 两份 R2 同时通过时才为 `true`。缺少 R5/R6、其他固定 runner summary 或任一跨 SHA 证据时应失败并列出 blocker。可先运行 `python3 scripts/check_evidence_provenance_contract.py` 验证本地 provenance 判定逻辑。

## R4/R5/R6 production blocking evidence

Before final production approval, refresh these fixed-runner or pre-production producers and consume them with `python3 scripts/check_production_evidence_manifest.py --require-fixed-runner`:

```bash
python3 scripts/verify_fixed_runner_release_capacity.py
python3 scripts/verify_preprod_recovery_drill.py --build-dir build/release
python3 scripts/verify_tls_preprod_multi_run.py --build-dir build/release --skip-build
```

Passing criteria:
- `runtime/validation/fixed-runner-release-capacity-summary.json` has `passed=true`.
- `runtime/validation/preprod-recovery-drill-summary.json` has `passed=true`.
- `runtime/validation/tls-preprod-multi-run-summary.json` has `passed=true`.
- `runtime/validation/r2-production-evidence-manifest-fixed-runner-summary.json` has `passed=true` when checked with `--require-fixed-runner`.
