from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from scripts.gates.governance import check_tooling_metrics as metrics_gate
from scripts.lib.tooling_metrics import collect_tooling_metrics


CLI_SOURCE = """\
import argparse

def main() -> int:
    argparse.ArgumentParser().parse_args()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
"""


def prepare_repository(root: Path) -> tuple[Path, Path]:
    tools_dir = root / "scripts/tools"
    tools_dir.mkdir(parents=True)
    (root / "tests/python").mkdir(parents=True)
    (root / ".github/workflows").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    covered_relative = Path("scripts") / "tools" / "covered.py"
    auxiliary_relative = Path("scripts") / "tools" / "auxiliary.py"
    (root / covered_relative).write_text(CLI_SOURCE, encoding="utf-8")
    (root / auxiliary_relative).write_text(CLI_SOURCE, encoding="utf-8")
    (root / "tests/python/test_cli_contract_a.py").write_text(
        'COMMAND = "covered.py"\n\ndef test_command() -> None:\n    assert COMMAND\n',
        encoding="utf-8",
    )
    (root / "tests/python/test_cli_contract_b.py").write_text(
        'COMMAND = "auxiliary.py"\n\ndef test_command() -> None:\n    assert COMMAND\n',
        encoding="utf-8",
    )
    inventory = {
        "schema_version": 5,
        "public_entrypoints": [covered_relative.as_posix()],
        "scripts": {covered_relative.as_posix(): {"category": "public_entrypoint"}},
        "script_growth_exceptions": {},
    }
    (root / "docs/script-inventory.json").write_text(
        json.dumps(inventory), encoding="utf-8"
    )
    existing_dependency = (
        Path("scripts") / "tools" / "existing_dependency.py"
    ).as_posix()
    shared = f"""\
name: fixture
jobs:
  check:
    steps:
      - name: shared fragment
        run: |
          prepare toolchain
          execute validation
          upload evidence
      - name: existing dependency
        run: python3 {existing_dependency}
"""
    for index in range(3):
        (root / f".github/workflows/check-{index}.yml").write_text(
            shared, encoding="utf-8"
        )

    current = collect_tooling_metrics(root)
    baseline = {
        "schema_version": 1,
        "policy": {"fragment_lines": 3, "minimum_workflows": 3},
        "limits": {
            "public_entrypoints": {"maximum": current["public_entrypoints"]},
            "workflow_duplicate_fragments": {
                "maximum": current["workflow_duplicate_fragments"]
            },
            "untested_cli": {
                "maximum": current["untested_cli"],
                "allowlist": current["untested_cli_paths"],
            },
            "cli_implementations": {"maximum": current["cli_implementations"]},
            "tool_files": {"maximum": current["tool_files"]},
            "library_files": {"maximum": current["library_files"]},
            "other_script_files": {"maximum": current["other_script_files"]},
            "large_scripts_over_500": {
                "maximum": current["large_scripts_over_500"]
            },
            "large_scripts_over_800": {
                "maximum": current["large_scripts_over_800"]
            },
            "workflow_script_dependencies": {
                "maximum": current["workflow_script_dependencies"]
            },
            "workflow_script_dependency_edges": {
                "maximum": current["workflow_script_dependency_edges"]
            },
            "cross_cli_imports": {"maximum": current["cross_cli_imports"]},
            "windows_compatibility_fragments": {
                "maximum": current["windows_compatibility_fragments"]
            },
        },
        "known_surfaces": {
            "cli_implementations": current["cli_implementation_paths"],
            "tool_files": current["tool_file_paths"],
            "library_files": current["library_file_paths"],
            "other_script_files": current["other_script_file_paths"],
            "workflow_script_dependencies": current[
                "workflow_script_dependency_paths"
            ],
            "workflow_script_dependency_edges": current[
                "workflow_script_dependency_edge_paths"
            ],
            "cross_cli_import_edges": current["cross_cli_import_edges"],
        },
        "external_metrics": {
            "automation_change_failure_rate": {
                "source": "github-actions",
                "owner": "@maintainer",
                "window_days": 90,
                "target_percent": 10,
                "measurement": "failed relevant runs divided by completed relevant runs",
            }
        },
    }
    baseline_path = root / "docs/tooling-metrics-baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    return baseline_path, root / "summary.json"


