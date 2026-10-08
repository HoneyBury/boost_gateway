# 云单节点目标准入 Runbook

更新时间：2026-10-08

本文档负责 `TODO-0020` 的阿里云 Ubuntu 24.04 x86-64 单节点选择、Terraform 创建和
主机准入。它只建立生产候选主机，不执行发布、切流、七天试运行或 HA/跨区 DR 声明。

## 当前状态

[`cloud-target-contract.json`](../../deploy/cloud/cloud-target-contract.json) 当前是结构有效的
`draft`，因此 Terraform 配置可被审查和验证，但真实 provisioning 被门禁阻断。下列值必须在
付费资源创建前冻结：

- region、availability zone、ECS instance type 和不可变 Ubuntu 24.04 x86-64 image ID；
- 私网 DNS hostname、secret source、加密远程 Terraform state backend；
- 含 ECS、系统盘、数据盘、快照、公网出站流量在内的月成本报价和报价时间；
- 与生产实例不同故障域的 backup 目标；
- 云盘健康、物理温度和掉电恢复三项云环境替代控制及其验收方式；实际证据在创建后采集。

现有 `aliyunserver` 仍只承担 external canary、异机 vault 和 evidence verification。它的
2 vCPU、约 1.6 GiB memory 和 40 GB 根盘不满足本合同，不能被 Terraform import 或改名为
生产候选。

## 固定边界

Terraform 位于 [`deploy/cloud/alicloud-single-node/`](../../deploy/cloud/alicloud-single-node/)，
固定创建以下资源：

- 单独 VPC、vSwitch 和默认隔离的 security group；
- PostPaid、非抢占、启用 deletion protection 的 ECS；
- 512 GiB 加密 ESSD PL1 系统盘；
- 独立 512 GiB 加密 ESSD PL1 数据盘，实例删除时保留；
- 数据盘每日快照和 30 天 retention；
- 只允许明确私网 CIDR 访问 TCP 22 与 9201 的 security-group 和 UFW 规则；
- chrony、Docker、持久 journald、服务身份和 root-only secret 目录。

公网地址只用于初始软件包和 release 下载的出站流量。security group 和 UFW 均不创建公网
入站规则。TCP 9201 在本阶段只允许私网访问；Docker API、Redis、Prometheus、Alertmanager、
Grafana 和 exporter 均不得公开。目标机只部署已经校验的 immutable release，不 clone 仓库、
不运行 Conan/CMake/Ninja，也不保存 Terraform、云账户或应用 secret。

Terraform state 必须使用 `versions.tf` 中声明的 partial S3 backend，并在初始化时提供经批准的
加密远程 backend 与 locking 配置。backend credential 只从操作者环境或凭据链读取，不写入
`.tfvars`、命令历史、plan、state、summary 或 Git。

## 合同门禁

普通检查验证草案结构、容量下限、网络边界、IaC 文件和显式偏差：

```bash
python3 scripts/gates/infrastructure/check_cloud_target_contract.py
```

准备创建资源前必须执行严格检查：

```bash
python3 scripts/gates/infrastructure/check_cloud_target_contract.py \
  --require-selected
```

严格选择检查只有在 `status=selected`、所有选择字段和成本已冻结时才通过。创建后的主机
准入和生产闭环还必须执行 `--require-admission-evidence`；它要求三项替代证据均为
`accepted` 且带有绝对 `evidence_reference` 和 SHA-256。不要通过删除偏差项、降低
CPU/memory/disk 门槛或改动 `operations-host-policy.json` 使其通过。

## 选择和计划

1. 在阿里云控制台确认同一区域内可用的 12 vCPU 或更高 ECS SKU、Ubuntu 24.04 x86-64
   image 和 ESSD PL1；保存含税月度报价引用。
2. 确认操作者到 `10.42.0.0/16` 的 VPN、专线或受控堡垒路径。未验证私网路径时不创建实例。
3. 选择加密且带锁的远程 state backend；选择与 ECS 不同 region/account 的备份故障域。
4. 更新 `cloud-target-contract.json`，保留全部容量和安全值，最后才把 `status` 改为
   `selected`。
5. 从 `terraform.tfvars.example` 建立未跟踪的 `terraform.tfvars`。该文件只含资源选择与 CIDR，
   不含 access key、private key、token 或应用凭据。
