#!/usr/bin/env python3
"""Run a real HTTPS JWKS rotation, outage, stale-grace, and rollback drill."""

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
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.evidence_provenance import build_evidence_provenance  # noqa: E402


JWKS_PATH = "/.well-known/jwks.json"
ISSUER = "https://identity.boost-gateway.test"
AUDIENCE = "boost-game-client"


class FixtureState:
    def __init__(self, document_path: Path, outage_path: Path, redirect_path: Path) -> None:
        self.document_path = document_path
        self.outage_path = outage_path
        self.redirect_path = redirect_path
        self._lock = threading.Lock()
        self._statuses: Counter[int] = Counter()

    def record(self, status: int) -> None:
        with self._lock:
            self._statuses[status] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            statuses = {str(key): value for key, value in sorted(self._statuses.items())}
        return {"request_count": sum(statuses.values()), "status_counts": statuses}


def handler_for(state: FixtureState) -> type[BaseHTTPRequestHandler]:
    class JwksHandler(BaseHTTPRequestHandler):
        server_version = "BoostGatewayJwksFixture/1"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path != JWKS_PATH:
                state.record(404)
                self.send_error(404)
                return
            if state.outage_path.exists():
                body = b'{"error":"temporarily_unavailable"}\n'
                state.record(503)
                self.send_response(503)
            elif state.redirect_path.exists():
                body = b'{"error":"redirect_forbidden"}\n'
                state.record(302)
                self.send_response(302)
                self.send_header("Location", "https://not-allowlisted.invalid/jwks.json")
            else:
                try:
                    body = state.document_path.read_bytes()
                except OSError:
                    body = b'{"error":"jwks_not_ready"}\n'
                    state.record(503)
                    self.send_response(503)
                else:
                    state.record(200)
                    self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return JwksHandler


def run_checked(command: list[str], *, cwd: Path | None = None) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"command failed while preparing JWKS fixture: {command[1]}")


def generate_tls_and_signing_material(root: Path) -> dict[str, Path]:
    paths = {
        "ca_key": root / "ca.key",
        "ca_cert": root / "ca.pem",
        "server_key": root / "server.key",
        "server_csr": root / "server.csr",
        "server_cert": root / "server.pem",
        "server_extensions": root / "server.ext",
        "old_private": root / "old-private.pem",
        "old_public": root / "old-public.pem",
        "new_private": root / "new-private.pem",
        "new_public": root / "new-public.pem",
    }
    run_checked([
        "openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
        "-out", str(paths["ca_key"]),
    ])
    run_checked([
        "openssl", "req", "-x509", "-new", "-sha256", "-days", "2",
        "-key", str(paths["ca_key"]), "-subj", "/CN=BoostGateway JWKS Drill CA",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-out", str(paths["ca_cert"]),
    ])
    run_checked([
        "openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
        "-out", str(paths["server_key"]),
    ])
    run_checked([
        "openssl", "req", "-new", "-sha256", "-key", str(paths["server_key"]),
        "-subj", "/CN=localhost", "-out", str(paths["server_csr"]),
    ])
    paths["server_extensions"].write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectAltName=DNS:localhost,IP:127.0.0.1\n",
        encoding="ascii",
    )
    run_checked([
        "openssl", "x509", "-req", "-sha256", "-days", "2",
        "-in", str(paths["server_csr"]), "-CA", str(paths["ca_cert"]),
        "-CAkey", str(paths["ca_key"]), "-CAcreateserial",
        "-extfile", str(paths["server_extensions"]), "-out", str(paths["server_cert"]),
    ])
    for name in ("old", "new"):
        run_checked([
            "openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
            "-out", str(paths[f"{name}_private"]),
        ])
        run_checked([
            "openssl", "pkey", "-in", str(paths[f"{name}_private"]),
            "-pubout", "-out", str(paths[f"{name}_public"]),
        ])
    for name, path in paths.items():
        if name.endswith("_key") or name.endswith("_private"):
            path.chmod(0o600)
    return paths


def validate_probe_summary(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["probe summary must be an object"]
    if payload.get("probe_summary_version") != 1:
        errors.append("probe_summary_version must be 1")
    if payload.get("overall_pass") != payload.get("passed"):
        errors.append("probe pass fields differ")
    checks = payload.get("checks")
    if not isinstance(checks, list) or not checks:
        errors.append("probe checks must be a non-empty array")
    forbidden_keys = {
        "token", "private_key", "private_key_pem", "public_key_pem", "pem",
        "modulus", "jwk", "jwks_document", "n", "e",
    }

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in forbidden_keys:
                    errors.append(f"probe summary contains forbidden field: {key}")
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str) and "BEGIN PRIVATE KEY" in value:
            errors.append("probe summary contains private key material")

    visit(payload)
    return errors


