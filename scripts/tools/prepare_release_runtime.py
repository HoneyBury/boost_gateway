#!/usr/bin/env python3
"""Download, verify, and stage an immutable Linux x64 release runtime."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts.tools.harden_release_sbom import verify_attested_sbom_predicate
from scripts.tools.prepare_docker_runtime_context import validate_runtime_dependencies
from scripts.tools.verify_release_archive import verify_archive
from scripts.tools.verify_release_package_consumer import extract_archive


REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
TAG_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SPDX_PREDICATE_TYPE = "https://spdx.dev/Document/v2.3"
SIGNER_WORKFLOW_SUFFIX = "/.github/workflows/release.yml"
PLATFORM = "linux-x64"
REQUIRED_RUNTIME_BINARIES = (
    "v2_gateway_demo",
    "v2_login_backend",
    "v2_room_backend",
    "v2_battle_backend",
    "v2_match_backend",
    "v2_leaderboard_backend",
    "sdk_full_flow_client",
)


def generated_at() -> str:
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
        raise RuntimeError(f"directory has no regular files: {path}")
    for item in files:
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(item)))
    return digest.hexdigest()


def write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


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


def run_json(command: list[str], label: str) -> Any:
    completed = run(command)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} did not return valid JSON: {exc}") from exc


def github_request(url: str) -> urllib.request.Request:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "boost-gateway-release-consumer",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def github_api_json(path: str, label: str) -> Any:
    url = "https://api.github.com/" + path.lstrip("/")
    try:
        with urllib.request.urlopen(github_request(url), timeout=60) as response:
            return json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {label} from GitHub API: {exc}") from exc


def download_asset(url: str, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.part")
    try:
        with urllib.request.urlopen(github_request(url), timeout=120) as response:
            with temporary.open("wb") as stream:
                shutil.copyfileobj(response, stream, length=1024 * 1024)
        temporary.replace(destination)
    except (OSError, urllib.error.URLError) as exc:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"cannot download release asset {destination.name}: {exc}") from exc


def validate_inputs(repository: str, tag: str, expected_commit: str) -> None:
    if REPOSITORY_RE.fullmatch(repository) is None:
        raise RuntimeError("repository must use the owner/name form")
    if TAG_RE.fullmatch(tag) is None:
        raise RuntimeError("tag must use the exact vMAJOR.MINOR.PATCH form")
    if COMMIT_RE.fullmatch(expected_commit) is None:
        raise RuntimeError("expected commit must be a full lowercase 40-character SHA")


def resolve_tag_commit(repository: str, tag: str) -> tuple[str, dict[str, Any]]:
    reference = github_api_json(
        f"repos/{repository}/git/ref/tags/{tag}", "tag reference"
    )
    if not isinstance(reference, dict) or not isinstance(reference.get("object"), dict):
        raise RuntimeError("GitHub tag reference response is incomplete")
    target = reference["object"]
    if target.get("type") == "commit":
        commit = str(target.get("sha", ""))
        evidence = {"reference": reference, "annotated_tag": None}
    elif target.get("type") == "tag":
        tag_object = github_api_json(
            f"repos/{repository}/git/tags/{target.get('sha', '')}", "annotated tag"
        )
        if not isinstance(tag_object, dict) or tag_object.get("tag") != tag:
            raise RuntimeError("annotated tag object does not match the requested tag")
        annotated_target = tag_object.get("object")
        if not isinstance(annotated_target, dict) or annotated_target.get("type") != "commit":
            raise RuntimeError("annotated tag does not point directly to a commit")
        commit = str(annotated_target.get("sha", ""))
        evidence = {"reference": reference, "annotated_tag": tag_object}
    else:
        raise RuntimeError(f"unsupported tag object type: {target.get('type')!r}")
    if COMMIT_RE.fullmatch(commit) is None:
        raise RuntimeError("tag did not resolve to a full commit SHA")
    return commit, evidence


def release_metadata(repository: str, tag: str) -> dict[str, Any]:
    metadata = github_api_json(f"repos/{repository}/releases/tags/{tag}", "release metadata")
    if not isinstance(metadata, dict) or metadata.get("tag_name") != tag:
        raise RuntimeError("release metadata does not match the requested tag")
    if metadata.get("draft") or metadata.get("prerelease"):
        raise RuntimeError("draft or prerelease assets cannot enter production staging")
    if not isinstance(metadata.get("assets"), list):
        raise RuntimeError("release metadata has no asset inventory")
    return metadata


def release_asset_digests(metadata: dict[str, Any], required: set[str]) -> dict[str, str]:
    digests: dict[str, str] = {}
    for item in metadata["assets"]:
        if not isinstance(item, dict) or item.get("name") not in required:
            continue
        name = str(item["name"])
        value = str(item.get("digest", ""))
        if name in digests:
            raise RuntimeError(f"release contains a duplicate required asset: {name}")
        if not value.startswith("sha256:") or SHA256_RE.fullmatch(value[7:]) is None:
            raise RuntimeError(f"release asset has no valid GitHub SHA-256 digest: {name}")
        digests[name] = value[7:]
    missing = required - set(digests)
    if missing:
        raise RuntimeError(f"release is missing required assets: {sorted(missing)}")
    return digests


def release_asset_urls(metadata: dict[str, Any], required: set[str]) -> dict[str, str]:
    urls: dict[str, str] = {}
    for item in metadata["assets"]:
        if not isinstance(item, dict) or item.get("name") not in required:
            continue
        name = str(item["name"])
        url = str(item.get("browser_download_url", ""))
        if name in urls or not url.startswith("https://github.com/"):
            raise RuntimeError(f"release asset has an invalid download URL: {name}")
        urls[name] = url
    if set(urls) != required:
        raise RuntimeError("release download URLs do not cover the required assets")
    return urls


def parse_checksum_manifest(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9_.+-]*)", raw_line)
        if match is None:
            raise RuntimeError(f"malformed checksum manifest line {line_number}")
        digest, name = match.groups()
        if name in entries:
            raise RuntimeError(f"duplicate checksum manifest entry: {name}")
        entries[name] = digest
    if not entries:
        raise RuntimeError("checksum manifest is empty")
    return entries


def verify_downloaded_assets(
    asset_dir: Path, asset_digests: dict[str, str], required_subjects: set[str]
) -> dict[str, str]:
    actual: dict[str, str] = {}
    for name, expected in asset_digests.items():
        path = asset_dir / name
        if not path.is_file():
            raise RuntimeError(f"downloaded asset is missing: {name}")
        value = sha256_file(path)
        if value != expected:
            raise RuntimeError(f"GitHub asset digest mismatch: {name}")
        actual[name] = value
    checksums = parse_checksum_manifest(asset_dir / "SHA256SUMS.txt")
    missing = required_subjects - set(checksums)
    if missing:
        raise RuntimeError(f"checksum manifest is missing required subjects: {sorted(missing)}")
    for name in required_subjects:
        if checksums[name] != actual[name]:
            raise RuntimeError(f"checksum manifest digest mismatch: {name}")
    return actual


def verify_attestation_subject(results: object, expected_sha256: str, predicate: str | None) -> None:
    if not isinstance(results, list) or not results:
        raise RuntimeError("attestation verification returned no results")
    for index, result in enumerate(results):
        statement = (
            result.get("verificationResult", {}).get("statement")
            if isinstance(result, dict)
            else None
        )
        if not isinstance(statement, dict):
            raise RuntimeError(f"attestation result {index} has no verified statement")
        subjects = statement.get("subject")
        if not isinstance(subjects, list) or not subjects:
            raise RuntimeError(f"attestation result {index} has no subject")
        subject_digests = {
            str(item.get("digest", {}).get("sha256", ""))
            for item in subjects
            if isinstance(item, dict) and isinstance(item.get("digest"), dict)
        }
        if expected_sha256 not in subject_digests:
            raise RuntimeError(f"attestation result {index} does not bind the runtime SHA-256")
        if predicate is not None and statement.get("predicateType") != predicate:
            raise RuntimeError(f"attestation result {index} has an unexpected predicate type")


def download_attestation_bundles(
    repository: str, runtime_digest: str, evidence_dir: Path
) -> dict[str, Path]:
    response = github_api_json(
        f"repos/{repository}/attestations/sha256:{runtime_digest}",
        "runtime attestation bundles",
    )
    attestations = response.get("attestations") if isinstance(response, dict) else None
    if not isinstance(attestations, list) or not attestations:
        raise RuntimeError("GitHub API returned no runtime attestation bundles")
    wanted = {
        "https://slsa.dev/provenance/v1": "runtime-provenance-bundle.json",
        SPDX_PREDICATE_TYPE: "runtime-sbom-bundle.json",
    }
    bundles: dict[str, Path] = {}
    for item in attestations:
        bundle = item.get("bundle") if isinstance(item, dict) else None
        envelope = bundle.get("dsseEnvelope") if isinstance(bundle, dict) else None
        payload = envelope.get("payload") if isinstance(envelope, dict) else None
        if not isinstance(payload, str):
            continue
        try:
            statement = json.loads(base64.b64decode(payload, validate=True))
        except (ValueError, json.JSONDecodeError):
            continue
        predicate = statement.get("predicateType") if isinstance(statement, dict) else None
        if predicate not in wanted:
            continue
        if predicate in bundles:
            raise RuntimeError(f"GitHub API returned duplicate {predicate} bundles")
        path = evidence_dir / wanted[predicate]
        write_json(path, bundle)
        bundles[predicate] = path
    missing = set(wanted) - set(bundles)
    if missing:
        raise RuntimeError(f"GitHub API is missing required attestation bundles: {sorted(missing)}")
    return bundles


def verify_attestations(
    gh: str,
    repository: str,
    tag: str,
    commit: str,
    runtime: Path,
    sbom: Path,
    evidence_dir: Path,
) -> dict[str, Any]:
    runtime_digest = sha256_file(runtime)
    bundles = download_attestation_bundles(repository, runtime_digest, evidence_dir)
    common = [
        str(runtime),
        "--repo",
        repository,
        "--format",
        "json",
        "--signer-workflow",
        repository + SIGNER_WORKFLOW_SUFFIX,
        "--signer-digest",
        commit,
        "--source-digest",
        commit,
        "--source-ref",
        f"refs/tags/{tag}",
    ]
    provenance = run_json(
        [
            gh,
            "attestation",
            "verify",
            *common,
            "--bundle",
            str(bundles["https://slsa.dev/provenance/v1"]),
        ],
        "provenance attestation",
    )
    sbom_attestation = run_json(
        [
            gh,
            "attestation",
            "verify",
            *common,
            "--bundle",
            str(bundles[SPDX_PREDICATE_TYPE]),
            "--predicate-type",
            SPDX_PREDICATE_TYPE,
        ],
        "SBOM attestation",
    )
    verify_attestation_subject(provenance, runtime_digest, None)
    verify_attestation_subject(sbom_attestation, runtime_digest, SPDX_PREDICATE_TYPE)
    try:
        standalone = json.loads(sbom.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"standalone SBOM is invalid JSON: {exc}") from exc
    if not isinstance(standalone, dict):
        raise RuntimeError("standalone SBOM must be a JSON object")
    binding = verify_attested_sbom_predicate(standalone, sbom_attestation)
    if not binding["passed"]:
        raise RuntimeError("published SBOM does not match the verified SPDX attestation")
    write_json(evidence_dir / "runtime-provenance-verification.json", provenance)
    write_json(evidence_dir / "runtime-sbom-verification.json", sbom_attestation)
    write_json(evidence_dir / "runtime-sbom-binding-summary.json", binding)
    return {
        "provenance_verification_sha256": sha256_file(
            evidence_dir / "runtime-provenance-verification.json"
        ),
        "sbom_verification_sha256": sha256_file(
            evidence_dir / "runtime-sbom-verification.json"
        ),
        "sbom_binding_summary_sha256": sha256_file(
            evidence_dir / "runtime-sbom-binding-summary.json"
        ),
        "provenance_bundle_sha256": sha256_file(
            bundles["https://slsa.dev/provenance/v1"]
        ),
        "sbom_bundle_sha256": sha256_file(bundles[SPDX_PREDICATE_TYPE]),
    }


def inspect_elf(binary: Path) -> dict[str, Any]:
    if not binary.is_file():
        raise RuntimeError(f"release archive is missing required binary: {binary.name}")
    if binary.read_bytes()[:4] != b"\x7fELF":
        raise RuntimeError(f"required binary is not ELF: {binary.name}")
    if not binary.stat().st_mode & 0o111:
        raise RuntimeError(f"required binary is not executable: {binary.name}")
    identity = run(["file", str(binary)]).stdout.strip()
    if "ELF 64-bit" not in identity or "x86-64" not in identity:
        raise RuntimeError(f"required binary is not Linux x86-64 ELF: {binary.name}")
    ldd_output = run(["ldd", str(binary)]).stdout
    libraries = validate_runtime_dependencies(binary, ldd_output)
    return {
        "name": binary.name,
        "sha256": sha256_file(binary),
        "file_identity": identity,
        "runtime_libraries": libraries,
    }


def ensure_linux_x64_host() -> None:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise RuntimeError("release staging must run on the admitted Linux x86_64 host")


def stage_runtime(
    extraction_root: Path,
    expected_root: str,
    destination: Path,
    evidence_dir: Path,
    release: dict[str, Any],
    tag_evidence: dict[str, Any],
    repository: str,
    tag: str,
    commit: str,
    asset_digests: dict[str, str],
    attestation: dict[str, Any],
) -> dict[str, Any]:
    package_root = extraction_root / expected_root
    config_source = package_root / "share/boost_gateway/config"
    if not config_source.is_dir():
        raise RuntimeError("release archive does not contain the installed configuration tree")
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        bin_dir = temporary / "bin"
        config_dir = temporary / "config"
        staged_evidence = temporary / "evidence"
        bin_dir.mkdir()
        shutil.copytree(config_source, config_dir)
        shutil.copytree(ROOT / "deploy/runtime", temporary / "deploy/runtime")
        (temporary / "deploy/systemd").mkdir(parents=True)
        shutil.copy2(
            ROOT / "deploy/systemd/boost-gateway-compose.service",
            temporary / "deploy/systemd/boost-gateway-compose.service",
        )
        (temporary / "deploy/operations").mkdir(parents=True)
        shutil.copy2(
            ROOT / "deploy/operations/docker-compose.production.yml",
            temporary / "deploy/operations/docker-compose.production.yml",
        )
        shutil.copytree(ROOT / "env/monitoring", temporary / "env/monitoring")
        (temporary / "scripts/tools").mkdir(parents=True)
        for name in (
            "build_release_images.py",
            "check_release_compose.py",
            "verify_release_deployment.py",
        ):
            shutil.copy2(ROOT / "scripts/tools" / name, temporary / "scripts/tools" / name)
        binaries: list[dict[str, Any]] = []
        for name in REQUIRED_RUNTIME_BINARIES:
            source = package_root / "bin" / name
            entry = inspect_elf(source)
            shutil.copy2(source, bin_dir / name)
            (bin_dir / name).chmod(0o755)
            binaries.append(entry)
        shutil.copytree(evidence_dir, staged_evidence)
        write_json(staged_evidence / "release-metadata.json", release)
        write_json(staged_evidence / "tag-resolution.json", tag_evidence)
        manifest = {
            "schema_version": 1,
            "generated_at": generated_at(),
            "repository": repository,
            "tag": tag,
            "commit": commit,
            "platform": PLATFORM,
            "source_build_performed": False,
            "dependency_resolution_performed": False,
            "assets": asset_digests,
            "attestations": attestation,
            "configuration": {
                "source": f"{expected_root}/share/boost_gateway/config",
                "sha256": sha256_tree(config_dir),
            },
            "deployment_controller": {
                "dockerfiles_sha256": sha256_tree(temporary / "deploy/runtime"),
                "systemd_sha256": sha256_tree(temporary / "deploy/systemd"),
                "compose_sha256": sha256_file(
                    temporary / "deploy/operations/docker-compose.production.yml"
                ),
                "monitoring_sha256": sha256_tree(temporary / "env/monitoring"),
                "verification_tools_sha256": sha256_tree(temporary / "scripts/tools"),
            },
            "binaries": binaries,
        }
        write_json(temporary / "manifest.json", manifest)
        if destination.exists():
            raise RuntimeError(f"refusing to replace existing release staging: {destination}")
        temporary.replace(destination)
        return manifest
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    validate_inputs(args.repository, args.tag, args.expected_commit)
    ensure_linux_x64_host()
    if shutil.which(args.gh_command) is None:
        raise RuntimeError("GitHub CLI is required for release and attestation verification")
    destination = args.output_dir.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    commit, tag_evidence = resolve_tag_commit(args.repository, args.tag)
    if commit != args.expected_commit:
        raise RuntimeError(f"tag commit mismatch: {commit} != {args.expected_commit}")
    release = release_metadata(args.repository, args.tag)
    expected_root = f"boost-gateway-{args.tag}-{PLATFORM}"
    runtime_name = f"{expected_root}.tar.gz"
    sbom_name = f"{expected_root}-sbom.spdx.json"
    checksum_name = "SHA256SUMS.txt"
    required_assets = {runtime_name, sbom_name, checksum_name}
    subject_assets = {runtime_name, sbom_name}
    release_digests = release_asset_digests(release, required_assets)
    download_urls = release_asset_urls(release, required_assets)
    with tempfile.TemporaryDirectory(
        prefix=f".{args.tag}-download-", dir=destination.parent
    ) as download_name:
        download_dir = Path(download_name)
        for name in sorted(required_assets):
            download_asset(download_urls[name], download_dir / name)
        actual_digests = verify_downloaded_assets(
            download_dir, release_digests, subject_assets
        )
        archive = download_dir / runtime_name
        failures = verify_archive(archive, expected_root)
        if failures:
            raise RuntimeError("; ".join(failures))
        extraction_root = download_dir / "extracted"
        extraction_root.mkdir()
        extract_archive(archive, extraction_root)
        evidence = download_dir / "verification"
        evidence.mkdir()
        for name in sorted(required_assets):
            shutil.copy2(download_dir / name, evidence / name)
        attestation = verify_attestations(
            args.gh_command,
            args.repository,
            args.tag,
            commit,
            archive,
            download_dir / sbom_name,
            evidence,
        )
        return stage_runtime(
            extraction_root,
            expected_root,
            destination,
            evidence,
            release,
            tag_evidence,
            args.repository,
            args.tag,
            commit,
            actual_digests,
            attestation,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="HoneyBury/boost_gateway")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--gh-command", default="gh")
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=Path("runtime/validation/release-runtime-staging-summary.json"),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary_path = args.summary_path.resolve()
    try:
        manifest = prepare(args)
        summary = {
            "summary_version": 2,
            "generated_at": generated_at(),
            "overall_pass": True,
            "passed": True,
            "failed_step": "",
            "release": {
                "repository": args.repository,
                "tag": args.tag,
                "commit": args.expected_commit,
                "staging_path": str(args.output_dir.resolve()),
                "manifest_sha256": sha256_file(args.output_dir.resolve() / "manifest.json"),
            },
            "checks": {
                "tag_commit": True,
                "release_asset_sha256": True,
                "provenance_attestation": True,
                "sbom_attestation_binding": True,
                "linux_x64_elf": True,
                "no_source_build": not manifest["source_build_performed"],
            },
            "artifacts": {"summary_path": str(summary_path)},
        }
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        summary = {
            "summary_version": 2,
            "generated_at": generated_at(),
            "overall_pass": False,
            "passed": False,
            "failed_step": "release-runtime-staging",
            "failure": str(exc),
            "release": {
                "repository": args.repository,
                "tag": args.tag,
                "commit": args.expected_commit,
                "staging_path": str(args.output_dir.resolve()),
            },
            "artifacts": {"summary_path": str(summary_path)},
        }
    write_json(summary_path, summary)
    print(f"release runtime staging: {'PASS' if summary['passed'] else 'FAIL'}")
    if not summary["passed"]:
        print(f"  - {summary['failure']}")
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
