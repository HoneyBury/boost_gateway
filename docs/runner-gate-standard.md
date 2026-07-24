# Runner Gate Standard

更新时间：2026-07-22

本文定义 BoostGateway 自托管 Linux x64、Linux ARM64 和 macOS ARM64 runner 的命名、标签、缓存和证据准入规则。
它是 `docs/runner-inventory.md` 的配置标准；inventory 记录实际机器及在线状态，
`docs/fixed-runner-playbook.md` 提供具体执行命令。

## 命名和标签

Runner 的显示名应使用稳定、可读且不含环境秘密的格式：

```text
bg-<purpose>-u<ubuntu-release>-<x64|arm64>-<ordinal>
```

示例：`bg-preprod-u2404-x64-01`、`bg-validation-u2404-arm64-01`。已有 runner 不必为了符合该格式重新注册，
但必须有一个唯一 custom label。当前 `myserver` 使用
`preprod-r5-myserver` 作为其 R5 唯一标签。

GitHub 自动管理 `self-hosted`、OS 和架构 labels。不要尝试替换这些 labels；
下列 custom labels 承担准入语义：

| 标签模式 | 类型 | 含义 |
|---|---|---|
| `node-<runner-name>` | 身份 | 不可共享的机器定位；注册时即可添加。 |
| `preprod-r5-<runner-name>` | 身份 + 角色 | 精确将 R5 演练投递到一台指定机器。 |
| `preprod-r5` | 能力池 | 仅允许已通过目标平台完整 R5 准入的 runner 添加。 |
| `ubuntu-2204` / `ubuntu-2404` | 元数据 | 可选，用于调度审计；不能代替 Conan cache 分区。 |
| `conan-gcc13-release` | 元数据 | 可选，表示已对当前 GCC/Release namespace 完成离线 Conan 复验。 |
| `conan-gcc13-debug` | 元数据 | 可选，表示已对当前 GCC/Debug namespace 完成离线 Conan 复验。 |
| `conan-gcc13-grpc` | 元数据 | 可选，表示已对当前 GCC/gRPC namespace 完成离线 Conan 复验。 |

所有 `runs-on` labels 为 AND 条件。`["self-hosted","Linux","X64"]` 会匹配
所有 Linux x64 runner，因此不可用于需要确定机器或已验证能力的 R5 job。

## 生命周期

```text
registered -> initialized -> conan-ready -> docker-ready -> preprod-r5 eligible -> evidence verified
```

`registered` 只表示 GitHub 能投递 job。只有完成下列 G0-G5 后，runner 才能
拥有 `preprod-r5`。任一宿主 OS、GCC、Conan 版本、Docker 数据目录、Compose
文件或缓存内容发生漂移时，立即移除 `preprod-r5`，回到对应的预热 gate。

## Gate Matrix

| Gate | 目的 | 必需条件 | 通过证据 |
|---|---|---|---|
| G0 Identity | 机器可定位 | runner online，系统 labels 与唯一 custom label 正确 | `gh api .../runners/<id>/labels` 输出 |
| G1 Host | 工具链与容量 | Linux 使用 GCC 与 Docker/Compose；macOS 使用 Apple Clang 和原生进程模式；均要求 Python 3.12，构建前空间/并发满足平台基线 | host inventory 与平台资源 summary |
| G2 Conan | 固定工具链和本机 ABI-safe 依赖缓存 | Linux 的 `/opt/boost-gateway/{conan,sccache,tools}` 或 macOS `$RUNNER_TOOL_CACHE/boost-gateway` 可写；隔离 venv 只含 Conan `2.8.1`；按 OS release、实际 compiler、arch、build type 和 graph remote digest 生成 namespace；`--no-remote --build=never` install 成功 | `runner-cache-identity.json`、venv `conan --version` 和离线 install 日志 |
| G3 Runtime | 目标平台运行时准入 | Linux 的所有 Compose images 精确匹配 `linux/amd64` 或 `linux/arm64` 且离线 preflight 通过；macOS 的 Mach-O server/SDK 架构检查通过 | Docker preflight 或 macOS package summary |
| G4 R5 Drill | gateway restart 恢复路径 | Linux Docker Compose 或 macOS 原生进程启动，重启前后 SDK full-flow、readiness 和 record validation 全部通过 | `preprod-recovery-drill-summary.json` |
| G5 GitHub Evidence | 可接受的预发 artifact | workflow 使用 `preprod-r5` 或唯一 R5 label，候选 SHA 一致，R5/R6 artifact 上传且通过 | `preprod-evidence-<run-id>` artifact |

