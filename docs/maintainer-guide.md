# 维护者指南：代码、脚本与 Workflow 治理

更新时间：2026-08-11

本文面向需要修改跨模块代码、脚本、CI/CD 或生产门禁的维护者。当前产品事实以
[当前状态](current-state.md)为准，构建步骤以[开发者入门](ONBOARDING.md)为准；本文负责
解释“去哪里改、为什么这样分层、改完验证什么”。

## 当前项目地图

BoostGateway 是 C++20 实时服务框架。默认链路是多语言 SDK 经 length-prefixed TCP 进入
Gateway，再通过 `BackendEnvelope` 路由到 Login、Room、Battle、Matchmaking 和
Leaderboard。主要维护边界如下：

| 领域 | 权威代码/配置 | 变更时优先验证 |
|---|---|---|
| Gateway、Actor、路由和熔断 | `include/v2/`、`src/v2/` | unit、integration、e2e |
| 协议、Raft、Redis 和持久化演进 | `include/v3/`、`src/v3/`、`proto/v3/` | unit、specialized E2E、兼容性门禁 |
| 五个 backend 和业务闭环 | `examples/v2_*_backend/`、`src/v2/` | integration、e2e、SDK full-flow |
| C++/C ABI/Python/C# SDK | `sdk/` | sdk、consumer、distribution |
| 生产配置 | `env/` | config governance、Compose/K8s 专项 |
| 发布和生产证据 | `scripts/gates/`、`scripts/producers/`、`.github/workflows/` | 对应 fixed runner；本地 smoke 不能替代 |

发布线当前是 v3.6.6 / SDK 4.2.1，主任务已从扩展协议和 demo 转向 Ubuntu 24.04 x64
单节点部署、观测、备份恢复、72 小时预演和 30 天不可变验证。新增能力前应先确认它是否
服务于这条主线。

## 为什么工具面会膨胀

截至本页更新时，仓库有 157 个受版本控制的 Python 脚本、18 个 workflow，脚本约
57,000 行、workflow 约 5,700 行。Git 历史显示，自 2026-06-01 起有 223 个提交触及
`scripts/`、149 个提交触及 workflow；这些路径累计约增加 56,000 行、删除 13,500 行。

增长主要来自四类真实需求：多平台证据不可互换；发布、恢复、TLS、性能和长稳需要独立
fail-closed 门禁；兼容入口不能突然删除；生产证据需要详细 provenance 和 summary。它们
并非都能合并成一个脚本，但当前成本也很明确：公共入口难发现、长脚本职责过多、workflow
复制 Conan/build/upload 片段、catalog gate 依赖大量文本契约，修改时容易发生多处同步。

## 目标分层

```text
维护者日常入口        scripts/dev.py
                           |
稳定兼容入口          scripts/*.py
                           |
规范实现       gates / producers / tools / lib
                           |
自动化编排       .github/actions / workflows
                           |
事实与契约       docs/*.json / config / env
```

- `scripts/dev.py` 只做开发机任务发现和组合，不复制门禁业务逻辑，也不进入发布证据链。
- 根目录 public entrypoint 是对人和自动化稳定的兼容面；数量应只减不增，新增前必须说明
  为什么现有聚合入口不能承载。
- `gates/` 只判定通过/失败；`producers/` 产生测量或证据；`tools/` 执行单一操作；`lib/`
  放无 CLI 的共享实现。复用应下沉到 `lib/`，不要让一个脚本导入另一个 CLI 的 `main()`。
- workflow 负责 runner、权限、cache、artifact 和触发条件；业务判定留在脚本。重复三次以上
  的 setup/build/upload 片段应优先提取到本地 composite action。
- JSON 是 inventory、runner、平台和 evidence 的机器事实源；Markdown 解释意图和操作路径，
  不再维护第二份可执行清单。

## 日常入口

```bash
# 检查 Python 3.12、Git/CMake/Ninja 和现有 build tree 的工具配对
python3.12 scripts/dev.py doctor --build-dir build/contributor-debug

# 运行有界的文档、脚本、workflow、仓库和 TODO 治理及其契约单测
python3.12 scripts/dev.py check

# 按 CTest label 运行；会使用 build tree 中记录的 CTest，避免 IDE/PATH 版本错配
python3.12 scripts/dev.py test unit --build-dir build/contributor-debug --verbose

# 增量构建 unit/demo，运行 unit 和进程内业务闭环
python3.12 scripts/dev.py smoke --build-dir build/contributor-debug

# 完整 Python 脚本测试（首次按 ONBOARDING 创建 .venv/dev）
.venv/dev/bin/python -m pytest -q tests/python
```

