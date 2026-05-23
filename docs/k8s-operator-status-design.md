# K8s Operator Status Design

Date: 2026-05-23

## Overview

This document describes the status reporting design for the Boost Gateway K8s operator. The operator manages `BoostGatewayCluster` custom resources and reports status via standard Kubernetes status conditions and a `components[]` array.

There are two independent operator implementations in the repository:

| Implementation | Language | Framework | CRD | Location |
|---|---|---|---|---|
| Go operator (primary) | Go | controller-runtime | `BoostGatewayCluster` | `operator/boostgateway-operator/` |
| Python kopf operator (legacy) | Python | kopf | `GatewayServer` | `k8s/operator/operator.py` |

The Go operator is the active implementation. The Python operator predates it and is maintained for documentation/backup.

---

## 1. Status Conditions

Four standard Kubernetes conditions are reported under `.status.conditions[]`.

### 1.1 Ready

**Logic (Go controller, `boostgatewaycluster_controller.go` lines 141-168):**

```
Ready = True  IF  totalDesired > 0
               AND totalReady >= totalDesired
               AND allRolloutsReady == true
         False OTHERWISE
```

- `totalReady` is the sum of `ReadyReplicas` across all six components
- `totalDesired` is the sum of `Spec.Replicas` for all enabled components
- `allRolloutsReady` requires each component's `ObservedGeneration >= Generation`, `ReadyReplicas >= desired`, `UpdatedReplicas >= desired`, and (for deployments) `AvailableReplicas >= desired`

**Reasons:**
- `"ComponentsReady"` — all enabled components fully available
- `"WaitingForReplicas"` — still waiting for replicas to become ready

**Python operator equivalent (`operator.py` lines 304-320):**

- Polls each of the 6 component Deployments every 30 seconds
- `Ready = True` when all components report `ready == true` (i.e., `available_replicas > 0` and `available >= desired`)
- Tracks `failedHealthChecks` counter; resets to 0 when all components are ready

### 1.2 Progressing

**Logic (Go controller):**

```
Progressing = True   IF  totalReady < totalDesired
                       OR any component rollout is incomplete
              False  OTHERWISE
```

- During initial deployment or rolling update, `Progressing=True` with reason `"RolloutInProgress"`
- When `Ready=True`, `Progressing` is set to `False` with reason `"RolloutComplete"`

**Python operator equivalent (`operator.py` lines 323-342):**

- `Progressing = True` when any component has `available < replicas` for `replicas > 0`
- Reason `"RollingUpdateInProgress"` lists the component names still rolling
- Reason `"NoRolloutInProgress"` when all components are stable

### 1.3 Degraded

**Logic (Go controller, lines 143-167):**

```
Degraded = True   IF  len(degradedReasons) > 0
                    OR (totalReady == 0 AND totalDesired > 0)
               False OTHERWISE
```

A component is considered degraded when:
- **Deployment**: `ObservedGeneration < Generation` (stale), or `UpdatedReplicas < desired`, or `AvailableReplicas < ReadyReplicas`
- **StatefulSet**: `ObservedGeneration < Generation` (stale), or `UpdatedReplicas < desired`

**Reasons:**
- `"RolloutHealthIssue"` — one or more components have specific health problems (list is concatenated)
- `"NoReadyReplicas"` — no component reports any ready replicas despite desired > 0
- `"NoIssuesDetected"` — normal operation

**Python operator equivalent (`operator.py` lines 344-362):**

- Uses a consecutive failure counter (`failedHealthChecks`)
- `Degraded = True` when `consecutive_failures >= 3`
- The counter increments on each health check tick where any component is not ready
- Resets to 0 when all components are ready
- Reason `"ConsecutiveHealthCheckFailures"` with count in message

### 1.4 TLSReady

**Logic (Go controller):**

```
TLSReady = True   IF  spec.tls.enabled == true
              False IF  spec.tls.enabled is false or nil (TLS not configured)
```

Note: In the Go controller, `TLS` status is a configuration-level check, not a runtime secret verification.

