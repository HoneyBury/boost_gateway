from __future__ import annotations

import unittest
from unittest import mock

from scripts.tools import collect_container_restart_metrics as collector


class ContainerRestartMetricsTest(unittest.TestCase):
    @mock.patch.object(collector.subprocess, "run")
    def test_collects_whitelisted_restart_counts(self, run: mock.Mock) -> None:
        run.side_effect = [
            mock.Mock(returncode=0, stdout=f"{index}\n")
            for index, _ in enumerate(collector.CONTAINERS)
        ]

        counts, missing = collector.collect_restart_counts()

        self.assertEqual(missing, [])
        self.assertEqual(set(counts), set(collector.CONTAINERS))
        for call, container in zip(run.call_args_list, collector.CONTAINERS, strict=True):
            self.assertEqual(call.args[0][-1], container)

    def test_partial_collection_is_explicit_in_metrics(self) -> None:
        rendered = collector.render_metrics(
            {"boost-gateway": 2}, ["boost-redis"], 1234567890
        )

        self.assertIn(
            'boost_gateway_container_restart_count{container="boost-gateway"} 2',
            rendered,
        )
        self.assertIn("boost_gateway_container_restart_collection_success 0", rendered)
        self.assertIn(
            "boost_gateway_container_restart_collection_timestamp_seconds 1234567890",
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
