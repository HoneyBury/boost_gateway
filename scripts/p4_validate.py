#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def find_test_executable(build_dir: Path, base_name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    matches = sorted(p for p in build_dir.rglob(base_name + suffix) if p.is_file())
    if not matches:
        raise FileNotFoundError(f"{base_name}{suffix} not found under {build_dir}")
    return matches[0]


def run_step(name: str, cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    print(f"==> {name}")
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    subprocess.run(cmd, cwd=cwd, env=merged_env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run P4 validation checks.")
    parser.add_argument("--build-dir", default=str(Path("build/windows-msvc-debug").resolve()))
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    build_dir = Path(args.build_dir).resolve()
    operator_dir = root / "operator" / "boostgateway-operator"

    unit_tests = find_test_executable(build_dir, "project_v2_unit_tests")
    integration_tests = find_test_executable(build_dir, "project_v2_integration_tests")

    run_step(
        "RemoteActor and Raft unit tests",
        [
            str(unit_tests),
            '--gtest_filter=RemoteActorTest.*:RaftTest.*:RaftClusterTest.*:ProtoSchemaTest.*',
        ],
        unit_tests.parent,
    )
    run_step(
        "Backend routing integration tests",
        [
            str(integration_tests),
            '--gtest_filter=V2BackendRoutingTest.LeaderboardReplicatesCommittedScoresAcrossRaftFollowers:'
            'V2BackendRoutingTest.MatchmakingReplicatesQueuedPlayersAndMatchesAcrossFollowers:'
            'V2BackendRoutingTest.MatchmakingReplicatesExpiredQueuePurgeAcrossFollowers:'
            'V2BackendRoutingTest.LeaderboardRestoresCommittedScoresAfterRestart:'
            'V2BackendRoutingTest.MatchmakingRestoresCommittedMatchAfterRestart:'
            'V2BackendRoutingTest.LeaderboardFollowerCatchesUpAfterLeaderRestart:'
            'ServiceBusIntegrity.ProtoEnvelopeRoundTripsThroughMatchBackend:'
            'ServiceBusIntegrity.ProtoEnvelopeRoundTripsThroughLeaderboardBackend',
        ],
        integration_tests.parent,
    )

    go_env: dict[str, str] = {}
    if os.name == "nt":
        go_env["GOCACHE"] = r"C:\Users\Administrator\.codex\memories\go-build-cache"
        go_env["GOMODCACHE"] = r"C:\Users\Administrator\.codex\memories\go-mod-cache"
    run_step("Operator fake-client tests", ["go", "test", "./..."], operator_dir, go_env)

    print("Validation completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
