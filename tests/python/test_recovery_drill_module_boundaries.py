"""Boundary and fixture contracts for decomposed pre-production recovery."""

from importlib import import_module
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULES = {
    "scripts/lib/recovery_drill_runtime.py": ("run_expected_failure_step", "terminate_background_process"),
    "scripts/lib/recovery_drill_contract.py": ("resolve_compose_image_requirements", "run_sdk_leaderboard_probe"),
    "scripts/lib/recovery_drill_images.py": ("inspect_build_image_manifests", "image_inventory_step"),
    "scripts/lib/recovery_drill_preflight.py": ("prepare_drill_context", "run_docker_image_preflight"),
    "scripts/lib/recovery_drill_record.py": ("write_drill_record",),
}


def test_recovery_modules_are_cli_free_and_own_expected_contracts() -> None:
    for relative, names in MODULES.items():
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "ArgumentParser(" not in source
        assert "def main(" not in source
        module = import_module(relative[:-3].replace("/", "."))
        for name in names:
            assert callable(getattr(module, name))


def test_expected_failure_fixture_remains_fail_closed() -> None:
    runtime = import_module("scripts.lib.recovery_drill_runtime")
    failed = {"status": "failed", "stdout_tail": "", "stderr_tail": "service unavailable"}
    original = runtime.run_step
    runtime.run_step = lambda *_args, **_kwargs: dict(failed)
    try:
        observed = runtime.run_expected_failure_step("fixture", "recovery", ["false"], 1)
    finally:
        runtime.run_step = original
    assert observed["status"] == "passed"
    assert observed["expected_failure_observed"] is True


def test_recovery_facade_stays_below_the_oversized_threshold() -> None:
    facade = REPO_ROOT / "scripts/gates/production/verify_preprod_recovery_drill.py"
    assert len(facade.read_text(encoding="utf-8").splitlines()) <= 800