G0-G3 通过后可添加 `preprod-r5`；G4-G5 通过后才可被 R2/R3 作为生产候选证据。
本机手动演练只满足 G4 的技术路径验证，不能替代 G5。

## Conan Virtual Environment Contract

Conan 可执行文件也是可追溯构建输入，而不是由系统 Python 隐式提供的工具。所有
开发、runner 预热和 R5 离线证据必须使用 Conan `2.8.1` 的隔离 virtual environment。
开发 checkout 使用 `.venv/conan-2.8.1`；Ubuntu runner 使用
`/opt/boost-gateway/tools/conan-2.8.1-py3.12`；macOS runner 使用
`$RUNNER_TOOL_CACHE/boost-gateway/tools/conan-2.8.1-py3.12`。三者都必须由
`scripts/tools/ensure_conan_venv.py` 创建或验收。
该 helper 默认要求 venv 内解释器为 Python 3.12，并精确验证 Conan `2.8.1`；不能
只因宿主 `python` 或 `conan` 命令可用就视为通过 G2。

创建或升级 venv 仅允许在开发、runner 初始化或显式预热阶段联网执行：

```bash
# 开发 checkout
python3.12 scripts/tools/ensure_conan_venv.py --conan-version 2.8.1
source .venv/conan-2.8.1/bin/activate

# Ubuntu runner，预热前执行一次
conan_venv=/opt/boost-gateway/tools/conan-2.8.1-py3.12
python3.12 scripts/tools/ensure_conan_venv.py --venv "$conan_venv" --conan-version 2.8.1
export PATH="$conan_venv/bin:$PATH"
conan --version
```

R5 workflow 和手工离线复验必须使用 `--offline`；该模式不会创建、安装或升级，
缺失 venv 或版本偏离会立即失败。Conan 2.29 等现有全局安装可保留用于历史本地
任务，但不得进入 `PATH` 或作为 R5、预热、lockfile 生成的执行器。任何 Conan、
Python minor version 或 venv 路径漂移都会使 G2 失效，必须重新验收后才可保留
`preprod-r5` label。

`--no-remote` 只禁用 Conan remote，不能单独证明 recipe 不会在构建缺包时下载
上游源码。因此任何离线证据 install 都必须同时使用 `--build=never`；只有显式
联网预热阶段才允许 `--build=missing`。

所有仓库内会执行 `conan install` 的 workflow 都受同一规则约束，包括 GitHub-hosted
CI、release 和 fixed-runner 证据任务。它们必须通过 helper（直接调用或经
`setup-cpp-conan` composite action）把对应 venv 的 `bin` 加入 `GITHUB_PATH`，不得
探测或回退到全局 `conan`，也不得使用浮动版本范围。CI 的 Conan cache key 必须包含
`conan-2.8.1`，且不能用宽泛 restore key 恢复另一版本或另一依赖图的缓存。

## Conan and Docker Separation

| 维度 | Conan | Docker Compose images |
|---|---|---|
| 缓存边界 | 每个 runner OS release、实际 compiler、架构、build type 和依赖图独立 | `linux/amd64` 与 `linux/arm64` bundle 独立；Mac Docker 只消费 Linux bundle，不承载 Mach-O |
| 原因 | 本机二进制包可能依赖宿主 glibc | 容器携带 Ubuntu 24.04 用户态，host 提供内核/daemon |
| 共享方式 | 每台 runner 通过 lockfile `conan install --build=missing` 预热 | clean candidate checkout 的 bundle export/import，或后续 registry mirror |
| 离线验证 | `bootstrap_conan.py --no-remote` + `conan install --no-remote --build=never` | `--image-preflight-only --docker-pull-policy never` |
| 漂移处理 | 新 namespace，不能复制另一 Ubuntu release 的 Conan Home | Compose SHA、候选 SHA、image ID 或 digest 变化时重新导出/import |

## 三平台 R5 证据边界

