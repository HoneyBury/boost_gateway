"""Durable filesystem primitives shared by release lifecycle transactions."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class LifecycleError(RuntimeError):
    """Raised when a lifecycle contract cannot be satisfied safely."""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_tree(path: Path) -> str:
    if not path.is_dir():
        raise LifecycleError(f"directory is missing: {path}")
    digest = hashlib.sha256()
    files = sorted(
        item
        for item in path.rglob("*")
        if item.is_file()
        and "__pycache__" not in item.relative_to(path).parts
        and item.suffix not in {".pyc", ".pyo"}
    )
    if not files:
        raise LifecycleError(f"directory has no files: {path}")
    for item in files:
        if item.is_symlink():
            raise LifecycleError(
                f"symbolic links are forbidden in immutable input: {item}"
            )
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(item)))
    return digest.hexdigest()


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LifecycleError(f"{label} must be a JSON object: {path}")
    return value


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path: Path, content: bytes, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: dict[str, Any], mode: int = 0o640) -> None:
    atomic_write(
        path,
        (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        mode,
    )


def atomic_write_new_json(
    path: Path, value: dict[str, Any], mode: int = 0o640
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise LifecycleError(f"create-only JSON already exists: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise LifecycleError(f"create-only JSON already exists: {path}") from exc
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
