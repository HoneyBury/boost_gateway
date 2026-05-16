# 当前项目事实源

更新时间：2026-05-16

本文档作为当前进度的入口事实源。版本号以 `CMakeLists.txt` 中的 `BoostAsioDemo VERSION 3.3.2` 为准；提交状态以 `git HEAD` 为准。

## 稳定能力

- v1.x：维护期能力已经收束，主要保留历史协议、业务边界、运行手册和发布记录。
- v2.x：当前主线。`ActorSystem`、gateway-only ingress、五个后端服务、`BackendEnvelope`、typed envelope adapter、服务健康检查、TTL/readiness、WriteBehind drain 统计与失败上报已经进入可验收状态。
- R4 契约门禁：`scripts/verify_r4_contract.py` 覆盖通信契约、后端恢复、typed envelope、proto schema、gateway-only ingress 和短架构基线入口。
- 稳定性门禁：`scripts/verify_stability_soak.py` 覆盖 I/O accept policy、WriteBehind drain/failure、backend timeout/recovery 和短架构基线，提供 `smoke`、`short`、`medium` soak profile。
- RC 总门禁：`scripts/verify_release_candidate.py` 汇总可靠性矩阵、R4 契约、稳定性 soak 和可选 Release baseline，并输出结构化 summary。

## 增量能力

- v3 proto/gRPC：schema 校验、CMake target 和 release checklist 已存在，当前定位为传输契约与构建入口，不作为默认生产链路。
- Redis/Raft/Operator：文档和部分实现入口已经存在，当前仍应按独立可靠性闭环推进，不扩大默认发布承诺。
- Release baseline：`scripts/collect_release_baseline.py` 是 Release profile 的性能基线入口，适合固定机器或 CI release runner 执行。

## 保留边界

- 长稳 2h/8h soak、10K 连接生产容量基线、跨节点 Redis/Raft/Operator E2E 和生产级鉴权加固仍属于后续稳定性专项。
- 默认 CI/release workflow 使用有界 smoke 门禁，避免长时间占用终端或 runner。
- 文档出现编码显示异常时，以 UTF-8 文件内容和 CI 校验结果为准，PowerShell 控制台乱码不代表文件编码错误。

## 下一步优先级

1. 在固定 Release runner 上执行 `scripts/collect_release_baseline.py`，沉淀可比较的 Release 性能基线。
2. 将 `scripts/verify_stability_soak.py --soak-profile short` 纳入夜间任务，保留 release/tag 任务的 smoke 门禁。
3. 为 Redis/Raft/Operator 分别建立最小 E2E 验收矩阵，避免混入默认 gateway 发布门禁。
4. 持续扩充 `docs/reliability-matrix.md`，每新增可靠性承诺必须绑定测试或脚本证据。
