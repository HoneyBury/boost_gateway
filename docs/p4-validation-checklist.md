# P4 Validation Checklist

Date: 2026-05-15

The final hardening pass for JWT, OTLP tracing, and gateway bridge behavior
should be validated with the following focused checks.

## Quick start

- PowerShell one-shot entry:
  `powershell -ExecutionPolicy Bypass -File scripts/p4_validate.ps1`

This wrapper currently runs the highest-signal local checks that do not require
external infra binaries beyond the existing build and Go toolchain.
Current default build root is `build/windows-msvc-debug`.

## JWT / auth

- `project_v2_unit_tests --gtest_filter="JwtValidatorTest.*"`
- `project_v2_integration_tests --gtest_filter="ServiceBusIntegrity.LoginBackendAcceptsRs256JwtAndValidatesToken"`

Expected:

- HS256 and RS256 validation both pass.
- RS256 login succeeds when issuer / audience match.
- invalid signatures are rejected by `token_validate`.

## Raft-backed services

- `project_v2_unit_tests --gtest_filter="RaftTest.ApplyCallbackReplaysCommittedEntriesAfterRestart:RaftClusterTest.LeaderReplicatesCommittedLogToFollowers"`
- `project_v2_integration_tests --gtest_filter="V2BackendRoutingTest.LeaderboardReplicatesCommittedScoresAcrossRaftFollowers:V2BackendRoutingTest.MatchmakingReplicatesQueuedPlayersAndMatchesAcrossFollowers:V2BackendRoutingTest.LeaderboardRestoresCommittedScoresAfterRestart:V2BackendRoutingTest.MatchmakingRestoresCommittedMatchAfterRestart"`

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
- kind smoke asserts `status.components[]` for all 6 components and validates
  steady-state `Ready/Progressing/Degraded/TLSReady` conditions
- fake-client tests now also verify `desiredReplicas`, `TLSReady`, and cert-manager `Certificate` reconcile

## Proto / transport

- `project_v2_unit_tests --gtest_filter="ProtoSchemaTest.*"`
- `project_v2_integration_tests --gtest_filter="ServiceBusIntegrity.ProtoEnvelopeRoundTripsThroughLoginBackend:ServiceBusIntegrity.ProtoEnvelopeRoundTripsThroughRoomBackend:ServiceBusIntegrity.ProtoEnvelopeRoundTripsThroughBattleBackend:ServiceBusIntegrity.ProtoEnvelopeRoundTripsThroughMatchBackend:ServiceBusIntegrity.ProtoEnvelopeRoundTripsThroughLeaderboardBackend"`

Expected:

- `ServiceEnvelope`-style payloads can be encoded/decoded locally without generated gRPC stubs
- `login/room/battle/match/leaderboard` 后端都接受 legacy raw JSON 与 wrapped envelope payload
- `TypedEnvelope` helper 已覆盖 login / room / battle / match / leaderboard 的消息 kind

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

## Build note

- `tests/v2` GoogleTest discovery now uses `PRE_TEST` for `project_v2_integration_tests`
  to avoid Visual Studio post-build discovery failures in local Windows builds.
- Redis live/integration tests may be reported as `Skipped` in CI when no Redis
  service is provisioned; this is expected and not treated as a regression by itself.

## Dependency governance

- `bash scripts/inspect_dependency_layout.sh`

Expected:

- vendored sources, archive caches, and toolchain caches are all visible in one report
- offline bootstrap commands remain documented and current
