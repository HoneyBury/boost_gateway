import unittest
from unittest.mock import patch

from scripts.producers.collect_v2_perf_baseline import (
    apply_cpu_affinity,
    format_cpu_set,
    parse_cpu_set,
)


class PerfCpuAffinityTest(unittest.TestCase):
    def test_parse_and_format_cpu_set(self) -> None:
        self.assertEqual(parse_cpu_set("0-2,4,6-7"), {0, 1, 2, 4, 6, 7})
        self.assertEqual(format_cpu_set({7, 2, 1, 0, 6, 4}), "0-2,4,6-7")

    def test_parse_rejects_invalid_cpu_sets(self) -> None:
        for value in ("", "0,,2", "2-0", "a", "1-2-3", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_cpu_set(value)

    @patch("scripts.producers.collect_v2_perf_baseline.platform.system", return_value="Linux")
    @patch("scripts.producers.collect_v2_perf_baseline.os.sched_setaffinity", create=True)
    @patch("scripts.producers.collect_v2_perf_baseline.os.sched_getaffinity", create=True)
    def test_apply_cpu_affinity_verifies_effective_set(
        self,
        get_affinity,
        set_affinity,
        _system,
    ) -> None:
        get_affinity.side_effect = [{0, 1, 2, 3}, {0, 1}]

        result = apply_cpu_affinity("0-1")

        set_affinity.assert_called_once_with(0, {0, 1})
        self.assertTrue(result["applied"])
        self.assertEqual(result["allowed_cpu_set_before"], "0-3")
        self.assertEqual(result["effective_cpu_set"], "0-1")
        self.assertEqual(result["cpu_count"], 2)

    @patch("scripts.producers.collect_v2_perf_baseline.platform.system", return_value="Linux")
    @patch("scripts.producers.collect_v2_perf_baseline.os.sched_setaffinity", create=True)
    @patch(
        "scripts.producers.collect_v2_perf_baseline.os.sched_getaffinity",
        return_value={2, 3},
        create=True,
    )
    def test_apply_cpu_affinity_rejects_cpu_outside_allowed_set(
        self,
        _get_affinity,
        set_affinity,
        _system,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "outside the collector's allowed set"):
            apply_cpu_affinity("0")
        set_affinity.assert_not_called()


if __name__ == "__main__":
    unittest.main()
