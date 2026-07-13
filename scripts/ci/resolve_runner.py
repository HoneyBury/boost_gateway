#!/usr/bin/env python3
"""
Resolve runner configuration from .github/runner-matrix.json.

Usage:
    python3 scripts/ci/resolve_runner.py --workflow ci           # print runner JSON string
    python3 scripts/ci/resolve_runner.py --workflow ci --json    # print full entry as JSON
    python3 scripts/ci/resolve_runner.py --validate              # validate matrix consistency, exit 0/1
"""

import json
import os
import sys


MATRIX_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", ".github", "runner-matrix.json"
)
KNOWN_WORKFLOWS = [
    "ci",
    "conan-validate",
    "grpc-experimental",
    "long-soak-capacity",
    "nightly-stability",
    "perf-regression",
    "preprod-evidence",
    "production-candidate-evidence",
    "production-gates",
    "production-readiness",
    "release",
    "specialized-e2e",
]
SELF_HOSTED_LINUX_X64 = ["self-hosted", "Linux", "X64"]
GITHUB_HOSTED_UBUNTU = "ubuntu-latest"


def load_matrix(path=None):
    if path is None:
        path = MATRIX_PATH
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        print(f"runner-matrix.json not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_workflow(matrix, workflow_name):
    """Resolve runner for a workflow. Falls back to default on disabled or missing."""
    default = matrix.get("default_runner", '["self-hosted","Linux","X64"]')
    entry = matrix.get("workflows", {}).get(workflow_name)
    if entry is None:
        return default, False, {}
    if not entry.get("enabled", True):
        return default, False, entry.get("capabilities", {})
    return entry.get("runner", default), True, entry.get("capabilities", {})


def cmd_workflow(args, matrix):
    """--workflow <name> [--json]"""
    name = args[0]
    runner, enabled, capabilities = resolve_workflow(matrix, name)
    if "--json" in args:
        print(
            json.dumps(
                {
                    "workflow": name,
                    "runner": json.loads(runner),
                    "enabled": enabled,
                    "capabilities": capabilities,
                }
            )
        )
    else:
        print(runner)


def cmd_validate(matrix):
    """--validate: check matrix consistency."""
    errors = []
    sv = matrix.get("schema_version")
    if sv != 3:
        errors.append(f"schema_version should be 3, got {sv}")

    default = matrix.get("default_runner")
    if not default:
        errors.append("default_runner is required")
    else:
        try:
            if json.loads(default) != SELF_HOSTED_LINUX_X64:
                errors.append(
                    "default_runner should remain ['self-hosted', 'Linux', 'X64'] for fixed-runner evidence workflows"
                )
        except json.JSONDecodeError:
            errors.append(f"default_runner is not valid JSON: {default}")

    workflows = matrix.get("workflows", {})
    if not workflows:
        errors.append("workflows section is empty")

    missing_workflows = []
    for wf in KNOWN_WORKFLOWS:
        if wf not in workflows:
            missing_workflows.append(wf)
    if missing_workflows:
        errors.append(f"workflows missing entries: {', '.join(missing_workflows)}")

    for name, entry in workflows.items():
        if "enabled" not in entry:
            errors.append(f"workflow '{name}' missing 'enabled' field")
        runner = entry.get("runner")
        if runner:
            try:
                parsed_runner = json.loads(runner)
            except json.JSONDecodeError:
                errors.append(f"workflow '{name}' runner is not valid JSON: {runner}")
                parsed_runner = None
            if name == "ci" and parsed_runner != GITHUB_HOSTED_UBUNTU:
                errors.append("workflow 'ci' should default to GitHub-hosted ubuntu-latest fallback")
            if (
                name not in {"ci", "production-readiness"}
                and parsed_runner != SELF_HOSTED_LINUX_X64
            ):
                errors.append(f"workflow '{name}' should default to ['self-hosted', 'Linux', 'X64']")
            if name == "production-readiness" and parsed_runner != GITHUB_HOSTED_UBUNTU:
                errors.append("workflow 'production-readiness' should default to GitHub-hosted ubuntu-latest fallback")
        if name == "ci":
            if entry.get("vars_hint") != "CI_RUNNER":
                errors.append("workflow 'ci' should expose vars_hint=CI_RUNNER")
            if not entry.get("capabilities", {}).get("github_hosted_fallback"):
                errors.append("workflow 'ci' should declare capabilities.github_hosted_fallback=true")
        if name == "release" and entry.get("vars_hint") != "RELEASE_RUNNER":
            errors.append("workflow 'release' should expose vars_hint=RELEASE_RUNNER")
        if name == "conan-validate" and entry.get("vars_hint") != "CONAN_VALIDATE_RUNNER":
            errors.append("workflow 'conan-validate' should expose vars_hint=CONAN_VALIDATE_RUNNER")
        if name == "specialized-e2e" and entry.get("vars_hint") != "SPECIALIZED_E2E_RUNNER":
            errors.append("workflow 'specialized-e2e' should expose vars_hint=SPECIALIZED_E2E_RUNNER")

    deprecated = matrix.get("deprecated_platforms", {})
    for plat in deprecated:
        if not isinstance(deprecated[plat], str):
            errors.append(f"deprecated_platforms['{plat}'] should be a string explanation")

    if errors:
        for e in errors:
            print(f"  ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print("runner-matrix.json: validation passed")
    sys.exit(0)


def main():
    matrix = load_matrix()
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        doc = __doc__ or ""
        print(doc.strip())
        return

    if "--validate" in args:
        cmd_validate(matrix)
    elif "--workflow" in args:
        idx = args.index("--workflow")
        if idx + 1 >= len(args):
            print("error: --workflow requires a workflow name argument", file=sys.stderr)
            sys.exit(1)
        cmd_workflow(args[idx + 1 :], matrix)
    else:
        print(f"error: unknown arguments: {' '.join(args)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
