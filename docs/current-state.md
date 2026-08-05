# 当前项目事实源

更新时间：2026-08-05

本文档只记录当前仍成立的实现、发布和规划事实。历史候选、已关闭清单和逐 run 交付记录
位于 [`docs/archive/`](archive/README.md)，不再混入当前执行优先级。

## 当前结论

- 仓库发布线是 v3.6.6 / SDK 4.2.1，按 Linux x64-only patch manifest 构建和复验；
  `miniserver` 在 W32 自然观测周期结束前继续运行不可变 v3.6.5 deployment。
- v3.6.2 三平台 runtime、SDK 4.2.0、symbols/dSYM 和供应链资产保持不可变历史事实；
  Linux ARM64 与 macOS ARM64 不进入 v3.6.6 新资产集合。
- Mac 外部 canary 可以继续使用线协议兼容的历史 macOS SDK 4.2.0 访问 v3.6.5 服务端；
  canary deployment identity 必须记录实际 SDK 版本，不得伪装为 4.2.1。
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
`/var/lib/boost-gateway-evidence/release/`。

`TODO-0010` 已在同一目标机真实完成。历史 v3.6.2/v3.6.0 演练中，同 release 重复
install/deploy 返回相同 identity 和
`idempotent=true`，`todo0010-20260725T152655Z` data/backup/evidence 哨兵、Redis key 和
Compose volume 清单均保留。前向 upgrade 和真实 rollback 分别在约 55–57 秒内完成，rollback
记录恢复了 runtime asset、image environment 和 configuration digest。受控 Prometheus pause
使 transaction `20260725T161238-upgrade-257dce6b1f94` 的候选验证真实失败，独立 recovery
summary 随后 PASS 并自动恢复 v3.6.0；transaction
`20260725T163615-upgrade-db4ab2d7639d` 将运行版本恢复到 v3.6.2。2026-08-01 的正式
v3.6.5 Release 随后通过同一治理入口完成 install 和 upgrade：transaction
`20260801T193531-upgrade-242675750f37` 在 65.254 秒内 PASS，当前 `current` 是
`v3.6.5-b6d0c8554223-8a1afcfd58dd`，`previous` 是
`v3.6.2-faf2d03ff1b9-8a1afcfd58dd`，受保护状态未改变。目标机证据位于
`/var/lib/boost-gateway/deployment-transactions/`。

v3.6.6 annotated tag 已固定到
`d0db2cfd2efaffca55522a58402a48015b39d091`。受治理 main 演练 `31019859848`、正式
Release `31020678952` 和独立 aoi Linux x64 published-asset verification
`31021854876` 均 PASS；发布包含 11 个 Linux x64-only 治理资产，runtime archive
SHA-256 是 `17d88d752931fb57a07fb1c0b28517ad326bbcb69c3c3626e10007e7e544ac7d`。
该事实只关闭发布和异机复验，不代表 `miniserver` 已升级；W32 收口前 current 仍是上述
v3.6.5 deployment。

`TODO-0012` 已于 2026-07-28 完成：production-validation Redis 已实际启用 AOF `everysec` +
RDB，声明并验证不高于 60 秒的 RPO；加密 daily backup 已复制到异机 vault，至少两份独立
backup/restore/target volume 通过 leaderboard submit/top/rank 和 release SDK full-flow。仓库的
example policy、初始 activation decision 和单轮恢复 summary 仍故意保留
`formal_todo0012_claim=false`，用于阻止单个候选或单轮产物越权声明完成；它们不是当前目标机
是否已激活的事实源，最终任务状态以 `docs/todos/tasks.json` 的聚合验收为准。

`TODO-0011` 的仓库实现和目标机激活均已推进：生产 Compose 形成 45 天 Prometheus、
node-exporter、cAdvisor、Redis persistence 和 Docker restart-count 指标契约；目标机已经形成
真实 Alertmanager receiver 的 firing/resolved 投递证据、完整 host/container/application/Redis
指标样本和异机 bootstrap 包复验。生产预检继续拒绝默认 Grafana 凭据、占位 receiver、过期或
单边投递声明；ledger 可生成 create-only daily/weekly/incident/final record 及带 `SHA256SUMS` 的
异机包。`TODO-0011` 仍保持 open，剩余边界是自然 daily/完整 ISO weekly 周期与最终报告覆盖，
不能把已激活的采集和 receiver 错写成整段长期观测已经完成。W31 的历史 gap 已被不可变保留；
最早可用于关闭任务的完整干净周期是 W32（`2026-08-03T00:00:00Z` 至
`2026-08-10T00:00:00Z`），周报在 `2026-08-10T00:45:00Z` 自然运行。之后仍须更新
firing/resolved 回执、生成 final ledger，并在异机验证新 evidence package。

