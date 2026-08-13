from __future__ import annotations

from unittest import mock

from scripts.gates.release.check_mainline_readiness import (
    REPOSITORY_SUITE,
    process_supervisor_kills_process_group,
    run_repository_suite,
)


def test_process_group_gate_accepts_captured_pid() -> None:
    source = """
const pid_t pid = state.pid;
const pid_t process_group = -pid;
::kill(process_group, SIGTERM);
::kill(process_group, SIGKILL);
"""

    assert process_supervisor_kills_process_group(source)


def test_process_group_gate_rejects_direct_child_signals() -> None:
    source = """
const pid_t pid = state.pid;
::kill(pid, SIGTERM);
::kill(pid, SIGKILL);
"""

    assert not process_supervisor_kills_process_group(source)


def test_repository_suite_runs_each_governed_command_and_records_failure() -> None:
    completed = [
        mock.Mock(returncode=0, stdout="PASS") for _ in REPOSITORY_SUITE
    ]
    completed[-1] = mock.Mock(returncode=2, stdout="contract failed")
    checks: list[dict[str, object]] = []

    with mock.patch(
        "scripts.gates.release.check_mainline_readiness.subprocess.run",
        side_effect=completed,
    ) as run:
        run_repository_suite(checks)

    assert len(checks) == len(REPOSITORY_SUITE)
    assert all(check["passed"] for check in checks[:-1])
    assert checks[-1]["passed"] is False
    todo_call = run.call_args_list[REPOSITORY_SUITE.index("scripts/manage_todos.py")]
    assert todo_call.args[0][-1] == "check"
