from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.lib.release_lifecycle_io import (
    LifecycleError,
    atomic_write_json,
    atomic_write_new_json,
    load_json_object,
    sha256_tree,
)


def test_atomic_json_writers_preserve_replace_and_create_only_contracts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "record.json"
    atomic_write_json(path, {"version": 1})
    atomic_write_json(path, {"version": 2})

    assert load_json_object(path, "record") == {"version": 2}
    create_only = tmp_path / "state" / "immutable.json"
    atomic_write_new_json(create_only, {"created": True})
    with pytest.raises(LifecycleError, match="already exists"):
        atomic_write_new_json(create_only, {"created": False})


def test_sha256_tree_is_stable_and_ignores_python_cache(tmp_path: Path) -> None:
    root = tmp_path / "release"
    root.mkdir()
    (root / "config.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    first = sha256_tree(root)

    cache = root / "__pycache__"
    cache.mkdir()
    (cache / "ignored.pyc").write_bytes(b"ignored")
    assert sha256_tree(root) == first

    (root / "config.json").write_text(json.dumps({"a": 2}), encoding="utf-8")
    assert sha256_tree(root) != first
