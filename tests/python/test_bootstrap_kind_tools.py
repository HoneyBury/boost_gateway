from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from scripts.tools import bootstrap_kind_tools


def metadata(payload: bytes) -> dict[str, str]:
    return {
        "version": "test",
        "url": "https://example.invalid/tool",
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_install_tool_reuses_checksum_verified_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"verified tool"
    destination = tmp_path / "kind"
    destination.write_bytes(payload)
    monkeypatch.setattr(bootstrap_kind_tools.urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("unexpected download"))
    assert bootstrap_kind_tools.install_tool("kind", metadata(payload), tmp_path) == destination
    assert destination.stat().st_mode & 0o111


def test_install_tool_rejects_download_with_wrong_checksum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bootstrap_kind_tools.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(b"tampered"),
    )
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        bootstrap_kind_tools.install_tool("kubectl", metadata(b"expected"), tmp_path)
    assert not (tmp_path / ".kubectl.download").exists()
