# Ubuntu 不可变 Release 单节点部署

更新时间：2026-07-24

本文档对应 `TODO-0009`。输入固定为 `v3.6.2` Linux x64 Release 和 commit
`ac99ae353a2a6e846f934c8d81c78a07f420f683`；目标是已通过 `TODO-0008` 静态准入的
Ubuntu 24.04 x64 主机。目标机不执行 CMake、Conan、编译或公共 Conan 访问。

本入口只负责单一 release 的验证、镜像构建、首次启动和 full-flow。幂等 install、upgrade、
rollback、previous/current 状态机属于 `TODO-0010`，不得由本入口假装完成。

目标机 staging 已准备完成时，推荐从 Mac 使用唯一的一键入口。`-t` 只用于让远端 `sudo`
读取密码；脚本会在任何写操作前拒绝 macOS、非 Ubuntu 24.04 或非 x86-64 主机：

```bash
ssh -t miniserver \
  'sudo bash /home/honeybury/boost-gateway-todo0009/deploy/operations/deploy_verified_release.sh'
```

脚本允许同一个 `v3.6.2` staging 在中途失败后安全重试，但若 `current` 指向其它版本会立即
失败，不会越过 `TODO-0010` 做升级或回滚，也不会自动 reboot。当前测试机的一键入口还会
先验证 `127.0.0.1:7890`，将 Docker daemon 的 HTTP/HTTPS proxy 收敛到 Mihomo systemd
drop-in；仅当 drop-in 变化时重启 Docker，避免 `docker pull` 绕过代理直连超时。

## 信任链

`scripts/prepare_release_runtime.py` 使用匿名 GitHub REST API 下载精确的 runtime、独立 SPDX
和 `SHA256SUMS.txt`，并执行以下 fail-closed 校验：

1. annotated tag 必须解引用到调用方给定的完整 commit，Release 不得是 draft/prerelease。
2. 三个下载文件必须同时匹配 GitHub asset digest；runtime 和 SPDX 还必须匹配 checksum 清单。
3. 下载 runtime 的 SLSA provenance 与 SPDX attestation bundle，使用固定 signer workflow、
   signer/source commit 和 `refs/tags/v3.6.2` 离线验签。
4. 已验签的 SPDX predicate 必须与独立 SPDX JSON canonical exact-match。
5. 安全解包后，gateway、五个 backend 和 `sdk_full_flow_client` 必须是可执行的 Linux x86-64
   ELF，且动态依赖只能是批准的 Ubuntu 系统运行库。

只有全部通过后才会原子产生 staging。staging manifest 同时记录 release/config/controller
摘要，并明确 `source_build_performed=false`、`dependency_resolution_performed=false`。

## 固定 GitHub CLI

Attestation bundle 是公开输入，不需要把 GitHub token 放到运营主机。验签工具必须使用发布
workflow 相同的 `gh 2.96.0` Linux amd64 archive 和 SHA-256：

```bash
work=/tmp/gh-2.96.0-linux-amd64
mkdir -p "$work"
HTTPS_PROXY=http://127.0.0.1:7890 curl --fail --location --retry 3 \
  https://github.com/cli/cli/releases/download/v2.96.0/gh_2.96.0_linux_amd64.tar.gz \
  --output "$work/gh_2.96.0_linux_amd64.tar.gz"
printf '%s  %s\n' \
  83d5c2ccad5498f58bf6368acb1ab32588cf43ab3a4b1c301bf36328b1c8bd60 \
  "$work/gh_2.96.0_linux_amd64.tar.gz" | sha256sum --check
tar -xzf "$work/gh_2.96.0_linux_amd64.tar.gz" -C "$work"
sudo install -o root -g root -m 0755 \
  "$work/gh_2.96.0_linux_amd64/bin/gh" /usr/local/bin/gh
```

代理地址只是当前测试机示例；若 Docker daemon 已单独配置代理，不应把代理凭据写入
release manifest、Compose env 或 summary。

## 下载与 staging

在仓库控制器副本中运行，输出目录必须不存在：

