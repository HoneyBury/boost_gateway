# GitHub Actions Runner Inventory

更新时间：2026-07-24
GitHub API 验证时间：2026-07-24（本次文档整理）

本文档是仓库 self-hosted runner 身份和在线状态的单一事实源。标签、缓存和 G0-G5 准入
规则见 [Runner Gate Standard](runner-gate-standard.md)，具体预热与证据命令见
[固定 Runner 手册](fixed-runner-playbook.md)。

验证命令：

```bash
gh api repos/HoneyBury/boost_gateway/actions/runners
```

## 当前快照

| Runner | OS/Arch | 状态 | Busy | 当前角色 |
|---|---|---|---|---|
| `aoi-omen-gaming-laptop-16-am0xxx` | Linux X64 | `online` | `false` | Linux x64 release、asset verification、fixed-runner 验证 |
| `HoneyBurydeMacBook-Pro` | macOS ARM64 | `online` | `false` | macOS native runtime、SDK、JWKS、dSYM |
| `HoneyBury-M4-Linux-ARM64` | Linux ARM64 | `online` | `false` | Linux ARM64 release、R5、SDK、JWKS、symbols |
| `MyDesktop-Win` | Windows X64 | `offline` | `false` | 已退出当前维护平台 |
| `myserver` | Linux X64 | `offline` | `false` | 历史 R5 runner；离线期间不得调度新证据 |

## 当前标签

| Runner | 必需身份/能力标签 |
|---|---|
| AOI Linux x64 | `node-aoi-omen-gaming-laptop-16-am0xxx` |
| macOS ARM64 | `node-honeybury-macbook-pro`, `macos-arm64-candidate`, `sdk-osx-arm64`, `jwks-macos-arm64` |
| Linux ARM64 | `node-honeybury-m4-linux-arm64`, `ubuntu-2404`, `conan-gcc13-release`, `conan-gcc13-debug`, `conan-gcc13-grpc`, `preprod-r5`, `sdk-linux-arm64`, `debug-symbols-linux-arm64`, `jwks-linux-arm64` |
| myserver | `preprod-r5-myserver`；当前 offline，历史 label 不表示当前准入 |

`["self-hosted","Linux","X64"]` 可以匹配多台机器，只适用于不要求唯一宿主的任务。
R5、cache admission、published-asset verification 等机器绑定证据必须使用唯一
`node-*` 或专用 capability label。

## 已准入能力

### AOI Linux x64

- Ubuntu 22.04、GCC 13.4、Python 3.12、Conan 2.8.1。
- `/opt/boost-gateway` 持久 Conan/sccache namespace 已用于 strict-offline 构建。
- kind `v0.32.0`、kubectl `v1.36.1` 和固定 Kubernetes node image 已完成真实 Operator
  Ready/scale/restart/rollback/delete/cleanup 验证。
- v3.6.2 Linux x64 release 和 published-asset verification 已完成。

### macOS ARM64

- Apple Clang 原生构建，Conan 2.8.1 cache 位于 runner tool cache。
- 使用原生六服务进程完成 R5、SDK full-flow、JWKS、package 和 dSYM 验证。
- Mac-hosted Linux VM 的结果不计入 macOS 原生证据。
- v3.6.2 macOS runtime、SDK 和 dSYM 资产已完成线上复验；notarization 未声明。

### Linux ARM64

- OrbStack Ubuntu 24.04 ARM64、GCC 13、Python 3.12、Conan 2.8.1。
- Release/Debug/gRPC namespace 均完成 `--no-remote --build=never` admission。
- 原生 `linux/arm64` Docker、R5/R6、SDK、JWKS、debug-symbol 和 v3.6.2 published asset
  verification 已完成。
- VM service PATH 必须包含 `/usr/bin/gh`；Mac host 保持
  `machines.forward_ports=false`，避免污染 macOS 原生端口。

## 调度规则

1. 普通主 CI 默认使用 GitHub-hosted `ubuntu-latest`，不消耗 fixed runner。
2. 平台发布和资产复验由 `.github/runner-matrix.json` 的 platform map 选择唯一 runner。
3. 调度前重新查询 API；`online` 只表示可接单，不表示 cache、Docker 或 R5 仍满足准入。
4. OS、compiler、Conan、Docker root 或 cache 图变化后，必须从对应 gate 重新准入。
5. `myserver` 和 Windows 当前 offline；历史成功 run 不得替代新候选或新环境证据。

## 历史记录

v3.5 和 v3.6 的逐 run、artifact ID、cache graph 与故障修复记录已经保存在：

- [v3.5.x 维护计划](archive/releases/v3.5.x-maintenance-plan.md)
- [v3.5.2 冻结清单](archive/releases/v3.5.2-freeze-todo.md)
- [v3.6 实现状态](archive/releases/v3.6-implementation-status.md)
- [固定 Runner 手册](fixed-runner-playbook.md)

历史 run 只证明其绑定 SHA、runner 和环境的事实，不自动代表当前在线状态或准入状态。
