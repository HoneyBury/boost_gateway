#!/usr/bin/env python3
"""Export or import the complete R5 Docker Compose image cache.

The bundle is deliberately tied to the Compose file and image IDs.  It is not a
registry mirror: import it on the fixed Linux runner, then let the existing R5
preflight prove that Docker can run entirely from its local cache.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COMPOSE = ROOT / "env/docker/docker-compose.yml"
DEFAULT_BUNDLE = ROOT / "runtime/r5-docker-cache/r5-docker-images-linux-amd64.tar.gz"


def run(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )


def fail(message: str) -> None:
    raise RuntimeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compose_document(compose_file: Path) -> dict[str, Any]:
    completed = run(["docker", "compose", "-f", str(compose_file), "config", "--format", "json"])
    if completed.returncode:
        fail("cannot resolve Docker Compose configuration: " + completed.stderr.strip())
    try:
        document = json.loads(completed.stdout)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        fail(f"invalid Docker Compose JSON: {exc}")
    if not isinstance(document, dict):
        fail("Compose configuration is not an object")
    return document


def compose_requirements(document: dict[str, Any]) -> list[dict[str, str]]:
    project = str(document.get("name", ""))
    services = document.get("services")
    if not isinstance(services, dict) or not project:
        fail("Compose configuration has no project name or services")

    requirements: list[dict[str, str]] = []
    for service, raw in sorted(services.items()):
        if not isinstance(raw, dict):
            fail(f"Compose service {service} is not an object")
        build_backed = bool(raw.get("build"))
        image = str(raw.get("image") or (f"{project}-{service}" if build_backed else ""))
        if not image:
            fail(f"Compose service {service} has neither image nor build")
        requirements.append({"service": str(service), "image": image, "source": "build" if build_backed else "registry"})
    return requirements


def build_compose_images(document: dict[str, Any], requirements: list[dict[str, str]]) -> None:
    """Build each application image explicitly because Compose may ignore the platform env."""
    services = document["services"]
    by_service = {item["service"]: item for item in requirements}
    for service, requirement in by_service.items():
        if requirement["source"] != "build":
            continue
        raw_service = services[service]
        raw_build = raw_service.get("build")
        build = {"context": raw_build} if isinstance(raw_build, str) else raw_build
        if not isinstance(build, dict) or not build.get("context"):
            fail(f"Compose build configuration is invalid for {service}")
        context = Path(str(build["context"]))
        dockerfile = Path(str(build.get("dockerfile", "Dockerfile")))
        if not dockerfile.is_absolute():
            dockerfile = context / dockerfile
        command = [
            "docker", "build", "--platform", "linux/amd64", "--pull",
            "--file", str(dockerfile), "--tag", requirement["image"],
        ]
        build_args = build.get("args") or {}
        if isinstance(build_args, dict):
            for key, value in sorted(build_args.items()):
                command.extend(["--build-arg", f"{key}={value}"])
        elif isinstance(build_args, list):
            for value in build_args:
                command.extend(["--build-arg", str(value)])
        else:
            fail(f"Compose build args are invalid for {service}")
        if build.get("target"):
            command.extend(["--target", str(build["target"])])
        command.append(str(context))
        completed = run(command, env=linux_amd64_env())
        if completed.returncode:
            fail(f"cannot build {service} for linux/amd64: {completed.stderr.strip()}")


def image_metadata(image: str) -> dict[str, Any]:
    completed = run(["docker", "image", "inspect", image])
    if completed.returncode:
        fail(f"required image is absent: {image}: {completed.stderr.strip()}")
    try:
        item = json.loads(completed.stdout)[0]
    except (IndexError, TypeError, json.JSONDecodeError) as exc:
        fail(f"cannot inspect image {image}: {exc}")
    return {
        "image": image,
        "image_id": str(item.get("Id", "")),
        "repo_tags": item.get("RepoTags") or [],
        "repo_digests": item.get("RepoDigests") or [],
        "os": str(item.get("Os", "")),
        "architecture": str(item.get("Architecture", "")),
    }


def linux_amd64_env() -> dict[str, str]:
    environment = os.environ.copy()
    environment["DOCKER_DEFAULT_PLATFORM"] = "linux/amd64"
    return environment


def export_bundle(args: argparse.Namespace) -> None:
    compose_file = args.compose_file.resolve()
    if not compose_file.is_file():
        fail(f"Compose file does not exist: {compose_file}")
    document = compose_document(compose_file)
    requirements = compose_requirements(document)
    environment = linux_amd64_env()
    registry_images = sorted({item["image"] for item in requirements if item["source"] == "registry"})
    for image in registry_images:
        completed = run(["docker", "pull", "--platform", "linux/amd64", image], env=environment)
        if completed.returncode:
            fail(f"cannot pull {image}: {completed.stderr.strip()}")
    if not args.skip_build:
        build_compose_images(document, requirements)

    images = sorted({item["image"] for item in requirements})
    inventory = [image_metadata(image) for image in images]
    wrong_platform = [item["image"] for item in inventory if (item["os"], item["architecture"]) != ("linux", "amd64")]
    if wrong_platform:
        fail("images are not linux/amd64: " + ", ".join(wrong_platform))

    bundle = args.bundle.resolve()
    bundle.parent.mkdir(parents=True, exist_ok=True)
    manifest = Path(str(bundle) + ".manifest.json")
    with tempfile.TemporaryDirectory(prefix="boost-r5-image-export-") as temp:
        tar_path = Path(temp) / "images.tar"
        completed = run(["docker", "save", "--output", str(tar_path), *images])
        if completed.returncode:
            fail("docker save failed: " + completed.stderr.strip())
        with tar_path.open("rb") as source, gzip.open(bundle, "wb", compresslevel=6) as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "target_platform": "linux/amd64",
        "compose_file": str(compose_file.relative_to(ROOT)),
        "compose_sha256": sha256(compose_file),
        "bundle": {"path": bundle.name, "sha256": sha256(bundle), "bytes": bundle.stat().st_size},
        "requirements": requirements,
        "image_inventory": inventory,
        "source_host": {"system": platform.system(), "machine": platform.machine()},
    }
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"exported {len(images)} images to {bundle}")
    print(f"manifest: {manifest}")
    print("Copy both files to the Linux runner, then run this command there:")
    print(f"python3 scripts/tools/r5_docker_cache_bundle.py import --bundle {bundle.name}")


def import_bundle(args: argparse.Namespace) -> None:
    bundle = args.bundle.resolve()
    manifest_path = Path(str(bundle) + ".manifest.json")
    if not bundle.is_file() or not manifest_path.is_file():
        fail("bundle and adjacent .manifest.json are both required")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"invalid bundle manifest: {exc}")
    if manifest.get("target_platform") != "linux/amd64":
        fail("bundle is not declared for linux/amd64")
    if sha256(bundle) != manifest.get("bundle", {}).get("sha256"):
        fail("bundle SHA-256 does not match manifest")
    compose_file = args.compose_file.resolve()
    if not args.allow_compose_drift and sha256(compose_file) != manifest.get("compose_sha256"):
        fail("Compose file differs from bundle manifest; export again from the candidate checkout")

    with tempfile.TemporaryDirectory(prefix="boost-r5-image-import-") as temp:
        tar_path = Path(temp) / "images.tar"
        with gzip.open(bundle, "rb") as source, tar_path.open("wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        if not args.verify_only:
            completed = run(["docker", "load", "--input", str(tar_path)])
            if completed.returncode:
                fail("docker load failed: " + completed.stderr.strip())

    expected = {str(item["image"]): item for item in manifest.get("image_inventory", [])}
    failures: list[str] = []
    for image, item in expected.items():
        try:
            actual = image_metadata(image)
        except RuntimeError as exc:
            failures.append(str(exc))
            continue
        if actual["image_id"] != item.get("image_id"):
            failures.append(f"image ID differs for {image}")
        if (actual["os"], actual["architecture"]) != ("linux", "amd64"):
            failures.append(f"wrong platform for {image}: {actual['os']}/{actual['architecture']}")
    if failures:
        fail("cache validation failed: " + "; ".join(failures))
    print(f"validated {len(expected)} linux/amd64 cached images from {bundle}")
    print("Next: python3 scripts/verify_preprod_recovery_drill.py --mode docker-compose --image-preflight-only --docker-pull-policy never")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    for name in ("export", "import"):
        child = subparsers.add_parser(name)
        child.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
        child.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE)
    export = subparsers.choices["export"]
    export.add_argument("--skip-build", action="store_true", help="require prebuilt application images")
    importer = subparsers.choices["import"]
    importer.add_argument("--verify-only", action="store_true", help="do not run docker load")
    importer.add_argument("--allow-compose-drift", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "export":
            export_bundle(args)
        else:
            import_bundle(args)
    except (OSError, RuntimeError) as exc:
        print(f"R5 Docker cache bundle: ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
