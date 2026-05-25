# Script And Config Consolidation Plan

This plan keeps current script paths stable while making ownership explicit.

## Stage 1: Inventory

`docs/script-inventory.json` is the maintained index for all top-level scripts.
New scripts must be added there before they are referenced by workflows or docs.

Stable public entrypoints should remain small in number. Prefer calling:

- `scripts/verify_release_candidate.py`
- `scripts/check_mainline_readiness.py`
- `scripts/verify_production_candidate_evidence.py`
- `scripts/check_production_evidence_manifest.py`
- `scripts/render_production_readiness_report.py`
- `scripts/run_long_soak_capacity.py`
- `scripts/verify_sdk_enterprise_delivery.py`
- `scripts/verify_preprod_recovery_drill.py`
- `scripts/verify_tls_preprod_multi_run.py`

## Stage 2: Summary Contract

Production evidence summaries must expose:

- `summary_version: 2`
- `overall_pass` and `passed`
- `failed_category`
- `failed_step`
- `artifacts`

`scripts/check_validation_summary_contract.py` verifies the current core evidence
set. Producer scripts that still emit legacy summaries should be upgraded before
they are promoted to public entrypoints.

## Stage 3: Workflow Boundaries

Workflows should call one public entrypoint or one aggregate gate per stage, then
render summaries with `scripts/render_validation_summary.py`.

Long-running workflows are fixed-runner only:

- `.github/workflows/long-soak-capacity.yml`
- `.github/workflows/production-evidence.yml`
- `.github/workflows/release-baseline.yml`
- `.github/workflows/specialized-e2e.yml`

Default CI/release workflows must stay bounded.

## Stage 4: Config Source Of Truth

`env/` is the production configuration source of truth:

- `env/docker/`
- `env/k8s/`
- `env/monitoring/`
- `env/redis/`

Root-level `docker-compose.yml`, `docker-compose.operator.yml`, `prometheus/`,
`grafana/`, and `k8s/` are legacy/reference surfaces unless explicitly promoted
through `env/` and the governance checks.

## Stage 5: Physical Script Moves

Do not move scripts directly. Use this migration sequence:

1. Add the target classification to `docs/script-inventory.json`.
2. Move implementation to a future subdirectory such as `scripts/gates/`,
   `scripts/producers/`, `scripts/tools/`, or `scripts/legacy/`.
3. Keep a top-level shim at the old path for at least one release cycle.
4. Update workflows and docs to call the public entrypoint, not the internal path.
5. Remove the shim only after `docs/reliability-matrix.md` and all workflows no
   longer reference it.

This avoids breaking existing fixed-runner automations while still allowing the
script tree to become less crowded over time.
