from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from scripts.tools import operator_kind_smoke


def test_pull_image_retries_transient_registry_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    returncodes = iter((1, 1, 0))
    calls: list[list[str]] = []
    monkeypatch.setattr(
        operator_kind_smoke.subprocess,
        "run",
        lambda command, check: calls.append(command) or SimpleNamespace(returncode=next(returncodes)),
    )
    monkeypatch.setattr(operator_kind_smoke.time, "sleep", lambda _seconds: None)
    operator_kind_smoke.pull_image("kindest/node@sha256:test")
    assert len(calls) == 3


def test_pull_image_fails_after_bounded_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        operator_kind_smoke.subprocess,
        "run",
        lambda _command, check: SimpleNamespace(returncode=1),
    )
    monkeypatch.setattr(operator_kind_smoke.time, "sleep", lambda _seconds: None)
    with pytest.raises(subprocess.CalledProcessError):
        operator_kind_smoke.pull_image("kindest/node@sha256:test", attempts=2)
