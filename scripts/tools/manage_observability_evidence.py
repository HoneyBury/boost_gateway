#!/usr/bin/env python3
"""Create immutable observability records and verifiable off-host packages."""

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
import sys
from pathlib import Path

from scripts.lib.observability_evidence import *  # noqa: E402,F401,F403

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("--ledger-root", type=Path, default=DEFAULT_ROOT)
    record_parser.add_argument("--kind", choices=sorted(RECORD_KINDS), required=True)
    record_parser.add_argument("--record-id", required=True)
    record_parser.add_argument("--summary", type=Path, action="append", required=True)
    record_parser.add_argument("--deployment-record", type=Path, default=DEFAULT_DEPLOYMENT)
    record_parser.add_argument("--attributes-json", type=Path)

    manifest_parser = subparsers.add_parser("manifest")
    manifest_parser.add_argument("--ledger-root", type=Path, default=DEFAULT_ROOT)
    manifest_parser.add_argument("--manifest-id", required=True)

    package_parser = subparsers.add_parser("package")
    package_parser.add_argument("--manifest", type=Path, required=True)
    package_parser.add_argument("--output", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify-package")
    verify_parser.add_argument("--package", type=Path, required=True)
    verify_parser.add_argument("--extract-to", type=Path, required=True)
    verify_parser.add_argument("--receipt", type=Path, required=True)

    seal_parser = subparsers.add_parser("seal")
    seal_parser.add_argument("--ledger-root", type=Path, default=DEFAULT_ROOT)

    args = parser.parse_args()
    try:
        if args.command == "record":
            attributes = (
                load_json_object(args.attributes_json, "record attributes")
                if args.attributes_json
                else None
            )
            path, value = create_record(
                args.ledger_root,
                args.kind,
                args.record_id,
                args.summary,
                args.deployment_record,
                attributes=attributes,
            )
            result = {"record": str(path), "record_sha256": sha256_file(path), **value}
        elif args.command == "manifest":
            path, value = build_manifest(args.ledger_root, args.manifest_id)
            result = {"manifest": str(path), "manifest_sha256": sha256_file(path), **value}
        elif args.command == "package":
            result = package_manifest(args.manifest, args.output)
        elif args.command == "verify-package":
            result = verify_package(args.package, args.extract_to, args.receipt)
        else:
            result = seal_legacy_records(args.ledger_root)
    except (EvidenceError, PackageVerificationError, OSError, ValueError) as exc:
        print(f"observability evidence: FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
