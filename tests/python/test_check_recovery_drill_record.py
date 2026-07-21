"""Regression coverage for recovery drill record path resolution."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.gates.production import check_recovery_drill_record


def test_relative_record_paths_resolve_from_repository_root() -> None:
    relative = Path("docs/production/production-recovery-drill-record-template.json")
    assert check_recovery_drill_record.resolve_record_path(str(relative)) == (
        check_recovery_drill_record.REPO_ROOT / relative
    )
    assert check_recovery_drill_record.resolve_record_path(str(relative)).exists()


def test_executed_failed_drill_is_rejected(tmp_path: Path) -> None:
    template_path = check_recovery_drill_record.REPO_ROOT / (
        "docs/production/production-recovery-drill-record-template.json"
    )
    record = json.loads(template_path.read_text(encoding="utf-8"))
    record["template"] = False
    record["verification"]["passed"] = False
    record_path = tmp_path / "failed-record.json"
    record_path.write_text(json.dumps(record), encoding="utf-8")

    checks = check_recovery_drill_record.validate_record(record, record_path, False)
    passed_check = next(check for check in checks if check["name"] == "verification:passed")
    assert passed_check["passed"] is False
