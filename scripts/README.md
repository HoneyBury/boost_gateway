# Script Inventory

The maintained script index is `docs/script-inventory.json`.

Canonical implementation paths may live under role-oriented subdirectories such
as `scripts/gates/` and `scripts/lib/`. Root-level script names remain stable
compatibility shims unless explicitly retired.

Canonical groups migrated so far:

- SDK gates: `scripts/gates/sdk/`
- Production/recovery/evidence gates: `scripts/gates/production/`
- Transport/TLS gates: `scripts/gates/transport/`
- Governance/docs/config gates: `scripts/gates/governance/`
- Release/RC/perf gates: `scripts/gates/release/`
- Tools: `scripts/tools/`
- Producers: `scripts/producers/`
- Wrappers: `scripts/wrappers/ps1/` and `scripts/wrappers/sh/`
- CI/CD runner matrix helper: `scripts/tools/read_runner_matrix.py`
- Conan bootstrap helper: `scripts/tools/bootstrap_conan.py`
- Pinned isolated Conan environment helper: `scripts/tools/ensure_conan_venv.py`
- Conan lockfile helper: `scripts/tools/generate_conan_lock.py`
- Fixed-runner Conan/sccache namespace resolver: `scripts/tools/resolve_runner_cache.py`
- Conan lockfile workflow gate: `scripts/check_conan_lockfile_workflows.py`
- Workflow catalog gate: `scripts/check_workflow_catalog.py`
- Workflow Python CLI contract gate: `scripts/check_workflow_python_cli_contracts.py`
- Evidence provenance contract gate: `scripts/check_evidence_provenance_contract.py`
- R5 Docker image policy contract gate: `scripts/check_r5_docker_image_policy_contract.py`
- R5 offline image-cache transport: `scripts/tools/r5_docker_cache_bundle.py`

Use these stable public entrypoints first:

- `verify_release_candidate.py` for local/PR bounded release checks.
- `check_mainline_readiness.py` for docs, script, config, and evidence governance checks.
- `check_legacy_helper_inventory.py` for legacy/helper compatibility-surface governance.
- `check_workflow_catalog.py` and `check_workflow_python_cli_contracts.py` for workflow inventory and workflow-to-script CLI drift governance before pushing CI changes.
- `check_evidence_provenance_contract.py` for R2/R3 same-candidate provenance and decision-path regression coverage.
- `check_r5_docker_image_policy_contract.py` for cached/offline/missing/refresh R5 image policy regression coverage.
- `verify_production_candidate_evidence.py` for R0 production-candidate aggregation.
- `check_production_evidence_manifest.py` and `render_production_readiness_report.py` for R2/R3 production readiness.
- `run_long_soak_capacity.py` for fixed-runner N1 long-soak/capacity evidence.
- `verify_sdk_enterprise_delivery.py` for N5 SDK delivery.
- `verify_preprod_recovery_drill.py` and `verify_tls_preprod_multi_run.py` for R5/R6 pre-production evidence.
- `manage_todos.py` for the versioned project TODO board and explicit GitHub Issue synchronization.

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
