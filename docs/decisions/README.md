# v3.6 决策索引

更新时间：2026-07-21

本目录保存当前有效的下一次 minor 决策。历史 ADR 位于
`docs/archive/process/`，仅用于追溯，不代表当前默认行为。

## 阶段边界

- 目标版本是 `v3.6.0`，当前 `CMakeLists.txt` 和已发布 `v3.5.3` tag 不因
  ADR 被修改。
- 下表中的 ADR 状态均为 `accepted_for_implementation`，表示可以按已冻结
  边界开始开发，不表示能力已经完成、已经发布或可以默认启用。
- 所有能力的默认激活状态均为 `blocked_until_gates_pass`。实现只有在 ADR
  指定的兼容、迁移、发布资产和验证矩阵全部通过后，才能单独评审是否进入
  默认生产链路。
- gRPC 继续保持 `experimental_only` / `defer_default_transport`。Raft 采用
  protobuf schema 不等于把 gRPC 升格为默认传输。
- `v3.5.x` 维护线继续只接受其维护计划允许的补丁，不回灌本目录决策产生的
  新功能或发布承诺。

## 决策清单

| 实现顺序 | 决策 | 状态 | 默认激活 | 主要依赖 |
|---|---|---|---|---|
| 1 | [Raft schema 与滚动迁移](v3.6-raft-wire-schema.md) | `accepted_for_implementation` | `blocked_until_gates_pass` | protobuf runtime 与 gRPC 解耦、混合版本集群门禁 |
| 2 | [外部身份 JWKS / 多 kid](v3.6-identity-jwks.md) | `accepted_for_implementation` | `blocked_until_gates_pass` | 外部 IdP、后台有界刷新与安全配置门禁 |
| 3 | [macOS ARM64 支持](v3.6-macos-arm64.md) | `accepted_for_implementation` | `blocked_until_gates_pass` | Conan ARM64 图、独立 smoke runner |
| 4 | [Python wheel / NuGet 分发](v3.6-sdk-distribution.md) | `accepted_for_implementation` | `blocked_until_gates_pass` | 平台/RID 矩阵、clean install 与 full-flow |
| 5 | [独立调试符号资产](v3.6-debug-symbols.md) | `accepted_for_implementation` | `blocked_until_gates_pass` | Release 资产、SBOM/attestation 与栈回溯验证 |

Raft 排在首位是因为当前磁盘状态、state-machine command 和内部 RPC 是三套
不同的隐式 JSON 契约；损坏或未来版本数据不能再被当作空状态继续启动。身份
紧随其后，先补静态多 `kid` 的无中断轮换，再引入在线 JWKS。平台决策先于
正式跨语言包，是为了避免 wheel/NuGet 在没有 RID 支持矩阵时形成过度承诺。

## 机器事实源

`v3.6-decision-manifest.json` 是本目录的机器可检索引。治理门禁必须同时检查：

- 五个决策均存在，目标 minor 与文档一致；
- 状态为 `accepted_for_implementation`；
- 默认激活保持 `blocked_until_gates_pass`；
- 每项都声明兼容窗口、发布资产、验证门禁和依赖；
- 文档明确迁移、回滚与不支持边界。

自动门禁只能证明决策材料完整，不能代替安全、发布或架构人工评审。
