# v3.5.3 发布后主线执行计划

更新时间：2026-07-22

## 阶段目标

`v3.5.3` 已在最终 SHA `b9c348b4b58fdeeffa9d82ff87a67ed781a96b78` 完成 Release、连续 8h、生产证据汇总、tag publish 和线上资产独立验签。当前阶段不移动已发布 tag，也不直接扩大业务或协议面；先修正容量证据的资源隔离与统计口径，再根据可归因数据决定运行时优化和下一 minor 的范围。

## 优先级与验收标准

| 优先级 | 工作项 | 交付物 | 完成标准 |
|---|---|---|---|
| P0 | Service/load generator CPU 隔离 | 独立 CPU set、固定 loadgen I/O 线程数、逐进程 affinity 证据 | service 与 loadgen CPU set 均来自 Linux 实际 affinity，集合不重叠；summary 记录两侧约束和线程数 |
| P0 | 逐轮资源差值纠偏 | 每轮 before/after 快照和 CPU/RSS/fd/thread delta | CPU 使用率只使用相邻快照与本轮时长计算；单进程结果不超过分配 CPU 的物理上限与允许误差 |
| P1 | 可归因的 1/2/4 service CPU 轴 | 完整饱和曲线选点后的 fixed-case 三轮 summary | workload identity 一致；真实 lifecycle 完整且 0 rejected/failed；P99、吞吐、CPU 和扩展效率可比较 |
| P1 | 单核 `io_cores` 单变量实验 | 同一 fixed-case 的 `io_cores=1/2/4` 三轮对照 | loadgen 隔离且其它参数固定；只有聚合决策支持且人工评审通过后才调整默认或部署配置 |
| P1 | 长任务中断取证 | 原子 checkpoint、取消 summary、完整子进程组清理 | SIGINT/SIGTERM 可记录当前步骤、连续时长、完成轮数和资源样本；中断段不得累计成 2h/8h 通过 |
| P1 | SBOM 语义质量 | 非占位文件 hash、依赖组件/version 清单和发布前/发布后门禁 | 禁止全零 checksum；运行时资产与 Conan 依赖可追溯；签名验证与内容质量分别阻断 |
| P2 | 下一 minor ADR | 身份、SDK 分发、Raft schema、平台/符号包决策 | 五项 ADR 和 P0-P6 仓库内实现已完成；fixed-runner、真实外部依赖和不可变发布资产证据尚待补齐，未满足各 ADR 退出条件前不进入默认生产链路 |

## 执行顺序

1. 将现有 `--cpu-set` 明确定义为 service CPU set，load generator 使用独立且不重叠的 CPU set，并固定其 I/O 线程数。
2. 修复资源分析的累计基线问题，按每轮相邻进程快照计算 CPU 时间差；聚合器拒绝缺少隔离证据的新矩阵。
3. 先在 fixed runner 运行完整 closed-loop 饱和曲线，以 Gateway CPU 达到阈值且 loadgen 保有余量的膝点作为固定 case，再分别执行 service CPU 与单核 `io_cores=1/2/4` 三轮单变量实验。
4. 根据聚合结果描述 1/2/4 service CPU 的实测扩展边界；不得用旧的整栈共核矩阵推断 Gateway 独占 CPU 扩展率，也不得为通过而放宽 P99 门槛或自动修改默认值。
5. 增加长稳取消与 checkpoint 契约。checkpoint 只保留诊断事实，最终 long/overnight 仍要求单一 run ID 连续运行不少于 7200/28800 秒。
6. 修正 SBOM 生成源并增加语义门禁，避免“文件已签名”被误解为“组件清单完整”。
7. 五项下一 minor ADR 与 P0-P6 仓库内实现已完成并登记到 `docs/decisions/v3.6-decision-manifest.json` 和 `docs/v3.6-implementation-status.md`；由治理门禁保持默认阻断和事实口径。
8. 下一步按 exact SHA 生成 Linux release/SDK/debug-symbol、macOS ARM64 和真实 JWKS rotation 证据，再冻结并独立复验 GitHub Release 资产。Raft protobuf writer 只允许显式配置且全 peer capability 成立时启用；任何能力撤销都必须回落 legacy writer。

## 当前事实