def run_gate(root: Path, baseline: Path, summary: Path) -> int:
    arguments = [
        "check_tooling_metrics.py",
        "--baseline",
        str(baseline),
        "--summary-path",
        str(summary),
    ]
    with mock.patch.object(metrics_gate, "ROOT", root), mock.patch(
        "sys.argv", arguments
    ):
        return metrics_gate.main()


def test_matching_tooling_metrics_baseline_passes(tmp_path: Path) -> None:
    baseline, summary = prepare_repository(tmp_path)

    assert run_gate(tmp_path, baseline, summary) == 0
    document = json.loads(summary.read_text(encoding="utf-8"))
    assert document["metrics"]["workflow_duplicate_fragments"] == 1
    assert document["metrics"]["windows_compatibility_fragments"] == 0
    assert document["external_metrics"]["automation_change_failure_rate"]["status"] == (
        "external-verification-required"
    )


def test_windows_script_compatibility_fails_without_platform_review(tmp_path: Path) -> None:
    baseline, summary = prepare_repository(tmp_path)
    covered_relative = Path("scripts") / "tools" / "covered.py"
    covered = tmp_path / covered_relative
    covered.write_text(
        covered.read_text(encoding="utf-8")
        + '\nEXECUTABLE = "gateway_pressure.exe"\n',
        encoding="utf-8",
    )

    assert run_gate(tmp_path, baseline, summary) == 1
    document = json.loads(summary.read_text(encoding="utf-8"))
    failed = {item["name"] for item in document["checks"] if not item["passed"]}
    assert "limit:windows_compatibility_fragments" in failed
    assert document["metrics"]["windows_compatibility_fragment_details"] == [
        {
            "kind": "windows-executable",
            "line": 10,
            "path": covered_relative.as_posix(),
        }
    ]


def test_new_untested_cli_fails_without_baseline_review(tmp_path: Path) -> None:
    baseline, summary = prepare_repository(tmp_path)
    new_cli = tmp_path / "scripts" / "tools" / "new_cli.py"
    new_cli.write_text(CLI_SOURCE, encoding="utf-8")

    assert run_gate(tmp_path, baseline, summary) == 1
    document = json.loads(summary.read_text(encoding="utf-8"))
    failed = {item["name"] for item in document["checks"] if not item["passed"]}
    assert "limit:untested_cli" in failed
    assert "untested-cli-allowlist" in failed
    assert "limit:cli_implementations" in failed
    assert "limit:tool_files" in failed
    assert "new-script-growth-exceptions" in failed


def test_comment_or_filename_only_does_not_count_as_cli_coverage(tmp_path: Path) -> None:
    prepare_repository(tmp_path)
    test_path = tmp_path / "tests/python/test_cli_contract_a.py"
    test_path.write_text("# covered.py is not executed here\n", encoding="utf-8")
    (tmp_path / "tests/python/test_covered.py").write_text(
        "# A matching test filename without Python references is not coverage.\n",
        encoding="utf-8",
    )

    metrics = collect_tooling_metrics(tmp_path)

    covered = (Path("scripts") / "tools" / "covered.py").as_posix()
    assert covered in metrics["untested_cli_paths"]


def test_new_unclassified_script_module_requires_review(tmp_path: Path) -> None:
    baseline, summary = prepare_repository(tmp_path)
    helper = tmp_path / "scripts" / "gates" / "helper.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("VALUE = 1\n", encoding="utf-8")

    assert run_gate(tmp_path, baseline, summary) == 1
    document = json.loads(summary.read_text(encoding="utf-8"))
    failed = {item["name"] for item in document["checks"] if not item["passed"]}
    assert "limit:other_script_files" in failed
    assert "new-script-growth-exceptions" in failed