| 证据轨道 | 运行内容 | 当前状态 |
|---|---|---|
| Linux x64 production R5 | x86_64 ELF、`linux/amd64` Compose images、restart/full-flow/snapshot | 已实现 |
| Linux ARM64 production R5 | ARM64 ELF、`linux/arm64` Compose images、相同语义的 restart/full-flow/snapshot | runs `29906228268`、`29907949804`、`29908827298` 完成 Release/Debug/gRPC cache 准入；run `29909904605` 完成原生 image preflight、R5/R6，G0-G5 已通过 |
| macOS ARM64 production R5 | Apple Clang Mach-O server 原生编排、gateway restart 前后 native SDK full-flow | 原生 candidate `29927622379`、baseline `29952053505`、capacity/R4 `29948796107` 与 2h soak `29961425142` 已形成预冻结 artifact；最终 frozen-SHA refresh 仍待完成 |
| Mac-hosted Linux container | OrbStack/Docker Desktop 运行 Linux image | 可作为相应 Linux OCI 目标的宿主兼容性证据，不是 macOS 原生证据 |

macOS 与 Linux 的 POSIX 源码兼容不意味着 Docker 产物兼容。Docker for Mac 的
daemon 位于 Linux VM；`prepare_docker_runtime_context.py` 只接受 ELF。Docker bundle
schema 显式区分 `linux/amd64` 与 `linux/arm64`，而 macOS R5 直接消费 Mach-O。三个
平台分别建立性能、容量、恢复、发布资产和 readiness 结论，任何一个平台的成功都
不能替代另一个平台。

Mac-hosted Linux ARM64 runner 是原生 `aarch64` Linux 边界，不是 macOS workflow，
也不是 amd64 仿真。当前 Mac runner host 禁用 OrbStack Rosetta，并要求本地 Docker
image inventory 不包含 `linux/amd64`；linux-x64 workflow 必须调度到远端 X64 runner。

## Registration and Admission

注册新 runner 时只添加不可共享身份标签。registration token 是短期敏感凭据，
不要输出、保存或写入文档。

```bash
runner_name="bg-preprod-u2404-x64-01"
registration_token="$(gh api --method POST \
  repos/HoneyBury/boost_gateway/actions/runners/registration-token --jq .token)"

./config.sh --url https://github.com/HoneyBury/boost_gateway \
  --token "$registration_token" --name "$runner_name" \
  --labels "node-${runner_name}" --unattended
```

在 runner 主机完成 G1-G3 后，追加唯一 R5 标签和共享能力标签。POST 是追加而非
替换，避免丢失其它 custom labels：

```bash
runner_id="$(gh api repos/HoneyBury/boost_gateway/actions/runners \
  --jq ".runners[] | select(.name == \"${runner_name}\") | .id")"

printf '%s' "{\"labels\":[\"preprod-r5-${runner_name}\",\"preprod-r5\"]}" |
gh api --method POST \
  "repos/HoneyBury/boost_gateway/actions/runners/${runner_id}/labels" --input -
```

当任一 gate 失效时先移除共享能力标签，阻止通用 R5 workflow 调度到该机器：

```bash
gh api --method DELETE \
  "repos/HoneyBury/boost_gateway/actions/runners/${runner_id}/labels/preprod-r5"
```

## Workflow Selection Policy

`preprod-evidence.yml` 默认使用
`["self-hosted","Linux","X64","preprod-r5"]`，因此只会调度到已准入的
R5 runner 池。首次验证、排障和机器专属复验必须传入唯一 label：

```bash
gh workflow run preprod-evidence.yml --repo HoneyBury/boost_gateway --ref <candidate-sha> \
  -f 'runner=["self-hosted","Linux","X64","preprod-r5-myserver"]' \
  -f recovery_mode=docker-compose -f docker_pull_policy=never \
  -f include_redis_recovery=true
```

多个 runner 都通过 G0-G3 后，使用能力池即可让 GitHub 选择任一合格机器：

```bash
-f 'runner=["self-hosted","Linux","X64","preprod-r5"]'
```

每次 dispatch 前都应确认 runner 未 busy、候选 SHA 已推送，且 G2/G3 缓存仍与
当前 checkout 匹配。完整命令、监控和 artifact 验收见
`docs/fixed-runner-playbook.md`。
