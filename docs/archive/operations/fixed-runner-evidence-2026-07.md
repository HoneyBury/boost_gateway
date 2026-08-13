# 2026-07 固定 Runner 历史证据

状态：已归档，不作为当前候选或 runner 在线状态事实源。

本记录保存 2026-07 Linux x64、Linux ARM64、macOS ARM64 固定 runner 接入期间的运行索引、
cache namespace、artifact 和失败诊断。当前操作方式以
[固定 Runner 执行手册](../../fixed-runner-playbook.md)为准。
完整原始手册由 Git 提交 `c2fe7e7` 保留，避免在当前文档树中复制历史正文。

## Linux x64

- pre-freeze R5/R6：run `29900090220`，候选 `b319be4`。
- Release/R0：run `29902388234` / `29902403738`，候选 `76715ba`。
- completion batch：run `29912054968`、`29912422049`、`29913176854`，
  候选 `d687b9e`；这些结果不与其它 SHA 拼接。
- dedicated artifact：debug symbols `29922341090`、SDK `29923068133`、
  JWKS `29923314097`，候选 `00ce82e`。
- artifact upload timeout `29920565226` 保留为传输失败诊断，不作为通过证据。

## Linux ARM64 与 macOS ARM64

- Linux ARM64 Release offline seed/verify：`29905671975` / `29906228268`。
- Debug、gRPC 和 R5/R6：`29907949804`、`29908827298`、`29909904605`。
- ARM package/security：macOS JWKS `29925779628`，Linux ARM64 JWKS
  `29926003937`，SDK `29926636641`，symbols `29926847088`。
- macOS candidate `29927622379` 只证明绑定 SHA 的原生运行，不继承 Linux 容量结论。

## 早期 Ubuntu R5/R6

- `29196150703` 记录早期 gRPC cache 能力。
- `29415968573` 记录 AOI runner R5/R6 远端执行。
- `29420321189` / `29421659838` 记录不同 GCC/Ubuntu cache 图缺包的严格离线失败。

这些运行仅用于追溯能力形成过程。判断当前证据必须读取对应 workflow artifact 的 provenance、
候选 SHA、runner identity、lockfile digest 和 summary，而不能只依据这里的 run 编号。