def add_check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def run_drill(args: argparse.Namespace) -> dict[str, Any]:
    summary_path = resolve_path(args.summary_path)
    probe = resolve_path(args.probe)
    provenance = build_evidence_provenance(
        ROOT,
        build_configuration=args.configuration,
        conan_lockfile=args.conan_lockfile,
        candidate_revision=args.candidate_revision,
    )
    checks: list[dict[str, Any]] = []
    probe_summary: dict[str, Any] = {}
    server_stats: dict[str, Any] = {"request_count": 0, "status_counts": {}}

    add_check(checks, "probe-executable", probe.is_file() and os.access(probe, os.X_OK),
              "compiled JWKS rotation probe is executable")
    add_check(checks, "openssl-cli", shutil.which("openssl") is not None,
              "OpenSSL CLI is available for ephemeral fixture material")
    add_check(checks, "candidate-matches-checkout",
              provenance.get("revision_matches_checkout") is True,
              "candidate revision matches the checked-out commit")

    if not all(check["passed"] for check in checks):
        return finalize_summary(summary_path, checks, probe_summary, server_stats, provenance)

    try:
        with tempfile.TemporaryDirectory(prefix="boost-gateway-jwks-drill-") as directory:
            work = Path(directory)
            paths = generate_tls_and_signing_material(work)
            certificate_check = subprocess.run(
                ["openssl", "verify", "-CAfile", str(paths["ca_cert"]),
                 "-verify_hostname", "localhost", str(paths["server_cert"])],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            add_check(checks, "tls-chain-and-hostname",
                      certificate_check.returncode == 0,
                      "server certificate chains to the ephemeral CA and matches localhost")

            state_file = work / "current-jwks.json"
            outage_file = work / "outage.enabled"
            redirect_file = work / "redirect.enabled"
            state = FixtureState(state_file, outage_file, redirect_file)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(state))
            server.daemon_threads = True
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(paths["server_cert"], paths["server_key"])
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever,
                                      name="jwks-https-fixture", daemon=True)
            thread.start()
            endpoint = f"https://localhost:{server.server_port}{JWKS_PATH}"
            probe_summary_path = work / "probe-summary.json"
            environment = os.environ.copy()
            environment["SSL_CERT_FILE"] = str(paths["ca_cert"])
            command = [
                str(probe),
                "--jwks-uri", endpoint,
                "--allowed-host", "localhost",
                "--old-public-key", str(paths["old_public"]),
                "--old-private-key", str(paths["old_private"]),
                "--new-public-key", str(paths["new_public"]),
                "--new-private-key", str(paths["new_private"]),
                "--state-file", str(state_file),
                "--outage-file", str(outage_file),
                "--redirect-file", str(redirect_file),
                "--issuer", ISSUER,
                "--audience", AUDIENCE,
                "--summary-path", str(probe_summary_path),
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=args.timeout_seconds,
                    check=False,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            server_stats = state.snapshot()
            if probe_summary_path.is_file():
                probe_summary = json.loads(probe_summary_path.read_text(encoding="utf-8"))
            probe_errors = validate_probe_summary(probe_summary)
            add_check(checks, "probe-summary-contract", not probe_errors,
                      "; ".join(probe_errors) if probe_errors else "probe summary is structured and redacted")
            add_check(checks, "probe-exit", completed.returncode == 0,
                      f"probe exit code={completed.returncode}")
            add_check(checks, "probe-overall-pass", probe_summary.get("overall_pass") is True,
                      "all C++ rotation and failure phases passed")
            statuses = server_stats.get("status_counts", {})
            add_check(checks, "https-success-responses", int(statuses.get("200", 0)) >= 3,
                      "the C++ resolver completed at least three real TLS JWKS fetches")
            add_check(checks, "controlled-outage-responses", int(statuses.get("503", 0)) >= 2,
                      "refresh failure and no-snapshot startup both reached controlled outage mode")
            add_check(checks, "redirect-response-rejected", int(statuses.get("302", 0)) >= 1,
                      "the C++ fetcher received and rejected a real HTTPS redirect")
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        add_check(checks, "drill:fatal", False, str(error))

    return finalize_summary(summary_path, checks, probe_summary, server_stats, provenance)


def finalize_summary(
    summary_path: Path,
    checks: list[dict[str, Any]],
    probe_summary: dict[str, Any],
    server_stats: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    failed = [check for check in checks if not check["passed"]]
    return {
        "summary_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "jwks_rotation" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "endpoint_policy": {
            "scheme": "https",
            "host": "localhost",
            "allow_loopback_http": False,
            "trusted_ephemeral_ca": True,
            "issuer": ISSUER,
            "audience": AUDIENCE,
        },
        "server": server_stats,
        "probe": probe_summary,
        "sensitive_material": {
            "storage": "ephemeral-temporary-directory",
            "persisted_in_summary_or_artifact": False,
        },
        "provenance": provenance,
        "artifacts": {"summary_path": str(summary_path)},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--candidate-revision", required=True)
    parser.add_argument("--configuration", default="Release")
    parser.add_argument(
        "--conan-lockfile",
        default="conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock",
    )
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=Path("runtime/validation/jwks-rotation-summary.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")
    summary = run_drill(args)
    summary_path = resolve_path(args.summary_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    passed = summary["overall_pass"]
    print(
        f"JWKS HTTPS rotation drill: {'PASS' if passed else 'FAIL'} "
        f"({summary['total_checks'] - summary['failed_checks']}/{summary['total_checks']} checks)"
    )
    print(f"summary: {summary_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
