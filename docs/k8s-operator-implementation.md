# K8s Operator Implementation Plan

Date: 2026-05-15

## Decision

The repository now contains a real operator scaffold under
`operator/boostgateway-operator/`.

The chosen implementation model is:

- Language: Go
- Framework: `controller-runtime`
- Local environment: `kind`
- Production target: managed Kubernetes

This choice is intentional:

- The cluster control plane work here is reconcile-driven, not request/response.
- Kubernetes operators are materially easier to build and maintain in Go than in
  C++ because the upstream SDK, code samples, and controller ecosystem are in Go.
- `kind` is the lowest-friction local loop for CRD/controller testing and is the
  same shape we can reuse in CI.

## Resource Model

The operator introduces a root custom resource:

- `BoostGatewayCluster`

This is preferred over driving the whole platform from the existing
`GameServer` CRD because the current deployment shape is a multi-service cluster,
not a pool of identical game session pods.

`BoostGatewayCluster` is responsible for:

- `gateway`
- `login`
- `room`
- `battle`
- `match`
- `leaderboard`

Current scaffold behavior:

- Reconciles `Deployment` + `Service` for `gateway/login/room/battle`
- Reconciles `StatefulSet` + headless `Service` for `match/leaderboard`
- Injects stable Raft peer env vars into `match/leaderboard` pods
- Reconciles per-service `ConfigMap` objects and injects them into pod env
- Reconciles a placeholder TLS `Secret` when `.spec.tls.enabled=true`
- Writes basic `.status.phase`, `.status.readyReplicas`, `.status.conditions`
- Uses HTTP readiness/liveness probes when `managementPort` is configured

## Production Shape

The current scaffold is intentionally conservative. Before production rollout,
the operator should evolve as follows.

### Keep as Deployments

- `gateway`
- `login`
- `room`
- `battle`

These are horizontal stateless or near-stateless services from the Kubernetes
point of view.

### StatefulSets

- `match`
- `leaderboard`

Reason:

- These services are the best candidates for stable pod identity.
- The repository now contains in-memory Raft log replication, and stable DNS
  identities are the correct next step for cluster membership.

### Externalize or isolate Redis

Do not couple Redis lifecycle tightly into this operator in the first production
iteration.

Preferred order:

1. managed Redis or cloud cache
2. separate Redis chart/operator
3. only then self-managed Redis inside this operator

## Environment Choice

### Local development

- `kind`
- Docker Desktop on Windows

Why:

- fastest start-up
- easy CRD/controller iteration
- simple CI reuse

### CI

- `kind` again

Why:

- one environment model from laptop to pipeline
- supports install smoke tests for CRD + controller + sample CR

### Staging / production

- managed Kubernetes

Examples:

- EKS
- GKE
- AKS

Why:

- keeps the team focused on service and operator correctness instead of control
  plane operations

## Next Implementation Steps

1. Keep both fake-client and `envtest` reconcile tests:
   fake-client stays as the fast edit loop, `envtest` becomes the API-accurate gate.
2. Add cert-manager integration for TLS secrets.
3. Inject Raft peer membership into `match` and `leaderboard` pods from stable DNS.
4. Rework Helm so Helm installs the operator and a sample `BoostGatewayCluster`,
   instead of trying to template every runtime object directly.
5. Add a CI `kind` smoke test that runs `make install`, `make install-sample`,
   and validates generated Deployments / StatefulSets.

## Notes About Existing Assets

Current repository state:

- `env/k8s/operator/` contains earlier static CRD/RBAC assets.
- `env/k8s/helm/boost-gateway/templates/` is empty.

That means the old K8s module was documentation-grade, not controller-grade.
The new operator scaffold is the first implementation path that can grow into a
real platform control plane.
