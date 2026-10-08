# Enterprise Operations Readiness Decision

Generated at: `2026-10-08T12:30:00Z`

## Decision

**READY FOR A CONTROLLED CLOUD SINGLE-NODE PILOT.** This decision accepts the
software-stability result under the user-approved home-network boundary. It does
not claim that the original 30-day automated gate passed, and it does not approve
unrestricted public production, multi-node HA, cross-zone disaster recovery, or
an arbitrary capacity envelope.

- Audit result: **PASS**
- Original governed finalizer: **FAIL PRESERVED**
- Supplemental formal 30-day claim: **false**
- Unrestricted production ready: **false**

## Independently Recomputed Gates

| Metric | Recomputed value | Required interpretation |
| --- | ---: | --- |
| Window duration | 2,592,000 seconds | complete fixed wall-clock interval |
| Expected canary samples | 43,200 | immutable denominator |
| Recorded / successful / failed / missing | 43,156 / 43,154 / 2 / 44 | original evidence retained |
| Raw coverage | 99.898148% | original gate below 99.9% |
| Raw inclusive availability | 99.893519% | original gate below 99.9% |
| Recorded-sample success | 99.995366% | diagnostic only |
| Scope-adjusted successful minutes | 43,154 / 43,154 | 100%, approved software scope |
| Prometheus coverage / samples | 100.00% / 172,800 | passes 99.9% coverage |
| Maximum Prometheus gap | 15 seconds | passes 120-second limit |
| Peak host memory | 12.94% | passes 80% limit |
| Peak filesystem use | 30.17% | passes 75% limit |
| Governed containers / maximum restarts | 13 / 0 | zero unknown restarts |
| Ledger daily / weekly / incident records | 76 / 14 / 4 | traceability present |
| Off-host verified checksums | 290 | 289 entries plus manifest |

## Audit Checks

| Check | Result | Evidence |
| --- | --- | --- |
| `raw-count-balance` | PASS | expected=43200, recorded=43156, successful=43154, failed=2, missing=44 |
| `raw-duration` | PASS | duration_seconds=2592000 |
| `raw-recomputed-rates` | PASS | coverage=0.998981481481, availability=0.998935185185, recorded_success=0.999953656502 |
| `raw-result-preserved` | PASS | original finalizer FAIL and absent controller final.json remain explicit |
| `candidate-consistent` | PASS | candidate={'commit': 'db0f905d0421b2052b9de7f49d9bf71787915e23', 'deployment_id': 'v3.6.7-fb5f6bfb2626-fa8b69b36dec', 'runtime_digest': 'sha256:2f2c2b7408ffde856815309b4dd7b29446558b0415a0971c32c333140b5a8aff', 'tag': 'v3.6.7'} |
| `scope-raw-matches` | PASS | scope assessment repeats the immutable aggregate without rewriting it |
| `scope-adjusted-recomputed` | PASS | evaluated=43154, successful=43154, excluded_missing=44, excluded_failed=2 |
| `host-window-hard-gates` | PASS | coverage, gap, memory, disk, restart, OOM, thermal, growth and storage thresholds pass |
| `lifecycle-verification` | PASS | passed=22/22 |
| `alert-delivery` | PASS | email_total=37->39, failures=3->3 |
| `observability-preflight` | PASS | preflight passes and binds the final alert delivery attestation |
| `backup-vault-copy` | PASS | backup_id=todo0012-scheduled-20261008T022120Z-743a7dc7 |
| `backup-retention` | PASS | retained=12, deleted=1 |
| `offhost-final-package` | PASS | manifest_entries=289, verified_checksums=290 |
| `ledger-coverage` | PASS | daily=76, weekly=14, incident=4 |
| `evidence-hash-bindings` | PASS | completion marker binds all independently loaded closure inputs |
| `completion-boundary` | PASS | scope-adjusted completion is explicit; formal_30_day_claim remains false |

## Proven Scope

- Immutable Linux x64 release consumption and a single-node Ubuntu 24.04 deployment without a server-side source build.
- Idempotent install, upgrade, verification, rollback, and reboot recovery with retained deployment identity.
- External full-flow canary, Prometheus host/container/application coverage, Alertmanager delivery, and an auditable ledger.
- Redis AOF/RDB policy, encrypted off-host backup, retention, isolated restore, and business-flow recovery.
- A 72-hour immutable shakedown with 4,320/4,320 successful samples and the scope-adjusted 30-day software result with 43,154/43,154 successful applicable minutes.
- Zero governed container restarts, OOM events, sustained thermal throttling, storage errors, or unexplained resource growth during the final window.

## Unsupported Or Unproven Scope

