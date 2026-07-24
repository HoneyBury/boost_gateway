#!/usr/bin/env python3
"""Run opt-in Redis/Raft/Operator specialized E2E gates."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        parent for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "__init__.py").is_file()
    )
    sys.path.insert(0, str(repo_import_root))

import argparse
import fnmatch
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from scripts.lib.evidence_provenance import build_evidence_provenance


RAFT_UNIT_FILTER = (
    "RaftClusterTest.*:"
    "RaftStateCodecTest.*:"
    "RaftWireCodecTest.*:"
    "RaftTest.InitialPersistenceFailureMarksNodeUnhealthyAndDoesNotStart:"
    "RaftTest.MultiNodeInitialPersistenceFailureDoesNotStart:"
    "RaftTest.InvalidRequestVoteDoesNotPoisonPersistentNode:"
    "RaftTest.InvalidAppendEntriesDoesNotPoisonPersistentNode:"
    "RaftTest.OversizedLocalCommandIsRejectedWithoutPoisoningLeader:"
    "RaftTest.RuntimePersistenceFailureLatchesUnhealthyAfterStorageRecovers:"
    "RaftTest.PersistentLogAndCommitStateRestoreAfterRestart:"
    "RaftTest.ApplyCallbackReplaysCommittedEntriesAfterRestart"
)

RAFT_INTEGRATION_FILTER = (
    "RaftE2ETest.E2E_MixedVersionRollingUpgradeAndRollbackKeepsLegacyWriterAndCommittedLog:"
    "V2BackendRoutingTest.LeaderboardReplicatesCommittedScoresAcrossRaftFollowers:"
    "V2BackendRoutingTest.MatchmakingReplicatesQueuedPlayersAndMatchesAcrossFollowers:"
    "V2BackendRoutingTest.MatchmakingReplicatesExpiredQueuePurgeAcrossFollowers:"
    "V2BackendRoutingTest.LeaderboardRestoresCommittedScoresAfterRestart:"
    "V2BackendRoutingTest.MatchmakingRestoresCommittedMatchAfterRestart:"
    "V2BackendRoutingTest.LeaderboardRejectsCorruptRaftStateBeforeListening:"
    "V2BackendRoutingTest.MatchmakingRejectsFutureRaftStateBeforeListening:"
    "V2BackendRoutingTest.RaftProtobufRequestVoteReturnsProtobufReply:"
    "V2BackendRoutingTest.RaftCapabilityHandlerRequiresExplicitProtobufMessage:"
    "V2BackendRoutingTest.LeaderboardReelectsAndLogicalRestartCatchesUpOverBackendRpc"
)

REDIS_DEGRADED_FILTER = (
    "RedisClientTest.ConnectFailsGracefully:"
    "RedisClientTest.DisconnectedOperationsReturnEmpty:"
    "RedisEventStoreTest.NoRedisAppendReturnsFalse:"
    "RedisEventStoreTest.NoRedisReadReturnsEmpty:"
    "RedisConnectionPoolTest.AcquireWhenRedisDownReturnsEmpty:"
    "RedisLeaderboardDegradedTest.*"
)

REDIS_LIVE_FILTER = (
    "RedisClientTest.SetAndGet:"
    "RedisClientTest.DelRemovesKey:"
    "RedisClientTest.ExistsReturnsTrue:"
    "RedisClientTest.ExistsReturnsFalse:"
    "RedisClientTest.IncrIncrements:"
    "RedisClientTest.IncrNewKeyStartsAtOne:"
    "RedisClientTest.LPushAndLRange:"
    "RedisClientTest.ZAddAndZRangeWithScores:"
    "RedisClientTest.HSetAndHGet:"
    "RedisClientTest.HGetNonExistentField:"
    "RedisClientTest.ZRevRangeWithScores:"
    "RedisClientTest.ZRevRank:"
    "RedisClientTest.ZScore:"
    "RedisClientTest.MoveSemantics:"
    "RedisEventStoreTest.AppendAndRead:"
    "RedisEventStoreTest.LatestSequence:"
    "RedisEventStoreTest.ReadByType:"
    "RedisEventStoreTest.TotalEvents:"
    "RedisEventStoreTest.FromSequenceFilter:"
    "RedisEventStoreTest.ClientAccess:"
    "RedisConnectionPoolTest.AcquireReturnsValidConnection:"
    "RedisConnectionPoolTest.ReleaseReturnsToPool:"
    "RedisConnectionPoolTest.AcquireAfterReleaseReusesConnection:"
    "RedisConnectionPoolTest.MaxSizeEnforced:"
    "RedisConnectionPoolTest.MoveSemantics:"
    "RedisConnectionPoolTest.DeadConnectionRevivedOnAcquire:"
    "RedisLeaderboardLiveTest.*"
)

REDIS_EVENT_STORE_LIVE_FILTER = (
    "RedisClientTest.SetAndGet:"
    "RedisClientTest.DelRemovesKey:"
    "RedisClientTest.ExistsReturnsTrue:"
    "RedisClientTest.ExistsReturnsFalse:"
    "RedisClientTest.IncrIncrements:"
    "RedisClientTest.IncrNewKeyStartsAtOne:"
    "RedisClientTest.LPushAndLRange:"
    "RedisClientTest.ZAddAndZRangeWithScores:"
    "RedisClientTest.HSetAndHGet:"
    "RedisClientTest.HGetNonExistentField:"
    "RedisClientTest.ZRevRangeWithScores:"
    "RedisClientTest.ZRevRank:"
    "RedisClientTest.ZScore:"
    "RedisClientTest.MoveSemantics:"
    "RedisEventStoreTest.AppendAndRead:"
    "RedisEventStoreTest.LatestSequence:"
    "RedisEventStoreTest.ReadByType:"
    "RedisEventStoreTest.TotalEvents:"
    "RedisEventStoreTest.FromSequenceFilter:"
    "RedisEventStoreTest.ClientAccess:"
    "RedisConnectionPoolTest.AcquireReturnsValidConnection:"
    "RedisConnectionPoolTest.ReleaseReturnsToPool:"
    "RedisConnectionPoolTest.AcquireAfterReleaseReusesConnection:"
    "RedisConnectionPoolTest.MaxSizeEnforced:"
    "RedisConnectionPoolTest.MoveSemantics:"
    "RedisConnectionPoolTest.DeadConnectionRevivedOnAcquire"
)


def exe_name(base: str) -> str:
    return f"{base}.exe" if os.name == "nt" else base


def find_executable(build_dir: Path, base_name: str) -> Path:
    names = {exe_name(base_name), base_name}
    matches = sorted(p for p in build_dir.rglob("*") if p.is_file() and p.name in names)
    direct_matches = [
        p for p in matches
        if "build" not in p.relative_to(build_dir).parts[:-1]
    ]
    if direct_matches:
        matches = sorted(direct_matches, key=lambda p: (len(p.relative_to(build_dir).parts), str(p)))
    if os.name == "nt":
        preferred = [
            p for p in matches
            if any(part.lower() in {"debug", "release", "relwithdebinfo", "minsizerel"} for part in p.parts)
        ]
        if preferred:
            matches = preferred
    if not matches:
        raise FileNotFoundError(f"{exe_name(base_name)} not found under {build_dir}")
    return matches[0]


def tail(text: str | bytes | None, max_chars: int = 4000) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return text if len(text) <= max_chars else text[-max_chars:]


def parse_gtest_list_output(output: str) -> list[str]:
    """Return fully-qualified test names from ``--gtest_list_tests`` output."""
    tests: list[str] = []
    suite = ""
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line or line.startswith("Running main()"):
            continue
        if not line[0].isspace() and line.split("#", 1)[0].rstrip().endswith("."):
            suite = line.split("#", 1)[0].strip()[:-1]
            continue
        if suite and line[0].isspace():
            test_name = line.split("#", 1)[0].strip()
            if test_name:
                tests.append(f"{suite}.{test_name}")
    return tests


def match_gtest_filter(filter_expression: str, registered_tests: list[str]) -> tuple[list[str], list[str]]:
    """Return selected tests and positive patterns which matched no registered test."""
    positive_expression, separator, negative_expression = filter_expression.partition("-")
    positive_patterns = [pattern for pattern in positive_expression.split(":") if pattern] or ["*"]
    negative_patterns = [pattern for pattern in negative_expression.split(":") if pattern] if separator else []
    missing_patterns = [
        pattern
        for pattern in positive_patterns
        if not any(fnmatch.fnmatchcase(test_name, pattern) for test_name in registered_tests)
    ]
    matched_tests = [
        test_name
        for test_name in registered_tests
        if any(fnmatch.fnmatchcase(test_name, pattern) for pattern in positive_patterns)
        and not any(fnmatch.fnmatchcase(test_name, pattern) for pattern in negative_patterns)
    ]
    return matched_tests, missing_patterns


def parse_gtest_run_count(output: str) -> int | None:
    matches = re.findall(r"\[==========\]\s+Running\s+(\d+)\s+tests?\s+from", output)
    return int(matches[-1]) if matches else None


def run_step(name: str, category: str, cmd: list[str], cwd: Path, timeout_seconds: int) -> dict[str, object]:
    print(f"==> {name}", flush=True)
    started = time.monotonic()
    gtest_filter = next(
        (argument.removeprefix("--gtest_filter=") for argument in cmd if argument.startswith("--gtest_filter=")),
        None,
    )
    matched_tests: list[str] | None = None
    if gtest_filter is not None:
        try:
            listed = subprocess.run(
                [cmd[0], "--gtest_list_tests"],
                cwd=cwd,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "name": name,
                "category": category,
                "command": cmd,
                "cwd": str(cwd),
                "status": "timeout",
                "duration_seconds": round(time.monotonic() - started, 3),
                "stdout_tail": tail(exc.stdout),
                "stderr_tail": "gtest test discovery timed out",
            }
        if listed.returncode != 0:
            return {
                "name": name,
                "category": category,
                "command": cmd,
                "cwd": str(cwd),
                "status": "failed",
                "returncode": listed.returncode,
                "duration_seconds": round(time.monotonic() - started, 3),
                "stdout_tail": tail(listed.stdout),
                "stderr_tail": tail(listed.stderr) or "gtest test discovery failed",
            }
        registered_tests = parse_gtest_list_output(listed.stdout or "")
        matched_tests, missing_patterns = match_gtest_filter(gtest_filter, registered_tests)
        if missing_patterns or not matched_tests:
            reason = (
                f"gtest filter patterns matched zero registered tests: {', '.join(missing_patterns)}"
                if missing_patterns
                else "gtest filter matched zero registered tests"
            )
            return {
                "name": name,
                "category": category,
                "command": cmd,
                "cwd": str(cwd),
                "status": "failed",
                "returncode": 0,
                "duration_seconds": round(time.monotonic() - started, 3),
                "expected_test_count": len(matched_tests),
                "matched_tests": matched_tests,
                "stdout_tail": tail(listed.stdout),
                "stderr_tail": reason,
            }

    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "category": category,
            "command": cmd,
            "cwd": str(cwd),
            "status": "timeout",
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(exc.stdout),
            "stderr_tail": tail(exc.stderr),
        }

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    status = "passed" if completed.returncode == 0 else "failed"
    executed_test_count = parse_gtest_run_count(stdout) if matched_tests is not None else None
    if completed.returncode == 0 and matched_tests is not None and executed_test_count != len(matched_tests):
        status = "failed"
        stderr = (stderr + "\n" if stderr else "") + (
            "gtest executed test count does not match the validated filter: "
            f"expected {len(matched_tests)}, observed {executed_test_count}"
        )

    return {
        "name": name,
        "category": category,
        "command": cmd,
        "cwd": str(cwd),
        "status": status,
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        **({"expected_test_count": len(matched_tests), "matched_tests": matched_tests} if matched_tests is not None else {}),
        **({"executed_test_count": executed_test_count} if executed_test_count is not None else {}),
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build/default"))
    parser.add_argument("--configuration", default="Debug")
    parser.add_argument("--profile", choices=["default", "redis-live", "raft-ha", "all"], default="default")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--include-redis-live", action="store_true")
    parser.add_argument("--include-operator-kind", action="store_true")
    parser.add_argument("--build-timeout-seconds", type=int, default=180)
    parser.add_argument("--test-timeout-seconds", type=int, default=120)
    parser.add_argument("--operator-timeout-seconds", type=int, default=600)
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/specialized-e2e-summary.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.profile in {"redis-live", "all"}:
        args.include_redis_live = True
    if args.profile == "all":
        args.include_operator_kind = True
    root = Path(__file__).resolve().parents[3]
    build_dir = args.build_dir.resolve()
    summary_path = args.summary_path if args.summary_path.is_absolute() else root / args.summary_path
    summary: dict[str, object] = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "build_dir": str(build_dir),
        "configuration": args.configuration,
        "provenance": build_evidence_provenance(
            root,
            build_configuration=args.configuration,
        ),
        "profile": args.profile,
        "include_redis_live": args.include_redis_live,
        "include_operator_kind": args.include_operator_kind,
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "overall_pass": False,
        "passed": False,
        "failed_category": "",
        "failed_step": "",
        "steps": [],
        "artifacts": {
            "summary_path": str(summary_path),
        },
    }

    try:
        if not args.skip_build:
            build_cmd = ["cmake", "--build", str(args.build_dir)]
            if args.configuration:
                build_cmd.extend(["--config", args.configuration])
            build_cmd.extend(["--target", "project_v2_unit_tests", "project_v2_integration_tests"])
            step = run_step(
                "build specialized E2E targets",
                "build",
                build_cmd,
                root,
                args.build_timeout_seconds,
            )
            summary["steps"].append(step)
            if step["status"] != "passed":
                raise RuntimeError(step["name"])

        unit_tests = find_executable(build_dir, "project_v2_unit_tests")
        integration_tests = find_executable(build_dir, "project_v2_integration_tests")
        run_redis_degraded = args.profile in {"default", "redis-live", "all"}
        run_raft = args.profile in {"default", "raft-ha", "all"}
        if run_raft:
            summary["steps"].append(run_step(
                "Raft cluster and persistence gates",
                "raft-ha",
                [str(unit_tests), f"--gtest_filter={RAFT_UNIT_FILTER}"],
                unit_tests.parent,
                args.test_timeout_seconds,
            ))
            summary["steps"].append(run_step(
                "Raft-backed service recovery gates",
                "raft-ha",
                [str(integration_tests), f"--gtest_filter={RAFT_INTEGRATION_FILTER}"],
                integration_tests.parent,
                args.test_timeout_seconds,
            ))
        if run_redis_degraded:
            summary["steps"].append(run_step(
                "Redis service degraded-mode gates",
                "redis",
                [str(unit_tests), f"--gtest_filter={REDIS_DEGRADED_FILTER}"],
                unit_tests.parent,
                args.test_timeout_seconds,
            ))
            summary["steps"].append(run_step(
                "Redis event-store degraded-mode gates",
                "redis",
                [
                    str(unit_tests),
                    "--gtest_filter=RedisClientTest.ConnectFailsGracefully:"
                    "RedisClientTest.DisconnectedOperationsReturnEmpty:"
                    "RedisEventStoreTest.NoRedisAppendReturnsFalse:"
                    "RedisEventStoreTest.NoRedisReadReturnsEmpty:"
                    "RedisConnectionPoolTest.AcquireWhenRedisDownReturnsEmpty",
                ],
                unit_tests.parent,
                args.test_timeout_seconds,
            ))
        if args.include_redis_live:
            summary["steps"].append(run_step(
                "Redis service live gates",
                "redis",
                [str(unit_tests), f"--gtest_filter={REDIS_LIVE_FILTER}"],
                unit_tests.parent,
                args.test_timeout_seconds,
            ))
            summary["steps"].append(run_step(
                "Redis event-store live gates",
                "redis",
                [
                    str(unit_tests),
                    f"--gtest_filter={REDIS_EVENT_STORE_LIVE_FILTER}",
                ],
                unit_tests.parent,
                args.test_timeout_seconds,
            ))
        if args.include_operator_kind:
            summary["steps"].append(run_step(
                "Operator kind smoke",
                "operator",
                [sys.executable, str(root / "scripts/tools/operator_kind_smoke.py")],
                root,
                args.operator_timeout_seconds,
            ))
    except (FileNotFoundError, RuntimeError) as exc:
        failed = next((step for step in summary["steps"] if step.get("status") != "passed"), None)
        if failed:
            summary["failed_category"] = str(failed.get("category", "unknown"))
            summary["failed_step"] = str(failed.get("name", "unknown"))
        else:
            summary["failed_category"] = "discovery"
            summary["failed_step"] = str(exc)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"specialized E2E failed: {exc}", file=sys.stderr)
        print(f"summary: {summary_path}", file=sys.stderr)
        return 1

    failed = next((step for step in summary["steps"] if step.get("status") != "passed"), None)
    if failed:
        summary["failed_category"] = str(failed.get("category", "unknown"))
        summary["failed_step"] = str(failed.get("name", "unknown"))
    else:
        summary["overall_pass"] = True
        summary["passed"] = True
    summary["duration_seconds"] = round(
        sum(float(step.get("duration_seconds", 0.0)) for step in summary["steps"] if isinstance(step, dict)),
        3,
    )

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
