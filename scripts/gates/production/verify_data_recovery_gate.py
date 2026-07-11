#!/usr/bin/env python3
"""Run the P3 data recovery and consistency gate."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


V2_ARCHIVE_FILTER = (
    "BattleReplayTest.SettlementContainsReplayPayload:"
    "V2BattleAuthoritativeTest.BuildResultSummaryFindsWinner:"
    "JsonFileStoreTest.PersistDelegatesCorrectly:"
    "JsonFileStoreTest.PersistWithFullArchive"
)

V2_REDIS_DEGRADED_FILTER = (
    "RedisClientTest.ConnectFailsGracefully:"
    "RedisClientTest.DisconnectedOperationsReturnEmpty:"
    "RedisEventStoreTest.NoRedisAppendReturnsFalse:"
    "RedisEventStoreTest.NoRedisReadReturnsEmpty:"
    "RedisConnectionPoolTest.AcquireWhenRedisDownReturnsEmpty"
)

V2_REDIS_LIVE_FILTER = (
    "RedisClientTest.SetAndGet:"
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
    "RedisEventStoreTest.AppendAndRead:"
    "RedisEventStoreTest.LatestSequence:"
    "RedisEventStoreTest.ReadByType:"
    "RedisEventStoreTest.TotalEvents:"
    "RedisEventStoreTest.FromSequenceFilter:"
    "RedisConnectionPoolTest.AcquireReturnsValidConnection:"
    "RedisConnectionPoolTest.ReleaseReturnsToPool:"
    "RedisConnectionPoolTest.AcquireAfterReleaseReusesConnection:"
    "RedisConnectionPoolTest.MaxSizeEnforced:"
    "RedisConnectionPoolTest.DeadConnectionRevivedOnAcquire"
)

V2_CACHE_FILTER = (
    "V2CachedDataStoreTest.SaveReplayLoadsFromCache:"
    "V2CachedDataStoreTest.SaveResultLoadsFromCache:"
    "V2CachedDataStoreTest.SaveSnapshotLoadsFromCache:"
    "V2CachedDataStoreTest.LoadFallsBackToDelegateOnCacheMiss:"
    "V2CachedDataStoreTest.CacheHitAvoidsDelegateRead:"
    "V2CachedDataStoreTest.PersistDelegatesToWriteBehind:"
    "V2CachedDataStoreTest.FlushWritesAllPendingToDelegate"
)

V2_WRITE_BEHIND_FILTER = (
    "V2WriteBehindStoreTest.WriteBehindSaveReplayIsFlushed:"
    "V2WriteBehindStoreTest.WriteBehindSaveResultIsFlushed:"
    "V2WriteBehindStoreTest.WriteBehindSaveSnapshotIsFlushed:"
    "V2WriteBehindStoreTest.WriteBehindMultipleWritesAllFlushed:"
    "V2WriteBehindStoreTest.WriteBehindDestructorFlushesRemaining:"
    "V2WriteBehindStoreTest.WriteBehindFlushReportsDelegateFailures:"
    "V2WriteBehindStoreTest.WriteBehindDestructorDrainsLargePendingQueue"
)

V2_DATA_LAYER_FILTER = (
    "V2DataLayerIntegrationTest.E2ESaveFlushReadCache:"
    "V2DataLayerIntegrationTest.CacheMissFallsBackToDelegate:"
    "V2DataLayerIntegrationTest.ReadBeforeFlushReturnsCachedWrite:"
    "V2DataLayerIntegrationTest.PersistRoundTrip:"
    "V2DataLayerIntegrationTest.MultipleBattlesEndToEnd:"
    "V2DataLayerIntegrationTest.CacheEvictsWhenFull"
)

V2_RAFT_RECOVERY_FILTER = (
    "RaftTest.PersistentLogAndCommitStateRestoreAfterRestart:"
    "RaftTest.ApplyCallbackReplaysCommittedEntriesAfterRestart"
)

V2_SETTLEMENT_REPLAY_FILTER = (
    "MultiProcessFixture.SurrenderSettlementDeliveredToBothPlayers:"
    "MultiProcessFixture.FrameLimitSettlementDelivered"
)


def exe_name(base: str) -> str:
    return f"{base}.exe" if os.name == "nt" else base


def find_executable(build_dir: Path, base_name: str) -> Path:
    names = {exe_name(base_name), base_name}
    matches = sorted(p for p in build_dir.rglob("*") if p.is_file() and p.name in names)
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


def normalize_output(text: str | bytes | None) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    return text


def tail(text: str | bytes | None, max_chars: int = 4000) -> str:
    text = normalize_output(text)
    return text if len(text) <= max_chars else text[-max_chars:]


def run_step(name: str, category: str, cmd: list[str], cwd: Path, timeout_seconds: int) -> dict[str, object]:
    print(f"==> {name}", flush=True)
    started = time.monotonic()
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
            "timeout_seconds": timeout_seconds,
            "status": "timeout",
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(exc.stdout),
            "stderr_tail": tail(exc.stderr),
        }

    stdout = normalize_output(completed.stdout)
    stderr = normalize_output(completed.stderr)
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    status = "passed" if completed.returncode == 0 else "failed"
    if completed.returncode == 0 and "0 tests from 0 test suites ran" in stdout:
        status = "failed"
        stderr = (stderr + "\n" if stderr else "") + "gtest filter matched zero tests"
    return {
        "name": name,
        "category": category,
        "command": cmd,
        "cwd": str(cwd),
        "timeout_seconds": timeout_seconds,
        "status": status,
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }


def run_step_retrying_timeout(
    name: str,
    category: str,
    cmd: list[str],
    cwd: Path,
    timeout_seconds: int,
) -> dict[str, object]:
    first_attempt = run_step(name, category, cmd, cwd, timeout_seconds)
    if first_attempt["status"] != "timeout":
        return first_attempt

    print(f"==> retrying {name} after a timeout", flush=True)
    retry = run_step(name, category, cmd, cwd, timeout_seconds)
    retry["attempts"] = [first_attempt, retry.copy()]
    retry["recovered_from_timeout"] = retry["status"] == "passed"
    return retry


def cmake_build_args(args: argparse.Namespace, targets: list[str]) -> list[str]:
    cmd = ["cmake", "--build", str(args.build_dir)]
    if args.configuration:
        cmd.extend(["--config", args.configuration])
    cmd.extend(["--target", *targets])
    return cmd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build/windows-ninja-debug"))
    parser.add_argument("--configuration", default="Debug")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--include-redis-live", action="store_true")
    parser.add_argument("--include-settlement-replay", action="store_true")
    parser.add_argument("--build-timeout-seconds", type=int, default=180)
    parser.add_argument("--test-timeout-seconds", type=int, default=120)
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/data-recovery-summary.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[3]
    build_dir = args.build_dir.resolve()
    summary_path = args.summary_path if args.summary_path.is_absolute() else root / args.summary_path
    summary: dict[str, object] = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "build_dir": str(build_dir),
        "configuration": args.configuration,
        "include_redis_live": args.include_redis_live,
        "include_settlement_replay": args.include_settlement_replay,
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
            targets = ["project_v2_unit_tests", "project_v2_integration_tests"]
            if args.include_settlement_replay:
                targets.append("project_v2_multi_process_tests")
            step = run_step(
                "build P3 data recovery targets",
                "build",
                cmake_build_args(args, targets),
                root,
                args.build_timeout_seconds,
            )
            summary["steps"].append(step)
            if step["status"] != "passed":
                raise RuntimeError(step["name"])

        v2_unit_tests = find_executable(build_dir, "project_v2_unit_tests")
        v2_integration_tests = find_executable(build_dir, "project_v2_integration_tests")

        summary["steps"].append(run_step(
            "persistent replay/archive storage round trips",
            "persistence",
            [str(v2_unit_tests), f"--gtest_filter={V2_ARCHIVE_FILTER}"],
            v2_unit_tests.parent,
            args.test_timeout_seconds,
        ))
        summary["steps"].append(run_step(
            "Redis degraded persistence behavior",
            "redis",
            [str(v2_unit_tests), f"--gtest_filter={V2_REDIS_DEGRADED_FILTER}"],
            v2_unit_tests.parent,
            args.test_timeout_seconds,
        ))
        summary["steps"].append(run_step(
            "cached data store replay/result/snapshot coverage",
            "cache",
            [str(v2_unit_tests), f"--gtest_filter={V2_CACHE_FILTER}"],
            v2_unit_tests.parent,
            args.test_timeout_seconds,
        ))
        summary["steps"].append(run_step(
            "write-behind drain and delegate failure coverage",
            "writebehind",
            [str(v2_unit_tests), f"--gtest_filter={V2_WRITE_BEHIND_FILTER}"],
            v2_unit_tests.parent,
            args.test_timeout_seconds,
        ))
        summary["steps"].append(run_step_retrying_timeout(
            "data layer flush/read/cache consistency",
            "data_layer",
            [str(v2_integration_tests), f"--gtest_filter={V2_DATA_LAYER_FILTER}"],
            v2_integration_tests.parent,
            args.test_timeout_seconds,
        ))
        summary["steps"].append(run_step(
            "Raft committed state replay after restart",
            "raft",
            [str(v2_unit_tests), f"--gtest_filter={V2_RAFT_RECOVERY_FILTER}"],
            v2_unit_tests.parent,
            args.test_timeout_seconds,
        ))

        if args.include_redis_live:
            summary["steps"].append(run_step(
                "Redis live persistence/event-store behavior",
                "redis",
                [str(v2_unit_tests), f"--gtest_filter={V2_REDIS_LIVE_FILTER}"],
                v2_unit_tests.parent,
                args.test_timeout_seconds,
            ))
        if args.include_settlement_replay:
            multi_process_tests = find_executable(build_dir, "project_v2_multi_process_tests")
            summary["steps"].append(run_step(
                "settlement replay delivery across multi-process fixture",
                "settlement",
                [str(multi_process_tests), f"--gtest_filter={V2_SETTLEMENT_REPLAY_FILTER}"],
                multi_process_tests.parent,
                args.test_timeout_seconds,
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
        print(f"data recovery gate failed: {exc}", file=sys.stderr)
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
