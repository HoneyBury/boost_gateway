# 生产候选实测与发布硬化规划

更新时间：2026-05-17

本文档是 P0-P6 生产稳定化与交付闭环完成后的下一阶段规划。当前阶段不继续横向扩展功能模块，重点是把已有 gate 和文档转化为持续、可比较、可发布的生产候选证据。

## 阶段目标

- 让固定 runner 周期性沉淀真实依赖、长稳和容量数据。
- 把 2h/8h soak、5K/10K 连接、Kubernetes rollout/rollback、runtime observability 和 SDK 多语言接入从“可执行入口”推进到“可比较事实”。
- 在 release 前形成一份可审计的生产候选包：提交、镜像 tag、部署方式、性能基线、P5/P6 summary、监控截图/summary、回滚记录和残余风险。

## H0：固定 Runner 常态化

目标：让 P5/P6 不再依赖人工临时执行。

任务：

- 配置并标记 `production-evidence`、`production-resilience`、`redis-live`、`operator-kind`、`release-baseline`、`observability` runner。
- 每周至少运行一次 `production-resilience.yml` 和 `production-evidence.yml`。
- 归档 `production-resilience-summary.json`、`production-evidence-summary.json`、`p5-*-summary.json`、`p6-*-summary.json`。

验收：

- 最近三次固定 runner 执行均可追溯。
- preflight 失败和业务失败能通过 `failed_category` / `failed_step` 清晰区分。

## H1：长稳与资源曲线

目标：补齐生产候选最需要的长时间运行事实。

任务：

- 执行 2h / 8h soak，记录 RSS、fd、线程数、CPU、错误率、P50/P90/P99。
- 将结果写入 `runtime/validation/long-soak-summary.json` 和 `runtime/perf/long-soak/**`。
- 更新 `docs/performance-baseline.md` 的长稳章节。

验收：

- 2h soak 无 fd 泄漏，RSS 增长可解释。
- 8h soak 至少完成一次并记录残余风险。

## H2：容量边界与退化阈值

目标：把 5K/10K 连接和 battle 高负载从“容量探索”变成可复测数据。

任务：

- 固定机器运行 capacity profile，覆盖 echo 1K/5K/10K 和 battle 100/500。
- 对比 backend pool、OTel on/off、Redis on/off 的成本。
- 明确 release 阻断阈值和 capacity 仅作边界探索时的标记规则。

验收：

- 至少三轮 baseline 通过。
- capacity 失败时能说明瓶颈、错误类型和下一步优化方向。

## H3：Kubernetes 发布演练

目标：让 Kubernetes 路径具备真实生产发布和回滚证据。

任务：

- 在 kind 或固定测试集群运行 rollout/rollback/probe 演练。
- 补齐 PDB、HPA、requests/limits、镜像 tag 和 sample CR 删除路径证据。
- 扩展 Operator kind smoke，覆盖 rollout 状态、组件健康、回滚和删除。

验收：

- `production-resilience.yml --include-operator-kind` 连续通过。
- 回滚 runbook 能按步骤恢复服务。

## H4：观测闭环增强

目标：减少“能看到错误但看不到延迟和根因”的盲区。

任务：

- 增加 route latency histogram/summary 或明确替代采集方案。
- 评估后端指标采集方式：HTTP management、sidecar exporter 或文件指标。
- 在固定观测 runner 上启用 runtime HTTP 和外部 OTel collector 长稳。

验收：

- Prometheus/Grafana 能展示关键请求量、错误、超时和延迟。
- OTel collector 异常不影响 gateway 主流程。

## H5：SDK 企业接入包

目标：降低后续客户端开发和企业接入成本。

任务：

- 补 Python/C# 真实业务示例，覆盖 login、room、ready、battle、heartbeat、disconnect。
- 输出 SDK 与 gateway 版本兼容矩阵。
- 标准化错误码、trace id、重连建议和回调线程限制说明。

验收：

- C++/Python/C# 最小业务闭环都能在真实 gateway 上跑通。
- 客户端接入文档能独立指导开发者完成连接、登录、心跳、断线恢复和错误排查。

## 推荐执行顺序

1. H0：先把固定 runner 常态化，避免后续数据不可复现。
2. H1：再跑长稳，确认没有明显资源泄漏。
3. H2：随后做容量边界，找出 5K/10K 的真实瓶颈。
4. H3：并行推进 Kubernetes 发布演练。
5. H4：补观测盲区，尤其是延迟和后端维度。
6. H5：最后打磨 SDK 企业接入包，服务客户端开发。