`TODO-0013` 的 v3.6.2 诊断窗口完整记录 4,320 个分钟，但五次真实 gateway-to-backend
timeout 使结果为 FAIL；原始样本和 incident 均保留。#73 修复进入 v3.6.5 后，Mac 外部主机
通过 Tailscale 真实路径完成权威窗口
`[2026-08-01T19:38:00Z, 2026-08-04T19:38:00Z)`：4,320 个预期分钟全部记录且全部
成功，coverage、recorded success、inclusive availability 均为 100%，没有 gap、duplicate、
invalid sample 或 candidate/endpoint 漂移。固定结束聚合
`72h-20260804T1938Z.json` 的 SHA-256 是
`89071385fba47501ada771a2e02109b30f1228fdb036151ae70fd1fae17ff63f`，绑定 v3.6.5
deployment、commit `94f0c5d12d29839bed1598c17f661550c28d84f0` 和 runtime digest
`b6d0c8554223e78d81c9da314256d31883b91fe7aacd8a7f9504840db524c487`。该证据关闭
`TODO-0013` 的外部 canary 能力任务，但不是 `TODO-0016` Day 0；v3.6.5 Battle RSS
线性增长仍要求新的不可变 runtime 候选和独立正式窗口。

该 v3.6.5 诊断窗口同时确认 Battle backend working set 约以 0.48–0.50 MiB/h 线性增长。
Issue #78 的 RCA 定位到完成 battle 的 runtime/per-battle/replay 状态没有完整释放；修复已由
PR #79 合入主线，并在 aoi Linux x64 runner 通过完整 CI 与 ASan/UBSan/LSan 资源专项。
v3.6.5 因此不得成为 `TODO-0016` Day 0。替代候选 v3.6.6 已完成 Release 和独立资产
复验；W32 收口和受控生产 upgrade 完成前，deployment/configuration identity 继续留空，
不得把发布完成写成生产已激活。

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
- C++ SDK、稳定 C ABI、Python ctypes wrapper、C# P/Invoke wrapper 和 4.2.1 Linux x64 分发资产。
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

1. 已在服务器不编译源码的前提下，以不可变 release asset 完成幂等安装、升级和回滚。
2. 完成 W32 自然 metrics/ledger 周期；v3.6.5 外部 SDK canary 诊断窗口已完成并通过。
3. 已完成异机备份、Redis/host/runtime 恢复演练，并满足 5/10 分钟 RTO 边界。
4. 收口 required checks、review、CODEOWNERS、SECURITY 和 Action SHA pinning。
5. 关闭 `TODO-0011`/`TODO-0013` 后执行独立的 72 小时上线预演，再冻结单一
   tag/SHA/digest 连续运行至少 30 天。

30 天验证要求连续时长不少于 `2,592,000s`，availability/canary success 与证据覆盖率
均不低于 99.9%；runtime 变化会重置 Day 0。完整口径见
[single-node-enterprise-validation-plan.md](single-node-enterprise-validation-plan.md)。

## 当前阻断和非目标

- P3 数据恢复与 P4 可观测性仍由
  `scripts/gates/production/verify_data_recovery_gate.py` 和
  `scripts/gates/production/verify_observability_gate.py` 作为当前发布能力验证。
- `admin_service` 仅属于 `legacy-v1 / demo-only` 历史管理面，不进入默认 gate，不能据此
  声明当前 v2 主线提供正式 admin 控制面。

- 不把 v3.6.2 的三平台发布解释为多节点 HA、任意规模容量或所有云环境支持。
- 不因 PoC 完整而把 gRPC 升级为默认传输。
- 不在当前运营主线中扩大业务 demo、公共协议或 SDK 表面积。
- 不把不同 SHA、不同 runner 或不同平台的证据拼接为同一冻结候选。
- PyPI/NuGet.org trusted publishing 和 Apple notarization 仍是独立工作，不由 GitHub
  Release 资产自动解除。
- 性能优化必须由长期指标或 incident 驱动，并保留 RCA、前后基线、回归和回滚方案。

## 当前验证入口

```bash
python3.12 scripts/gates/governance/check_current_docs_install.py
python3.12 scripts/check_mainline_readiness.py
python3.12 scripts/gates/governance/check_config_source_layout.py
python3.12 scripts/gates/transport/check_transport_config_governance.py
python3.12 scripts/gates/governance/check_next_minor_decisions.py
python3.12 scripts/verify_release_candidate.py \
  --skip-release-baseline --soak-profile smoke
```

开发构建和分层测试见 [ONBOARDING.md](ONBOARDING.md)。生产证据入口、前置依赖和
fixed-runner 操作见 [release-governance.md](release-governance.md)与
[fixed-runner-playbook.md](fixed-runner-playbook.md)。
