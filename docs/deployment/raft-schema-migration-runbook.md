# Raft v3.6 schema migration runbook

## Scope

This runbook covers the v3.6 Phase B state: nodes can strictly read legacy JSON and
protobuf v1 RPC payloads, but RequestVote and AppendEntries writers remain fixed to
legacy JSON. It does not authorize the Phase C protobuf writer.

The repository contains both an in-process protocol-profile E2E and a thirteen-stage
three-process gate using distinct old/new backend binaries. The process gate performs
two complete upgrade/rollback cycles after legacy nodes have advanced v0 state. A
production rollout still requires the real-binary gate on the fixed runner for the
exact candidate revision; a local summary is diagnostic evidence only.

## Admission

Before a candidate can enter a mixed-version environment:

1. Run `release.yml` for the exact candidate SHA with the pinned non-gRPC lockfile.
2. Require `runtime/validation/raft-release-evidence-summary.json` to report
   `overall_pass=true` and no failed checks.
3. Confirm its specialized, data-recovery, Conan, package-consumer and SBOM inputs
   have one candidate revision, workflow run ID, runner and lockfile SHA-256.
4. Confirm the SBOM contains `protobuf` and `abseil`, excludes `grpc`, and the Conan
   producer records `no_remote=true` with `build_policy=never`.
5. Require `runtime/validation/raft-mixed-binary-summary.json` to contain thirteen
   passing stages, distinct legacy/candidate SHA-256 values, the expected legacy
   SHA-256, six successful downgrade records and the governed
   `v0 -> v1 -> v0 -> v1 -> v0` schema trajectory.

Local summaries and a dirty worktree are development diagnostics, not admission
evidence.

## Rolling upgrade

1. Verify all three nodes are healthy and have the same committed index.
2. Replace one follower with the Phase B binary. Keep the RPC writer on legacy JSON.
3. Wait for the node to rejoin, catch up to the committed index and remain healthy.
4. Repeat for the second follower.
5. Transfer or re-elect leadership, then replace the former leader.
6. Confirm all voting peers explicitly answer the protobuf capability request.
7. Keep the writer on legacy JSON. Full capability is evidence for a later Phase C
   decision, not an automatic activation signal.

Stop the rollout on a malformed/future payload, identity mismatch, unhealthy Raft
node, missing capability response, lost committed entry or divergent index.

## Rolling rollback

1. Stop one follower at a time. Never convert a state file while its backend is
   running.
2. Convert that node's v1 state with the candidate release tool:

   ```bash
   raft_state_tool downgrade \
     --state <storage-dir>/<node-id>.raft.json \
     --node-id <node-id> \
     --summary <storage-dir>/downgrade-summary.json
   ```

3. Require `overall_pass=true`, retain the emitted `.v1.bak` and
   `.downgrade-v1-v0.json` paths, then start the legacy binary. The tool validates
   node identity, checksum, state invariants and existing v0-to-v1 migration
   sidecars before atomically replacing the main state with the exact six-field v0
   representation.
4. Verify new nodes immediately withdraw the peer's cached protobuf capability
   after a missing or invalid response.
5. Confirm consensus traffic remains legacy JSON and append a bounded validation
   command after each replacement.
6. Re-elect leadership before rolling back the current leader.
7. Require every surviving node to retain the validation commands and committed
   indexes after the old node rejoins.

If conversion fails, preserve all files and restart the Phase B binary. Never delete
or hand-edit state or sidecars. Re-running the tool is accepted only while the v0
main file still exactly matches its downgrade record; once the legacy binary has
advanced the state, another downgrade attempt correctly fails. A later Phase B
upgrade validates the complete history and writes content-addressed
`.v0.history.<sha256>.bak` / `.migration-v0-v1.history.<sha256>.json` sidecars.
The next rollback similarly writes `.v1.history.<sha256>.bak` and
`.downgrade-v1-v0.history.<sha256>.json`.

Each direction retains at most eight complete transition pairs: the fixed first pair
plus seven history pairs. The ninth distinct transition fails before replacing the
main state. Archive and reset beyond that bound requires a separately governed
operator procedure; do not delete history merely to bypass the limit.

## Evidence commands

```bash
python scripts/verify_specialized_e2e.py \
  --build-dir build/release --configuration Release --profile raft-ha --skip-build \
  --summary-path runtime/validation/raft-release-specialized-summary.json

python scripts/verify_data_recovery_gate.py \
  --build-dir build/release --configuration Release --skip-build \
  --summary-path runtime/validation/raft-release-data-recovery-summary.json

python scripts/verify_raft_mixed_binary.py \
  --legacy-binary /opt/boost-gateway/releases/v3.5.3/bin/v2_leaderboard_backend \
  --legacy-revision b9c348b4b58fdeeffa9d82ff87a67ed781a96b78 \
  --expected-legacy-sha256 <platform-sha256> \
  --candidate-binary build/release/examples/v2_leaderboard_backend/v2_leaderboard_backend \
  --state-tool build/release/tools/raft_state_tool/raft_state_tool \
  --configuration Release \
  --summary-path runtime/validation/raft-mixed-binary-summary.json
```

The complete release workflow additionally creates the strict offline Conan,
package-consumer and SBOM inputs before invoking
`scripts/verify_raft_release_evidence.py`.
