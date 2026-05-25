# Script Inventory

The maintained script index is `docs/script-inventory.json`.

Use these stable public entrypoints first:

- `verify_release_candidate.py` for local/PR bounded release checks.
- `check_mainline_readiness.py` for docs, script, config, and evidence governance checks.
- `verify_production_candidate_evidence.py` for R0 production-candidate aggregation.
- `check_production_evidence_manifest.py` and `render_production_readiness_report.py` for R2/R3 production readiness.
- `run_long_soak_capacity.py` for fixed-runner N1 long-soak/capacity evidence.
- `verify_sdk_enterprise_delivery.py` for N5 SDK delivery.
- `verify_preprod_recovery_drill.py` and `verify_tls_preprod_multi_run.py` for R5/R6 pre-production evidence.

Other scripts are internal producers, aggregate gates, tooling, platform wrappers, or legacy compatibility surfaces. Keep new workflow and documentation references on the public entrypoints unless there is a specific reason to call a producer directly.
