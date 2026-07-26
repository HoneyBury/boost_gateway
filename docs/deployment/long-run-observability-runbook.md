# Long-run observability and evidence ledger

This runbook is the maintained execution entry for `TODO-0011` on the admitted Ubuntu
24.04 x86-64 operations host. Repository checks and a short deployment verification do
not close this TODO. Closure also requires real target-host samples, firing and resolved
notification receipts, scheduled records, and a copy verified on another host.

## Boundaries

- This task observes the current Redis RDB state. It does not change persistence,
  encryption, backup, restore, RPO, or RTO policy; those belong to `TODO-0012`.
- The local release SDK full-flow remains a deployment check. A once-per-minute external
  canary belongs to `TODO-0013`.
- Daily and weekly records created here are operational evidence. They do not start or
  prove the 72-hour or formal 30-day windows in `TODO-0016` and `TODO-0017`.
- The topology is single-node evidence and is not an HA or capacity claim.

## Production topology

The production Compose topology runs 13 healthy services. Prometheus retains 45 days
on `boost-gateway-production-prometheus-data` and scrapes gateway, Prometheus,
redis-exporter, node-exporter, and cAdvisor. Node exporter records host CPU, load,
memory, filesystems, disk I/O, network, hwmon/thermal-zone, systemd, and the governed
Docker restart-count and identity textfile. cAdvisor records container CPU, memory, and
lifecycle metrics. The host collector maps each governed Docker name to its cgroup ID;
Prometheus joins that mapping to cAdvisor samples and verifies all 13 containers. Host
filesystem and network coverage comes from node-exporter and is not claimed as
per-container evidence.

cAdvisor is the only permitted privileged service. The production contract fixes its
image, read-only root filesystem, exact read-only host mounts, `/dev/kmsg` device, and
absence of published ports. Any broader privileged service or mount fails
`check_release_compose.py`.

Install the restart-count collector before activating the new deployment:

```bash
CONTROLLER=/home/honeybury/boost-gateway-controller
sudo "$CONTROLLER/deploy/operations/install_observability_host_units.sh"
sudo systemctl status --no-pager boost-gateway-container-metrics.timer
sudo systemctl list-timers --all 'boost-gateway-observability-evidence-*'
sudo cat /var/lib/boost-gateway-evidence/metrics/container-restarts.prom
```

The installer also enables create-only daily and weekly evidence timers. The daily job at
00:15 UTC queries only the previous full UTC day; the Monday 00:45 UTC job queries only
the previous full ISO week. Both connect directly to `127.0.0.1:9090`, do not receive
Compose environment files, cannot access the Docker socket, and write reports under
`observability/reports`. A persistent-timer retry validates an existing record and never
overwrites it.

## Credentials and notification receiver

Production Compose reads Alertmanager configuration only from the root-managed file
`/etc/boost-gateway/alertmanager.yml`. The checked-in
`env/monitoring/alertmanager.yml` remains a local-development placeholder and is
rejected by production preflight.

Create `/etc/boost-gateway/compose.env` with a non-`admin` Grafana user, a password of
at least 20 characters, and the numeric `boost-gateway` group ID. Do not print the
password or store it in evidence:

```bash
sudo bash -c 'umask 027; {
  printf "GRAFANA_ADMIN_USER=boost-gateway-operator\n"
  printf "GRAFANA_ADMIN_PASSWORD=%s\n" "$(openssl rand -hex 32)"
  printf "BOOST_GATEWAY_GID=%s\n" "$(getent group boost-gateway | cut -d: -f3)"
} > /etc/boost-gateway/compose.env'
sudo chown root:boost-gateway /etc/boost-gateway/compose.env
sudo chmod 0640 /etc/boost-gateway/compose.env
```

Configure one real email, webhook, Slack, PagerDuty, or other Alertmanager integration.
The top-level route receiver must not be named `default`, and the file must be
root-owned `0640`. Receiver credentials must use a separate root-owned `0640` file,
not an inline YAML value. The production container runs as UID 65534 with only the
numeric `boost-gateway` group needed to read these two files.

```bash
sudo install -o root -g boost-gateway -m 0640 \
  /path/to/private/alertmanager.yml \
  /etc/boost-gateway/alertmanager.yml
```

For Gmail SMTP to a QQ Mail recipient, first enable Google two-step verification and
create a dedicated 16-character app password. Then run the interactive helper. It
stores the app password outside YAML, validates the config, starts an isolated
Alertmanager on `127.0.0.1:19093`, sends firing and resolved emails, asks for their
destination `Message-ID` headers, writes the attestation, and runs production preflight:

```bash
sudo /home/honeybury/boost-gateway-controller/deploy/operations/configure_gmail_alertmanager.sh
```

Send a governed test alert through Alertmanager, verify the firing notification at the
destination, resolve it, and verify the resolved notification. Record destination-side
delivery identifiers without recording receiver credentials:

