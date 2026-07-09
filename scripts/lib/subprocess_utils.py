#!/usr/bin/env python3
"""Shared subprocess helpers for validation scripts."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def normalize_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def tail_output(value: str | bytes | None, max_chars: int) -> str:
    text = normalize_output(value)
    return text if len(text) <= max_chars else text[-max_chars:]


def emit_text(text: str, *, stderr: bool = False) -> None:
    stream = sys.stderr if stderr else sys.stdout
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        stream.buffer.write(text.encode(encoding, errors="replace"))


def run_step(
    *,
    name: str,
    command: list[str],
    cwd: Path,
    timeout_seconds: int,
    category: str | None = None,
    tail_chars: int = 6000,
    stream_output: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    proc: subprocess.Popen[str] | None = None
    stdout: str | bytes | None = ""
    stderr: str | bytes | None = ""
    status = "failed"
    returncode: int | None = None

    try:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
        returncode = proc.returncode
        status = "passed" if proc.returncode == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            time.sleep(0.5)
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                stdout, stderr = proc.communicate(timeout=2)
                returncode = proc.returncode
            except subprocess.TimeoutExpired:
                stdout = exc.stdout
                stderr = exc.stderr
        else:
            stdout = exc.stdout
            stderr = exc.stderr

    stdout_text = normalize_output(stdout)
    stderr_text = normalize_output(stderr)
    if stream_output:
        if stdout_text:
            emit_text(stdout_text)
        if stderr_text:
            emit_text(stderr_text, stderr=True)

    result: dict[str, Any] = {
        "name": name,
        "command": command,
        "status": status,
        "passed": status == "passed",
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout": tail_output(stdout_text, tail_chars),
        "stderr": tail_output(stderr_text, tail_chars),
    }
    if category is not None:
        result["category"] = category
    if returncode is not None:
        result["returncode"] = returncode
    return result