6. 严格合同检查通过后初始化远程 backend，执行 `terraform plan -out`，核对资源、价格和
   destroy/replace 动作。`apply` 是单独的付费变更步骤。

示例验证命令：

```bash
cd deploy/cloud/alicloud-single-node
terraform fmt -check
terraform init \
  -backend-config=/path/to/root-owned/backend.hcl
terraform validate
terraform plan -out=/secure/path/boost-gateway-cloud.tfplan
```

仓库不提供可直接 apply 的 `terraform.tfvars` 或 backend 配置。未选择合同时，
`run_cloud_production_closure.py` 会在第一步因 `cloud_target_contract` 失败，不能把后续本地检查
解释为云主机已准入。

## 创建后的准入顺序

1. 从 Terraform output 和阿里云控制台分别核对 instance、private IP、security group、系统盘、
   数据盘和 snapshot policy identity。
2. 等待 cloud-init 完成，确认 NTP 同步、Docker/Compose 版本、UFW、journald、service user 和
   secret 目录权限。
3. 依据 Terraform data disk ID 识别唯一未格式化附加盘，记录设备 identity 后再创建 filesystem
   和 mount；禁止按不稳定的 `/dev/vdX` 名称盲目格式化。把应用数据目录迁到该持久盘并复验
   owner/mode。
4. 以 root 使用 `deploy/cloud/cloud-operations-host-policy.json` 执行 baseline；该策略保持所有
   非网络主机门槛，只把 9201 从公网入口改为 firewall-protected trusted ingress。再用同一
   策略执行 `scripts/check_operations_host.py admit`。把选定合同安装为 root-owned、不可由
   group/world 写入的 `/etc/boost-gateway/cloud-target-contract.json`。
5. guest SMART、物理温度和 firmware power recovery 在 ECS 中不可观察。对应 host check 不得
   被概括为 PASS；必须先生成合同中指定的 provider volume health、CloudMonitor alarm、ECS
   auto-recovery 和真实 reboot/instance replacement 证据，由云偏差门禁验收。
6. 完成 host admission、reboot verification 和偏差替代证据后，才进入 `TODO-0021` 的
   immutable release 部署与私网切流。

对应命令为：

```bash
sudo python3 scripts/apply_operations_host_baseline.py plan \
  --policy deploy/cloud/cloud-operations-host-policy.json
sudo python3 scripts/apply_operations_host_baseline.py apply \
  --policy deploy/cloud/cloud-operations-host-policy.json \
  --restart-docker
sudo install -o root -g root -m 0644 \
  deploy/cloud/cloud-target-contract.json \
  /etc/boost-gateway/cloud-target-contract.json
sudo python3 scripts/check_operations_host.py admit \
  --policy /etc/boost-gateway/operations-host-policy.json \
  --cloud-contract /etc/boost-gateway/cloud-target-contract.json
python3 scripts/gates/infrastructure/check_cloud_target_contract.py \
  --contract /etc/boost-gateway/cloud-target-contract.json \
  --require-admission-evidence
```

每项替代证据使用独立 root-owned JSON 文件，mode 不得允许 group/world 写入。合同中的
`evidence_reference` 使用绝对路径，`evidence_sha256` 绑定文件原始 bytes。最小结构为：

```json
{
  "schema_version": 1,
  "passed": true,
  "host_check": "storage:smart-health",
  "target_id": "boost-gateway-alicloud-single-node-01",
  "provider": "alicloud",
  "region_id": "selected-region",
  "zone_id": "selected-zone",
  "instance_id": "selected-instance-id",
  "observed_at": "UTC timestamp",
  "source": "provider API or drill identity without credentials"
}
```

另外两份文件分别使用 `thermal:temperature` 和 `power:restart-on-power-loss`。原 host gate 会
逐项校验文件权限、SHA-256、target/provider/region/zone/instance 绑定和 `passed=true`；文件缺失、
摘要漂移或字段不匹配均保持 FAIL。

## 停止和回退

以下任一情况立即停止：合同严格检查失败、plan 出现公网入站、secret 进入 state、SKU 低于
容量下限、root disk 小于 512 GB、数据盘会随实例删除、远程 state 无加密或 locking、私网管理
路径不可用，或云偏差没有独立证据。

切流前回退目标保持 `miniserver`，责任人为 `@HoneyBury`，至少保留 168 小时。云网络、实例、
磁盘和供应商故障均按生产候选 incident 记录；已经结束的家庭路由器排除边界不适用于云阶段。
