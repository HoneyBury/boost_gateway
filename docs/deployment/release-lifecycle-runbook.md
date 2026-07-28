# Immutable Release Lifecycle

更新时间：2026-07-25

本文档对应 `TODO-0010` 的仓库入口。目标是已经完成 `TODO-0008` 主机准入、并由
`TODO-0009` 部署了首个不可变 release 的 Ubuntu 24.04 x64 单节点 Docker Compose
主机。稳定入口是 `scripts/manage_release_deployment.py`；实现位于
`scripts/tools/manage_release_deployment.py`。

本入口只管理 install、deploy、upgrade、rollback、status 和 verify。备份、恢复、AOF/RPO
与异机保留属于 `TODO-0012`；45 天观测和 evidence ledger 属于 `TODO-0011`；外部每分钟
canary 属于 `TODO-0013`。本阶段不引入 Kubernetes、多节点 HA、跨故障域灾备或容量声明。

## 状态与不变量

- `/opt/boost-gateway/releases/<deployment-id>` 保存不可变 release tree。
- `/opt/boost-gateway/deployments/<deployment-id>` 保存 image IDs、配置快照、attestation
  summaries 和 deployment record。
- `/opt/boost-gateway/current` 与 `/opt/boost-gateway/previous` 只允许指向
  `/opt/boost-gateway/deployments/` 下的完整 deployment。
- `/etc/boost-gateway/compose-images.env` 固定指向
  `/opt/boost-gateway/current/compose-images.env`，不能复制成独立可漂移配置。
- `/var/lib/boost-gateway/deployment-transactions/<transaction-id>/` 保存操作 record、部署验证
  summary，以及发生自动恢复时的 recovery verification summary。
- Redis/data、backup 和 evidence 路径不属于 release tree。生命周期操作不得删除、清空或
  回滚这些持久状态。

所有命令（包括 `status`）都必须以 root 运行。管理器使用全局文件锁、
原子目录和 symlink 切换，并拒绝非 Linux x64 release、可变 image tag、失败的 release/image
summary、配置 digest 漂移和逃逸 deployments 根目录的指针。

候选先通过自身 Compose 文件和显式 image env 启动，完成全部部署验证后才提交 `current`；
因此 systemd 重启只能消费最后一个 verified deployment。每个关键 record、目录和 symlink rename
都会同步父目录。若进程或主机在 transaction 中断，下一条受控命令会先对账：已提交且 verified
的 current 会完成提交，未提交候选会恢复原 verified current，不能跳过失败 transaction 继续升级。

## 接管 TODO-0009 首次部署

先保留 `TODO-0009` 生成的四个输入：verified release staging、六个不可变 image ID 的 env、
release staging summary 和 image build summary。`install` 对四项都进行交叉校验；`--config-dir`
仅用于提供与 release manifest digest 完全一致的受控配置快照，通常省略并使用 release 的
`config/`。

```bash
sudo python3 scripts/manage_release_deployment.py install \
  --release-dir /opt/boost-gateway/releases/v3.6.2-deploy-r3 \
  --image-env /etc/boost-gateway/compose-images.env \
  --release-summary /var/lib/boost-gateway-evidence/release/release-runtime-staging-summary.json \
  --image-summary /var/lib/boost-gateway-evidence/release/image-build-summary.json
```

命令输出中的 `deployment_id` 是后续唯一参数。安装完成后接管现有 TODO-0009 `current`
指针；管理器只接受它指向同一个已安装 release，其他已有 current 必须使用 upgrade。

```bash
sudo python3 scripts/manage_release_deployment.py deploy \
  --deployment-id <deployment-id>
sudo python3 scripts/manage_release_deployment.py verify
sudo python3 scripts/manage_release_deployment.py status
```

deploy 会执行 Compose 预检、候选激活、验证通过后的 systemd 提交，以及 health、readiness、Prometheus targets、
Redis PING 和 release 自带 SDK full-flow。只有验证通过后 deployment 才变为 verified。
`status` 同时检查 current record、未完成 transaction、systemd enabled/active 状态，以及六个
运行容器的实际 image ID。

## 同版本幂等复验

使用完全相同的四个输入重复 `install`，必须返回原 deployment record，而不是创建第二份
release。随后对当前 `deployment_id` 重复 deploy，必须返回 `idempotent=true`，并重新执行
完整 verify。

幂等实机证据必须同时记录操作前后的 deployment ID、release/config/image digests、current/
previous 指针、持久 data/evidence/backup 路径和 SDK full-flow 结果。目录存在或单元测试通过
不能替代“数据、证据和备份未被擦除”的目标机前后对照。

## 安装并升级到第二个 release

第二个 release 必须先独立完成 TODO-0009 的 tag/commit/checksum/attestation/SPDX/ELF 校验和
runtime-only image build。不能把 tag 名、历史目录或未通过 summary 的候选当成 previous
verified release。

