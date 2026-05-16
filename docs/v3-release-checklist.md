# v3.x 发布清单

> 适用范围：`v3.3.x` 收口之后的生产就绪阶段。
> 目的：统一版本口径、安装产物、配置、脚本、镜像、控制面和发布门槛，避免 README、CMake、install/package、CI 和实际产物不一致。

## 1. 版本口径

每次 v3.x 发布前必须明确并记录以下四个版本字段：

| 字段 | 位置 | 说明 |
|---|---|---|
| Framework version | `README.md` / docs | 当前主线对外说明版本 |
| CMake project version | `CMakeLists.txt` | 安装包、CPack、导出包的工程版本 |
| Git tag | release tag | 对外发布标签 |
| Validation baseline | `docs/development-log.md` / `docs/performance-baseline.md` | 本次发布所依据的测试与性能基线 |

要求：

- 四个字段不能互相矛盾。
- 如果主线处于 `v3.3.x` 之后的生产就绪阶段，README 和 docs 必须明确该阶段状态。
- 如果版本仍为候选版，必须标明 `candidate` / `rc` / `ongoing`，不能伪装成稳定正式版。

## 2. 二进制交付面

### 2.1 正式安装的 v2/v3 入口

以下目标必须在 `cmake --install` 后可见，或在发布说明中明确标注为何不安装：

| 目标 | 类型 | 状态 |
|---|---|---|
| `v2_gateway_demo` | 推荐主入口 | 必装 |
| `v2_login_backend` | backend | 必装 |
| `v2_room_backend` | backend | 必装 |
| `v2_battle_backend` | backend | 必装 |
| `v2_match_backend` | backend | 必装 |
| `v2_leaderboard_backend` | backend | 必装 |
| `v2_gateway_pressure` | benchmark / load tool | 必装 |

### 2.2 v1 与实验入口

| 目标 | 类型 | 状态 |
|---|---|---|
| `echo_server` / `echo_client` | v1 维护入口 | 可装，需标明维护用途 |
| `gateway_pressure` | v1 压测工具 | 可装 |
| `login_server` / `room_server` / `battle_server` | 实验独立入口 | 可装，需标明 experimental |
| `login_demo` / `room_demo` / `battle_demo` / `admin_demo` | showcase | 可装，需标明 demo-only |

## 3. 配置与脚本

发布包至少应包含以下内容：

| 类别 | 内容 |
|---|---|
| 配置 | `config/` 下运行所需样例配置 |
| 文档 | `README.md`、`CHANGELOG.md`、`docs/README.md`、运行与发布文档 |
| 验证脚本 | `scripts/p4_validate.ps1`、`scripts/collect_v2_perf_baseline.py`、`scripts/collect_v2_perf_baseline.ps1`（Windows wrapper）、必要的 smoke / proto 生成入口说明 |
| 部署文件 | `deploy/` 下 systemd / Docker 相关说明 |
| 环境说明 | `env/README.md` 或等价部署文档 |

要求：

- 每个必须的配置文件都要在文档中说明用途、默认端口、可选依赖。
- Redis、TLS、OTel、cert-manager 等可选依赖必须注明是 opt-in 还是正式生产必需。

## 4. 控制面与协议入口

| 项目 | 发布要求 |
|---|---|
| Operator | 必须说明当前支持的 CRD 字段、status conditions、已验证范围 |
| Proto generation | 必须说明 `scripts/generate_proto_cpp.ps1` 和相关 CMake target 的使用方式 |
| Typed envelope | 必须说明当前是 helper 过渡层还是正式 generated transport |
| Legacy 兼容 | 必须说明 raw payload / 旧协议是否仍兼容，以及退役计划 |

## 5. 发布前检查

每次进入 v3.x 生产候选前，至少确认：

1. `cmake --preset <release-preset>`
2. `cmake --build --preset <release-preset>`
3. `ctest --preset <release-preset>`
4. 关键集成测试通过：
   - backend routing
   - service bus integrity
   - Redis / replay 恢复
   - Operator 关键状态断言
5. `cmake --install` 后二进制、配置、文档齐全
6. `docs/performance-baseline.md` 本次发布相关表格已更新
7. `docs/architecture-acceptance-criteria.md` 关键指标不再大面积停留在“未测定”
8. `docs/development-log.md`、`CHANGELOG.md`、`README.md` 已同步

## 6. 发布阻断条件

出现以下任一情况时，不得进入生产候选发布：

| 阻断项 | 说明 |
|---|---|
| 版本口径冲突 | README、CMake、tag、docs 表述不一致 |
| install 产物缺失 | README 声称存在的关键 backend / 工具未被安装或未说明 |
| 性能基线缺失 | 本次发布涉及主链变更但没有更新性能基线 |
| 关键恢复链未验证 | Redis / replay / Raft / Operator 恢复链缺少有效测试 |
| 协议状态不清 | typed envelope、proto generation、legacy 兼容状态未说明 |
| 控制面不可审计 | status conditions、components、TLS 状态无文档或无验证结论 |

## 7. 发布记录模板

每次 v3.x 发布说明至少包含：

- 版本号与 tag
- 本次基线日期
- 实测测试数 / 关键集成测试
- 性能基线摘要
- 已知限制
- 可选依赖说明
- 回滚条件
