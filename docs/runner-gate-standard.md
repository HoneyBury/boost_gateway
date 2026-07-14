# Runner Gate Standard

更新时间：2026-07-14

本文定义 BoostGateway 自托管 Ubuntu runner 的命名、标签、缓存和证据准入规则。
它是 `docs/runner-inventory.md` 的配置标准；inventory 记录实际机器及在线状态，
`docs/fixed-runner-playbook.md` 提供具体执行命令。

## 命名和标签

Runner 的显示名应使用稳定、可读且不含环境秘密的格式：

```text
bg-<purpose>-u<ubuntu-release>-x64-<ordinal>
```

示例：`bg-preprod-u2404-x64-01`。已有 runner 不必为了符合该格式重新注册，
但必须有一个唯一 custom label。当前 `myserver` 使用
`preprod-r5-myserver` 作为其 R5 唯一标签。

GitHub 自动管理 `self-hosted`、OS 和架构 labels。不要尝试替换这些 labels；
下列 custom labels 承担准入语义：

| 标签模式 | 类型 | 含义 |
|---|---|---|
| `node-<runner-name>` | 身份 | 不可共享的机器定位；注册时即可添加。 |
| `preprod-r5-<runner-name>` | 身份 + 角色 | 精确将 R5 演练投递到一台指定机器。 |
| `preprod-r5` | 能力池 | 仅允许已通过完整 R5 准入的 Linux runner 添加。 |
| `ubuntu-2204` / `ubuntu-2404` | 元数据 | 可选，用于调度审计；不能代替 Conan cache 分区。 |
| `conan-gcc13-release` | 元数据 | 可选，表示已对当前 GCC/Release namespace 完成离线 Conan 复验。 |

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
| G1 Host | 工具链与容量 | Ubuntu x64、Docker/Compose、Python、Conan、GCC；构建/import 前至少 25GB 可用空间 | host inventory 和 `df -h /` |
| G2 Conan | 本机 ABI-safe 依赖缓存 | `/opt/boost-gateway/{conan,sccache}` 可写；按 OS release/GCC/arch/build type/graph remote digest 生成 namespace；`--no-remote` install 成功 | `runner-cache-identity.json` 和离线 install 日志 |
| G3 Docker | 完整离线 Compose image cache | 所有 Compose images 为 `linux/amd64`；registry digest 和 image ID 可读取；`docker_pull_policy=never` preflight 通过 | `r5-docker-image-preflight-summary.json` |
| G4 R5 Drill | gateway restart 恢复路径 | `docker-compose` 启动、重启前后 SDK full-flow、snapshot、record validation 全部通过 | `preprod-recovery-drill-summary.json` |
| G5 GitHub Evidence | 可接受的预发 artifact | workflow 使用 `preprod-r5` 或唯一 R5 label，候选 SHA 一致，R5/R6 artifact 上传且通过 | `preprod-evidence-<run-id>` artifact |

G0-G3 通过后可添加 `preprod-r5`；G4-G5 通过后才可被 R2/R3 作为生产候选证据。
本机手动演练只满足 G4 的技术路径验证，不能替代 G5。

## Conan and Docker Separation

| 维度 | Conan | Docker Compose images |
|---|---|---|
| 缓存边界 | 每个 runner OS release、GCC、架构、build type 和依赖图独立 | 可在 Ubuntu 22.04/24.04 x64 runner 间运输 |
| 原因 | 本机二进制包可能依赖宿主 glibc | 容器携带 Ubuntu 24.04 用户态，host 提供内核/daemon |
| 共享方式 | 每台 runner 通过 lockfile `conan install --build=missing` 预热 | clean candidate checkout 的 bundle export/import，或后续 registry mirror |
| 离线验证 | `bootstrap_conan.py --no-remote` + `conan install --no-remote` | `--image-preflight-only --docker-pull-policy never` |
| 漂移处理 | 新 namespace，不能复制另一 Ubuntu release 的 Conan Home | Compose SHA、候选 SHA、image ID 或 digest 变化时重新导出/import |

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
gh workflow run preprod-evidence.yml --repo HoneyBury/boost_gateway --ref develop \
  -f 'runner=["self-hosted","Linux","X64","preprod-r5-myserver"]' \
  -f recovery_mode=docker-compose -f docker_pull_policy=never
```

多个 runner 都通过 G0-G3 后，使用能力池即可让 GitHub 选择任一合格机器：

```bash
-f 'runner=["self-hosted","Linux","X64","preprod-r5"]'
```

每次 dispatch 前都应确认 runner 未 busy、候选 SHA 已推送，且 G2/G3 缓存仍与
当前 checkout 匹配。完整命令、监控和 artifact 验收见
`docs/fixed-runner-playbook.md`。
