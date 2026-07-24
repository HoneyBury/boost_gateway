# BoostGateway Operator

This directory contains the first runnable scaffold for a `BoostGatewayCluster`
operator built with `controller-runtime`.

## Scope

- `BoostGatewayCluster` root custom resource
- Reconciles `Deployment` + `Service` pairs for:
  - `gateway`
  - `login`
  - `room`
  - `battle`
- Reconciles `StatefulSet` + headless `Service` pairs for:
  - `match`
  - `leaderboard`
- Reconciles per-service `ConfigMap` objects and injects them via `envFrom`
- Reconciles a placeholder TLS `Secret` when `.spec.tls.enabled=true`
- Reconciles a cert-manager `Certificate` when
  `.spec.tls.managedByCertManager=true` and `.spec.tls.certManagerIssuer` is set
- Injects Raft peer environment variables for stateful components:
  - `RAFT_NODE_ID`
  - `RAFT_PEERS`
  - `RAFT_ELECTION_TIMEOUT_MIN_MS`
  - `RAFT_ELECTION_TIMEOUT_MAX_MS`
  - `RAFT_HEARTBEAT_INTERVAL_MS`
- Updates `.status.phase`, `.status.readyReplicas`, `.status.desiredReplicas`,
  `.status.components[]`, and `Ready` / `Progressing` / `Degraded` /
  `TLSReady` conditions
- Designed for local development on `kind`

## Layout

- `api/v1alpha1/`: API types and scheme registration
- `internal/controller/`: reconcile loop
- `config/`: install manifests
- `hack/kind-config.yaml`: local cluster bootstrap

## Local workflow

```bash
kind create cluster --config hack/kind-config.yaml
kubectl create namespace boost-gateway
make install
make install-sample
```

## Test workflow

```bash
make test
```

The repository-level P5 gate wraps this default test path:

```bash
python3.12 ../../scripts/gates/production/verify_control_plane_gate.py
```

The default gate also validates the committed CRD, RBAC, manager deployment,
and sample `BoostGatewayCluster` manifests:

```bash
python3.12 ../../scripts/gates/k8s/check_operator_manifests.py
```

`envtest` is also wired for reconcile-level validation. It requires
`KUBEBUILDER_ASSETS` to point at the local API server / etcd binaries.

```bash
set KUBEBUILDER_ASSETS=C:\path\to\kubebuilder\bin
make test-envtest
```

For a full local install smoke path on `kind`:

```bash
make kind-smoke
```

The kind smoke path installs the CRD/controller/sample, waits for the sample
`BoostGatewayCluster` to become `Running`, asserts steady-state conditions and
six component status entries, then deletes the sample CR and waits for deletion.

Repository-level fixed-runner variants:

```bash
python3.12 ../../scripts/gates/production/verify_control_plane_gate.py --include-envtest
python3.12 ../../scripts/gates/production/verify_control_plane_gate.py --include-kind
python3.12 ../../scripts/gates/production/verify_control_plane_gate.py --include-envtest --include-kind
```

## Next steps

- Add rollout/rollback failure injection beyond the current status/delete smoke
- Exercise readiness/liveness probe failures on a fixed kind runner
- Decide whether Helm should install only the operator or also a sample
  `BoostGatewayCluster`
