#!/usr/bin/env python3
"""Fail closed unless production observability credentials and alert delivery are proven."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        parent for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "__init__.py").is_file()
    )
    sys.path.insert(0, str(repo_import_root))

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.lib.observability_preflight import *  # noqa: E402,F401,F403
from scripts.lib.observability_preflight import (  # noqa: E402
    _email_password_file,
    _validate_alertmanager_text,
)

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alertmanager-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--compose-env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--delivery-attestation", type=Path, default=DEFAULT_ATTESTATION)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    try:
        summary = validate_preflight(
            args.alertmanager_config, args.compose_env, args.delivery_attestation
        )
    except (OSError, PreflightError, subprocess.SubprocessError) as exc:
        print(f"observability preflight: FAIL: {exc}", file=sys.stderr)
        return 1
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(args.summary_path, 0o640)
    print("observability preflight: PASS")
    print(f"summary: {args.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
