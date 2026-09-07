# External business canary runbook

This runbook owns the repository side of `TODO-0013`. The runner must be a
host outside the production service host so the sample includes the real
client network path and does not contaminate production CPU or memory
evidence. The v3.6.2 runtime is an immutable subject; this installation only
adds an external released-SDK client.

## Contract

Every UTC minute the runner uses the released Python SDK 4.2.0 to execute:

1. connect and login two dedicated synthetic users;
2. create/join/ready a short-lived room;
3. start a battle and submit both users' inputs;
4. request settlement with `finish:surrender`;
5. submit and query the leaderboard;
6. disconnect, reconnect and log in again.

The two user IDs are fixed for the life of the candidate. Only the room ID is
unique per run. This bounds the Redis leaderboard at two synthetic members
instead of creating two persistent members per minute. Give both accounts
only the permissions needed by this flow and do not reuse human credentials.

Each step records latency, one typed error category and an SDK error code when
available. Raw JSON contains hashes of the synthetic identities, never tokens,
and binds `tag`, full commit, gateway runtime digest, deployment ID, SDK
version and the TCP endpoint. Samples and incidents are opened with create-only
filesystem semantics under `/var/lib/boost-gateway-canary`.

## Alertmanager route

Production Alertmanager remains loopback-only on the service host. Do not
publish port 9093. Establish a separately governed route before installing the
canary. The normal topology is:

```text
external canary 127.0.0.1:19093
  -> pinned SSH local forward / restricted relay
  -> production host 127.0.0.1:9093
```

For SSH forwarding, use a dedicated non-shell account restricted to local
forwarding of `127.0.0.1:9093`, a dedicated key, `BatchMode=yes`, a pinned
`known_hosts` entry, `ExitOnForwardFailure=yes`, and a supervised service with
restart-on-failure. The tunnel or relay configuration and key remain
host-managed and are not copied into canary evidence. Verify the route with a
synthetic Alertmanager API request and retain its receiver delivery receipt.
Set `BOOST_GATEWAY_CANARY_ALERTMANAGER_URL=http://127.0.0.1:19093` only after
that check passes.

A failed business sample posts `BoostGatewayExternalCanaryFailed` directly to
`/api/v2/alerts` and creates an incident input linked to Issue #27. A second
timer checks sample freshness at `:45`; after 130 seconds it posts
`BoostGatewayExternalCanarySilent`. Failed Alertmanager delivery is recorded
and makes the service fail. The watchdog retries both an undelivered business
failure and a stale-stream alert using a new create-only incident until one
delivery succeeds.

## Independent external dead-man

The Alertmanager path above cannot report the disappearance of the whole
`aliyunserver` host, its Tailscale identity, or all of its outbound networking.
`TODO-0017` therefore requires a Healthchecks.io check whose email integration
is operated by the provider. It must not traverse the canary SSH tunnel,
production Alertmanager, the production Gmail SMTP relay, `miniserver`, or the
Mac. This is a Day 0 admission control, not a replacement for the business
canary or its Alertmanager alerts.

The repository coupling is deliberately narrow:

```text
natural UTC :45 watchdog succeeds  -> deadman@success -> Healthchecks.io POST
natural UTC :45 watchdog fails     -> deadman@failure -> Healthchecks.io POST /fail
host/timer/all egress disappears   -> no POST         -> provider missing heartbeat
                                                          | Down / Up email
                                                          v
                                                   destination mailbox
```

The watchdog service drop-in may trigger only
`boost-gateway-external-deadman@success.service` from `OnSuccess=` and
`boost-gateway-external-deadman@failure.service` from `OnFailure=`. There is no
independent success timer: otherwise a healthy host could keep the provider
check green while the natural-minute canary/watchdog path is broken. A provider
failure notification supplements the create-only local canary incident; it
does not make a failed sample successful.

Configure one dedicated provider check with this exact contract:

The schedule/grace semantics follow the provider's maintained
[check configuration contract](https://healthchecks.io/docs/configuring_checks/), and the
snapshot must come from its
[read-only Management API](https://healthchecks.io/docs/api/) rather than a write-capable
credential on the monitored host.

| Provider field | Required value |
|---|---|
| provider | hosted `Healthchecks.io` |
| schedule | OnCalendar `*-*-* *:*:45` |
| timezone | `UTC` |
| grace | `90` seconds |
| allowed request methods | `POST` only |
| manual resume | `false` |
| admitted preflight status | `new` or `up`; Day 0 requires `up` after the drill |
| notification | dedicated provider-side email integration enabled for this check |

Use a read-only Management API response to build the sanitized provider
snapshot consumed by preflight. Its exact keys are `schema_version`, `provider`,
`provider_unique_key`, `check_identity_sha256`, `observed_at`, `schedule`,
`tz`, `grace`, `methods`, `manual_resume`, `filter_http_body`, `status`, `source` and
`secret_material_recorded`. `filter_http_body=false` prevents provider-side body
filters from silently ignoring an empty POST. `provider_unique_key` is the stable 40-character
lowercase hex identifier returned by the v3 read-only API; it is not the UUID
or a bearer credential. `check_identity_sha256` is recomputed as SHA-256 over
the canonical UUID ASCII bytes held separately by preflight. The snapshot must
not contain the UUID, ping/update/pause/resume URL, API key, project ping key,
channels/integration ID, recipient address, cookies or authorization headers.
A screenshot or an operator claim without the machine-readable snapshot is not
sufficient.

The only raw ping capability installed on `aliyunserver` is the canonical UUID
in `/etc/boost-gateway-external-deadman/ping_uuid`. It is a root-owned regular
non-symlink file with mode `0600` and is passed to the oneshot through systemd
`LoadCredential=`. The runtime credential directory must be root-owned mode
`0550`; its materialized `ping_uuid` must be root-owned mode `0440`, have one
hard link and remain below that directory. Do not put it in an environment variable, command argument,
unit, repository, journal, event receipt, evidence package, or support message.
The provider Management API key remains on the workstation, not the canary host. Each
success/failure invocation has a 15-second bound and writes a create-only,
secret-free receipt below
`/var/lib/boost-gateway-external-deadman/events/`; a failed provider request
leaves the reporter unit failed. `OnSuccess=` dispatch does not retroactively
change the watchdog result; admission must inspect the reporter result and
provider status as well as the watchdog.
The reporter connects with direct certificate-validated TLS to the one fixed
`hc-ping.com:443` origin, removes inherited proxy environment variables, sends
an empty POST body and accepts only HTTP 200 with the bounded provider `OK`
response. Event receipts have the exact fields `schema_version`, `provider`,
`signal_status`, `observed_at`, `check_identity_sha256`,
`provider_http_status`, `delivery_accepted`, `transport_error`, `overall_pass`,
`create_only` and `secret_material_recorded`; they never contain the request
path or UUID.

### Missing-heartbeat acceptance drill

Install and validate in shadow mode first. Activation may bind the watchdog
drop-in only after the local artifact/config checks and sanitized provider
snapshot pass; a partial install must leave the existing canary scheduling
unchanged. After activation, observe a natural `:45` watchdog success, a
corresponding provider `up` state, and its create-only success receipt before
starting the drill.

The acceptance drill must suppress the heartbeat rather than sending the
provider `/fail` endpoint. The governed drill entry schedules and verifies a
transient automatic rearm **before** it stops the watchdog timer. Wait through
one missed `:45` plus the 90-second grace until Healthchecks.io changes to
`down` and the destination mailbox receives the Down email. The pre-scheduled
rearm must then start the watchdog timer, verify it is active, and create an
`O_EXCL` rearm receipt; the next successful natural watchdog produces the
provider recovery to `up` and its Up email. Do not power off either production
host, alter Tailscale, pause/delete the provider check, or use an explicit
failure ping as a substitute for this test.

Capture the destination-side RFC 5322 `Message-ID` for both emails. Only after
the sanitized pre-drill/down/up provider snapshots, host identity, current
canary candidate, installed artifact digests, relevant event/rearm receipts,
exact UTC transitions and both Message-IDs are bound in one create-only
attestation may the dead-man gate pass. The attestation records identity/config
digests, never any raw secret or recipient address. If the provider never goes
Down, the automatic rearm is not proven, either email is missing, or the final
provider status is not `up`, preserve the failed evidence and keep Day 0
blocked.

The attester uses schema v1 with this exact top-level field set; unknown or
missing fields fail closed:

```json
{
  "schema_version": 1,
  "attestation_id": "<drill-id>",
  "created_at": "<UTC timestamp>",
  "create_only": true,
  "overall_pass": true,
  "provider": "healthchecks.io",
  "subject": {
    "canary_host_id_sha256": "<64 lowercase hex>",
    "check_identity_sha256": "<64 lowercase hex>",
    "reporter_sha256": "<64 lowercase hex>",
    "service_unit_sha256": "<64 lowercase hex>",
    "watchdog_dropin_sha256": "<64 lowercase hex>",
    "provider_contract_sha256": "<64 lowercase hex>",
    "candidate_record_sha256": "<64 lowercase hex>"
  },
  "drill": {
    "failure_mode": "missing-heartbeat",
    "armed_at": "<UTC timestamp>",
    "heartbeat_stopped_at": "<UTC timestamp>",
    "provider_down_at": "<UTC timestamp>",
    "rearmed_at": "<UTC timestamp>",
    "provider_up_at": "<UTC timestamp>",
    "provider_unique_key": "<40 lowercase hex read-only identifier>",
    "final_status": "up"
  },
  "deliveries": {
    "down": {"message_id": "<provider-down-message-id>", "observed_at": "<UTC timestamp>"},
    "up": {"message_id": "<provider-up-message-id>", "observed_at": "<UTC timestamp>"}
  },
  "artifacts": [
    {"role": "<allowlisted role>", "basename": "<file name>", "size": 1, "sha256": "<64 lowercase hex>"}
  ],
  "secret_material_recorded": false
}
```

The Down and Up Message-IDs must be syntactically valid and distinct. Every
artifact entry is an allowlisted role plus basename, byte size and SHA-256; it
must not record an arbitrary or absolute source path. The complete tree is
scanned before creation and rejects UUIDs, ping/API URLs or keys, email
addresses, raw provider bodies and other secret-like keys or values. `@` is
accepted only inside the two Message-ID values.

The create-only output path is content addressed:

```text
/var/lib/boost-gateway-external-deadman/attestations/<content-sha>-<drill-id>.json
```

After local validation it must be copied byte-for-byte into `miniserver` raw
evidence and a distinct `aliyunserver` production-evidence archive, with the
copy/readback digests recorded outside the immutable source. Neither mirror may
replace the active attestation or relax create-only behavior.

As of 2026-09-07 this provider configuration, activation, drill and attestation
have **not** been completed. Existing canary minutes and Alertmanager delivery
receipts do not satisfy this gate and cannot be retroactively counted as a
`TODO-0017` Day 0.

### Governed dead-man commands

The implementation is `scripts/tools/manage_external_deadman.py`; the oneshot
reporter is `scripts/tools/external_deadman_reporter.py`. Both support repository
and flat installed layouts. Host mutations require Linux root. Install only from
a clean reviewed revision already merged into main:

```bash
python3 scripts/tools/manage_external_deadman.py install-shadow --checkout /absolute/reviewed/checkout
```

Shadow installation copies root-controlled artifacts and validates the service;
it does not bind or restart the existing watchdog. Existing install targets are
refused. A failed partial installation stays unadmitted and requires inspection.

Configure the provider check with OnCalendar `*-*-* *:*:45`, UTC, 90-second grace,
POST only, HTTP body filtering off and manual resume false. Enable and verify the
dedicated QQ email integration. The operational contract forbids pausing a check
during an admitted interval. Read-only API responses omit integration IDs, so
real Down/Up mailbox receipts are required to prove notification delivery.

Install the UUID into the root-owned 0600 credential file without placing its
value in shell arguments or chat. Keep the read-only Management API key on the
workstation in a separate 0600 file. Collect a sanitized snapshot there:

```bash
python3 scripts/tools/manage_external_deadman.py snapshot \
  --credential-file /absolute/protected/ping_uuid \
  --api-key-file /absolute/protected/read-only-key \
  --output /absolute/evidence/provider-before.json
```

Only the sanitized snapshot is copied to the observer. Admission snapshots expire
after five minutes. The installed host manager is
`/usr/local/libexec/boost-gateway-deadman/manage_external_deadman.py`:

```bash
python3 /usr/local/libexec/boost-gateway-deadman/manage_external_deadman.py preflight \
  --snapshot /absolute/evidence/provider-before.json --output /absolute/evidence/shadow-preflight.json
python3 /usr/local/libexec/boost-gateway-deadman/manage_external_deadman.py activate \
  --snapshot /absolute/evidence/provider-before.json
```

Activation preserves the previous observer script, atomically installs the new
observer and adds the instance-specific watchdog drop-in. A local activation
exception restores that observer and removes the newly created binding. The
production runtime is not rebuilt. Strict watchdog success requires exactly one
successful current-minute sample with matching candidate/endpoint and all six
successful business steps. Normal collection now records the actual production
and observer host identities in new samples.

After a natural heartbeat and fresh provider `up` snapshot, run `preflight
--active` and `arm-drill --drill-id <safe-id> --snapshot <fresh-up-snapshot>`.
The arm command verifies an eight-minute automatic recovery timer before stopping
heartbeat. Collect a `down` snapshot before rearm; after automatic recovery collect
an `up` snapshot and the successful reporter event. `rearm` rejects early or
non-systemd invocation.

Run `attest --drill-id <safe-id> --down <snapshot> --up <snapshot>
--success <reporter-event> --deliveries <mailbox-receipts-json>`. The deliveries
file contains exactly `down` and `up`, each with `message_id` and `observed_at`.
The attester binds before/armed/stopped/rearmed records, seven artifact digests,
the unchanged installed subject, chronological recovery and distinct Message-IDs.
Failed drills retain evidence and never grant Day 0 admission.

## Install

Install the published Linux wheel for the runner architecture into a dedicated
root-owned virtual environment. Do not modify the Ubuntu system Python and do
not import the SDK from a repository checkout. Verify the downloaded wheel
against `SHA256SUMS.txt` before installation, then confirm that its bundled
native library reports 4.2.0:

```bash
sudo python3 -m venv /opt/boost-gateway-canary/venv
sudo /opt/boost-gateway-canary/venv/bin/pip install \
  /path/to/boost_gateway_sdk-4.2.0-py3-none-manylinux_2_39_aarch64.whl
sudo /opt/boost-gateway-canary/venv/bin/python -c \
  'import boost_gateway_sdk as sdk; print(sdk.assert_compatible_version())'
```

Export the active immutable `/opt/boost-gateway/current/record.json` from the
production host over an authenticated channel. Copy the environment example,
fill the fixed identities and dedicated tokens, then make it root-owned mode
0600. Shell expansion must not be used in token values because systemd reads
the file directly.

```bash
sudo ./deploy/operations/install_external_canary_host_units.sh \
  --environment-file /root/boost-gateway-canary.environment \
  --deployment-record /root/deployment-record.json \
  --run-now
```

The installer creates the unprivileged `boost-gateway-canary` user, copies the
runner and candidate record, validates the environment/candidate/released SDK
and verifies that the local machine ID differs from the production host identity
through the hardened systemd unit, then enables the run and watchdog timers. It
never prints the environment file. Confirm scheduling and
one complete sample:

```bash
systemctl list-timers 'boost-gateway-external-canary*'
journalctl -u boost-gateway-external-canary@run.service --since -10min
sudo find /var/lib/boost-gateway-canary/samples -type f -mmin -2
```

On an external host without systemd, use the same repository entrypoint with a
host scheduler. Pass `--environment-file` instead of exporting credentials into
the scheduler definition. The file must be a regular non-symlink owned by root
or the runner account with no group or other permissions; keys are allowlisted
and values are read literally without shell expansion. Pass a stable regular
host identity file to `--machine-id-path` during validation when the platform
does not provide `/etc/machine-id`.

Schedule `run` at least every 60 seconds. Schedule `watchdog` every 60 seconds
with `--initial-delay-seconds 30` so it checks after the corresponding business
sample finishes. The scheduler must retain non-zero exits, logs and the
create-only evidence root. Keep credentials and candidate records outside the
repository and command-line arguments.

```bash
python external_business_canary.py \
  --environment-file /protected/canary/environment \
  --deployment-record /protected/canary/deployment-record.json \
  --machine-id-path /protected/canary/host-id validate

python external_business_canary.py \
  --environment-file /protected/canary/environment \
  --deployment-record /protected/canary/deployment-record.json run

python external_business_canary.py \
  --environment-file /protected/canary/environment \
  --deployment-record /protected/canary/deployment-record.json \
  watchdog --initial-delay-seconds 30
```

### macOS launchd scheduling

On macOS, keep the canary in a logged-in external user session with the host on
AC power, Tailscale connected and system idle sleep disabled. Display sleep and
screen locking are allowed; lid sleep, logout, reboot and network changes break
the sampling window. Verify the power boundary before declaring a formal start:

```bash
pmset -g batt
pmset -g custom
pmset -g assertions
```

During an admitted observability, shakedown, or long-run window, install the
repository-owned keep-awake LaunchAgent so unplugging AC does not silently let
idle sleep interrupt the canary, Alertmanager forward, or backup-vault receiver.
This is a secondary guard only: the formal host boundary still requires AC power,
an open lid, and a logged-in user session.

```bash
install -m 0644 \
  deploy/operations/io.boostgateway.operations-keepawake.plist \
  "$HOME/Library/LaunchAgents/io.boostgateway.operations-keepawake.plist"
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/io.boostgateway.operations-keepawake.plist"
launchctl print "gui/$(id -u)/io.boostgateway.operations-keepawake"
pmset -g assertions
```

After the governed window has ended, remove the assertion without deleting its
repository definition:

```bash
launchctl bootout "gui/$(id -u)/io.boostgateway.operations-keepawake"
rm -f "$HOME/Library/LaunchAgents/io.boostgateway.operations-keepawake.plist"
```

Use `StartCalendarInterval` with an empty dictionary in both LaunchAgents. All
missing calendar fields are wildcards, so launchd starts the jobs on every
natural minute. The run job starts immediately; the watchdog job passes
`--initial-delay-seconds 30` and therefore checks the same minute after the
business sample. Do not use `StartInterval=60`: launchd may count the interval
after a oneshot exits, so a 10-15 second business flow drifts to 70-75 second
spacing and eventually skips required minutes.

```xml
<key>StartCalendarInterval</key>
<dict/>
```

Bootstrap the two jobs only after validation and the Alertmanager forward pass.
Do not invoke `run` manually while the scheduled job is loaded because two
samples in one UTC minute make the aggregate fail as a duplicate.

```bash
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/io.boostgateway.external-canary-run.plist"
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/io.boostgateway.external-canary-watchdog.plist"
```

Observe at least three consecutive natural-minute samples and their freshness
checks before recording the half-open formal window. If either job is unloaded,
the host sleeps, the candidate changes or a non-maintenance gap exceeds two
minutes, retain the evidence, supersede the declared window and start a new full
window. Stop the jobs without deleting historical samples:

```bash
launchctl bootout "gui/$(id -u)/io.boostgateway.external-canary-run"
launchctl bootout "gui/$(id -u)/io.boostgateway.external-canary-watchdog"
```

When deployment changes, atomically provision the new exported record before
starting its validation window. Aggregation rejects a window containing more
than one candidate or endpoint; never splice timelines across deployments.

## Aggregate and interpret

Approved maintenance is a reviewed JSON input with an ID, reviewer and exact
UTC half-open interval. Do not add a window retrospectively to hide an outage.
Use the checked-in example as the schema.

```bash
sudo -u boost-gateway-canary /opt/boost-gateway-canary/venv/bin/python \
  /usr/local/libexec/boost-gateway-canary/external_business_canary.py \
  aggregate --window 72h --end 2026-08-07T00:00:00Z \
  --maintenance-windows /etc/boost-gateway-canary/maintenance-windows.json

sudo -u boost-gateway-canary /opt/boost-gateway-canary/venv/bin/python \
  /usr/local/libexec/boost-gateway-canary/external_business_canary.py \
  aggregate --window 30d --end 2026-09-06T00:00:00Z \
  --maintenance-windows /etc/boost-gateway-canary/maintenance-windows.json
```

The report includes expected/recorded/successful samples, coverage, recorded
success rate, P50/P99 for every required step, all gaps and non-maintenance
gaps. `availability_including_approved_maintenance` always counts missing or
failed maintenance minutes as failures. The exclusion view is additional and
cannot replace the inclusive hard gate. Formal pass requires one candidate and
endpoint, no invalid/duplicate samples, coverage and inclusive availability
of at least 99.9 percent, and a maximum non-maintenance gap of two minutes.

Keep raw samples, incidents and aggregate reports for at least the governed
45-day observability period. A business failure, stale stream, candidate drift,
invalid sample or Alertmanager delivery failure is an incident input and must
not be silently excluded from a report.