**Reasons (Go):**
- `"TLSDisabled"` — TLS is not configured (status=False)
- `"CertManagerConfigured"` — TLS enabled, cert-manager issuer configured
- `"StaticSecretConfigured"` — TLS enabled, static secret configured

**Python operator equivalent (`operator.py` lines 365-411):**

- Actively checks for TLS Secret existence via CoreV1 API
- `True` when TLS is not configured (vacuous true), OR the named Secret exists
- `False` when a Secret name is configured but the Secret is not found
- `Unknown` when an API error occurs during check

---

## 2. Components Array

Each `.status.components[]` entry represents one of the six backend services managed by the operator.

### 2.1 Go Operator Component Status Fields

| Field | Type | Source | Description |
|---|---|---|---|
| `name` | string | Static | Component name: `gateway`, `login`, `room`, `battle`, `match`, `leaderboard` |
| `kind` | string | Dynamic | `"Deployment"` for stateless services, `"StatefulSet"` for `match`/`leaderboard` |
| `desiredReplicas` | int32 | `spec.replicas` | Desired replica count from the CR spec |
| `readyReplicas` | int32 | `status.readyReplicas` | Number of pods in Ready state |
| `updatedReplicas` | int32 | `status.updatedReplicas` | Number of pods at the current spec revision |
| `availableReplicas` | int32 | `status.availableReplicas` | Number of pods available to serve traffic |
| `observedGeneration` | int64 | `status.observedGeneration` | Last generation the controller observed |

### 2.2 Python Operator Component Status Fields

| Field | Type | Source | Description |
|---|---|---|---|
| `name` | string | Static | `gateway-server`, `login-backend`, `room-backend`, `battle-backend`, `matchmaking-backend`, `leaderboard-backend` |
| `kind` | string | Hardcoded | Always `"Deployment"` |
| `ready` | bool | `ready_replicas > 0 AND available >= desired` | Is the component healthy |
| `replicas` | int | `spec.replicas` | Desired replica count |
| `available` | int | `status.available_replicas` | Currently available pods |
| `message` | string | Derived | `nil` when healthy, `"Waiting for rollout: X/Y replicas available"` or `"Not found"` when missing |

### 2.3 Component Mapping

| Component | Go Operator Type | Python Operator Type | Raft Support |
|---|---|---|---|
| gateway | Deployment | Deployment | No |
| login | Deployment | Deployment | No |
| room | Deployment | Deployment | No |
| battle | Deployment | Deployment | No |
| match | StatefulSet | Deployment | Yes (headless service + stable DNS) |
| leaderboard | StatefulSet | Deployment | Yes (headless service + stable DNS) |

---

## 3. Status Update Triggers

### 3.1 Go Controller

| Trigger | Mechanism | Frequency |
|---|---|---|
| CR create | `Reconcile()` called by controller-runtime | Once on creation |
| CR update | `Reconcile()` called when `.spec` changes | On each spec change |
| Child change | Watches on Deployment/StatefulSet/Service | On any child object mutation |
| Status diff | `status.Write()` called only when `!reflect.DeepEqual(cluster.Status, desiredStatus)` | After each reconciliation where status changed |

Status is written only on actual change (`DeepEqual` check on line 206 of the controller). This avoids unnecessary writes during idle periods.

### 3.2 Python Operator

| Trigger | Decorator | Frequency |
|---|---|---|
| CR create | `@kopf.on.create` | Once on creation |
| CR update | `@kopf.on.update` | On spec change |
| CR delete | `@kopf.on.delete` | Once on deletion |
| Operator restart | `@kopf.on.resume` | On operator startup for each existing CR |
| Periodic health check | `@kopf.timer(interval=30.0)` | Every 30 seconds |

The Python operator does NOT watch child object changes; it relies entirely on the 30-second timer for health assessment.

### 3.3 Phase Transitions

```
                  create
    [PENDING] ──────────► [PROGRESSING]
                              │
                    Ready? ───┤
                              │
                    all ready ▼
                          [RUNNING]
                              │
                    spec change? ──► back to [PROGRESSING]
                              │
                    delete ────► [DRAINING] ──► (removed)
```

