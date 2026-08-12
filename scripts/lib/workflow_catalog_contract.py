"""Reusable workflow catalog, runner, platform, and Action supply-chain checks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REVIEWED_ACTIONS = {
    "actions/attest": ("f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6", "v4.2.0"),
    "actions/cache/restore": ("0057852bfaa89a56745cba8c7296529d2fc39830", "v4.3.0"),
    "actions/cache/save": ("0057852bfaa89a56745cba8c7296529d2fc39830", "v4.3.0"),
    "actions/checkout": ("11d5960a326750d5838078e36cf38b85af677262", "v4.4.0"),
    "actions/download-artifact": ("d3f86a106a0bac45b974a628896c90dbdf5c8093", "v4.3.0"),
    "actions/setup-dotnet": ("67a3573c9a986a3f9c594539f4ab511d57bb3ce9", "v4.3.1"),
    "actions/setup-go": ("40f1582b2485089dde7abd97c1529aa768e1baff", "v5.6.0"),
    "actions/setup-python": ("a26af69be951a213d495a4c3e4e4022e16d87065", "v5.6.0"),
    "actions/upload-artifact": ("ea165f8d65b6e75b540449e92b4886f43607fa02", "v4.6.2"),
    "anchore/sbom-action": ("e22c389904149dbc22b58101806040fa8d37a610", "v0.24.0"),
    "docker/setup-compose-action": ("2fe291b7677a45ee1269ec56a42604c143505e7e", "v1.3.0"),
    "google/osv-scanner-action/osv-scanner-action": (
        "9a498708959aeaef5ef730655706c5a1df1edbc2",
        "v2.3.8",
    ),
    "jwlawson/actions-setup-cmake": ("0d6a7d60b009d01c9e7523be22153ff8f19460d3", "v2.2.0"),
    "seanmiddleditch/gha-setup-ninja": ("96bed6edff20d1dd61ecff9b75cc519d516e6401", "v5"),
    "softprops/action-gh-release": ("3bb12739c298aeb8a4eeaf626c5b8d85266b0e65", "v2.6.2"),
}
DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[2] / "docs" / "workflow-catalog.json"


@dataclass(frozen=True)
class CatalogContext:
    checks: list[dict[str, Any]]
    catalog: dict[str, Any]
    workflow_paths: list[Path]
    production_platforms: list[str]
    release_platforms: list[str]


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def workflow_rules(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rules = catalog.get("workflows", {})
    if not isinstance(rules, dict):
        return {}
    return {
        str(stem): metadata
        for stem, metadata in rules.items()
        if isinstance(metadata, dict)
    }


def expected_runner_class(runner: str) -> str:
    if "ubuntu-latest" in runner:
        return "github-hosted"
    if "macOS" in runner and "ARM64" in runner:
        return "native-macos"
    if "self-hosted" in runner:
        return "self-hosted"
    return "unknown"


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def offline_composite_action_is_safe(text: str) -> bool:
    if 'conan-venv-offline: "true"' not in text:
        return False
    if 'bootstrap-args: "--no-remote"' in text:
        return True
    return (
        'run-bootstrap: "false"' in text
        and "scripts/bootstrap_conan.py --conan-home \"$CONAN_HOME\" --no-remote"
        in text
    )


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def workflow_name(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def workflow_dispatch_inputs(text: str) -> list[str]:
    """Return top-level workflow_dispatch input names without a YAML dependency."""
    in_dispatch = False
    in_inputs = False
    names: list[str] = []
    for line in text.splitlines():
        if line == "  workflow_dispatch:":
            in_dispatch = True
            continue
        if not in_dispatch:
            continue
        if line and not line.startswith("    "):
            break
        if line == "    inputs:":
            in_inputs = True
            continue
        if not in_inputs:
            continue
        match = re.fullmatch(r"      ([A-Za-z_][A-Za-z0-9_-]*):", line)
        if match:
            names.append(match.group(1))
        elif line and not line.startswith("      "):
            break
    return names


def top_level_permissions(text: str) -> dict[str, str]:
    permissions: dict[str, str] = {}
    in_permissions = False
    for line in text.splitlines():
        if line == "permissions:":
            in_permissions = True
            continue
        if not in_permissions:
            continue
        match = re.fullmatch(r"  ([a-z-]+): (read|write|none)", line)
        if match:
            permissions[match.group(1)] = match.group(2)
        elif line and not line.startswith("  "):
            break
    return permissions


def action_reference_checks(paths: list[Path]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    pattern = re.compile(r"^\s*(?:-\s+)?uses:\s+(.+?)\s*$")
    for path in paths:
        for line_number, line in enumerate(read(path).splitlines(), start=1):
            match = pattern.fullmatch(line)
            if not match:
                continue
            value = match.group(1)
            reference, separator, comment = value.partition(" # ")
            if reference.startswith("./") or reference.startswith("docker://"):
                continue
            has_revision = "@" in reference
            action, revision = reference.rsplit("@", 1) if has_revision else (reference, "")
            release_tag = comment.strip() if separator else None
            expected = REVIEWED_ACTIONS.get(action)
            location = f"{path}:{line_number}"
            add(checks, f"action-reference:{path.name}:{line_number}", has_revision,
                f"{location} reference={reference}")
            add(checks, f"action-allowlist:{path.name}:{line_number}", expected is not None,
                f"{location} action={action}")
            add(checks, f"action-pin:{path.name}:{line_number}",
                expected is not None and revision == expected[0], f"{location} revision={revision}")
            add(checks, f"action-release-comment:{path.name}:{line_number}",
                expected is not None and release_tag == expected[1],
                f"{location} release_tag={release_tag!r}")
    return checks


def evaluate_catalog_foundation(
    root: Path,
    workflows_root: Path,
    catalog_path: Path,
) -> CatalogContext:
    """Evaluate generic inventory contracts before repository-specific policy checks."""
    checks: list[dict[str, Any]] = []
    catalog = load_catalog(catalog_path)
    rules = workflow_rules(catalog)
    expected_names = {
        stem: str(metadata.get("display_name", ""))
        for stem, metadata in rules.items()
    }
    workflow_paths = sorted([
        *workflows_root.glob("*.yml"),
        *workflows_root.glob("*.yaml"),
    ])
    actual = {path.stem for path in workflow_paths}
    expected = set(rules)

    add(checks, "catalog-json", bool(catalog), f"catalog={catalog_path}")
    add(checks, "catalog-schema-version", catalog.get("schema_version") == 1,
        f"schema_version={catalog.get('schema_version')}")
    add(checks, "workflow-count", len(actual) == len(rules), f"actual={len(actual)}")
    add(checks, "workflow-set:expected", actual == expected,
        f"actual={sorted(actual)} expected={sorted(expected)}")

    matrix_path = root / ".github" / "runner-matrix.json"
    matrix = json.loads(read(matrix_path)) if matrix_path.exists() else {}
    matrix_workflows = set(matrix.get("workflows", {}))
    add(checks, "runner-matrix:exact-workflow-set", matrix_workflows == actual,
        f"matrix={sorted(matrix_workflows)} actual={sorted(actual)}")
    for stem, metadata in sorted(rules.items()):
        matrix_entry = matrix.get("workflows", {}).get(stem, {})
        default_runner = str(metadata.get("default_runner", ""))
        runner_class = str(metadata.get("runner_class", ""))
        triggers = metadata.get("triggers", [])
        lifecycle = str(metadata.get("lifecycle", ""))
        add(checks, f"catalog:{stem}:default-runner",
            default_runner == matrix_entry.get("runner"),
            f"catalog={default_runner!r} matrix={matrix_entry.get('runner')!r}")
        add(checks, f"catalog:{stem}:runner-class",
            runner_class == expected_runner_class(default_runner), f"runner_class={runner_class!r}")
        add(checks, f"catalog:{stem}:triggers",
            isinstance(triggers, list) and "workflow_dispatch" in triggers, f"triggers={triggers}")
        add(checks, f"catalog:{stem}:lifecycle",
            lifecycle in {"required", "maintained", "experimental"}, f"lifecycle={lifecycle!r}")

    boundary_path = root / "docs" / "platform-production-boundaries.json"
    boundary = json.loads(read(boundary_path)) if boundary_path.exists() else {}
    boundary_workflows = set(boundary.get("workflows", {}))
    production_platforms = boundary.get("policy", {}).get("production_platforms", [])
    release_platforms = boundary.get("policy", {}).get("release_platforms", [])
    add(checks, "platform-boundary:production-platforms",
        production_platforms == ["linux-x64", "linux-arm64", "macos-arm64"],
        f"production_platforms={production_platforms}")
    add(checks, "platform-boundary:release-platforms", release_platforms == ["linux-x64"],
        f"release_platforms={release_platforms}")
    add(checks, "platform-boundary:exact-workflow-set", boundary_workflows == actual,
        f"boundary={sorted(boundary_workflows)} actual={sorted(actual)}")
    for stem in (
        "release", "release-asset-verification", "perf-regression", "production-gates",
        "production-candidate-evidence", "long-soak-capacity", "preprod-evidence",
    ):
        platform_runners = matrix.get("workflows", {}).get(stem, {}).get("platforms", {})
        expected_platforms = (
            release_platforms if stem in {"release", "release-asset-verification"}
            else production_platforms
        )
        add(checks, f"runner-matrix:{stem}:production-platforms",
            set(platform_runners) == set(expected_platforms),
            f"{stem} platform runners={sorted(platform_runners)}")
    readiness_targets = (
        matrix.get("workflows", {}).get("production-readiness", {}).get("target_platforms", [])
    )
    add(checks, "runner-matrix:production-readiness:target-platforms",
        readiness_targets == production_platforms,
        f"readiness target platforms={readiness_targets}")
    for stem, states in sorted(boundary.get("workflows", {}).items()):
        add(checks, f"platform-boundary:{stem}:complete-platform-set",
            isinstance(states, dict) and set(states) == set(production_platforms),
            f"{stem} platforms={sorted(states) if isinstance(states, dict) else states}")

    readme_path = root / ".github" / "CI-CD.md"
    readme = read(readme_path) if readme_path.exists() else ""
    for stem in sorted(actual):
        filename = f"{stem}.yml"
        add(checks, f"readme:lists:{filename}", f"`{filename}`" in readme,
            f".github/CI-CD.md lists {filename}")
    add(checks, "readme:root-not-shadowed", not (root / ".github" / "README.md").exists(),
        ".github/README.md does not shadow the repository root README on GitHub")

    actions_root = root / ".github" / "actions"
    action_paths = workflow_paths + sorted([
        *actions_root.glob("*/action.yml"),
        *actions_root.glob("*/action.yaml"),
    ])
    checks.extend(action_reference_checks(action_paths))
    render_action_path = actions_root / "render-validation-summary" / "action.yml"
    render_action = read(render_action_path) if render_action_path.exists() else ""
    render_reference = "uses: ./.github/actions/render-validation-summary"
    add(checks, "shared-action:render-validation-summary:exists", bool(render_action),
        "the shared validation-summary renderer action exists")
    add(checks, "shared-action:render-validation-summary:contract", all(
        token in render_action for token in (
            "SUMMARY_PATH_PATTERNS", "compgen -G", "declare -A observed",
            "scripts/tools/render_validation_summary.py", '>> "$GITHUB_STEP_SUMMARY"',
        )), "the shared action expands paths, deduplicates summaries and renders the job summary")
    add(checks, "shared-action:render-validation-summary:reused",
        sum(text.count(render_reference) for text in map(read, workflow_paths)) >= 6,
        "at least six workflows reuse the shared renderer action")

    for path in workflow_paths:
        stem = path.stem
        text = read(path)
        metadata = rules.get(stem, {})
        triggers = set(metadata.get("triggers", []))
        add(checks, f"name:{stem}", workflow_name(text) == expected_names.get(stem),
            f"{path.name} name={workflow_name(text)!r}")
        add(checks, f"trigger:{stem}:dispatch", "workflow_dispatch:" in text,
            f"{path.name} supports workflow_dispatch")
        dispatch_inputs = workflow_dispatch_inputs(text)
        add(checks, f"trigger:{stem}:dispatch-input-limit", len(dispatch_inputs) <= 25,
            f"{path.name} declares {len(dispatch_inputs)}/25 workflow_dispatch inputs")
        has_tag_push = "push:" in text and "tags:" in text and "v*" in text
        add(checks, f"trigger:{stem}:tag-policy", has_tag_push == ("tag_push" in triggers),
            f"{path.name} tag_push={has_tag_push}")
        has_schedule = "schedule:" in text or "cron:" in text
        add(checks, f"trigger:{stem}:schedule-policy", has_schedule == ("schedule" in triggers),
            f"{path.name} scheduled={has_schedule}")
        expected_permissions = metadata.get("permissions", {})
        actual_permissions = top_level_permissions(text)
        add(checks, f"permissions:{stem}:least-privilege",
            actual_permissions == expected_permissions,
            f"{path.name} permissions={actual_permissions} expected={expected_permissions}")
        has_pull_request = "pull_request:" in text
        add(checks, f"trigger:{stem}:pr-policy", has_pull_request == ("pull_request" in triggers),
            f"{path.name} pull_request={has_pull_request}")
        if "uses: actions/setup-go@" in text:
            add(checks, f"go:{stem}:cache-dependency-path",
                "cache-dependency-path: operator/boostgateway-operator/go.sum" in text,
                f"{path.name} keys setup-go cache from the operator go.sum file")
        if metadata.get("strict_offline_conan") is True:
            add(checks, f"conan:{stem}:no-public-remote", "--allow-public" not in text,
                f"{path.name} does not enable a public Conan remote")
            add(checks, f"conan:{stem}:no-build-missing", "--build=missing" not in text,
                f"{path.name} does not build missing Conan dependencies")
            if "uses: ./.github/actions/setup-cpp-conan" in text:
                add(checks, f"conan:{stem}:offline-composite-action",
                    offline_composite_action_is_safe(text),
                    f"{path.name} configures setup-cpp-conan for runner-local offline use")

    return CatalogContext(
        checks=checks,
        catalog=catalog,
        workflow_paths=workflow_paths,
        production_platforms=list(production_platforms),
        release_platforms=list(release_platforms),
    )
