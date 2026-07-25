# Ubuntu 运营主机准入与重启验证

更新时间：2026-07-24

本文档对应 `TODO-0008` 的仓库实现入口。目标仅为 Ubuntu 24.04 x64 单节点
Docker Compose 运营主机，不证明容量上限、多节点高可用或跨故障域灾备。不可变 release
消费、upgrade/rollback 和生产 Compose overlay 分别属于后续 `TODO-0009`、`TODO-0010`，
本入口不在服务器上执行 Conan 或 CMake 构建。

## Fail-closed 原则

准入命令使用 `deploy/operations/operations-host-policy.json`。以下任一事实缺失或无法读取
都必须失败，而不是降级成 warning：

- Ubuntu 24.04、x86_64、kernel、至少 6 physical/12 logical CPU、标称 16 GB memory、
  512 GB physical disk、500 GB root filesystem 和 400 GiB root available space。标称容量与
  OS 可见容量分开记录，避免把固件保留内存或分区开销误判为硬件降配。
- Docker server 24+、Compose v2、NTP sync、UTC RTC、default route 和监听端口快照。
- 全部物理盘 SMART health、至少一个可读 thermal sensor、温度低于 85 C。
- sleep/suspend/hibernate targets 全部 masked，且断电恢复配置有本机绑定的人工/带外验证记录。
- UFW active、incoming default deny；公网只允许 gateway TCP 9201。SSH、management、backend、
  Redis、monitoring 和 exporter 必须绑定 loopback 或策略列出的 trusted network。
- 专用 `boost-gateway` 身份不可登录且不属于 `docker`/`sudo`。Docker socket 只由 root-owned
  systemd Compose unit 使用，业务身份不得直接取得等价 root 的 Docker 权限。
- secret、persistent data、log、backup、evidence 目录的 owner/group/mode 与策略完全一致。
- journald 持久化且有上限，Docker 默认日志轮转有 `max-size` 和 `max-file`。

summary 只记录系统事实、hash 和 operator identity，不记录 secret 内容、token、private key
或密码。重复执行不会修改系统，只会原子替换指定 summary，因此可用于配置管理收敛检查。
reboot challenge 固定写入 root 控制的 `/etc/boost-gateway/reboot-challenge.json`，应用身份
不能替换 boot-id 前置事实。成功验证后工具会记录 after boot ID；同一 boot 可幂等复验，
后续再次 reboot 必须先生成新 challenge，旧记录不能跨多个 boot 重放。

## 幂等准备

以下命令必须由受审计的 root 配置管理执行。创建已存在的用户和目录时保持相同结果；
不要把应用身份加入 `docker` group。

配置管理应先安装 Docker server 24+、Compose v2 plugin、`smartmontools`、`iproute2`、
`ufw` 和 `curl`，并确认 kernel thermal/hwmon sensor 可读。目标机不需要也不应为本任务
安装 Conan、CMake、compiler 或应用构建依赖。

先以非 root 查看完全展开的基线动作；该模式不修改系统：

```bash
python3 scripts/apply_operations_host_baseline.py plan
```

在目标测试机维护窗口显式应用。`--restart-docker` 只在 Docker 日志配置发生变化时重启
daemon；省略该选项会保留修改但 fail closed 标记 `docker:restart-required`：

```bash
sudo python3 scripts/apply_operations_host_baseline.py apply --restart-docker \
  --summary-path /var/lib/boost-gateway-evidence/host-baseline-summary.json
```

应用入口只收敛本任务的 service identity、目录、UFW、journald、Docker 日志、thermal
module、sleep targets 和 Compose lifecycle unit。它不创建断电自启声明、不部署 release、
不启动业务 Compose，也不执行 Conan/CMake。

```bash
sudo useradd --system --home-dir /var/lib/boost-gateway \
  --shell /usr/sbin/nologin --user-group boost-gateway || true

sudo install -d -o root -g root -m 0755 /opt/boost-gateway
sudo install -d -o root -g boost-gateway -m 0750 /etc/boost-gateway
sudo install -d -o root -g root -m 0700 /etc/boost-gateway/secrets
sudo install -d -o boost-gateway -g boost-gateway -m 0750 \
  /var/lib/boost-gateway /var/log/boost-gateway /var/lib/boost-gateway-evidence
sudo install -d -o root -g root -m 0700 /var/backups/boost-gateway

sudo install -D -o root -g root -m 0644 \
  deploy/operations/boost-gateway-journald.conf \
  /etc/systemd/journald.conf.d/boost-gateway.conf
sudo install -D -o root -g root -m 0644 \
  deploy/systemd/boost-gateway-compose.service \
  /etc/systemd/system/boost-gateway-compose.service
sudo install -D -o root -g root -m 0644 \
  deploy/operations/operations-host-policy.json \
  /etc/boost-gateway/operations-host-policy.json
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
sudo systemctl daemon-reload
sudo systemctl restart systemd-journald
```