def test_reviewed_unclassified_script_module_passes(tmp_path: Path) -> None:
    baseline, summary = prepare_repository(tmp_path)
    relative = (Path("scripts") / "gates" / "helper.py").as_posix()
    test_relative = "tests/python/test_helper_contract.py"
    (tmp_path / relative).parent.mkdir(parents=True)
    (tmp_path / relative).write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / test_relative).write_text(
        "import runpy\n\n"
        "def test_helper_contract() -> None:\n"
        f'    assert runpy.run_path("{relative}")["VALUE"] == 1\n',
        encoding="utf-8",
    )
    inventory_path = tmp_path / "docs/script-inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["script_growth_exceptions"][relative] = {
        "kind": "script-module",
        "domain": "governance",
        "consumers": ["governance gates"],
        "test": test_relative,
        "why_new_script": "Several governance gates share one reviewed implementation contract.",
        "replaces": "helpers embedded in gate commands",
        "retirement_condition": "The consumers move to another governed implementation module.",
        "temporary": False,
        "expires_on": "",
    }
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

    assert run_gate(tmp_path, baseline, summary) == 0


def test_reviewed_new_cli_with_complete_exception_passes(tmp_path: Path) -> None:
    baseline, summary = prepare_repository(tmp_path)
    new_relative = (Path("scripts") / "tools" / "new_cli.py").as_posix()
    test_relative = "tests/python/test_new_cli.py"
    (tmp_path / new_relative).write_text(CLI_SOURCE, encoding="utf-8")
    (tmp_path / test_relative).write_text(
        "import subprocess\nimport sys\n\n"
        "def test_new_cli_help() -> None:\n"
        f'    result = subprocess.run([sys.executable, "{new_relative}", "--help"])\n'
        "    assert result.returncode == 0\n",
        encoding="utf-8",
    )
    inventory_path = tmp_path / "docs/script-inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["script_growth_exceptions"][new_relative] = {
        "kind": "cli",
        "domain": "governance",
        "consumers": ["maintainers"],
        "test": test_relative,
        "why_new_script": "This command owns a distinct reviewed governance boundary.",
        "replaces": "",
        "retirement_condition": "The capability moves into an existing maintained command.",
        "temporary": False,
        "expires_on": "",
    }
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

    assert run_gate(tmp_path, baseline, summary) == 0


def test_reviewed_library_module_with_complete_exception_passes(tmp_path: Path) -> None:
    baseline, summary = prepare_repository(tmp_path)
    library_relative = (Path("scripts") / "lib" / "shared_release.py").as_posix()
    test_relative = "tests/python/test_shared_release.py"
    (tmp_path / library_relative).parent.mkdir(parents=True)
    (tmp_path / library_relative).write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / test_relative).write_text(
        "from scripts.lib.shared_release import VALUE\n\n"
        "def test_shared_release_value() -> None:\n"
        "    assert VALUE == 1\n",
        encoding="utf-8",
    )
    inventory_path = tmp_path / "docs/script-inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["script_growth_exceptions"][library_relative] = {
        "kind": "library-module",
        "domain": "release",
        "consumers": ["release commands"],
        "test": test_relative,
        "why_new_script": "Several release commands require one import-safe shared implementation.",
        "replaces": "helpers embedded in CLI modules",
        "retirement_condition": "The consumers move to another governed shared implementation.",
        "temporary": False,
        "expires_on": "",
    }
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

    assert run_gate(tmp_path, baseline, summary) == 0


