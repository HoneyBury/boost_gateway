# 固定 Runner 执行手册

更新时间：2026-07-14（R5 offline cache + Conan ABI partitioning）

本文档用于把 P1 的固定机器任务从“人工约定”收束为可执行入口。默认 CI/release 仍使用有界 smoke；以下任务只在固定 runner 或手动 workflow 上执行。

P2 生产证据 runner 的详细配置、workflow 输入和归档标准见本文档后续章节。

容量、长稳和 release/capacity 归档的推荐主事实源是 Ubuntu LTS 固定 runner。macOS 本机结果可以继续作为开发回归参考，但不作为最终生产容量声明依据。2026-07-11 已在同一 Linux runner 上完成 production resilience `29145497642`、production evidence `29146018657` 和 R0 candidate `29152333112`；历史长稳/容量批次 `29146495724` 已失败，必须以其 artifact 而不是 workflow 名称判断证据强度。最新 capacity 闭环见 `29183833041`。

GitHub-hosted `ubuntu-latest` 仍可作为主线有界回归兜底，但不是 fixed-runner 证据替代物。2026-07-11，在线 Linux runner 已在 `cb1c853` 上成功执行 release、Conan validation、nightly stability、CI 和 perf regression 的 bounded 验证；release baseline、capacity、production evidence 和 long soak 仍必须在同一类 fixed runner 上归档完整 summary。

GitHub 仓库 Actions runner inventory 的单一事实源见 `docs/runner-inventory.md`。截至 2026-07-11，`aoi-omen-gaming-laptop-16-am0xxx` 已作为在线 Linux runner 匹配 `["self-hosted","Linux","X64"]`；`MyDesktop-Win` 仍离线。第一批真实证据刷新已解除 runner 不可用阻断，但仍需按下表执行并归档 summary。

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
  -o "&:with_grpc=False" -o "&:with_sqlite=False" --output-folder=build/conan-release --build=missing -s build_type=Release
python3.12 scripts/tools/ensure_conan_venv.py --venv "$conan_venv" --conan-version 2.8.1 --offline
python3 scripts/bootstrap_conan.py --conan-home "$CONAN_HOME" --no-remote
conan install . --profile:host conan/profiles/linux-gcc-x64 --profile:build conan/profiles/linux-gcc-x64 \
  --lockfile conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock \
  -o "&:with_grpc=False" -o "&:with_sqlite=False" --output-folder=build/conan-release-offline --build=never --no-remote -s build_type=Release
```

`ci.yml` 是有意保留的例外：它运行在 GitHub-hosted runner 上，使用 checkout 内 `.conan2-local` 与 `actions/cache`；`production-readiness.yml` 只下载并汇聚已有 artifact，不执行 Conan。

### 新机器的 Docker 镜像预热（R5 必须执行）

R5 Docker Compose recovery drill 依赖 runner 的 Docker daemon 已缓存 `env/docker/docker-compose.yml` 展开的全部镜像。新机器首次接入、Docker 数据目录清理或 registry mirror 变更后，先在 runner 上成功执行一次：

```bash
docker compose -f /path/to/boost_gateway/env/docker/docker-compose.yml pull
docker compose -f /path/to/boost_gateway/env/docker/docker-compose.yml build
python3 scripts/verify_preprod_recovery_drill.py \
  --mode docker-compose \
  --image-preflight-only \
  --docker-pull-policy never
```

该镜像缓存由 Docker daemon 管理，不属于 `${GITHUB_WORKSPACE}` 或 Conan home。R5 会从 `docker compose config --format json` 动态获取当前 6 个构建镜像和 5 个 registry 镜像，并在 `r5-docker-image-preflight-summary.json` 记录 image ID、RepoDigest 和缺失清单。`docker_pull_policy=never` 是 fixed-runner R5 默认值，完全禁止远端访问；`missing` 和 `always` 仅用于明确标注为诊断的联网检查。缺少本项目构建镜像时不会尝试从 registry 猜测拉取，而是要求先执行 Compose build 或导入已校验 bundle。

若新 runner 访问 `registry-1.docker.io:443` 失败，应通过受信任 mirror 完成首次预热；预热完成后可使用 `never` 离线执行。R5 不能用 `bounded-local` 结果替代预发恢复演练。

### 从受信任工作站离线传入 Docker 缓存

当 runner 不能访问 Docker Hub、但受信任工作站能够访问时，在**同一候选提交**的工作站 checkout 中创建 bundle。该工具从 Compose JSON 动态发现镜像，拉取 registry 镜像、构建全部项目镜像，并强制导出 `linux/amd64`。它会同时保存 Compose SHA-256、压缩包 SHA-256、每个镜像的 ID/标签/平台；不要只传 tar 包而遗漏相邻 manifest。

```bash
python3 scripts/tools/r5_docker_cache_bundle.py export \
  --bundle runtime/r5-docker-cache/r5-docker-images-linux-amd64.tar.gz
