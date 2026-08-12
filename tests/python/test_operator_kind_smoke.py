from __future__ import annotations

import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts.tools import operator_kind_smoke


class OperatorKindSmokeTest(unittest.TestCase):
    def test_pull_image_retries_transient_registry_failure(self) -> None:
        returncodes = iter((1, 1, 1, 0))
        calls: list[list[str]] = []

        def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
            calls.append(command)
            return SimpleNamespace(returncode=next(returncodes))

        with mock.patch.object(
            operator_kind_smoke.subprocess, "run", side_effect=run
        ), mock.patch.object(operator_kind_smoke.time, "sleep", return_value=None):
            operator_kind_smoke.pull_image("kindest/node@sha256:test")

        self.assertEqual(4, len(calls))

    def test_pull_image_fails_after_bounded_attempts(self) -> None:
        result = SimpleNamespace(returncode=1)
        with mock.patch.object(
            operator_kind_smoke.subprocess, "run", return_value=result
        ), mock.patch.object(operator_kind_smoke.time, "sleep", return_value=None):
            with self.assertRaises(subprocess.CalledProcessError):
                operator_kind_smoke.pull_image(
                    "kindest/node@sha256:test", attempts=2
                )

    def test_pull_image_uses_exact_cached_digest_without_registry(self) -> None:
        calls: list[list[str]] = []

        def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
            calls.append(command)
            return SimpleNamespace(returncode=0)

        with mock.patch.object(operator_kind_smoke.subprocess, "run", side_effect=run):
            operator_kind_smoke.pull_image("kindest/node@sha256:test")

        self.assertEqual(
            [["docker", "image", "inspect", "kindest/node@sha256:test"]], calls
        )


if __name__ == "__main__":
    unittest.main()
