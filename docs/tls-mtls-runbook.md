# TLS / mTLS Runbook

更新时间：2026-05-18

本文档对应生产业务闭环 P6。当前项目已经具备 gateway->backend TLS client 配置、security policy、feature flag、证书生成和测试门禁；默认生产链路仍是 plain TCP。不要把默认生产部署描述成已经启用全链路 TLS/mTLS。

## 当前边界

默认配置：

- `feature_flags.v3_tls_enabled.enabled=false`
- `feature_flags.v3_tls_enabled.rollout_percentage=0`
- `security_policy.require_tls=false`

这意味着默认 Docker Compose / K8s 生产链路不强制 TLS。各服务的 `tls_required` / `mtls_required` 是灰度策略声明，只有在全局 `require_tls=true` 且 feature flag 命中时才进入 TLS backend client 路径。

## 证书

开发/预发证书：

```bash
python3 scripts/gen_certs.py
```

如需给实验 gRPC mTLS 或其他本机双向 TLS 验证生成临时 client cert：

```bash
python3 scripts/gen_certs.py --include-client
```

也可以为轮换演练输出到独立目录：

```bash
python3 scripts/gen_certs.py --output-dir runtime/tls-readiness/rotated-certs --days 90
```

产物：

- `certs/ca.crt`
- `certs/server.crt`
- `certs/server.key`
- `certs/client.crt` / `certs/client.key`（仅 `--include-client`）

实验 gRPC `GatewayGrpcServer` / `GrpcClient` 的 TLS/mTLS E2E 也复用这组开发证书；当前仓库只把它们用于本机或 CI 验证，不得作为生产 CA 或生产 client cert。

生产不得使用开发 CA。正式环境应由 Vault、云 Secret Manager、cert-manager 或企业 CA 管理，并在发布记录里写清证书指纹、过期时间和轮换窗口。

## 验证入口

TLS profile 边界检查：

```bash
python3 scripts/check_tls_profile.py --generate-dev-certs
```

N4 传输安全与配置治理聚合门禁：

```bash
python3 scripts/check_transport_config_governance.py --generate-dev-certs --summary-path runtime/validation/n4-transport-config-governance-summary.json
```

TLS profile 生产业务闭环实测：

```bash
python3 scripts/check_transport_config_governance.py --generate-dev-certs --include-tls-full-flow --build-dir build/release --summary-path runtime/validation/n4-transport-config-governance-summary.json
```

R1 TLS 上线前置证据：

```bash
python3 scripts/verify_tls_production_readiness.py --build-dir build/release --skip-build --summary-path runtime/validation/r1-tls-production-readiness-summary.json
```

该入口会在本机启动 gateway、五个 backend 和 SDK full-flow client，验证：

- 默认 TLS profile full-flow 仍可通过。
- gateway 使用 `server` verify mode 与指定 CA 时，backend TLS full-flow 可通过。
- 轮换后的证书目录可以完成同一业务闭环。
- CA 不匹配会导致预期失败，并把失败 summary 归档为诊断证据。
- plain TCP 与 backend TLS profile 的单次业务闭环耗时会写入 R1 summary；该对比是 smoke 级上线前置检查，不替代固定 runner 多轮容量基线。

P5-P8 聚合入口会自动运行该检查：

```bash
python3 scripts/verify_p5_p8_business_closure.py --build-dir build/default --skip-build
```

该检查验证：

- 默认生产 config 没有误开启 TLS。
- leaderboard 保留 mTLS 敏感服务策略。
- gateway bridge 按 `security_policy.require_tls` 和 `v3_tls_enabled` 控制 TLS。
- backend connection 在传入 `tls_config` 时具备 TLS handshake 路径。
- backend 服务端具备 opt-in TLS listener；`BackendTlsListenerCompletesLoginRequest` 覆盖 backend TLS listener + `BackendConnection` 的真实 request/response 闭环。
- `scripts/verify_sdk_full_flow_client.py --backend-tls` 会启动五个 backend、gateway 和 SDK full-flow client，验证 login、room、battle、matchmaking、leaderboard 业务闭环全部通过 TLS profile。
- 证书生成器和证书可读性正常。
- Docker Compose 和 Kubernetes 为 backend TLS profile 保留显式 cert mount / Secret mount，默认仍关闭。
- 配置治理门禁能发现 Docker/K8s/Helm 与生产配置事实源之间的漂移。

`scripts/verify_production_resilience_gate.py` 也会运行 N4 聚合门禁，默认写出 `runtime/validation/p5-transport-config-governance-summary.json`，用于发布前归档。

## 证书轮换与回滚

生产证书轮换建议使用灰度发布：

1. 在 Secret Manager、Kubernetes Secret 或 Vault 中发布新 CA/server/client 证书，记录指纹与过期时间。
2. 先运行 `scripts/check_tls_profile.py --generate-dev-certs` 验证本地 profile 没有误打开默认 TLS。
3. 在预发或固定 runner 上打开 `security_policy.require_tls=true` 与 `feature_flags.v3_tls_enabled` 灰度比例，运行 SDK full-flow。
4. 观察连接失败率、backend TLS handshake 错误、leaderboard mTLS 拒绝数和证书过期告警。
5. 回滚时先关闭 `v3_tls_enabled`，必要时回退 Secret 版本并滚动重启 gateway/backend。

当前仓库已具备 opt-in backend TLS listener、五个 backend 配置接入、本机 TLS profile SDK full-flow 实测，以及 R1 证书轮换 / CA 不匹配失败诊断 / plain-vs-TLS smoke 对比。默认生产仍保持 plain TCP；真正上线 TLS profile 前，还需要在固定 runner 或预发环境补多轮归档、证书过期告警、mTLS client cert 缺失和容量级性能损耗对比。

## 上线要求

真正启用 TLS/mTLS 前，必须额外完成：

- 五个 backend 服务端 TLS listener 与证书加载全部启用。
- Compose/K8s TLS profile 中 Secret/volume 挂载，并显式设置 `BACKEND_TLS_ENABLED=true`。
- SDK full-flow 在 TLS profile 下通过并归档 summary；本机入口为 `runtime/validation/n4-tls-full-flow-summary.json`。
- R1 TLS production readiness 通过并归档 `runtime/validation/r1-tls-production-readiness-summary.json`。
- 错误证书、CA 不匹配、服务名不匹配、client cert 缺失的诊断用例；当前 R1 已覆盖 CA 不匹配 expected failure，服务名和 client cert 缺失仍需预发/固定 runner 继续沉淀。

上述内容未完成前，P6 的交付状态是“安全配置与灰度边界收束完成”，不是“默认生产 TLS transport 已上线”。
