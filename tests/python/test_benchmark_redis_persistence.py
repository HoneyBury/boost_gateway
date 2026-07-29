from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.tools import benchmark_redis_persistence as benchmark


class FakeBenchmarkProcess:
    def __init__(self, *, returncode: int = 0) -> None:
        self.returncode: int | None = None
        self._final_returncode = returncode
        self._polled = False
        self.killed = False
        self.communicate_calls = 0

    def poll(self) -> int | None:
        if not self._polled:
            self._polled = True
            return None
        self.returncode = self._final_returncode
        return self.returncode

    def communicate(self, timeout: int | None = None) -> tuple[str, str]:
        self.communicate_calls += 1
        if self.killed:
            self.returncode = -9
            return "", "killed"
        self.returncode = self._final_returncode
        if self.returncode:
            return "", "injected benchmark failure"
        return (
            '"eval leaderboard","50000.0","0.30","0.10","0.25","0.60","0.90","1.20"\n',
            "",
        )

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class HangingBenchmarkProcess(FakeBenchmarkProcess):
    def poll(self) -> int | None:
        return None


class FakeDocker:
    def __init__(
        self,
        *,
        delayed_fsync: bool = False,
        leaderboard_members: int = 800,
        active_image: str | None = None,
        fail_info_during_workload: bool = False,
        ambiguous_volume_create: bool = False,
        empty_initial_io: bool = True,
        omit_candidate_delayed_fsync: bool = False,
        empty_rdb_workload_io: bool = True,
    ) -> None:
        self.commands: list[list[str]] = []
        self.volumes: dict[str, dict[str, str]] = {}
        self.networks: dict[str, dict[str, str]] = {}
        self.containers: dict[str, dict[str, str]] = {}
        self.info_calls: dict[str, int] = {}
        self.io_calls: dict[str, int] = {}
        self.bgsave_states: dict[str, int] = {}
        self.processes: list[FakeBenchmarkProcess] = []
        self.delayed_fsync = delayed_fsync
        self.leaderboard_members = leaderboard_members
        self.active_image = active_image
        self.fail_info_during_workload = fail_info_during_workload
        self.ambiguous_volume_create = ambiguous_volume_create
        self.empty_initial_io = empty_initial_io
        self.omit_candidate_delayed_fsync = omit_candidate_delayed_fsync
        self.empty_rdb_workload_io = empty_rdb_workload_io

    @staticmethod
    def labels(command: list[str]) -> dict[str, str]:
        labels: dict[str, str] = {}
        for index, value in enumerate(command[:-1]):
            if value != "--label":
                continue
            key, label_value = command[index + 1].split("=", 1)
            labels[key] = label_value
        return labels

    @staticmethod
    def option(command: list[str], option: str) -> str:
        return command[command.index(option) + 1]

    def start_benchmark(
        self, command: list[str], *, returncode: int, hanging: bool
    ) -> FakeBenchmarkProcess:
        self.commands.append(command)
        name = self.option(command, "--name")
        self.containers[name] = self.labels(command)
        process = (
            HangingBenchmarkProcess()
            if hanging
            else FakeBenchmarkProcess(returncode=returncode)
        )
        self.processes.append(process)
        return process

    def __call__(
        self, command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[object]:
        self.commands.append(command)
        text = bool(kwargs.get("text"))
        stdout: str | bytes = "" if text else b""
        returncode = 0

        if command[1:3] == ["context", "show"]:
            stdout = "default\n" if text else b"default\n"
        elif command[1:3] == ["context", "inspect"]:
            value = "unix:///var/run/docker.sock\n"
            stdout = value if text else value.encode()
        elif command[1:3] == ["image", "inspect"]:
            value = [{"Id": command[-1], "Os": "linux", "Architecture": "amd64"}]
            stdout = json.dumps(value) if text else json.dumps(value).encode()
        elif command[1:2] == ["inspect"]:
            value = [
                {
                    "Id": "b" * 64,
                    "Name": "/boost-redis",
                    "Image": self.active_image or "sha256:" + "a" * 64,
                    "State": {"Running": True, "Health": {"Status": "healthy"}},
                    "Config": {"Labels": {"com.docker.compose.service": "redis"}},
                    "Mounts": [
                        {
                            "Type": "volume",
                            "Name": "boost-gateway-production-redis-data",
                            "Destination": "/data",
                            "RW": True,
                        }
                    ],
                }
            ]
            stdout = json.dumps(value) if text else json.dumps(value).encode()
        elif command[1:3] == ["volume", "ls"]:
            value = "\n".join(sorted(self.volumes))
            stdout = value if text else value.encode()
        elif command[1:3] == ["volume", "create"]:
            self.volumes[command[-1]] = self.labels(command)
            if self.ambiguous_volume_create:
                self.ambiguous_volume_create = False
                raise subprocess.TimeoutExpired(command, 60)
            stdout = command[-1] if text else command[-1].encode()
        elif command[1:3] == ["volume", "inspect"]:
            name = command[-1]
            if name == "boost-gateway-production-redis-data":
                labels = {"com.docker.compose.project": "production"}
                mount = "/var/lib/docker/volumes/active/_data"
            elif name in self.volumes:
                labels = self.volumes[name]
                mount = f"/var/lib/docker/volumes/{name}/_data"
            else:
                returncode = 1
                labels = {}
                mount = ""
            value = [
                {
                    "Name": name,
                    "Driver": "local",
                    "Mountpoint": mount,
                    "Scope": "local",
                    "Labels": labels,
                }
            ]
            stdout = json.dumps(value) if text else json.dumps(value).encode()
        elif command[1:3] == ["volume", "rm"]:
            self.volumes.pop(command[-1], None)
        elif command[1:3] == ["network", "ls"]:
            value = "\n".join(sorted(self.networks))
            stdout = value if text else value.encode()
        elif command[1:3] == ["network", "create"]:
            self.networks[command[-1]] = self.labels(command)
            stdout = command[-1] if text else command[-1].encode()
        elif command[1:3] == ["network", "inspect"]:
            name = command[-1]
            if name not in self.networks:
                returncode = 1
                value = []
            else:
                value = [
                    {
                        "Name": name,
                        "Driver": "bridge",
                        "Internal": True,
                        "Labels": self.networks[name],
                    }
                ]
            stdout = json.dumps(value) if text else json.dumps(value).encode()
        elif command[1:3] == ["network", "rm"]:
            self.networks.pop(command[-1], None)
        elif command[1:3] == ["ps", "-a"]:
            value = "\n".join(sorted(self.containers))
            stdout = value if text else value.encode()
        elif command[1:3] == ["run", "-d"]:
            name = self.option(command, "--name")
            self.containers[name] = self.labels(command)
            stdout = "container-id" if text else b"container-id"
        elif command[1:3] == ["container", "inspect"]:
            name = command[-1]
            if name not in self.containers:
                returncode = 1
                value = []
            else:
                value = [
                    {
                        "Id": "c" * 64,
                        "Name": f"/{name}",
                        "Config": {"Labels": self.containers[name]},
                    }
                ]
            stdout = json.dumps(value) if text else json.dumps(value).encode()
        elif command[1:3] == ["rm", "-f"]:
            self.containers.pop(command[-1], None)
        elif "redis-cli" in command:
            container = command[2]
            arguments = command[command.index("redis-cli") + 1 :]
            mode = "aof_everysec_rdb" if "aof_everysec_rdb" in container else "rdb_only"
            if arguments == ["--raw", "PING"]:
                stdout = "PONG\n" if text else b"PONG\n"
            elif arguments[:3] == ["--raw", "CONFIG", "GET"]:
                appendonly = "yes" if mode == "aof_everysec_rdb" else "no"
                value = (
                    f"appendonly\n{appendonly}\nappendfsync\neverysec\n"
                    "maxmemory-policy\nnoeviction\ndir\n/data\n"
                    "save\n300 100 60 10000\n"
                    "stop-writes-on-bgsave-error\nyes\nrdbchecksum\nyes\n"
                    "aof-load-truncated\nno\nno-appendfsync-on-rewrite\nno\n"
                )
                stdout = value if text else value.encode()
            elif arguments == ["--raw", "INFO", "all"]:
                count = self.info_calls.get(container, 0) + 1
                self.info_calls[container] = count
                if self.fail_info_during_workload and count == 2:
                    raise subprocess.CalledProcessError(1, command)
                bgsave_state = self.bgsave_states.get(container, 0)
                if bgsave_state == 1:
                    bgsave_in_progress = 1
                    self.bgsave_states[container] = 2
                elif bgsave_state == 2:
                    bgsave_in_progress = 0
                    self.bgsave_states[container] = 3
                else:
                    bgsave_in_progress = 0
                bgsave_complete = self.bgsave_states.get(container, 0) >= 3
                delayed = (
                    1
                    if self.delayed_fsync and mode == "aof_everysec_rdb" and count >= 3
                    else 0
                )
                enabled = 1 if mode == "aof_everysec_rdb" else 0
                child_cpu = 0.2 if bgsave_complete else 0.0
                delayed_line = (
                    f"aof_delayed_fsync:{delayed}\n"
                    if enabled and not self.omit_candidate_delayed_fsync
                    else ""
                )
                value = (
                    f"used_cpu_sys:0.5\nused_cpu_user:{0.5 + count * 0.1}\n"
                    f"used_cpu_sys_children:{child_cpu}\n"
                    f"used_cpu_user_children:{child_cpu}\n"
                    f"used_memory_rss:{1000000 + count * 10000}\n"
                    f"{delayed_line}aof_enabled:{enabled}\n"
                    "aof_last_write_status:ok\nrdb_last_bgsave_status:ok\n"
                    f"rdb_bgsave_in_progress:{bgsave_in_progress}\n"
                    f"rdb_changes_since_last_save:{0 if bgsave_complete else 100}\n"
                )
                stdout = value if text else value.encode()
            elif arguments == ["--raw", "BGSAVE"]:
                self.bgsave_states[container] = 1
                value = "Background saving started\n"
                stdout = value if text else value.encode()
            elif arguments == ["--raw", "DBSIZE"]:
                stdout = "2\n" if text else b"2\n"
            elif arguments == ["--raw", "ZCARD", "lb:global"]:
                value = f"{self.leaderboard_members}\n"
                stdout = value if text else value.encode()
        elif command[1:4] == ["exec", command[2], "cat"]:
            container = command[2]
            count = self.io_calls.get(container, 0) + 1
            self.io_calls[container] = count
            mode = "aof_everysec_rdb" if "aof_everysec_rdb" in container else "rdb_only"
            if count == 1 and self.empty_initial_io:
                value = ""
                stdout = value if text else value.encode()
                return subprocess.CompletedProcess(command, returncode, stdout, b"")
            if count == 2 and mode == "rdb_only" and self.empty_rdb_workload_io:
                value = ""
                stdout = value if text else value.encode()
                return subprocess.CompletedProcess(command, returncode, stdout, b"")
            if count == 1:
                write_bytes = 100
            elif count == 2:
                write_bytes = 100 if mode == "rdb_only" else 10100
            else:
                write_bytes = 8100 if mode == "rdb_only" else 18100
            value = f"8:0 rbytes=10 wbytes={write_bytes} rios=1 wios=1\n"
            stdout = value if text else value.encode()

        return subprocess.CompletedProcess(command, returncode, stdout, b"")


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.1
        return self.value


class RedisPersistenceBenchmarkTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        repository = Path(__file__).resolve().parents[2]
        self.profile = repository / "env/redis/redis.production-validation.conf"
        self.policy = (
            repository
            / "deploy/operations/backup-recovery-policy.candidate-v1.json"
        )
        self.summary = self.root / "evidence" / "benchmark.json"
        self.lock = self.root / "lifecycle.lock"
        self.image = "sha256:" + "a" * 64

    def _run(
        self,
        *,
        runner: FakeDocker | None = None,
        starter_returncode: int = 0,
        hanging: bool = False,
    ) -> dict[str, object]:
        docker = runner or FakeDocker()

        def start(command: list[str], **_: object) -> FakeBenchmarkProcess:
            return docker.start_benchmark(
                command, returncode=starter_returncode, hanging=hanging
            )

        return benchmark.benchmark_persistence(
            benchmark_id="aof-gate",
            candidate_profile=self.profile,
            redis_image=self.image,
            summary_path=self.summary,
            repetitions=3,
            requests=1000,
            clients=4,
            keyspace=1000,
            sample_interval_seconds=0.01,
            workload_timeout_seconds=1.0,
            active_volume="boost-gateway-production-redis-data",
            policy_path=self.policy,
            lock_path=self.lock,
            docker="docker-test",
            runner=docker,
            starter=start,
            monotonic=Clock(),
            sleeper=lambda _: None,
            host_platform=("Linux", "x86_64"),
            identity={
                "host": {"host_id_sha256": "1" * 64},
                "operator": {"name": "test", "uid": 1000, "source": "test"},
            },
            provenance={
                "commit": "c" * 40,
                "ref": "main",
                "worktree_clean": True,
                "runner_path": "scripts/tools/benchmark_redis_persistence.py",
                "runner_sha256": "d" * 64,
            },
        )

    def test_derives_baseline_by_changing_only_appendonly(self) -> None:
        baseline, candidate = benchmark.load_candidate_profile(self.profile)
        self.assertIn(b"appendonly no", baseline)
        self.assertIn(b"appendonly yes", candidate)
        self.assertEqual(
            candidate.replace(b"appendonly yes", b"appendonly no"), baseline
        )

    def test_runs_three_balanced_rounds_per_mode_and_reports_metrics(self) -> None:
        runner = FakeDocker()
        result = self._run(runner=runner)

        self.assertTrue(result["overall_pass"])
        self.assertTrue(result["measurement_complete"])
        self.assertFalse(result["activation_ready"])
        self.assertFalse(result["formal_todo0012_claim"])
        self.assertEqual(6, len(result["rounds"]))
        self.assertEqual(3, result["aggregates"]["rdb_only"]["repetitions"])
        self.assertEqual(3, result["aggregates"]["aof_everysec_rdb"]["repetitions"])
        self.assertEqual(
            [
                "rdb_only",
                "aof_everysec_rdb",
                "aof_everysec_rdb",
                "rdb_only",
                "rdb_only",
                "aof_everysec_rdb",
            ],
            [item["mode"] for item in result["rounds"]],
        )
        self.assertEqual({}, runner.volumes)
        self.assertEqual({}, runner.networks)
        self.assertEqual({}, runner.containers)
        flattened = [value for command in runner.commands for value in command]
        mounts = [value for value in flattened if value.startswith("type=")]
        self.assertFalse(
            any("boost-gateway-production-redis-data" in value for value in mounts)
        )
        run_commands = [
            command for command in runner.commands if command[1:3] == ["run", "-d"]
        ]
        self.assertEqual(6, len(run_commands))
        for command in run_commands:
            self.assertIn("boost-gateway.todo=TODO-0012", command)
            self.assertIn("boost-gateway.benchmark-id=aof-gate", command)
            self.assertIn("--network-alias", command)
        client_commands = [
            command for command in runner.commands if command[1:3] == ["run", "--name"]
        ]
        self.assertEqual(6, len(client_commands))
        for command in client_commands:
            self.assertIn("boost-gateway.benchmark-role=client", command)
            self.assertIn("--cpus", command)
            self.assertNotIn("exec", command[1:3])
        network_commands = [
            command
            for command in runner.commands
            if command[1:3] == ["network", "create"]
        ]
        self.assertEqual(6, len(network_commands))
        self.assertTrue(all("--internal" in command for command in network_commands))
        self.assertTrue(result["active_volume_identity_before_sha256"])
        self.assertEqual("boost-redis", result["active_redis_runtime"]["container"])
        self.assertEqual(self.image, result["active_redis_runtime"]["image_id"])
        self.assertEqual("c" * 40, result["controller"]["commit"])
        self.assertTrue(result["controller"]["worktree_clean"])
        self.assertEqual("candidate_only", result["policy"]["activation_state"])
        self.assertEqual(
            benchmark.sha256_file(self.profile), result["policy"]["profile_sha256"]
        )
        self.assertIn(
            "redis_bgsave_disk_write_bytes_percent",
            result["candidate_impact_percent"],
        )
        self.assertEqual(
            result["active_volume_identity_before_sha256"],
            result["active_volume_identity_after_sha256"],
        )
        for item in result["rounds"]:
            self.assertGreater(item["workload"]["throughput_requests_per_second"], 0)
            self.assertGreater(item["redis_rss_sampled_peak_bytes"], 0)
            self.assertGreaterEqual(item["redis_rss_sample_count"], 5)
            self.assertGreater(item["redis_bgsave_disk_write_bytes"], 0)
            self.assertGreater(item["redis_bgsave_children_cpu_seconds"], 0)
            self.assertEqual("ok", item["redis_bgsave"]["last_status"])
            self.assertEqual("internal_bridge", item["network_mode"])
            self.assertTrue(item["redis_cgroup_io_empty_baseline_accepted"])
            self.assertEqual(
                item["mode"] == "rdb_only",
                item["redis_cgroup_io_empty_after_workload_accepted"],
            )
            self.assertEqual(0, item["redis_aof_delayed_fsync"])
            expected_counter = item["mode"] == "aof_everysec_rdb"
            self.assertEqual(
                {"before": expected_counter, "after": expected_counter},
                item["redis_aof_delayed_fsync_counter_present"],
            )
        bgsaves = [
            command
            for command in runner.commands
            if command[-2:] == ["--raw", "BGSAVE"]
        ]
        self.assertEqual(6, len(bgsaves))

    def test_rejects_incomplete_dimensions_and_nonimmutable_image(self) -> None:
        with self.assertRaisesRegex(benchmark.BenchmarkError, "three repetitions"):
            benchmark.benchmark_persistence(
                benchmark_id="bad",
                candidate_profile=self.profile,
                redis_image=self.image,
                summary_path=self.summary,
                repetitions=2,
                host_platform=("Linux", "x86_64"),
            )
        with self.assertRaisesRegex(benchmark.BenchmarkError, "immutable"):
            benchmark.inspect_image(FakeDocker(), "docker-test", "redis:7-alpine")

        policy = json.loads(self.policy.read_text())
        policy["redis"]["profile_sha256"] = "0" * 64
        tampered_policy = self.root / "tampered-policy.json"
        tampered_policy.write_text(json.dumps(policy))
        with self.assertRaisesRegex(benchmark.BenchmarkError, "policy binding differs"):
            benchmark.load_policy_binding(
                tampered_policy, benchmark.sha256_file(self.profile)
            )

    def test_rejects_remote_docker_and_incomplete_workload_effects(self) -> None:
        with mock.patch.object(
            benchmark,
            "docker_text",
            side_effect=("default", "tcp://remote.example:2376"),
        ):
            with self.assertRaisesRegex(
                benchmark.BenchmarkError, "local system socket"
            ):
                benchmark.assert_local_docker(FakeDocker(), "docker-test")

        with self.assertRaisesRegex(benchmark.BenchmarkError, "effects are incomplete"):
            self._run(runner=FakeDocker(leaderboard_members=0))

        self.summary.unlink()
        with self.assertRaisesRegex(
            benchmark.BenchmarkError, "runtime binding differs"
        ):
            self._run(runner=FakeDocker(active_image="sha256:" + "c" * 64))

    def test_workload_failure_writes_truthful_summary_and_cleans_target(self) -> None:
        runner = FakeDocker()
        with self.assertRaisesRegex(benchmark.BenchmarkError, "benchmark failed"):
            self._run(runner=runner, starter_returncode=1)

        evidence = json.loads(self.summary.read_text())
        self.assertFalse(evidence["overall_pass"])
        self.assertFalse(evidence["activation_ready"])
        self.assertFalse(evidence["formal_todo0012_claim"])
        self.assertEqual({}, runner.volumes)
        self.assertEqual({}, runner.networks)
        self.assertEqual({}, runner.containers)
        self.assertEqual(
            evidence["active_volume_identity_before_sha256"],
            evidence["active_volume_identity_after_sha256"],
        )

    def test_delayed_fsync_fails_closed_and_summary_is_create_only(self) -> None:
        with self.assertRaisesRegex(benchmark.BenchmarkError, "delayed AOF fsync"):
            self._run(runner=FakeDocker(delayed_fsync=True))
        first = self.summary.read_bytes()
        with self.assertRaisesRegex(benchmark.BenchmarkError, "already exists"):
            self._run()
        self.assertEqual(first, self.summary.read_bytes())

    def test_hung_workload_is_killed_and_temporary_target_is_cleaned(self) -> None:
        runner = FakeDocker()
        with self.assertRaisesRegex(benchmark.BenchmarkError, "workload timeout"):
            self._run(runner=runner, hanging=True)
        evidence = json.loads(self.summary.read_text())
        self.assertFalse(evidence["overall_pass"])
        self.assertEqual({}, runner.volumes)
        self.assertEqual({}, runner.networks)
        self.assertEqual({}, runner.containers)

    def test_sampling_failure_kills_client_and_cleans_all_targets(self) -> None:
        runner = FakeDocker(fail_info_during_workload=True)
        with self.assertRaisesRegex(benchmark.BenchmarkError, "command failed"):
            self._run(runner=runner, hanging=True)
        self.assertTrue(runner.processes[0].killed)
        self.assertGreaterEqual(runner.processes[0].communicate_calls, 1)
        self.assertEqual({}, runner.volumes)
        self.assertEqual({}, runner.networks)
        self.assertEqual({}, runner.containers)

    def test_empty_cgroup_io_requires_an_explicit_zero_write_phase(self) -> None:
        runner = FakeDocker(empty_initial_io=True)
        baseline = benchmark.cgroup_io(
            runner,
            "docker-test",
            "boost-redis-benchmark-aof-gate-rdb_only-1",
            allow_empty=True,
        )
        self.assertEqual({"write_bytes": 0, "devices": 0}, baseline)

        strict_runner = FakeDocker(empty_initial_io=True)
        with self.assertRaisesRegex(benchmark.BenchmarkError, "io.stat"):
            benchmark.cgroup_io(
                strict_runner,
                "docker-test",
                "boost-redis-benchmark-aof-gate-rdb_only-1",
            )

        rdb_runner = FakeDocker(empty_initial_io=False, empty_rdb_workload_io=True)
        first = benchmark.cgroup_io(
            rdb_runner,
            "docker-test",
            "boost-redis-benchmark-aof-gate-rdb_only-1",
        )
        self.assertGreater(first["devices"], 0)
        workload = benchmark.cgroup_io(
            rdb_runner,
            "docker-test",
            "boost-redis-benchmark-aof-gate-rdb_only-1",
            allow_empty=True,
        )
        self.assertEqual({"write_bytes": 0, "devices": 0}, workload)

    def test_missing_delayed_fsync_counter_is_allowed_only_without_aof(self) -> None:
        self.assertEqual(
            (0, False),
            benchmark.delayed_fsync_counter({"aof_enabled": "0"}, aof_required=False),
        )
        with self.assertRaisesRegex(benchmark.BenchmarkError, "aof_delayed_fsync"):
            benchmark.delayed_fsync_counter({"aof_enabled": "1"}, aof_required=True)
        with self.assertRaisesRegex(benchmark.BenchmarkError, "aof_delayed_fsync"):
            self._run(runner=FakeDocker(omit_candidate_delayed_fsync=True))

    def test_ambiguous_create_is_reconciled_by_exact_labels(self) -> None:
        runner = FakeDocker(ambiguous_volume_create=True)
        with self.assertRaisesRegex(benchmark.BenchmarkError, "command failed"):
            self._run(runner=runner)
        self.assertEqual({}, runner.volumes)
        self.assertEqual({}, runner.networks)
        self.assertEqual({}, runner.containers)


if __name__ == "__main__":
    unittest.main()
