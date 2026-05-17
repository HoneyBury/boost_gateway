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

产物：

- `certs/ca.crt`
- `certs/server.crt`
- `certs/server.key`

生产不得使用开发 CA。正式环境应由 Vault、云 Secret Manager、cert-manager 或企业 CA 管理，并在发布记录里写清证书指纹、过期时间和轮换窗口。

## 验证入口

TLS profile 边界检查：

```bash
python3 scripts/check_tls_profile.py --generate-dev-certs
```

P5-P8 聚合入口会自动运行该检查：

```bash
python3 scripts/verify_p5_p8_business_closure.py --build-dir build/default --skip-build
```

该检查验证：

- 默认生产 config 没有误开启 TLS。
- leaderboard 保留 mTLS 敏感服务策略。
- gateway bridge 按 `security_policy.require_tls` 和 `v3_tls_enabled` 控制 TLS。
- backend connection 在传入 `tls_config` 时具备 TLS handshake 路径。
- 证书生成器和证书可读性正常。

## 上线要求

真正启用 TLS/mTLS 前，必须额外完成：

- backend 服务端 TLS listener 与证书加载。
- Compose/K8s TLS profile 中 Secret/volume 挂载。
- SDK full-flow 在 TLS profile 下通过。
- 错误证书、CA 不匹配、服务名不匹配、client cert 缺失的诊断用例。

上述内容未完成前，P6 的交付状态是“安全配置与灰度边界收束完成”，不是“默认生产 TLS transport 已上线”。
