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
    (root / covered_relative).write_text(CLI_SOURCE, encoding="utf-8")
    (root / "tests/python/test_covered.py").write_text(
        "# explicit coverage for covered.py\n", encoding="utf-8"
    )
    inventory = {
        "public_entrypoints": [covered_relative.as_posix()],
        "scripts": {covered_relative.as_posix(): {"category": "public_entrypoint"}},
    }
    (root / "docs/script-inventory.json").write_text(
        json.dumps(inventory), encoding="utf-8"
    )
    shared = """\
name: fixture
jobs:
  check:
    steps:
      - name: shared fragment
        run: |
          prepare toolchain
          execute validation
          upload evidence
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
    assert document["external_metrics"]["automation_change_failure_rate"]["status"] == (
        "external-verification-required"
    )


def test_new_untested_cli_fails_without_baseline_review(tmp_path: Path) -> None:
    baseline, summary = prepare_repository(tmp_path)
    new_cli = tmp_path / "scripts" / "tools" / "new_cli.py"
    new_cli.write_text(CLI_SOURCE, encoding="utf-8")

    assert run_gate(tmp_path, baseline, summary) == 1
    document = json.loads(summary.read_text(encoding="utf-8"))
    failed = {item["name"] for item in document["checks"] if not item["passed"]}
    assert "limit:untested_cli" in failed
    assert "untested-cli-allowlist" in failed