- `v3.5.3` tag Release run `29708970775` 成功，GitHub Release 包含 Linux x64 tarball、SPDX SBOM 和 `SHA256SUMS.txt`。
- 同 SHA 8h run `29711044558` 连续执行 `28801.652s`，完成 3207 轮；960 个资源样本覆盖 `28801.542s`，最大间隔 `30.073s`，FD 起止均为 4。
- 线上资产验证 run `29740136895` 的 checksum、archive layout、离线 runtime consumer、provenance attestation 和 SBOM attestation 全部通过。
- 独立验签只证明已发布 SBOM 未被替换；`v3.5.3` SPDX 内容仍只有顶层 package 和 13 个 bin/lib 文件，文件 SHA1 为占位全零且未列出 Conan 第三方组件。后续发布必须增加独立的 SBOM semantic gate。
- 发布后分支已新增确定性 SBOM enrich/verify：覆盖发行包全部普通文件 SHA-256，并从 Conan lockfile 补入 9 个运行时依赖及 PURL/recipe revision/`DEPENDS_ON`；release 与 published asset verification 在 attestation 前后分别执行同一语义门禁。该修复只影响后续发布，不重写 `v3.5.3` 资产。
- 已发布 CPU 矩阵的延迟、吞吐和失败数是有效的整栈端到端事实，但 collector 在启动子进程前约束自身 affinity，使服务端与 load generator 共用 CPU set；它不能作为 Gateway 独占 1/2/4 vCPU 的扩展结论。
- 旧资源分析使用实验初始 CPU 时间反复计算每轮差值，出现单核进程超过 100% 的不可解释结果；下一矩阵必须使用相邻快照。
- 纠偏提交 `29fc4cff` 的 AOI focused run `29742852766` 验证了 service CPU `0` 与 loadgen CPU `4-7` 的进程级隔离，且实际发出请求的延迟、吞吐和资源数据可用于诊断；但它仍使用旧客户端 lifecycle 口径，不能证明命名中的 5K/10K 客户端全部进入稳态，也不能据此调整 `io_cores`。
- 候选 `375910f3` 的 AOI runs `29790072882`、`29791850363`、`29793036782` 确实记录了 service/loadgen 隔离 affinity 和资源差值，但旧 pressure client 没有证明所有目标客户端已真实启动、TCP 连接并认证后进入稳态；被计划结束的未启动客户端也会进入旧推导计数。因此旧聚合的 `evidence_complete=true` 已撤销，数据只能用于诊断实际发出流量相对平台期，不能证明 5K/10K 并发或 1/2/4 CPU 支持范围。
- 发布后 runtime 候选 `37897e8f3e2e4d231a1e9736e06709907cfee11b` 的主线 CI run `29822268701` 已成功。完整曲线 run `29822268782` 在 AOI fixed runner 上形成 6 点、每点三轮的 18/18 有效证据，并以 2,000 客户端、10ms 间隔的 `echo-sat-c2000-i10-60s` 作为轴比较选点；其中 200K 是配置请求率上限，不是客户端数或生产容量承诺。
- Service CPU 轴 runs `29823733478` / `29823736393` / `29823739153` 的 1/2/4 CPU 三轮证据完整通过，响应率中位数为 145,292.20 / 195,749.49 / 198,267.39 ops/s，结论为 `partial_cpu_scaling`。`io_cores` 轴 runs `29823742465` / `29823745289` / `29823733478` 的 1/2/4 三轮证据也完整通过，响应率中位数为 132,338.66 / 142,426.29 / 145,292.20 ops/s，结论为 `no_material_io_core_gain`。两个聚合器均明确 `automatic_default_change=false`，当前不调整线程、backend pool 或部署规格。
- OTel run `29823748288` 的 fresh Gateway/Battle Backend off/on 各三轮对照通过：吞吐中位数变化 `+0.103%`、P99 和 Gateway CPU 时间无变化、RSS `+46.695%`；46,069 个 routed/enqueued spans 与 45,824 个 exported/collector spans 加 245 buffered 精确对账，无 failed batch 或 collector error。
- 长任务取消取证已覆盖 `run_long_soak_capacity.py` 和 `verify_production_resilience_gate.py`：SIGINT/SIGTERM 分层 TERM/KILL 进程组、停止后续步骤、清除旧目标 summary，并在 `finally` 原子记录 `interrupted`、signal、当前步骤和完成步骤。发布后 SBOM 复验也新增 standalone JSON 与已验证 SPDX predicate 的 fail-closed 结构绑定。
- AOI runner 取消探针 run `29795945950`（`c33c50a1`）验证了 GitHub step 取消桥接：Linux parent-death signal 触发外层收尾，workflow 在上传前等待记录的 orchestrator PID 退出。顶层、P5 resilience 和 stability summary 均为 `interrupted=true`、`overall_pass=false`、`SIGTERM`；stability 保留 38.766 秒、9 轮、3 个资源样本和 partial resource summary，job cleanup 未再终止 orphan process。前序 run `29795322300` 只形成 fail-closed 初始 summary，证明仅有 Python handler 不足，保留为桥接修复前的失败诊断。

## 证据约束

- summary 必须记录候选 SHA、实际 checkout SHA、workflow/run、runner、构建类型、Conan lockfile、service/loadgen CPU set 和 loadgen I/O 线程数。
- service 与 loadgen affinity 必须在进程启动后回读；只记录请求参数不足以证明隔离生效。
- 每轮资源统计必须携带 before/after 原始计数和 elapsed time，使聚合结果可独立复算。
- 旧 schema 继续可读取用于历史归档；新的可归因 CPU matrix 必须拒绝旧 schema 或缺失隔离证据。
- 取消的 soak 必须保留 `interrupted=true` 的失败证据；不同 run、不同进程或不连续区间的时长不得合并。

## 当前边界

- 不移动或重建 `v3.5.3` tag，不用发布后文档或采集器提交冒充已发布 SHA 的证据。
- 不因一次轴实验自动调整 Gateway 线程默认值、backend pool 或 battle worker 数量；本轮聚合决策明确要求保留当前默认值并进入人工评审。
- gRPC 继续保持 `experimental_only` / `defer_default_transport`。`grpc-experimental.yml` 已有 `BOOST_BUILD_GRPC=ON` 的 fixed-runner run，但实验交付完整不等于默认传输具备升级收益。
- 不把多个中断 soak 片段拼接成连续长稳结论。
- Python/C# wheel/NuGet、JWKS/多 `kid`、Raft RPC/command codec、macOS ARM64 和独立 debug symbols 的仓库内候选实现已落地；Linux/macOS fixed-runner、真实 JWKS、NuGet consumer 和完整发布资产证据仍未交付。在 manifest 退出条件完成前继续保持默认阻断。

## 阶段退出条件

P0/P1 的六项证据工程已经在运行时候选 `37897e8` 上完成。下一 minor 的五项 ADR 与 P0-P6 仓库内候选实现也已完成，但这不代表默认激活或发布资产已经交付。下一步只收集 `docs/v3.6-implementation-status.md` 列出的同 SHA 外部证据；任何默认链路变化仍必须等待对应退出条件。既有 P0/P1 容量证据的事实锚点始终是 `37897e8`，不能用后续实现提交或 `v3.5.3` tag 代替。