- The original controller finalizer did not pass and did not create `final.json`; its immutable result remains authoritative.
- No multi-node failover, quorum service, cross-availability-zone continuity, or regional disaster recovery claim is supported.
- Historical capacity measurements are candidate- and runner-specific; the selected cloud instance has no production capacity envelope yet.
- Default production traffic is plain TCP. A public cloud endpoint requires TLS/mTLS or a private network boundary before exposure.
- Linux ARM64 and macOS ARM64 evidence remains platform-specific and cannot substitute for the Linux x64 cloud target. Linux ARM64 SDK Issue #63 remains open.
- The home-router exclusion applies only to the completed home validation. Cloud network, disk, and instance failures are production incidents.

## Performance And Resilience Change Audit

| Work item | RCA and before evidence | After evidence and regression | Rollback decision |
| --- | --- | --- | --- |
| TODO-0005 / Issue #19 | Linux ARM64 tail attributed to Gateway route queue/topology under the recorded runner layout. | Isolated runs kept the 250 ms gate; battle-100 P99 was 10/10/10 ms with zero failures. | Shipped only in immutable v3.6.2; revert the isolated runtime change with a new release. |
| Issue #65 / PR #67 | Battle/Tank plugin state ownership leaked after instance teardown. | Mainline and ASan/UBSan/LSan passed with leak detection enabled and no suppressions. | Single runtime commit revert; do not disable leak detection. |
| Issue #72 / v3.6.5 | Backend session threads and stale pooled routes caused five observed transport timeouts and backend memory growth. | Corrected release passed full regression and a 4,320/4,320 external window. | Roll back through the governed immutable release lifecycle. |
| Issue #78 / PR #79 | Completed Battle runtime, replay, and per-battle state caused 0.48-0.50 MiB/hour linear RSS growth. | 2,048 lifecycle cycles, full ServiceBusIntegrity, CI, and ASan/UBSan/LSan passed; the final host audit found no unexplained growth. | Revert PR #79 only through a new release; the leaking candidates remain rejected. |
| Issue #83 / PR #82 | Redis RDB stale alert contradicted the active `save` thresholds while AOF remained healthy. | PromQL, 142 monitoring checks, governance, hosted CI, and fixed-runner CI passed without weakening persistence gates. | Revert the rule change if it suppresses a real checkpoint failure; retain separate AOF/BGSAVE critical alerts. |
| Issue #86 | Login session and token maps were not cleared on disconnect; unchanged resource gate measured 0.713714 MiB/window. | Same eight-window workload and thresholds measured 0.0 MiB/window, zero FD/thread growth, and full-flow PASS. | Single commit revert; the original failing run remains the comparison baseline. |

The SMTP relay startup, TLS hostname, backup-vault locking/capacity, dead-man,
and fixed-end controller corrections are retained as operational incident and
deployment evidence. They changed no performance threshold and were verified
through their respective recovery, delivery, or governance gates.

## Next Release Inputs

1. `TODO-0020`: admit a reproducible Ubuntu 24.04 x64 cloud target with IaC, private management access, durable storage, time synchronization, firewall rules, and secrets outside the repository.
2. `TODO-0021`: deploy the immutable release through the governed lifecycle, provide TLS/mTLS or private-only business ingress, and preserve the current host as rollback until cutover acceptance.
3. `TODO-0022`: move backup and evidence copies to a separate cloud failure domain and execute real restore and host-replacement drills.
4. `TODO-0023`: establish a capacity envelope on the selected cloud SKU, run a seven-day low-traffic pilot, and use cloud incidents without the historical home-network exclusion.
5. Keep multi-node HA and cross-zone disaster recovery as separate architecture work; they are not prerequisites for the controlled single-node pilot.

## Evidence Digests

| Input | SHA-256 |
| --- | --- |
| `aggregate` | `a92c8ca13937fa7819a49eb0fc0bae6a219391d7d6c4cd727d8ac53d49f2a48d` |
| `alert` | `d31b1039baaedb3a53fa569ed5e0a787630c2761ea72cce4f88b8f7ec3548748` |
| `backup_receipt` | `394368aed6c88638e27f4f3d3c7ce5ce281d80f958206918e63cc933fdd5f865` |
| `completion` | `d217dcf3114f9d4a8099c0c75de46a77c79c8d2af57cc133ae13fc8f765032c7` |
| `host` | `bf181b412968ec233cc140d54e7fb919a5c17fea476ca8acaab17eb91f8e1b72` |
| `lifecycle` | `c02c218b6130d9d1106dbf651a0cae5d5e29677f525a1821f0563412bdf9e253` |
| `offhost` | `fda292f91fab5c64fa70e2530525cd3f938188923a658758bc441b5e85cad7f3` |
| `preflight` | `cb273edf29de0a56690207f05fb4bce512c062ab10f8c39c6d3efbc81c8ed9eb` |
| `retention` | `29cb2adfe22d3a0ac1690b88a35f58d40143d30c40b6c350d193cdd9faeca3c3` |
| `scope` | `8332bd56c2d157baea3dc9be002a4f678107bb3d7bbf89df1eefa5e827740cf1` |
