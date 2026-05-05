# Git 工作流与提交规范

## 1. 分支策略

当前建议采用：

- `main`
  稳定主线，始终保持可构建、可测试、可演示
- `develop`
  日常开发集成分支
- `feature/*`
  单功能开发分支
- `fix/*`
  缺陷修复分支

## 2. 推荐流程

### 2.1 常规功能开发

1. 从 `develop` 拉出 `feature/xxx`
2. 在 `feature/xxx` 开发并提交
3. 自测通过后合并回 `develop`
4. 一个阶段稳定后再从 `develop` 合并到 `main`

### 2.2 紧急修复

1. 从 `main` 或 `develop` 拉出 `fix/xxx`
2. 修复并补回归测试
3. 合并回 `develop`
4. 必要时同步合并回 `main`

## 3. 提交信息规范

推荐使用英文祈使句，简洁说明“本次提交做了什么”。

### 3.1 推荐格式

```text
<type>: <summary>
```

例如：

- `feat: add session manager skeleton`
- `refactor: split examples from core modules`
- `test: add gateway integration tests`
- `docs: add game server architecture roadmap`

### 3.2 当前项目可接受的简化格式

如果不走前缀规范，也至少保证：

- 短
- 具体
- 可读

例如：

- `Restructure project layout and add test suites`
- `Upgrade session with dispatcher and heartbeat controls`

### 3.3 不推荐

- `update`
- `fix bug`
- `misc changes`
- `final`

## 4. 提交粒度要求

- 一个提交只做一类清晰变更
- 架构重构和纯文档修改尽量分开
- 修 bug 时尽量带测试
- 不要把无关格式化和业务修改混在同一提交

## 5. 合并规范

- `develop -> main` 建议使用合并提交保留阶段边界
- 大型功能建议通过 PR / MR 合并
- 合并前必须保证：
  - 能构建
  - 测试通过
  - 文档同步

## 6. 开发日志与提交日志关系

建议保持：

- Git 提交日志：记录“代码层面的变更”
- `docs/development-log.md`：记录“阶段层面的目标、结果、问题、计划”

两者互补，不互相替代。

## 7. 发布前检查清单

合并进 `main` 前至少确认：

- `cmake --build` 通过
- `ctest` 通过
- 示例程序可运行
- 协议变更已更新文档
- 新增模块有对应测试
- 开发日志已补阶段记录
