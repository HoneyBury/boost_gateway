# Script Inventory

The maintained script index is `docs/script-inventory.json`.

Every stable public entrypoint also has machine-checked lifecycle and discovery
metadata in that inventory: owner, maintenance domain, purpose, authoritative
documentation, support level, execution environment, typical duration, external
side effects, and an explicit retirement condition. Update the metadata in the
same change whenever an entrypoint's operational contract changes.

Before adding a canonical CLI or a file under `scripts/tools/` or `scripts/lib/`,
prefer extending an existing command. Move implementation to `scripts/lib/` only
when multiple commands share a directly tested, CLI-free contract. A genuinely new
surface must add a `script_growth_exceptions` record to the inventory with its
domain, consumers, direct test, reason it cannot extend an existing entrypoint,
replacement, retirement condition, temporary status, and expiry date. The tooling
metrics gate rejects missing, stale, invalid, or expired records and unreviewed
workflow dependency or cross-CLI import growth.

For day-to-day contributor work, start with the thin task facade instead of
discovering individual scripts:

```bash
python3.12 scripts/dev.py doctor
python3.12 scripts/dev.py commands --domain contributor
python3.12 scripts/dev.py check
python3.12 scripts/dev.py test unit --build-dir build/contributor-debug --verbose
python3.12 scripts/dev.py smoke --build-dir build/contributor-debug
```

`dev.py` only composes stable entrypoints. Canonical scripts remain directly
callable for CI, fixed-runner evidence, debugging, and documented operations.
Use `dev.py commands` (optionally with `--domain` or `--json`) to discover those
stable entrypoints instead of scanning this document or the physical script tree.

Canonical implementation paths may live under role-oriented subdirectories such
as `scripts/gates/` and `scripts/lib/`. Root-level script names remain stable
compatibility shims unless explicitly retired.

Canonical groups migrated so far:

- SDK gates: `scripts/gates/sdk/`
- Production/recovery/evidence gates: `scripts/gates/production/`
- Transport/TLS gates: `scripts/gates/transport/`
- Identity/security gates: `scripts/gates/security/`
- Governance/docs/config gates: `scripts/gates/governance/`
- Release/RC/perf gates: `scripts/gates/release/`
- Tools: `scripts/tools/`
- Producers: `scripts/producers/`
- CI/CD runner matrix helper: `scripts/tools/read_runner_matrix.py`
- Conan bootstrap helper: `scripts/tools/bootstrap_conan.py`
- Pinned isolated Conan environment helper: `scripts/tools/ensure_conan_venv.py`
- Conan lockfile helper: `scripts/tools/generate_conan_lock.py`
- Fixed-runner Conan/sccache namespace resolver: `scripts/tools/resolve_runner_cache.py`
- Conan lockfile workflow gate: `scripts/gates/governance/check_conan_lockfile_workflows.py`
- Workflow catalog gate: `scripts/gates/governance/check_workflow_catalog.py`
- Tooling metrics drift gate: `scripts/gates/governance/check_tooling_metrics.py`
- Repository governance gate: `scripts/gates/governance/check_repository_governance.py`
- Workflow Python CLI contract gate: `scripts/gates/governance/check_workflow_python_cli_contracts.py`
- Evidence provenance contract gate: `scripts/gates/governance/check_evidence_provenance_contract.py`
- R5 Docker image policy contract gate: `scripts/gates/governance/check_r5_docker_image_policy_contract.py`
- R5 offline image-cache transport for `linux/amd64` and `linux/arm64`: `scripts/tools/r5_docker_cache_bundle.py`

Use these stable public entrypoints first:

- `dev.py` for contributor diagnostics, bounded governance, tests, and the first business smoke.
- `verify_release_candidate.py` for local/PR bounded release checks.
- `check_mainline_readiness.py` for docs, script, config, and evidence governance checks.
- `check_legacy_helper_inventory.py` for legacy/helper compatibility-surface governance.
- `check_workflow_catalog.py` and `check_workflow_python_cli_contracts.py` for workflow inventory and workflow-to-script CLI drift governance before pushing CI changes.
- `check_repository_governance.py` for CODEOWNERS, contribution, security disclosure, support, and emergency-change policy drift.
- `check_evidence_provenance_contract.py` for R2/R3 same-candidate provenance and decision-path regression coverage.
- `check_r5_docker_image_policy_contract.py` for cached/offline/missing/refresh R5 image policy regression coverage.
- `verify_production_candidate_evidence.py` for R0 production-candidate aggregation.
- `check_production_evidence_manifest.py` and `render_production_readiness_report.py` for R2/R3 production readiness.
- `run_long_soak_capacity.py` for fixed-runner N1 long-soak/capacity evidence.
- `verify_sdk_enterprise_delivery.py` for N5 SDK delivery.
- `verify_preprod_recovery_drill.py` and `verify_tls_preprod_multi_run.py` for R5/R6 pre-production evidence.
- `verify_jwks_rotation.py` for the real HTTPS multi-`kid` rotation, stale-grace, outage, and rollback drill.
- `manage_todos.py` for the versioned project TODO board and explicit GitHub Issue synchronization.
- `manage_release_deployment.py` for immutable release install, deploy, upgrade, rollback, status, verification
  transactions, and fail-closed Redis persistence-mode transitions.
- `check_operations_host.py` for fail-closed Ubuntu operations-host admission and real reboot verification.
- `apply_operations_host_baseline.py` for the explicit plan/apply surface that converges the admitted host security baseline.
- `prepare_release_runtime.py` for anonymous download, supply-chain verification, and atomic Linux x64 release staging.
- `build_release_images.py` for network-disabled runtime-only image builds and immutable image-ID output.
- `verify_release_deployment.py` for resolved production Compose, health, Redis, and release SDK full-flow verification.
- `collect_redis_persistence_metrics.py` for fail-closed effective AOF/RDB state and delayed-fsync
  textfile metrics consumed by node-exporter.
- `prepare_redis_persistence_transition.py` for write-frozen BGSAVE, active-volume binding and offline
  RDB validation before any release changes the Redis persistence mode.
- `benchmark_redis_persistence.py` for lifecycle-locked RDB-only versus AOF-everysec Redis workload and
  explicit-checkpoint measurements on disposable server/client topologies without changing production.
- `review_redis_persistence_benchmark.py` for binding those measurements to the governed TODO-0012
  performance decision and data-compatible rollback contract without claiming production activation.
- `manage_backup_recovery.py`, `backup_vault_ssh_receiver.py`, and `verify_backup_vault.py` for create-only
  age-encrypted off-host backups, restricted SSH receipt handling, and link-free vault/RDB verification.
- `run_scheduled_backup.py` for fail-closed daily/weekly classification, forced-command upload, independent
  archive/manifest/receipt readback, and create-only systemd evidence summaries.
- `external_business_canary.py` for the external released-Python-SDK login/room/battle/settlement/
  leaderboard/reconnect sample, direct Alertmanager incident input, stale-stream watchdog, and create-only
  72-hour/30-day availability, latency and gap aggregation.
- `gates/governance/verify_release_source_authorization.py` for blocking release publication unless the
  annotated tag or manual rehearsal is bound to governed `main` and passing same-revision evidence.
- `export_backup_restore_bundle.py`, `send_restore_bundle.py`, `restore_bundle_ssh_receiver.py`,
  `restore_backup_isolated.py`, and `verify_restored_business_isolated.py` for Mac-only secret-bearing archive
  decryption, pinned forced-command transfer, fresh-volume Redis restore, and isolated release SDK business
  verification without a production switch.

R5 offline-cache execution order:

1. Create or verify the pinned Conan virtual environment with
   `ensure_conan_venv.py`; then run `resolve_runner_cache.py` on each runner and warm the exact Conan
   namespace from its lockfile. Production evidence consumes that namespace with
   `scripts/bootstrap_conan.py --no-remote`.
2. On a clean candidate checkout, use `r5_docker_cache_bundle.py export`; it
   rejects dirty source, candidate SHA drift, Compose drift, non-amd64 images
   and missing registry digests.
3. Import the bundle on the target runner, run
   `verify_preprod_recovery_drill.py --image-preflight-only --docker-pull-policy never`,
   then run the full Docker Compose recovery drill or `preprod-evidence.yml`.

`resolve_runner_cache.py` requires a runner-owned persistent root. Its default
is `/opt/boost-gateway`; provision `/opt/boost-gateway/conan` and
`/opt/boost-gateway/sccache` before dispatching fixed-runner workflows. See
`docs/fixed-runner-playbook.md` for the exact command and observed disk budget.

Other scripts are internal producers, aggregate gates, tooling, platform wrappers, or legacy compatibility surfaces. Keep new workflow and documentation references on the public entrypoints unless there is a specific reason to call a producer directly.
