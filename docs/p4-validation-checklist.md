# P4 Validation Checklist

Date: 2026-05-15

The final hardening pass for JWT, OTLP tracing, and gateway bridge behavior
should be validated with the following focused checks.

## JWT / auth

- `project_v2_unit_tests --gtest_filter="JwtValidatorTest.*"`
- `project_v2_integration_tests --gtest_filter="ServiceBusIntegrity.LoginBackendAcceptsRs256JwtAndValidatesToken"`

Expected:

- HS256 and RS256 validation both pass.
- RS256 login succeeds when issuer / audience match.
- invalid signatures are rejected by `token_validate`.

## Raft-backed services

- `project_v2_unit_tests --gtest_filter="RaftTest.ApplyCallbackReplaysCommittedEntriesAfterRestart:RaftClusterTest.LeaderReplicatesCommittedLogToFollowers"`
- `project_v2_integration_tests --gtest_filter="V2BackendRoutingTest.LeaderboardReplicatesCommittedScoresAcrossRaftFollowers:V2BackendRoutingTest.MatchmakingReplicatesQueuedPlayersAndMatchesAcrossFollowers"`

Expected:

- committed entries are applied on leaders and followers
- recovered nodes replay committed log entries into the local state machine
- `leaderboard` follower reads reflect leader-submitted scores
- `match` follower reads reflect both queued players and committed match results

## Operator / cluster control plane

- `cd operator/boostgateway-operator && go test ./...`
- `cd operator/boostgateway-operator && set KUBEBUILDER_ASSETS=C:\path\to\kubebuilder\bin && go test ./internal/controller -run TestEnvtest`
- `cd operator/boostgateway-operator && make kind-smoke`

Expected:

- fake-client reconcile tests pass in all dev environments
- envtest reconcile passes when local API server / etcd binaries are installed
- sample `BoostGatewayCluster` install path remains aligned with `config/default`
- kind smoke verifies CRD install, operator rollout, and sample cluster reconcile

## Gateway bridge / readiness

- `project_v2_integration_tests --gtest_filter="V2BackendRoutingTest.SecurityPolicyAllowsPlaintextWhenGlobalTlsDisabled"`
- `project_v2_integration_tests --gtest_filter="V2DemoServerSmokeTest.ReadyJsonFailsWhenConfiguredBackendUnavailable"`

Expected:

- bridge routing still works when global TLS enforcement is disabled
- readiness reports the backend failure explicitly when a configured backend is
  unreachable

## OTLP tracing

- `project_v2_integration_tests --gtest_filter="V2BackendRoutingTest.OtelExporter*"`

Expected:

- failed and successful bridge routes both emit spans safely
- exporter preserves `route.<message_type>` operation names
- collector upload path receives `/v1/traces`

## Multi-process regression

- `project_v2_multi_process_tests --gtest_filter="MultiProcessFixture.*"`

Expected:

- login, room, ready, battle start, input, settlement, and post-finish error
  handling all complete over real OS processes

## Dependency governance

- `bash scripts/inspect_dependency_layout.sh`

Expected:

- vendored sources, archive caches, and toolchain caches are all visible in one report
- offline bootstrap commands remain documented and current
