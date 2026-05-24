# Docs Index

`docs/` 顶层只保留当前仍需要维护的主文档。历史版本文档、阶段计划、交付记录、旧 runbook 已迁入 `docs/archive/`。

## 优先阅读

1. `current-state.md`
2. `architecture-overview.md`
3. `reliability-matrix.md`
4. `performance-baseline.md`
5. `v3-release-checklist.md`

## 当前主文档

- `current-state.md`
  当前项目事实源。先看这个。

- `architecture-overview.md`
  当前架构分层、服务边界和主链实现概览。

- `reliability-matrix.md`
  当前可靠性断言、对应代码与验证入口。

- `performance-baseline.md`
  当前性能事实、基线口径、容量边界和 soak 策略。

- `production-deployment-runbook.md`
  生产部署方式、发布动作和部署后验证。

- `production-operations-runbook.md`
  运维排障、告警响应、恢复与回滚流程。

- `production-configuration-runbook.md`
  生产配置口径和修改边界。

- `tls-mtls-runbook.md`
  TLS/mTLS profile、证书与启用前提。

- `fixed-runner-playbook.md`
  固定 runner 的执行口径、环境要求和证据沉淀方式。

- `production-evidence-runner.md`
  生产证据 workflow 的 runner 输入与归档方式。

- `production-candidate-evidence-manifest.json`
  R2 证据 manifest。

- `production-recovery-drill-record-template.json`
  预发恢复演练记录模板。

- `v3-release-checklist.md`
  当前 release gate 与最终发布检查口径。

## P0/P1/P2 收口门禁

- `scripts/check_current_docs_install.py`
  校验顶层主文档、归档目录和 CMake install 清单是否一致。

- `scripts/check_mainline_readiness.py`
  校验默认生产主链边界、gRPC/Tank demo 边界和固定 runner 证据入口。

- `scripts/check_p3_p4_release_readiness.py`
  校验 P3 data recovery 与 P4 observability 已接入 RC 总门禁，并使用统一 summary 契约。

- `scripts/check_production_evidence_manifest.py --require-fixed-runner`
  投产前把 R4/R5/R6 固定 runner 或预发证据提升为阻断项。

## 归档文档

- `archive/history-v1/`
  v1 维护期文档。

- `archive/history-v2/`
  v2 阶段文档、阶段验收和旧事实文档。

- `archive/plans/`
  各阶段 roadmap、plan、development log、实施计划。

- `archive/runbooks/`
  已退出主维护面的旧 runbook。

- `archive/process/`
  工程流程、发布流程、环境参考、第三方治理等过程型文档。

- `archive/releases/`
  阶段性交付记录和 release 说明。

## 使用规则

- 顶层文档与归档文档冲突时，以 `current-state.md` 为准。
- 需要看历史背景时再进入 `archive/`，不要把归档文档当成当前实施依据。