scp runtime/r5-docker-cache/r5-docker-images-linux-amd64.tar.gz* runner:/var/tmp/
```

在 Linux runner 的相同候选 checkout 中导入和校验。导入需要 Docker daemon 可写；bundle 在解压和 `docker load` 期间会额外占用约一份未压缩镜像空间。

```bash
python3 scripts/tools/r5_docker_cache_bundle.py import \
  --bundle /var/tmp/r5-docker-images-linux-amd64.tar.gz
python3 scripts/verify_preprod_recovery_drill.py \
  --mode docker-compose --image-preflight-only --docker-pull-policy never
```

第二条命令通过后，再以 `docker_pull_policy=never` 手动 dispatch `preprod-evidence.yml`。bundle 是临时跨机器运输方式，不替代配置受信任 registry mirror；工具会拒绝 Compose 文件或候选提交不匹配的 bundle，二者改变时必须重新导出。

### 2026-07-14 本机 R5 验证与剩余阻断

Ubuntu 24.04 x64 runner `myserver` 已完成真实 Docker Compose gateway restart
drill：Docker Engine `29.5.2`、Compose `v5.1.4`，11 个 Compose 镜像均为
`linux/amd64`，`docker_pull_policy=never` preflight、重启前后 SDK full-flow、
Docker snapshot 与 recovery record `32/32` 均通过。产物为
`runtime/validation/preprod-recovery-drill-summary.json`，其中
`overall_pass=true`。

该结果的 `run_id=local`；实现现已推送为候选 SHA `f6e0e57`，但本机结果仍只
证明 Docker 路径和离线缓存可运行，不能替代 `preprod-evidence.yml` 在目标预发
runner 生成的 artifact。以下阻断必须先关闭：

| 阻断 | 现象 | 解决方式 |
|---|---|---|
| 本机 Conan 分区 | `myserver` 已创建 `/opt/boost-gateway/{conan,sccache,tools}`，并完成固定 venv 与缓存预热 | 2026-07-14 已在 `ubuntu-24.04-gcc13.3.0-x64-release/conan-2.8.1-graph-b47fea14f7c99e7799f9` 通过 `--no-remote --build=never` lockfile install；其它 runner 仍须各自预热。 |
| 第二台 runner 接入 | 本机无其 SSH/API 入口，且没有已认证的 `gh` CLI | 提供 SSH 目标，或在管理节点执行 `gh auth login` 后用 `gh api repos/HoneyBury/boost_gateway/actions/runners` 确认 labels/online 状态；随后在该 runner 执行同一预热、bundle import 和 `never` preflight。 |
| Docker 磁盘容量 | `myserver` 清理后可用空间为 25GB，Docker images 约 1.4GB、BuildKit cache 为 0B | 导入/构建前保持至少 25GB 可用空间；若容量再次下降，先删除历史 build/旧 Conan namespace，保留当前 R5 Compose images。 |
| 候选可追溯性 | dirty checkout 可能令镜像内容与 manifest Git SHA 不一致 | `r5_docker_cache_bundle.py` 已拒绝 dirty checkout；先提交并 push 候选 SHA，再从该干净 checkout 导出 bundle。 |

关闭这些阻断后的固定顺序是：Conan 分区预热与 `--no-remote` 验证，导入或构建
Docker cache 并执行 `never` preflight，最后 dispatch `preprod-evidence.yml`
的 `docker-compose`/`never` 模式。

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
git checkout develop
git pull --ff-only
test -z "$(git status --porcelain)"
git rev-parse HEAD
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
  -o "&:with_grpc=False" -o "&:with_sqlite=False"
python3.12 scripts/tools/ensure_conan_venv.py --venv "$conan_venv" --conan-version 2.8.1 --offline
python3 scripts/bootstrap_conan.py --conan-home "$CONAN_HOME" --no-remote
conan install . --output-folder=build/conan-preprod-offline-check --build=never --no-remote \
  -s build_type=Release --profile:host conan/profiles/linux-gcc-x64 \
  --profile:build conan/profiles/linux-gcc-x64 \
  --lockfile conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock \
  -o "&:with_grpc=False" -o "&:with_sqlite=False"
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
gh workflow run preprod-evidence.yml --repo HoneyBury/boost_gateway --ref develop \
  -f 'runner=["self-hosted","Linux","X64","preprod-r5-myserver"]' \
  -f configure_preset=release -f build_dir=build/release -f configuration=Release \
  -f recovery_mode=docker-compose -f recovery_timeout_seconds=300 \
  -f docker_pull_policy=never -f docker_pull_attempts=1 \
  -f tls_runs=2 -f tls_timeout_seconds=240 \
  -f conan_profile=conan/profiles/linux-gcc-x64 \
  -f conan_lockfile=conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock
```

