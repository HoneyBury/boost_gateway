from __future__ import annotations

from scripts.gates.release.check_mainline_readiness import (
    process_supervisor_kills_process_group,
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
