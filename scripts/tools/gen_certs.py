#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


SERVER_EXT = """authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage=digitalSignature,nonRepudiation,keyEncipherment,dataEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=@alt_names
[alt_names]
DNS.1=localhost
DNS.2=*.boost-gateway.svc.cluster.local
IP.1=127.0.0.1
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate local TLS certificates for gateway/backend validation.")
    parser.add_argument("--output-dir", type=Path, default=Path("certs"))
    parser.add_argument("--days", type=int, default=3650)
    parser.add_argument("--common-name", default="localhost")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    certs_dir = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    certs_dir.mkdir(parents=True, exist_ok=True)

    openssl = shutil.which("openssl")
    if not openssl:
        raise SystemExit("openssl not found in PATH")

    print(f"==> Generating dev certificates in {certs_dir}")

    subprocess.run([
        openssl, "req", "-x509", "-newkey", "rsa:4096", "-sha256", "-days", str(args.days), "-nodes",
        "-keyout", str(certs_dir / "ca.key"),
        "-out", str(certs_dir / "ca.crt"),
        "-subj", "/CN=Boost Dev CA/O=Boost Gateway Dev/C=US",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-addext", "basicConstraints=critical,CA:TRUE",
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("  [OK] ca.key + ca.crt")

    subprocess.run([
        openssl, "req", "-new", "-newkey", "rsa:4096", "-sha256", "-days", str(args.days), "-nodes",
        "-keyout", str(certs_dir / "server.key"),
        "-out", str(certs_dir / "server.csr"),
        "-subj", f"/CN={args.common_name}/O=Boost Gateway Dev/C=US",
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    server_ext = certs_dir / "server.ext"
    server_ext.write_text(SERVER_EXT, encoding="utf-8")
    subprocess.run([
        openssl, "x509", "-req",
        "-in", str(certs_dir / "server.csr"),
        "-CA", str(certs_dir / "ca.crt"),
        "-CAkey", str(certs_dir / "ca.key"),
        "-CAcreateserial",
        "-out", str(certs_dir / "server.crt"),
        "-days", str(args.days), "-sha256",
        "-extfile", str(server_ext),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("  [OK] server.key + server.crt")

    for path in [certs_dir / "server.csr", server_ext]:
        if path.exists():
            path.unlink()

    print(f"==> Done. Certificates in {certs_dir}/")
    for item in sorted(certs_dir.iterdir()):
        print(f"  {item.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