6. 监控并验证 artifact：

```bash
gh run list --repo HoneyBury/boost_gateway --workflow preprod-evidence.yml \
  --branch develop --limit 1
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

当前验证状态（2026-07-13）：run `29186343065` 已在 `4855dc0` 通过 R6 两轮 TLS 预发验证，但旧版 R5 在请求 `auth.docker.io` 匿名令牌时连接被重置。当前代码已增加 `missing/never/always` 镜像策略和离线预检契约，本地策略测试通过；这仍不是 fixed-runner 应用代码通过 R5 的证据。runner 可用后按以下顺序验证：

1. 使用 `--image-preflight-only --docker-pull-policy never` 确认全部镜像已预热；缺失时通过 mirror 补齐。
2. 手动运行 `preprod-evidence.yml`，保持 `recovery_mode=docker-compose`、`docker_pull_policy=never` 与 `tls_runs=2`，且整个 job 必须成功。`missing` 或 `always` 的联网诊断结果不能解除 R5 预发阻断。
3. 使用该成功 run 的 artifact，再执行真实 2h soak、R0 和 `production-readiness.yml` 的 R2/R3 汇聚。

手动命令：

```bash
python scripts/bootstrap_conan.py
python scripts/generate_conan_lock.py --profile conan/profiles/linux-gcc-x64 --build-type Release --without-sqlite
conan install . --profile:host conan/profiles/linux-gcc-x64 --profile:build conan/profiles/linux-gcc-x64 --lockfile conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock -o "&:with_grpc=False" -o "&:with_sqlite=False" --output-folder=build/conan-release --build=missing -s build_type=Release
```

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
| 6 | `preprod-evidence.yml` | `recovery_mode=docker-compose`、`tls_runs=2`、Release + Conan lockfile | `runtime/validation/preprod-recovery-drill-summary.json`、`runtime/validation/tls-preprod-multi-run-summary.json` |
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
- 成功 run `29196150703`（`main` / `0af5c91`）使用 `no_remote=true`，证明当时 runner 上的预置缓存覆盖实验 gRPC 依赖图；该历史缓存路径不能跨 Ubuntu release 复用。

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
python scripts/run_long_soak_capacity.py --build-dir build/release --configuration Release --skip-build --run-2h-soak --run-capacity --run-business-capacity --perf-repetitions 3
python scripts/run_cloud_production_closure.py --build-dir build/release --configuration Release --include-compose --include-kind --include-production-evidence
```

通过标准：

- `runtime/validation/long-soak-capacity-summary.json` 中 `summary_version=2`、`overall_pass=true`，并包含 `environment` 与 `artifacts`。
- `runtime/validation/cloud-production-closure-summary.json` 中 `summary_version=2`、`overall_pass=true`，并包含 `environment` 与 `artifacts`。
- 长稳 summary 至少归档 `long-soak-2h-summary.json`；8h soak 可在同一云主机扩展执行并归档 `long-soak-8h-summary.json`。容量 summary 应同时归档 `capacity-baseline-summary.json`、`business-capacity-baseline-summary.json`、`runtime/perf/fixed-runner-capacity/summary.json` 和 `runtime/perf/fixed-runner-business-capacity/summary.json`。
- 云端部署收束必须同时包含 Compose 运行态快照、kind/control-plane 结果和 production evidence 聚合 summary。

N1/N2/N3 建议按以下顺序收集：

1. `python scripts/check_fixed_runner_environment.py --profile cloud-production --build-dir build/release`
2. `python scripts/run_long_soak_capacity.py --build-dir build/release --configuration Release --skip-build --run-2h-soak --run-capacity --run-business-capacity --perf-repetitions 3`
3. `python scripts/check_monitoring_operability.py --summary-path runtime/validation/n2-monitoring-operability-summary.json`
4. `python scripts/run_cloud_production_closure.py --build-dir build/release --configuration Release --include-compose --include-kind --include-production-evidence`

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
## R2/R3 cross-workflow aggregation

R0、真实 2h soak、当前 capacity/R4 与 R5/R6 在独立 workflow 中产生 summary，不能直接在各自的干净 workspace 运行最终 manifest。开始这一轮前先冻结候选提交，并确保四个 workflow 都从该完整 SHA dispatch。使用 `production-readiness.yml` 传入四类已完成 run ID，将 artifact 汇聚到同一 workspace，再分别运行 bounded/fixed 两份 R2 和最终 R3 readiness report。R2 会验证导入的 long-soak summary 实际设置了 `run_2h_soak=true`，并拒绝缺失 `generated_at`、缺失 provenance、checkout 不匹配或候选 SHA 不一致；capacity-only batch 不能替代 2h soak：

```bash
gh workflow run production-readiness.yml --ref develop \
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