```json
{
  "schema_version": 1,
  "overall_pass": true,
  "receiver": "operations-webhook",
  "alertmanager_config_sha256": "<sha256 of /etc/boost-gateway/alertmanager.yml>",
  "host_id_sha256": "<sha256 of /etc/machine-id bytes>",
  "tested_at": "2026-07-26T00:00:00Z",
  "firing_delivery": {
    "id": "<destination delivery id>",
    "observed_at": "2026-07-25T23:55:00Z"
  },
  "resolved_delivery": {
    "id": "<destination delivery id>",
    "observed_at": "2026-07-25T23:58:00Z"
  }
}
```

Install it as
`/var/lib/boost-gateway-evidence/observability/alert-delivery-attestation.json`, owned by
root with mode `0640`. The attestation must bind the current host and config, include
both delivery states, and be no older than seven days. The release Compose check runs
this preflight automatically when invoked as root on Linux and validates the config
with the pinned Alertmanager `amtool` image.

Manual verification:

```bash
sudo python3 "$CONTROLLER/scripts/tools/check_observability_preflight.py"
```

## Runtime verification

The lifecycle verifier fails unless all 13 services are healthy, all five Prometheus
jobs are up, retention is at least 45 days, every alert rule evaluates with `health=ok`,
and every required host, container, gateway RED, restart-count, and Redis persistence
metric has a real sample. It also requires joined CPU, memory, and start-time samples for
every governed container rather than accepting cAdvisor metric names alone.

```bash
sudo python3 "$CONTROLLER/scripts/tools/manage_release_deployment.py" verify
```

If thermal metrics are absent, do not weaken the verifier. Check `/sys/class/hwmon`,
`/sys/class/thermal`, node-exporter logs, and the host admission thermal evidence.

## Evidence records

Every record is create-only and binds the active deployment record plus one or more raw
summaries by size and SHA-256. Each raw summary is copied into the content-addressed
`observability/raw` directory before the record is written, so later regeneration of a
fixed summary path cannot invalidate historical evidence. Attributes are separate JSON
objects and secret-like keys are rejected.

Before the first upgrade from a controller that predates content-addressed snapshots,
seal all existing records while their referenced source summaries are still unchanged:

```bash
sudo python3 "$CONTROLLER/scripts/tools/manage_observability_evidence.py" seal
```

The command is idempotent. It fails if a legacy source has already drifted or an existing
content-addressed snapshot no longer matches its recorded digest.

Daily checkpoint example:

```bash
DATE="$(date -u +%F)"
printf '{"checkpoint_date":"%s"}\n' "$DATE" >/tmp/daily-attributes.json
sudo python3 "$CONTROLLER/scripts/tools/manage_observability_evidence.py" record \
  --kind daily --record-id "$DATE" \
  --summary /var/lib/boost-gateway-evidence/release/deployment-verification-summary.json \
  --summary /var/lib/boost-gateway-evidence/observability/observability-preflight-summary.json \
  --attributes-json /tmp/daily-attributes.json
rm -f /tmp/daily-attributes.json
```

Weekly records require `period_start` and `period_end`; incident records require
`title`, `severity`, `started_at`, and `status`; the final TODO-0011 report requires
`report_title`, `period_start`, and `period_end`. Each must reference the raw trend,
alert, incident, or report summaries used to reach its conclusion. All records set
`formal_30_day_claim=false`.

Scheduled reports cover target availability plus host, governed-container, gateway and
Redis trends. Missing series, non-finite samples and cadence gaps are retained in the
report's `gaps` array. The record is still created so an outage cannot erase its own
evidence; `coverage_complete=false`, `overall_pass=false`, and all formal/SLO claims
remain false. Inspect recent executions with:

```bash
sudo systemctl status --no-pager boost-gateway-observability-evidence@daily.service
sudo systemctl status --no-pager boost-gateway-observability-evidence@weekly.service
sudo journalctl -u 'boost-gateway-observability-evidence@*' --since '8 days ago'
```

## Off-host package

Build a create-only manifest and package after records exist:

```bash
ID="todo0011-$(date -u +%Y%m%dT%H%M%SZ)"
sudo python3 "$CONTROLLER/scripts/tools/manage_observability_evidence.py" manifest \
  --manifest-id "$ID"
sudo python3 "$CONTROLLER/scripts/tools/manage_observability_evidence.py" package \
  --manifest "/var/lib/boost-gateway-evidence/observability/manifests/$ID.json" \
  --output "/var/lib/boost-gateway-evidence/observability/$ID.tar.gz"
```

Copy the archive to a different host or storage system. After extracting it there, run
the standard verifier from the extraction directory:

```bash
sha256sum -c SHA256SUMS
```

The local package result deliberately reports `off_host_copy_verified=false`. Record the
remote host/storage identity, remote path, archive SHA-256, copy time, and the successful
`sha256sum -c` output as a raw summary, then include that summary in the final record.
An archive remaining only on the operations host does not satisfy TODO-0011.