`deploy/operations/docker-daemon.example.json` 是最小日志策略，不应直接覆盖已有
`/etc/docker/daemon.json`。配置管理应合并 `log-driver`/`log-opts`，运行
`dockerd --validate --config-file=/etc/docker/daemon.json` 后再安排 Docker restart。

UFW 基线至少满足：incoming deny、outgoing allow、公网 9201/tcp allow。SSH daemon 可以
监听 wildcard，但有效入口必须由 UFW 收敛到 Tailscale IPv4/IPv6 trusted CIDR，不能保留
`22/tcp ALLOW Anywhere`：

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 9201/tcp
sudo ufw allow from 100.64.0.0/10 to any port 22 proto tcp
sudo ufw allow from fd7a:115c:a1e0::/48 to any port 22 proto tcp
sudo ufw enable
```

如果管理网不是策略中的 RFC1918/CGNAT CIDR，应先评审并版本化修改 policy，不能在目标机
临时放宽规则。云安全组也应使用同一边界，但不能替代主机 UFW 证据。

## 断电恢复声明

AC power restore/last-state 通常不能通过统一 Linux API读取。必须在 firmware console、BMC
或一次受控断电恢复试验中确认，再从 example 创建
`/etc/boost-gateway/restart-on-power-loss.json`。`host_id_sha256` 使用：

```bash
sha256sum /etc/machine-id
sudo install -o root -g root -m 0644 restart-on-power-loss.json \
  /etc/boost-gateway/restart-on-power-loss.json
```

记录必须包含 `restart_on_power_loss=true`、UTC `verified_at`、可复核 `method` 和
`operator`。未知、仅口头确认、主机 hash 不符或文件可被非 root 修改时准入失败。

## 主机准入

在目标主机以 root 运行，以便读取 SMART、UFW、systemd 和受保护目录：

```bash
sudo python3 scripts/check_operations_host.py admit \
  --policy /etc/boost-gateway/operations-host-policy.json \
  --summary-path /var/lib/boost-gateway-evidence/host-admission-summary.json
```

通过结果只是 host admission，不代表服务已部署，更不代表 `TODO-0008` 已完成。应把
summary 复制到异机证据存储，并保留 policy commit SHA。
初始准入允许 9201 尚未监听，因为 `TODO-0009` 的服务部署依赖本 preflight；一旦进入
`prepare-reboot` 或 `verify-reboot`，9201 公网监听即变为必需项，且其它公网 TCP 监听仍失败。

## 真实 reboot 证据

后续 release/Compose 已由受治理路径放入 `/opt/boost-gateway/current` 后，启用 root-owned
生命周期 unit。`compose.env` 只能是 root-owned `0640`，不得包含在 summary 或日志中：

```bash
sudo systemctl enable --now boost-gateway-compose.service
sudo python3 scripts/check_operations_host.py prepare-reboot \
  --policy /etc/boost-gateway/operations-host-policy.json \
  --summary-path /var/lib/boost-gateway-evidence/pre-reboot-summary.json
sudo systemctl reboot
```

重连后运行验证。工具要求同一 machine-id hash、不同 kernel boot ID、unit enabled/active、
gateway + 5 backend + Redis + monitoring containers 全部为 Up，并检查 gateway、Prometheus、
Alertmanager 本机 endpoint：

```bash
sudo python3 scripts/check_operations_host.py verify-reboot \
  --policy /etc/boost-gateway/operations-host-policy.json \
  --summary-path /var/lib/boost-gateway-evidence/post-reboot-summary.json
```

只有真实主机的 admission、pre-reboot、post-reboot summary 全部通过，并且无交互登录前
systemd 已自动恢复拓扑，才满足 `TODO-0008` 的 reboot 验收项。开发机 mock、容器重启、
相同 boot ID 或手工 `docker compose up` 均不能替代该证据。

## 已完成的目标机证据

2026-07-25，目标 Ubuntu 24.04 x64 主机完成真实 reboot：pre-reboot boot ID
`347f0099-eaa5-4f0e-a0e8-7a93803e0f6d`，post-reboot boot ID
`3872f22c-1b67-4d29-8c04-280a58619c6e`。`boost-gateway-compose.service` 自动恢复为
enabled/active，gateway、五个 backend、Redis、Redis exporter、Prometheus、Alertmanager
和 Grafana 共 11 个容器全部 healthy；公网仅 9201，内部端口保持 loopback。
`/var/lib/boost-gateway-evidence/post-reboot-summary.json` 的正式 `verify-reboot` 结果为 PASS。
