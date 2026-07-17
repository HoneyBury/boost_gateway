#!/usr/bin/env python3
"""Install checksum-pinned kind and kubectl binaries into a runner-local directory."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import urllib.request
from pathlib import Path


TOOLS = {
    "kind": {
        "version": "v0.32.0",
        "url": "https://github.com/kubernetes-sigs/kind/releases/download/v0.32.0/kind-linux-amd64",
        "sha256": "50030de23cf40a18505f20426f6a8506bedf13c6e509244bd1fa9463721b0f54",
    },
    "kubectl": {
        "version": "v1.36.1",
        "url": "https://dl.k8s.io/release/v1.36.1/bin/linux/amd64/kubectl",
        "sha256": "629d3f410e09bf49b64ae7079f7f0bda1191efed311f7d37fdbab0ad5b0ec2b7",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def install_tool(name: str, metadata: dict[str, str], install_dir: Path) -> Path:
    destination = install_dir / name
    if destination.is_file() and sha256(destination) == metadata["sha256"]:
        destination.chmod(0o755)
        return destination
    partial = install_dir / f".{name}.download"
    if partial.exists():
        partial.unlink()
    request = urllib.request.Request(metadata["url"], headers={"User-Agent": "boost-gateway-release-validation"})
    with urllib.request.urlopen(request, timeout=300) as response, partial.open("wb") as output:
        shutil.copyfileobj(response, output)
    observed = sha256(partial)
    if observed != metadata["sha256"]:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"{name} checksum mismatch: expected {metadata['sha256']}, observed {observed}")
    partial.replace(destination)
    destination.chmod(0o755)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install-dir",
        type=Path,
        default=Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "boost-gateway-kubernetes-tools",
    )
    parser.add_argument("--github-path", type=Path)
    args = parser.parse_args()
    args.install_dir.mkdir(parents=True, exist_ok=True)
    for name, metadata in TOOLS.items():
        path = install_tool(name, metadata, args.install_dir)
        print(f"installed {name} {metadata['version']}: {path} sha256={metadata['sha256']}")
    if args.github_path:
        with args.github_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{args.install_dir.resolve()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