```bash
sudo python3 scripts/manage_release_deployment.py install \
  --release-dir <second-verified-release-dir> \
  --image-env <second-image-env> \
  --release-summary <second-release-summary> \
  --image-summary <second-image-summary>

sudo python3 scripts/manage_release_deployment.py upgrade \
  --deployment-id <second-deployment-id>
sudo python3 scripts/manage_release_deployment.py status
```

成功 upgrade 后，current 必须是第二个 deployment，previous 必须是升级前的 verified
deployment，且 `/etc/boost-gateway/compose-images.env` 仍固定指向 current。对应 transaction
必须包含 candidate、from_current、完成状态和 deployment verification summary；两个 deployment
record 必须保留 tag、commit、runtime asset digest、六个 image IDs、配置 digest 和 attestation
摘要。

## 失败自动恢复演练

在目标主机安排一次不损坏持久数据的受控候选失败。候选仍须通过 install 的供应链和 image
身份检查，但必须让 post-activation health/readiness/Prometheus/Redis/SDK 验证中的至少一项
真实失败。不得通过伪造 summary、修改 manager 或仅使用 test double 制造通过证据。

失败的 upgrade 必须返回非零并保留 transaction record。验收时检查：

1. transaction 状态为 `rolled_back`，并记录原始失败原因和 `restored_current`。
2. `recovery-verification-summary.json` 为 PASS；current 和 active image env 已恢复到升级前身份。
3. 运行中的六个项目 image IDs、runtime asset digest、配置 digest 和 checksum 与原 previous
   deployment 一致。
4. Redis 持久卷、data、backup 和 evidence 未回滚或删除，恢复后的 SDK full-flow 通过。

若候选激活和 previous 恢复都失败，必须保持 fail closed、保留 `recovery_failed` 记录并进入
incident 处理；不得把候选标记 verified 或人工改写 record。

固定测试机使用仓库脚本制造真实的 post-activation Prometheus outage。脚本会等待 transaction
进入 `candidate_activated` 并核对候选 gateway 镜像后，再暂停当前 Prometheus 120 秒，以 trap
保证解除暂停；它要求候选 verification summary
为 FAIL、recovery verification summary 为 PASS、transaction 为 `rolled_back`，且 current 恢复：

```bash
sudo deploy/operations/run_release_failure_drill.sh \
  <candidate-deployment-id> \
  <expected-current-deployment-id> \
  <candidate-gateway-image-sha256>
```

该脚本不执行 `down -v`、volume/image 删除或持久数据回滚。若候选意外通过，脚本自身必须失败，
不得生成伪造的自动恢复 PASS 结论。

## 显式回滚演练

成功升级并确认 current/previous 为两个不同的 verified deployment 后执行：

```bash
sudo python3 scripts/manage_release_deployment.py rollback
sudo python3 scripts/manage_release_deployment.py verify
sudo python3 scripts/manage_release_deployment.py status
```

rollback 的硬期限为 600 秒。通过记录必须包含 `elapsed_seconds`、恢复后的 runtime asset、image
environment 和 configuration digests；current/previous 应完成对调。随后必须从运行容器独立回读
六个 image IDs，复核 release checksum，并再次通过 health、ready、Prometheus targets、Redis
PING 和 SDK full-flow。数据卷 RPO 为 0，release rollback 不得回滚 Redis volume。

相同 volume 不代表不同 Redis persistence mode 之间天然兼容。release manager 会从 source/target
deployment 的 resolved Compose 识别 Redis mode，并为发生变化的事务生成
`candidate-persistence-transition-summary.json`。RDB-only→AOF everysec 需要先冻结写入并形成
fresh BGSAVE，AOF→RDB 还属于数据格式降级；在具备 `changes_since_last_save=0`、离线 RDB 校验和
checkpoint identity 的专用 transition hook 前，两种 mode change 一律 fail closed。失败候选的自动 previous 恢复和
显式 rollback 都受同一门禁约束；不得为了恢复可用性让旧 RDB-only Compose 忽略 active volume
中只存在于 AOF 的已确认写入。拒绝记录为
`recovery-persistence-transition-summary.json` 并保留现场进入恢复 incident。

## 关闭证据

每次实机操作至少归档：manager 标准输出、deployment record、transaction record、deployment/
recovery verification summaries、current/previous 与 active image-env symlink 快照、容器 image ID
快照、release/config/checksum 摘要、开始/结束时间、主机身份和操作者。记录不得包含 GitHub
token、TLS private key、Redis password、Grafana password 或其他 secret。

仓库测试和治理门禁只证明状态机契约可执行。`TODO-0010` 在以下事实全部具备前必须保持 open：

- 目标 Ubuntu 主机完成同版本 install/deploy 幂等复验。
- 两个不同、已验证的 immutable release 完成真实 upgrade。
- 受控部署失败自动恢复 previous，并保留失败和恢复 PASS summary。
- 显式 rollback 在 600 秒内恢复 digest、checksum、持久数据边界和完整业务流。

不得用本地 mock、旧 OrbStack/Kubernetes 演练、同一 release 的重复部署或静态 recovery gate
代替第二个 release 的目标机升级与回滚证据。