def test_new_workflow_script_dependency_fails_exact_surface_check(
    tmp_path: Path,
) -> None:
    baseline, summary = prepare_repository(tmp_path)
    workflow = tmp_path / ".github/workflows/check-0.yml"
    dependency = (Path("scripts") / "tools" / "unreviewed_dependency.py").as_posix()
    workflow.write_text(
        workflow.read_text(encoding="utf-8")
        + "\n      - name: unreviewed dependency\n"
        + f"        run: python3 {dependency}\n",
        encoding="utf-8",
    )

    assert run_gate(tmp_path, baseline, summary) == 1
    document = json.loads(summary.read_text(encoding="utf-8"))
    failed = {item["name"] for item in document["checks"] if not item["passed"]}
    assert "limit:workflow_script_dependencies" in failed
    assert "known-surface:workflow_script_dependencies" in failed
    assert "limit:workflow_script_dependency_edges" in failed
    assert "known-surface:workflow_script_dependency_edges" in failed


def test_reusing_dependency_in_new_workflow_fails_edge_check(tmp_path: Path) -> None:
    baseline, summary = prepare_repository(tmp_path)
    dependency = (Path("scripts") / "tools" / "existing_dependency.py").as_posix()
    (tmp_path / ".github/workflows/check-3.yml").write_text(
        f"""name: fourth fixture
jobs:
  check:
    steps:
      - name: reused dependency
        run: python3 {dependency}
""",
        encoding="utf-8",
    )

    assert run_gate(tmp_path, baseline, summary) == 1
    document = json.loads(summary.read_text(encoding="utf-8"))
    failed = {item["name"] for item in document["checks"] if not item["passed"]}
    assert "limit:workflow_script_dependencies" not in failed
    assert "limit:workflow_script_dependency_edges" in failed
    assert "known-surface:workflow_script_dependency_edges" in failed


def test_new_cross_cli_import_fails_exact_edge_check(tmp_path: Path) -> None:
    baseline, summary = prepare_repository(tmp_path)
    covered = tmp_path / "scripts" / "tools" / "covered.py"
    covered.write_text(
        "from scripts.tools."
        + "auxiliary import main as auxiliary_main\n"
        + CLI_SOURCE,
        encoding="utf-8",
    )

    assert run_gate(tmp_path, baseline, summary) == 1
    document = json.loads(summary.read_text(encoding="utf-8"))
    failed = {item["name"] for item in document["checks"] if not item["passed"]}
    assert "limit:cross_cli_imports" in failed
    assert "known-surface:cross_cli_import_edges" in failed


def test_package_style_cross_cli_import_is_not_hidden(tmp_path: Path) -> None:
    baseline, summary = prepare_repository(tmp_path)
    covered = tmp_path / "scripts" / "tools" / "covered.py"
    covered.write_text(
        "from scripts.tools import auxiliary\n" + CLI_SOURCE,
        encoding="utf-8",
    )

    assert run_gate(tmp_path, baseline, summary) == 1
    document = json.loads(summary.read_text(encoding="utf-8"))
    failed = {item["name"] for item in document["checks"] if not item["passed"]}
    assert "limit:cross_cli_imports" in failed
    assert "known-surface:cross_cli_import_edges" in failed


def test_versioned_and_environment_python_commands_are_discovered(
    tmp_path: Path,
) -> None:
    prepare_repository(tmp_path)
    workflow = tmp_path / ".github/workflows/check-0.yml"
    versioned = (Path("scripts") / "tools" / "versioned.py").as_posix()
    environment = (Path("scripts") / "tools" / "environment.py").as_posix()
    workflow.write_text(
        workflow.read_text(encoding="utf-8")
        + f"\n      - name: versioned Python\n        run: python3.12 {versioned}\n"
        + f'      - name: environment Python\n        run: "$EVIDENCE_PYTHON" {environment}\n',
        encoding="utf-8",
    )

    metrics = collect_tooling_metrics(tmp_path)
    assert versioned in metrics["workflow_script_dependency_paths"]
    assert environment in metrics["workflow_script_dependency_paths"]