```bash
export HTTPS_PROXY=http://127.0.0.1:7890
export HTTP_PROXY=http://127.0.0.1:7890
python3 scripts/prepare_release_runtime.py \
  --tag v3.6.2 \
  --expected-commit ac99ae353a2a6e846f934c8d81c78a07f420f683 \
  --output-dir /home/honeybury/boost-gateway-v3.6.2-verified-r3 \
  --summary-path /home/honeybury/release-runtime-staging-summary.json
```

首次激活只允许目标 `/opt/boost-gateway/current` 不存在的主机；已有 current 时停止，交给
`TODO-0010` 的 upgrade/rollback 状态机：

```bash
sudo install -d -o root -g boost-gateway -m 0750 /opt/boost-gateway/releases
sudo cp -a /home/honeybury/boost-gateway-v3.6.2-verified \
  /opt/boost-gateway/releases/v3.6.2
sudo chown -R root:boost-gateway /opt/boost-gateway/releases/v3.6.2
sudo test ! -e /opt/boost-gateway/current
sudo ln -s /opt/boost-gateway/releases/v3.6.2 /opt/boost-gateway/current
sudo install -o root -g root -m 0644 \
  /opt/boost-gateway/current/deploy/systemd/boost-gateway-compose.service \
  /etc/systemd/system/boost-gateway-compose.service
```

## Runtime-only 镜像

Dockerfile 固定 Ubuntu 24.04 digest
`sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90`。
先显式拉取固定 base 和运行拓扑依赖，然后在断网 build network 中生成六个项目镜像：

```bash
sudo docker pull ubuntu@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90
sudo docker pull redis:7-alpine
sudo docker pull oliver006/redis_exporter:v1.69.0
sudo docker pull prom/prometheus:v2.53.0
sudo docker pull prom/alertmanager:v0.28.1
sudo docker pull grafana/grafana:10.4.2

sudo python3 /opt/boost-gateway/current/scripts/tools/build_release_images.py \
  --staging-dir /opt/boost-gateway/current \
  --env-path /etc/boost-gateway/compose-images.env \
  --summary-path /var/lib/boost-gateway-evidence/release/image-build-summary.json
```

构建命令固定 `--platform linux/amd64 --pull=false --network=none`。工具在构建前重算二进制和
配置摘要，构建后校验 image ID、OS/arch 和 release/config OCI labels；Compose 只接收六个
`sha256:<64>` 项目 image ID，不接受 tag 或 `build:` 回退。

secret 文件必须 root-owned `0640`。下面命令在主机上生成密码且不打印到终端：

```bash
sudo sh -c 'umask 027; printf "GRAFANA_ADMIN_PASSWORD=%s\n" "$(openssl rand -hex 32)" > /etc/boost-gateway/compose.env'
sudo chown root:boost-gateway /etc/boost-gateway/compose.env /etc/boost-gateway/compose-images.env
sudo chmod 0640 /etc/boost-gateway/compose.env /etc/boost-gateway/compose-images.env
sudo systemctl daemon-reload
sudo systemctl enable --now boost-gateway-compose.service
```

## 生产验证

生产 Compose 只公开 gateway TCP 9201；management、backend、Redis、exporter、Prometheus、
Alertmanager 和 Grafana 均绑定 loopback。每个服务必须有 CPU/memory/PID 限制、healthcheck、
持久卷或日志卷，以及有界 `json-file` rotation。

验证时显式加载两个 root-owned env 文件；summary 不包含 secret：

```bash
sudo sh -c 'set -a; . /etc/boost-gateway/compose-images.env; . /etc/boost-gateway/compose.env; set +a; exec python3 /opt/boost-gateway/current/scripts/tools/verify_release_deployment.py --staging-dir /opt/boost-gateway/current --compose-file /opt/boost-gateway/current/deploy/operations/docker-compose.production.yml --summary-path /var/lib/boost-gateway-evidence/release/deployment-verification-summary.json'
```

该入口验证 resolved Compose 无源码 build、11 个容器 healthy、gateway/Prometheus/
Alertmanager/Grafana endpoint、Redis PING，并直接运行 release 自带的 `sdk_full_flow_client`。
全部通过后才能进入 `TODO-0008` 的 `prepare-reboot` 和真实重启验证。
