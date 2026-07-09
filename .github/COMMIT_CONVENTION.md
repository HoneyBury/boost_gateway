# Git Commit Convention

所有提交到本仓库的代码必须遵循本规范，以便自动化生成 changelog、追溯变更原因和责任。

## 提交格式

```
<type>(<scope>): <短描述>

<可选正文：解释 WHY，而非 WHAT>
```

每行不超过 72 字符。正文和脚注可省略。

## Type（必填）

| Type | 含义 | 对应 changelog 分类 |
|------|------|-------------------|
| `feat` | 新功能或新能力 | Features |
| `fix` | 缺陷修复 | Bug Fixes |
| `refactor` | 重构，无外部行为变化 | Code Refactoring |
| `docs` | 文档变更 | Documentation |
| `test` | 测试新增或改进 | Tests |
| `perf` | 性能优化 | Performance |
| `chore` | 构建/CI/依赖/工具链变更 | Chores |
| `governance` | CI 门禁、验证脚本、治理规则变更 | Governance |
| `style` | 代码格式（clang-format、空白等） | Styles |
| `revert` | 回滚之前的提交 | Reverts |

## Scope（可选但推荐）

当前仓库的 scope 取值：

| Scope | 含义 |
|-------|------|
| `gateway` | gateway 服务 |
| `login` | login 后端 |
| `room` | room 后端 |
| `battle` | battle 后端 |
| `match` | matchmaking 后端 |
| `leaderboard` | leaderboard 后端 |
| `sdk` | 客户端 SDK（C++/C ABI/Python/C#） |
| `proto` | Protobuf schema 或生成代码 |
| `envelope` | typed envelope / raw JSON 兼容层 |
| `cluster` | Raft 共识、集群路由 |
| `rt` | 运行时（actor、session、runtime） |
| `ci` | GitHub Actions workflow |
| `build` | CMake / Conan / 构建系统 |
| `deps` | 依赖管理（Conan、FetchContent、third_party） |
| `docs` | 文档（docs/ 目录） |
| `test` | 测试框架或测试代码 |
| `scripts` | scripts/ 下治理或工具脚本 |
| `deploy` | 部署配置（Docker/K8s/operator） |

## 短描述（必填）

- 英文，祈使句现在时
- 小写开头，末尾不加句号
- 不超过 50 字符
- 描述变更**做什么**，而非怎么做

```
good: fix(room): validate player count before battle start
bad:  fix(room): fixed the player count validation (过去时)
bad:  fix(room): changed validation logic to check count (说的是怎么做)
bad:  修复房间玩家数量验证（非英文）
```

## 正文（可选）

- 解释 WHY 而非 WHAT
- 每行不超过 72 字符
- 引用关联 issue 或 PR：`Refs: #123`

## 示例

```
feat(leaderboard): add batch submit endpoint for ranked sessions

Batch submit reduces Raft consensus round-trips by 60% for
leaderboard settlement at round end. Each session's entries
are submitted in a single Raft proposal.

Refs: #456
```

```
fix(room): reject join when room is already in battle

The state machine allowed join during BATTLE phase, causing
a race where the joining player would never receive state push.

Refs: #789
```

```
chore(ci): enable sccache build time capture across 5 workflows

Each CI run now archives build-time.json and sccache-stats.json
to runtime/perf/build-times/ for build performance tracking.
```

```
docs: add PR template, issue templates, and developer guide

- PULL_REQUEST_TEMPLATE.md with commit checklist
- ISSUE_TEMPLATE/bug_report.md and feature_request.md
- Expand ONBOARDING.md with coding standards, test policy,
  benchmark policy, protocol/SDK/demo guides
```

## 合并提交

- 一个 PR 可以包含多个提交
- 合入 main 时建议 squash merge，squash 后的提交信息遵循本规范
- 不要将格式修复（style）、文档小改（docs）与非相关的功能变更混在一个提交中

## 违背规范的提交

- CI 不会因提交信息格式拒绝构建
- 但 reviewer 可以在 PR 中要求修改提交信息
- Changelog 生成和 release note 会忽略不符合规范的提交
