# 脚本与 Workflow 减量轮次记录

状态：已归档。当前冻结线和下一步以
[主动减量计划](../../tooling-reduction-plan.md)为准。
收缩前的完整计划由 Git 提交 `c2fe7e7` 保留，避免在当前文档树中复制历史正文。

## 第一轮

- public entrypoint 保持 23，canonical CLI 保持 127。
- 超过 800 行脚本从 15 降到 10，超过 500 行从 31 降到 20。
- 跨 CLI 导入从 15 降到 8，Workflow 依赖边从 122 降到 103。
- 重复 Workflow 片段从 11 降到 5。
- 发布部署、备份恢复和治理聚合形成 CLI-free contract，原入口兼容。

## 第二轮

- 超过 800 行脚本从 10 降到 8，跨 CLI 导入从 8 清零。
- `collect_v2_perf_baseline.py` 和 `verify_preprod_recovery_drill.py` 建立可录制边界。
- public/CLI/tool 保持 23/127/55，迁移 library 纳入评审基线。

## 第三轮

- workflow catalog、operations host 和 stability soak 分别收敛到受治理契约。
- 超过 800 行脚本从 8 降到 5，超过 500 行保持 20。
- library 保持 55，growth exception 清零。
- Windows 名义兼容从脚本面移除并冻结为 0。

## v3.6.7 同步

异机 observability evidence package verifier 曾以一个 reviewed library exception 进入；
后续治理已将其合回既有 evidence contract，library 恢复到 55，例外清零。
