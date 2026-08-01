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
