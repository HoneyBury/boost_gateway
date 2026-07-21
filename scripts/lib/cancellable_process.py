#!/usr/bin/env python3
"""Signal-aware subprocess and atomic evidence helpers for long-running gates."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, Iterator


class CancellationState:
    def __init__(self) -> None:
        self._event = threading.Event()
        self.signal_number: int | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def signal_name(self) -> str:
        if self.signal_number is None:
            return ""
        with suppress(ValueError):
            return signal.Signals(self.signal_number).name
        return str(self.signal_number)

    def request(self, signal_number: int) -> None:
        if not self._event.is_set():
            self.signal_number = signal_number
            self._event.set()


@contextmanager
def installed_signal_handlers(state: CancellationState) -> Iterator[None]:
    previous: dict[int, Any] = {}

    def handle_signal(signal_number: int, _frame: object) -> None:
        state.request(signal_number)

    for signal_number in (signal.SIGINT, signal.SIGTERM):
        previous[signal_number] = signal.getsignal(signal_number)
        signal.signal(signal_number, handle_signal)
    try:
        yield
    finally:
        for signal_number, handler in previous.items():
            signal.signal(signal_number, handler)


def terminate_process_group(
    process: subprocess.Popen[str],
    grace_seconds: float,
    hard_timeout_seconds: float = 2.0,
) -> tuple[str, str]:
    def signal_tree(*, force: bool) -> None:
        if os.name == "nt":
            command = ["taskkill", "/PID", str(process.pid), "/T"]
            if force:
                command.append("/F")
            with suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    command,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=hard_timeout_seconds,
                )
            return

        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except (AttributeError, ProcessLookupError, PermissionError):
            if process.poll() is None:
                with suppress(ProcessLookupError, PermissionError):
                    process.kill() if force else process.terminate()

    def timeout_output(exc: subprocess.TimeoutExpired) -> tuple[str, str]:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return stdout, stderr

    signal_tree(force=False)
    try:
        return process.communicate(timeout=grace_seconds)
    except subprocess.TimeoutExpired as graceful_timeout:
        signal_tree(force=True)
        try:
            return process.communicate(timeout=hard_timeout_seconds)
        except subprocess.TimeoutExpired as hard_timeout:
            stdout, stderr = timeout_output(hard_timeout)
            if not stdout and not stderr:
                stdout, stderr = timeout_output(graceful_timeout)
            for stream in (process.stdout, process.stderr, process.stdin):
                if stream is not None:
                    with suppress(OSError):
                        stream.close()
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=0.1)
            return stdout, stderr


def run_cancellable_process(
    command: list[str],
    cwd: Path,
    timeout_seconds: float,
    cancellation: CancellationState,
    *,
    termination_grace_seconds: float | None = None,
    cancellation_grace_seconds: float = 0.5,
    timeout_grace_seconds: float = 0.5,
    poll_interval_seconds: float = 0.2,
) -> dict[str, Any]:
    if termination_grace_seconds is not None:
        cancellation_grace_seconds = termination_grace_seconds
        timeout_grace_seconds = termination_grace_seconds
    started = time.monotonic()
    if cancellation.cancelled:
        return {
            "status": "cancelled",
            "returncode": None,
            "signal": cancellation.signal_name,
            "stdout": "",
            "stderr": "",
            "duration_seconds": 0.0,
        }

    process = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    deadline = started + timeout_seconds
    while True:
        if cancellation.cancelled:
            stdout, stderr = terminate_process_group(process, cancellation_grace_seconds)
            return {
                "status": "cancelled",
                "returncode": process.returncode,
                "signal": cancellation.signal_name,
                "stdout": stdout,
                "stderr": stderr,
                "duration_seconds": round(time.monotonic() - started, 3),
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stdout, stderr = terminate_process_group(process, timeout_grace_seconds)
            return {
                "status": "timeout",
                "returncode": process.returncode,
                "signal": "",
                "stdout": stdout,
                "stderr": stderr,
                "duration_seconds": round(time.monotonic() - started, 3),
            }
        try:
            stdout, stderr = process.communicate(
                timeout=min(poll_interval_seconds, remaining)
            )
        except subprocess.TimeoutExpired:
            continue
        return {
            "status": "passed" if process.returncode == 0 else "failed",
            "returncode": process.returncode,
            "signal": "",
            "stdout": stdout,
            "stderr": stderr,
            "duration_seconds": round(time.monotonic() - started, 3),
        }


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, path)