固定 runner、发布和运维命令仍直接使用 `scripts/README.md` 与 runbook 中的明确入口，避免
开发者 facade 隐藏证据参数。

## 修改脚本的规则

1. 先判断角色：判断结果放 `gates/`，采集放 `producers/`，单一操作放 `tools/`，共享逻辑
   放 `lib/`。对外入口才放 `scripts/` 根目录。
2. 在 `docs/script-inventory.json` 精确登记一次。移动现有入口时保留薄 shim，并填写
   `canonical`；所有活动引用清零并跨过兼容周期后才能删除。
3. CLI 使用 `argparse`，有界超时，失败返回非零；证据 summary 使用 `summary_version: 2`、
   `overall_pass`、`passed`、`failed_category`、`failed_step` 和 `artifacts`。
4. 新参数必须补单测，并运行 workflow→Python CLI contract gate。不要在 workflow 中
   拼装脚本尚未声明的参数。
5. 运行期产物只写 `runtime/` 或显式临时目录，不写回 `scripts/`、`docs/` 或源目录。

## 修改 Workflow 的规则

1. 先确认能否扩展现有 workflow；新增 workflow 会同步增加 runner matrix、平台边界、文档
   和 catalog 维护成本。
2. 权限保持最小；第三方 Action 必须命中 reviewed allowlist、固定完整 commit SHA，并保留
   同行 release tag 注释。
3. PR workflow 必须 hosted、有超时且可取消旧提交；容量、长稳和生产证据必须使用已准入
   fixed runner，二者不能互相降级。
4. Conan、平台和 artifact identity 通过现有 composite action 与 runner matrix 解析，不在
   job 内新增另一套默认值。
5. 修改后至少运行 `scripts/dev.py check`。涉及实际 runner、secret、ruleset 或 artifact
   下载的结论还必须在 GitHub 外部状态中验证，本地检查不能证明它们生效。

## 验证分层

| 变更 | 最低本地验证 | 仍需外部验证 |
|---|---|---|
| 文档、清单、治理脚本 | `dev.py check` + `.venv/dev/bin/python -m pytest -q tests/python` | GitHub ruleset/secret 等外部状态 |
| C++ 纯逻辑 | `dev.py test unit` | 无，除非平台相关 |
| Gateway/backend/协议 | unit + integration；公共链路再加 e2e/sdk | 协议兼容或目标平台证据 |
| 脚本测试入口 | Python 单测 + `dev.py check` + 至少一次真实子命令 | fixed-runner 参数需目标 runner |
| workflow/composite action | `dev.py check` | `workflow_dispatch` rehearsal |
| 性能、长稳、恢复、TLS、发布 | 本地只做 contract/smoke | 对应准入 runner 或预生产环境 |

提交说明和 PR 必须记录实际运行的命令和结果，不用“全部测试通过”代替可复查证据。

## 渐进治理路线

本轮先解决入口和可验证性：增加开发者 facade；测试入口绑定配置时使用的 CTest；给脚本
inventory 和 workflow CLI contract 补回归测试；同步 Python 3.12 和当前版本事实。

后续按风险从低到高推进：

1. 已将 workflow 名称、触发类型、权限、runner 类别和生命周期提取到
   `docs/workflow-catalog.json`；检查代码消费清单并保留安全关键的语义断言。
2. 已把六个 workflow 重复的 summary path 解析、去重和 Step Summary 渲染迁入
   `.github/actions/render-validation-summary`；后续 setup/build/artifact 片段继续遵守三处提取规则。
3. 已从三个优先超大脚本按子域拆出 `perf_statistics`、`release_lifecycle_io` 和
   `recovery_evidence`，保留原 CLI 与可导入符号，并用独立单测覆盖统计、持久化和证据结构。
   后续继续采用“一个职责、原入口兼容、先有回归测试”的小步拆分，不做一次性重写。
4. 已为全部 public entrypoint 增加 owner、支持级别、运行环境、典型时长、外部副作用和退役
   条件，并由 script inventory gate 校验完整性与枚举漂移。每个版本继续评审未引用 shim、
   废弃 workflow input 和重复 summary renderer。
5. 持续观测四项指标：public entrypoint 数量、workflow 重复片段、无单测 CLI 数量、脚本与
   workflow 的变更失败率。治理目标是降低修改耦合，而不是单纯追求文件数更少。