The Go controller sets `phase`:
- `"Progressing"` during initial deployment or rolling update
- `"Running"` when all enabled components are ready and rollouts are complete

The Python operator sets `phase`:
- `"Running"` unconditionally after create/update/resume (no phase=Progressing state)

---

## 4. Current Implementation Status

### 4.1 Go Operator (Primary)

| Feature | Status | Location |
|---|---|---|
| Ready condition | Implemented | `boostgatewaycluster_controller.go:141-168` |
| Progressing condition | Implemented | `boostgatewaycluster_controller.go:141-168` |
| Degraded condition | Implemented (generation/replica heuristic) | `boostgatewaycluster_controller.go:141-168` |
| TLSReady condition | Implemented (config-level only) | `boostgatewaycluster_controller.go:197-204` |
| components[] array | Implemented (6 components) | `boostgatewaycluster_controller.go:67-139` |
| Type-level status fields | Implemented | `boostgatewaycluster_types.go:38-54` |
| CRD schema | Implemented | `config/crd/bases/gateway.boost.io_boostgatewayclusters.yaml` |
| Fake-client unit tests | Implemented | `boostgatewaycluster_controller_test.go` |
| envtest integration tests | Implemented | `boostgatewaycluster_envtest_test.go` |
| Status diff skip (no-op avoidance) | Implemented | `boostgatewaycluster_controller.go:206` |
| TLS secret runtime verification | Not implemented (config-level only) | — |

### 4.2 Python Operator (Legacy)

| Feature | Status | Location |
|---|---|---|
| Ready condition | Implemented | `operator.py:304-320` |
| Progressing condition | Implemented | `operator.py:323-342` |
| Degraded condition | Implemented (consecutive failure heuristic) | `operator.py:344-362` |
| TLSReady condition | Implemented (runtime secret check) | `operator.py:365-411` |
| components[] array | Implemented (6 components) | `operator.py:226-270` |
| Periodic health check (30s timer) | Implemented | `operator.py:793-844` |
| Consecutive failure tracking | Implemented | `operator.py:288-290` |
| Self-signed TLS cert creation | Implemented | `operator.py:419-520` |

### 4.3 Verification Coverage

| Verification | Coverage | Location |
|---|---|---|
| Operator unit tests (fake client) | Go controller reconcile logic | `boostgatewaycluster_controller_test.go` |
| Operator envtest (real API server) | CRD + controller + reconcile loop | `boostgatewaycluster_envtest_test.go` |
| CRD manifest validation | C++ unit test checks CRD YAML | `tests/v2/unit/k8s_operator_test.cpp:37-45` |
| Status conditions smoke test | Python unittest checks operator.py | `tests/v2/unit/k8s_operator_test.cpp:103-113` |
| Components array smoke test | Python unittest checks operator.py | `tests/v2/unit/k8s_operator_test.cpp:115-119` |
| Kind smoke (CR create/read/delete) | End-to-end via `operator_kind_smoke.py` | `scripts/operator_kind_smoke.py` |
| Control plane gate | Orchestrates all operator checks | `scripts/verify_control_plane_gate.py` |

### 4.4 Gaps (To Be Implemented)

| Gap | Impact | Priority |
|---|---|---|
| Go controller: TLSReady does not verify secret existence at runtime | TLS misconfiguration detected only at pod startup | Medium |
| Go controller: Degraded lacks dependency-awareness (e.g., Redis down cascade) | Service degradation may not propagate to dependent component status | Low |
| Python operator: No StatefulSet support | match/leaderboard always create Deployments, losing stable identity | Low (Go operator is primary) |
| Python operator: Status update on child object changes | Latency between child rollout completion and CR status update (up to 30s) | Low |
| No `status.phase` transition to `Draining` | CR deletion does not set a draining state before cleanup | Low |
| No conditions `reason`/`message` in CRD v1 (env/k8s/operator/) | Old CRD lacks rich condition fields | Low |
