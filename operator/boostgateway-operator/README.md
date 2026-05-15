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
- Injects Raft peer environment variables for stateful components:
  - `RAFT_NODE_ID`
  - `RAFT_PEERS`
  - `RAFT_ELECTION_TIMEOUT_MIN_MS`
  - `RAFT_ELECTION_TIMEOUT_MAX_MS`
  - `RAFT_HEARTBEAT_INTERVAL_MS`
- Updates `.status.phase`, `.status.readyReplicas`, and a basic `Ready` condition
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

## Next steps

- Wire TLS and cert-manager objects
- Reconcile `status.conditions` from actual pod readiness and rollout state
- Turn the sample install flow into a CI `kind` smoke test
