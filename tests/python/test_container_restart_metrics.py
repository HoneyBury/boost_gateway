from __future__ import annotations

import unittest
from unittest import mock

from scripts.tools import collect_container_restart_metrics as collector


class ContainerRestartMetricsTest(unittest.TestCase):
    @mock.patch.object(collector.subprocess, "run")
    @mock.patch.object(collector, "read_cgroup_id")
    def test_collects_whitelisted_container_samples(
        self, read_cgroup_id: mock.Mock, run: mock.Mock
    ) -> None:
        read_cgroup_id.side_effect = [
            f"/system.slice/docker-{index + 1:064x}.scope"
            for index, _ in enumerate(collector.CONTAINERS)
        ]
        run.side_effect = [
            mock.Mock(
                returncode=0,
                stdout=f"{index + 1:064x} {index} {1000 + index}\n",
            )
            for index, _ in enumerate(collector.CONTAINERS)
        ]

        samples, missing = collector.collect_container_samples()

        self.assertEqual(missing, [])
        self.assertEqual(set(samples), set(collector.CONTAINERS))
        for call, container in zip(run.call_args_list, collector.CONTAINERS, strict=True):
            self.assertEqual(call.args[0][-1], container)
            self.assertEqual(
                call.args[0][-2], "{{.Id}} {{.RestartCount}} {{.State.Pid}}"
            )

    def test_partial_collection_is_explicit_in_metrics(self) -> None:
        rendered = collector.render_metrics(
            {
                "boost-gateway": collector.ContainerSample(
                    container_id="a" * 64,
                    cgroup_id="/system.slice/docker-example.scope",
                    restart_count=2,
                )
            },
            ["boost-redis"],
            1234567890,
        )

        self.assertIn(
            'boost_gateway_container_restart_count{container="boost-gateway"} 2',
            rendered,
        )
        self.assertIn(
            'boost_gateway_container_info{container="boost-gateway",'
            f'container_id="{"a" * 64}",'
            'id="/system.slice/docker-example.scope"} 1',
            rendered,
        )
        self.assertIn("boost_gateway_container_restart_collection_success 0", rendered)
        self.assertIn(
            "boost_gateway_container_restart_collection_timestamp_seconds 1234567890",
            rendered,
        )

    def test_reads_unified_cgroup_path(self) -> None:
        with mock.patch.object(collector.Path, "read_text") as read_text:
            read_text.return_value = "0::/system.slice/docker-example.scope\n"

            self.assertEqual(
                collector.read_cgroup_id(123),
                "/system.slice/docker-example.scope",
            )


if __name__ == "__main__":
    unittest.main()
