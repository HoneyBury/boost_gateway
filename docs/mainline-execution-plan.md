# v3.5.3 发布后主线执行计划

更新时间：2026-07-20

## 阶段目标

`v3.5.3` 已在最终 SHA `b9c348b4b58fdeeffa9d82ff87a67ed781a96b78` 完成 Release、连续 8h、生产证据汇总、tag publish 和线上资产独立验签。当前阶段不移动已发布 tag，也不直接扩大业务或协议面；先修正容量证据的资源隔离与统计口径，再根据可归因数据决定运行时优化和下一 minor 的范围。

## 优先级与验收标准

| 优先级 | 工作项 | 交付物 | 完成标准 |
|---|---|---|---|
| P0 | Service/load generator CPU 隔离 | 独立 CPU set、固定 loadgen I/O 线程数、逐进程 affinity 证据 | service 与 loadgen CPU set 均来自 Linux 实际 affinity，集合不重叠；summary 记录两侧约束和线程数 |
| P0 | 逐轮资源差值纠偏 | 每轮 before/after 快照和 CPU/RSS/fd/thread delta | CPU 使用率只使用相邻快照与本轮时长计算；单进程结果不超过分配 CPU 的物理上限与允许误差 |
| P1 | 可归因的 1/2/4 CPU 矩阵 | 隔离后的 echo/battle/business 三轮 summary | workload identity 一致；0 rejected/failed；P99、吞吐、CPU、RSS 和变异度可比较 |
| P1 | 单核 `io_cores` 单变量实验 | `io_cores=1/2/4` 的 echo-5K/10K 对照 | loadgen 隔离且其它参数固定；只有三轮稳定结果证明收益后才调整默认或部署配置 |
| P1 | 长任务中断取证 | 原子 checkpoint、取消 summary、完整子进程组清理 | SIGINT/SIGTERM 可记录当前步骤、连续时长、完成轮数和资源样本；中断段不得累计成 2h/8h 通过 |
| P1 | SBOM 语义质量 | 非占位文件 hash、依赖组件/version 清单和发布前/发布后门禁 | 禁止全零 checksum；运行时资产与 Conan 依赖可追溯；签名验证与内容质量分别阻断 |
| P2 | 下一 minor ADR | 身份、SDK 分发、Raft schema、平台/符号包决策 | 每项明确兼容策略、迁移窗口、发布资产和验证矩阵；未通过 ADR 不进入默认生产链路 |

## 执行顺序

1. 将现有 `--cpu-set` 明确定义为 service CPU set，load generator 使用独立且不重叠的 CPU set，并固定其 I/O 线程数。
2. 修复资源分析的累计基线问题，按每轮相邻进程快照计算 CPU 时间差；聚合器拒绝缺少隔离证据的新矩阵。
3. 先在 fixed runner 只运行 echo-5K/10K 三轮诊断，再执行单核 `io_cores=1/2/4` 单变量实验。
4. 根据纠偏结果定义 1/2/4 vCPU 支持范围；不得用旧的整栈共核矩阵推断 Gateway 独占 CPU 扩展率，也不得为通过而放宽 P99 门槛。
5. 增加长稳取消与 checkpoint 契约。checkpoint 只保留诊断事实，最终 long/overnight 仍要求单一 run ID 连续运行不少于 7200/28800 秒。
6. 修正 SBOM 生成源并增加语义门禁，避免“文件已签名”被误解为“组件清单完整”。
7. 完成以上证据工程后，再为 JWKS/多 `kid`、跨语言 SDK 正式分发、内部 Raft schema 迁移和 macOS ARM64 建立下一 minor ADR。

## 当前事实

- `v3.5.3` tag Release run `29708970775` 成功，GitHub Release 包含 Linux x64 tarball、SPDX SBOM 和 `SHA256SUMS.txt`。
- 同 SHA 8h run `29711044558` 连续执行 `28801.652s`，完成 3207 轮；960 个资源样本覆盖 `28801.542s`，最大间隔 `30.073s`，FD 起止均为 4。
- 线上资产验证 run `29740136895` 的 checksum、archive layout、离线 runtime consumer、provenance attestation 和 SBOM attestation 全部通过。
- 独立验签只证明已发布 SBOM 未被替换；`v3.5.3` SPDX 内容仍只有顶层 package 和 13 个 bin/lib 文件，文件 SHA1 为占位全零且未列出 Conan 第三方组件。后续发布必须增加独立的 SBOM semantic gate。
- 已发布 CPU 矩阵的延迟、吞吐和失败数是有效的整栈端到端事实，但 collector 在启动子进程前约束自身 affinity，使服务端与 load generator 共用 CPU set；它不能作为 Gateway 独占 1/2/4 vCPU 的扩展结论。
- 旧资源分析使用实验初始 CPU 时间反复计算每轮差值，出现单核进程超过 100% 的不可解释结果；下一矩阵必须使用相邻快照。

## 证据约束

- summary 必须记录候选 SHA、实际 checkout SHA、workflow/run、runner、构建类型、Conan lockfile、service/loadgen CPU set 和 loadgen I/O 线程数。
- service 与 loadgen affinity 必须在进程启动后回读；只记录请求参数不足以证明隔离生效。
- 每轮资源统计必须携带 before/after 原始计数和 elapsed time，使聚合结果可独立复算。
- 旧 schema 继续可读取用于历史归档；新的可归因 CPU matrix 必须拒绝旧 schema 或缺失隔离证据。
- 取消的 soak 必须保留 `interrupted=true` 的失败证据；不同 run、不同进程或不连续区间的时长不得合并。

## 当前边界

- 不移动或重建 `v3.5.3` tag，不用发布后文档或采集器提交冒充已发布 SHA 的证据。
- 不在完成 loadgen 隔离前调整 Gateway 线程默认值、backend pool 或 battle worker 数量。
- gRPC 继续保持 `experimental_only` / `defer_default_transport`。`grpc-experimental.yml` 已有 `BOOST_BUILD_GRPC=ON` 的 fixed-runner run，但实验交付完整不等于默认传输具备升级收益。
- 不把多个中断 soak 片段拼接成连续长稳结论。
- Python/C# wheel/NuGet、JWKS/多 `kid`、Raft raw JSON 迁移、macOS ARM64 和独立 debug symbols 先进入下一 minor ADR。

## 阶段退出条件

本阶段只有同时满足以下条件才可结束：service/loadgen affinity 隔离可审计；逐轮 CPU 资源差值可复算；隔离后的 1/2/4 CPU 矩阵完成；单核 `io_cores` 结论来自固定变量的三轮实验；长稳取消不会遗留子进程且能生成部分失败证据；下一 minor 的功能范围由 ADR 而不是实现先行决定。
