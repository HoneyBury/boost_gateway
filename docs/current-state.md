# 当前项目事实源

更新时间：2026-07-24

本文档只记录当前仍成立的实现、发布和规划事实。历史候选、已关闭清单和逐 run 交付记录
位于 [`docs/archive/`](archive/README.md)，不再混入当前执行优先级。

## 当前结论

- 当前发布版本是
  [v3.6.2](https://github.com/HoneyBury/boost_gateway/releases/tag/v3.6.2)，tag 固定在
  `ac99ae353a2a6e846f934c8d81c78a07f420f683`。
- v3.6.2 已发布 Linux x64、Linux ARM64、macOS ARM64 runtime，SDK 4.2.0 wheel/
  NuGet、Linux debug symbols、macOS dSYM、逐 subject SPDX/attestation 和总 checksum。
- 三个平台均完成线上资产的独立下载、checksum、架构、运行时消费、SDK、符号绑定和
  attestation 复验；平台证据不可互换。
- 当前主线不是继续增加 demo 或协议表面积，而是执行 Ubuntu 24.04 x64 单节点自动
  部署、观测、追溯、备份恢复、72 小时预演和 30 天不可变运行计划。

发布过程和逐平台 run 记录已归档到
[v3.6 实现状态](archive/releases/v3.6-implementation-status.md)。当前任务和完成定义见
[主线执行计划](mainline-execution-plan.md)与
[单节点运营计划](single-node-enterprise-validation-plan.md)。

项目待办以 `docs/todos/tasks.json` 为版本化事实源，`docs/todos/BOARD.md` 提供生成视图。
`TODO-0008` 已在 Ubuntu 24.04 x64 目标主机真实完成：baseline、SMART/thermal、端口与
权限准入通过；boot ID 从 `347f0099-eaa5-4f0e-a0e8-7a93803e0f6d` 变为
`3872f22c-1b67-4d29-8c04-280a58619c6e` 后，systemd 在无交互登录条件下恢复 Compose、
11 个 healthy 容器、监控和端口拓扑，正式 `verify-reboot` summary 为 PASS。

`TODO-0009` 也已在同一目标机真实完成：v3.6.2 的 tag/commit/checksum/SLSA/SPDX/ELF
校验、固定 Ubuntu digest 的六个 runtime-only image、不可变项目 image ID 的生产 Compose
和 release SDK full-flow 均通过，全程没有源码构建或公共 Conan 访问。目标机证据位于
`/var/lib/boost-gateway-evidence/release/`，主线下一项为 `TODO-0010` 的幂等安装、升级和回滚。

## 默认生产链路

默认生产主链仍是 SDK + TCP gateway + `BackendEnvelope` + Login/Room/Battle/
Matchmaking/Leaderboard 五个 backend，并按部署需要使用 Redis、TLS 和观测组件。

```text
C++ / Python / C# SDK
          |
          | length-prefixed TCP
          v
Gateway :9201 ---- management HTTP :9080
   |---- Login         :9202
   |---- Room          :9302
   |---- Battle        :9303
   |---- Matchmaking   :9304
   `---- Leaderboard   :9305
```

当前默认构建和依赖选项：

| 选项 | 默认值 | 当前边界 |
|---|---|---|
| `BOOST_DEPENDENCY_PROVIDER` | `conan` | 严格使用 Conan 2.8.1 profile/lockfile，不隐式回退 |
| `BOOST_BUILD_RAFT_PROTOBUF` | `ON` | 内部 codec 可用，writer 激活仍受 capability 和回滚门禁控制 |
| `BOOST_BUILD_GRPC` | `OFF` | gRPC 已有 PoC 与专项证据，但不进入默认生产链路 |
| `BOOST_BUILD_SQLITE` | `OFF` | SQLite storage 是显式可选能力 |
| `BOOST_BUILD_TANK_DEMO` | `OFF` | 业务 demo 不进入默认生产构建 |

## 已稳定交付的能力

- Gateway session、Actor runtime、后端路由、熔断、限流、HTTP health/metrics 和配置治理。
- Login、Room、Battle、Matchmaking、Leaderboard 六服务闭环及 SDK full-flow。
- C++ SDK、稳定 C ABI、Python ctypes wrapper、C# P/Invoke wrapper 和 4.2.0 分发资产。
- schema-backed typed contract；五个业务域的 handler 已纳入 typed envelope 治理。
- Redis leaderboard/event store、Raft state/command/wire codec、恢复和 mixed-binary 门禁。
- TLS/mTLS profile、JWT/JWKS 轮换验证、OTel exporter/collector 对账。
- Docker Compose、Kubernetes、Operator、发布包、SBOM、provenance 和符号验证入口。
- Linux x64、Linux ARM64、macOS ARM64 的原生 Conan、运行时和发布消费证据。

P0-P6 的仓库内实现现已完成；具体历史交付见归档状态文档。这句话不表示所有可选能力
都已默认激活：已接受的 ADR 仍分别约束 Raft protobuf writer、公共 package registry、
Apple notarization 和实验 gRPC。对这些能力，默认生产链路和 manifest 阻断状态不变。

## 框架与业务边界

- `include/v2/`、`src/v2/` 承载公共连接、路由、协议、runtime、观测、持久化和 SDK
  支撑，不承载具体游戏规则。
- `include/v3/`、`src/v3/`、`proto/v3/` 是协议、Raft、Redis 和持久化演进层。
- 坦克大战及其它业务样例位于 `demo/games/`。`TankBattlePlugin` 是 SPI 验证实现，
  不属于默认生产 battle 主链。
- legacy raw JSON 只保留在明确的兼容窗口；新增业务消息必须使用 typed/schema contract。
- `BoostAsioDemo` 只作为历史仓库名和兼容标识保留，对外名称统一为 `BoostGateway`。

## 平台与证据边界

| 平台 | 当前状态 | 不能推导的结论 |
|---|---|---|
| Linux x64 | runtime/SDK/symbol 发布和独立复验完成 | 单次本地 smoke 不代表固定 runner 容量 |
| Linux ARM64 | 原生 Release、R0、R4、2h、runtime/SDK/symbol 发布复验完成 | 不能用 x64 package 或镜像代替 ARM64 证据 |
| macOS ARM64 | 原生 Release、R0、R4、2h、runtime/SDK/dSYM 发布复验完成 | 当前发布未声明 Apple notarization |

机器可读平台契约见 [platform-production-boundaries.json](platform-production-boundaries.json)，
runner 当前状态见 [`docs/runner-inventory.md`](runner-inventory.md)。

性能事实必须绑定 workload、候选 SHA、runner、lockfile、CPU 约束和原始 summary。
历史 2h/8h、capacity 和单变量轴证明对应候选与环境下的行为，不构成任意部署规模的容量
承诺。当前有效测量口径见 [performance-baseline.md](performance-baseline.md)。

## 当前主任务

当前两个月工作由 `TODO-0007` 至 `TODO-0018` 管理，目标是：

1. 在服务器不编译源码的前提下，以不可变 release asset 完成幂等安装、升级和回滚。
2. 建立 host preflight、45 天 metrics、外部 SDK canary 和 deployment/evidence ledger。
3. 完成异机备份、Redis/host/runtime 恢复演练，并满足 5/10 分钟 RTO 边界。
4. 完成 required checks、review、CODEOWNERS、SECURITY 和 Action SHA pinning。
5. 通过 72 小时上线预演后冻结单一 tag/SHA/digest，连续运行至少 30 天。

30 天验证要求连续时长不少于 `2,592,000s`，availability/canary success 与证据覆盖率
均不低于 99.9%；runtime 变化会重置 Day 0。完整口径见
[single-node-enterprise-validation-plan.md](single-node-enterprise-validation-plan.md)。

## 当前阻断和非目标

- 不把 v3.6.2 的三平台发布解释为多节点 HA、任意规模容量或所有云环境支持。
- 不因 PoC 完整而把 gRPC 升级为默认传输。
- 不在当前运营主线中扩大业务 demo、公共协议或 SDK 表面积。
- 不把不同 SHA、不同 runner 或不同平台的证据拼接为同一冻结候选。
- PyPI/NuGet.org trusted publishing 和 Apple notarization 仍是独立工作，不由 GitHub
  Release 资产自动解除。
- 性能优化必须由长期指标或 incident 驱动，并保留 RCA、前后基线、回归和回滚方案。

## 当前验证入口

```bash
python3.12 scripts/check_current_docs_install.py
python3.12 scripts/check_mainline_readiness.py
python3.12 scripts/check_config_source_layout.py
python3.12 scripts/check_transport_config_governance.py
python3.12 scripts/check_next_minor_decisions.py
python3.12 scripts/verify_release_candidate.py \
  --skip-release-baseline --soak-profile smoke
```

开发构建和分层测试见 [ONBOARDING.md](ONBOARDING.md)。生产证据入口、前置依赖和
fixed-runner 操作见 [release-governance.md](release-governance.md)与
[fixed-runner-playbook.md](fixed-runner-playbook.md)。
