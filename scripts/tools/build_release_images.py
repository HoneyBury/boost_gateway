#!/usr/bin/env python3
"""Build and attest local runtime images from a verified release staging tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
SERVICE_IMAGES = {
    "GATEWAY_IMAGE_ID": ("gateway", "v2_gateway_demo", "Dockerfile.gateway"),
    "LOGIN_IMAGE_ID": ("login", "v2_login_backend", "Dockerfile.backend"),
    "ROOM_IMAGE_ID": ("room", "v2_room_backend", "Dockerfile.backend"),
    "BATTLE_IMAGE_ID": ("battle", "v2_battle_backend", "Dockerfile.backend"),
    "MATCHMAKING_IMAGE_ID": ("matchmaking", "v2_match_backend", "Dockerfile.backend"),
    "LEADERBOARD_IMAGE_ID": (
        "leaderboard",
        "v2_leaderboard_backend",
        "Dockerfile.backend",
    ),
}
LABELS = {
    "tag": "org.opencontainers.image.version",
    "commit": "org.opencontainers.image.revision",
    "asset": "io.boost-gateway.release.asset.sha256",
    "config": "io.boost-gateway.release.config.sha256",
}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_tree(path: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file())
    if not files:
        raise RuntimeError(f"directory has no files: {path}")
    for item in files:
        relative = item.relative_to(path).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(item)))
    return digest.hexdigest()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"command failed ({command[0]}): {detail}")
    return completed


def load_manifest(staging: Path) -> dict[str, Any]:
    try:
        manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read release staging manifest: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise RuntimeError("release staging manifest must use schema_version 1")
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", str(manifest.get("tag", ""))):
        raise RuntimeError("release staging manifest has no exact release tag")
    if COMMIT_RE.fullmatch(str(manifest.get("commit", ""))) is None:
        raise RuntimeError("release staging manifest has no full commit SHA")
    if manifest.get("platform") != "linux-x64":
        raise RuntimeError("release staging manifest is not linux-x64")
    if manifest.get("source_build_performed") is not False:
        raise RuntimeError("release staging does not prove a source-build-free path")
    return manifest


def verify_staging(staging: Path, manifest: dict[str, Any]) -> dict[str, str]:
    assets = manifest.get("assets")
    configuration = manifest.get("configuration")
    if not isinstance(assets, dict) or not isinstance(configuration, dict):
        raise RuntimeError("release staging manifest lacks assets or configuration")
    runtime_name = f"boost-gateway-{manifest['tag']}-linux-x64.tar.gz"
    asset_digest = str(assets.get(runtime_name, ""))
    config_digest = str(configuration.get("sha256", ""))
    if SHA256_RE.fullmatch(asset_digest) is None or SHA256_RE.fullmatch(config_digest) is None:
        raise RuntimeError("release staging manifest has invalid asset/configuration digests")
    if sha256_tree(staging / "config") != config_digest:
        raise RuntimeError("staged configuration digest differs from the verified manifest")
    entries = manifest.get("binaries")
    if not isinstance(entries, list):
        raise RuntimeError("release staging manifest has no binary inventory")
    expected = {str(item[1]) for item in SERVICE_IMAGES.values()}
    inventory = {
        str(item.get("name")): str(item.get("sha256"))
        for item in entries
        if isinstance(item, dict)
    }
    for name in expected:
        path = staging / "bin" / name
        if name not in inventory or SHA256_RE.fullmatch(inventory[name]) is None:
            raise RuntimeError(f"release staging manifest has no valid digest for {name}")
        if not path.is_file() or sha256_file(path) != inventory[name]:
            raise RuntimeError(f"staged binary digest differs from the verified manifest: {name}")
    return {"asset": asset_digest, "config": config_digest}


def inspect_image(image: str, expected: dict[str, str]) -> dict[str, Any]:
    try:
        document = json.loads(run(["docker", "image", "inspect", image]).stdout)[0]
    except (IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot parse Docker image identity for {image}: {exc}") from exc
    image_id = str(document.get("Id", ""))
    if IMAGE_ID_RE.fullmatch(image_id) is None:
        raise RuntimeError(f"Docker returned an invalid image ID for {image}")
    if (document.get("Os"), document.get("Architecture")) != ("linux", "amd64"):
        raise RuntimeError(f"Docker image is not linux/amd64: {image}")
    labels = document.get("Config", {}).get("Labels", {})
    if not isinstance(labels, dict):
        raise RuntimeError(f"Docker image has no provenance labels: {image}")
    for field, label in LABELS.items():
        if labels.get(label) != expected[field]:
            raise RuntimeError(f"Docker image provenance label mismatch: {image}: {label}")
    return {
        "tag": image,
        "image_id": image_id,
        "os": document["Os"],
        "architecture": document["Architecture"],
        "labels": {label: labels[label] for label in LABELS.values()},
    }


def build_images(staging: Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    staging = staging.resolve()
    manifest = load_manifest(staging)
    digests = verify_staging(staging, manifest)
    expected = {
        "tag": str(manifest["tag"]),
        "commit": str(manifest["commit"]),
        "asset": digests["asset"],
        "config": digests["config"],
    }
    identity_suffix = digests["asset"][:12]
    environment: dict[str, str] = {}
    inventory: list[dict[str, Any]] = []
    for variable, (service, binary, dockerfile) in SERVICE_IMAGES.items():
        image = f"boost-gateway/{service}:{manifest['tag']}-{identity_suffix}"
        command = [
            "docker",
            "build",
            "--platform",
            "linux/amd64",
            "--pull=false",
            "--network=none",
            "--file",
            str(staging / "deploy/runtime" / dockerfile),
            "--tag",
            image,
            "--build-arg",
            f"RELEASE_TAG={expected['tag']}",
            "--build-arg",
            f"RELEASE_REVISION={expected['commit']}",
            "--build-arg",
            f"RELEASE_ASSET_SHA256={expected['asset']}",
            "--build-arg",
            f"RELEASE_CONFIG_SHA256={expected['config']}",
        ]
        if dockerfile == "Dockerfile.backend":
            command.extend(["--build-arg", f"SERVICE_BINARY={binary}"])
        command.append(str(staging))
        run(command)
        item = inspect_image(image, expected)
        item.update({"service": service, "binary": binary})
        inventory.append(item)
        environment[variable] = item["image_id"]
    return environment, inventory


def write_outputs(
    environment: dict[str, str],
    inventory: list[dict[str, Any]],
    env_path: Path,
    summary_path: Path,
) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(environment.items())),
        encoding="utf-8",
    )
    env_path.chmod(0o640)
    summary = {
        "summary_version": 2,
        "generated_at": now(),
        "overall_pass": True,
        "passed": True,
        "source_build_performed": False,
        "network_enabled_during_build": False,
        "target_platform": "linux/amd64",
        "images": inventory,
        "artifacts": {
            "compose_image_environment": str(env_path),
            "summary_path": str(summary_path),
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--env-path", type=Path, required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    args = parser.parse_args()
    try:
        environment, inventory = build_images(args.staging_dir)
        write_outputs(environment, inventory, args.env_path, args.summary_path)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"release runtime images: FAIL ({exc})")
        return 1
    print(f"release runtime images: PASS ({len(inventory)} immutable images)")
    print(f"summary: {args.summary_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
