#!/usr/bin/env bash
# Generate self-signed dev certificates for TLS/mTLS inter-service communication.
# Outputs to certs/ directory.
# Works on macOS (LibreSSL) and Linux (OpenSSL).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CERTS_DIR="$PROJECT_ROOT/certs"

echo "==> Generating dev certificates in $CERTS_DIR"
mkdir -p "$CERTS_DIR"

# ── CA ────────────────────────────────────────────────────────────────

openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
    -keyout "$CERTS_DIR/ca.key" \
    -out "$CERTS_DIR/ca.crt" \
    -subj "/CN=Boost Dev CA/O=Boost Gateway Dev/C=US" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "basicConstraints=critical,CA:TRUE" \
    2>/dev/null
echo "  [OK] ca.key + ca.crt"

# ── Server cert ───────────────────────────────────────────────────────

openssl req -new -newkey rsa:4096 -sha256 -days 3650 -nodes \
    -keyout "$CERTS_DIR/server.key" \
    -out "$CERTS_DIR/server.csr" \
    -subj "/CN=localhost/O=Boost Gateway Dev/C=US" \
    2>/dev/null

# Create extensions file for SAN
cat > "$CERTS_DIR/server.ext" <<'EXTEOF'
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage=digitalSignature,nonRepudiation,keyEncipherment,dataEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=@alt_names
[alt_names]
DNS.1=localhost
DNS.2=*.boost-gateway.svc.cluster.local
IP.1=127.0.0.1
EXTEOF

openssl x509 -req -in "$CERTS_DIR/server.csr" \
    -CA "$CERTS_DIR/ca.crt" \
    -CAkey "$CERTS_DIR/ca.key" \
    -CAcreateserial \
    -out "$CERTS_DIR/server.crt" \
    -days 3650 -sha256 \
    -extfile "$CERTS_DIR/server.ext" \
    2>/dev/null
echo "  [OK] server.key + server.crt"

# Cleanup CSR and ext
rm -f "$CERTS_DIR/server.csr" "$CERTS_DIR/server.ext"

echo "==> Done. Certificates in $CERTS_DIR/"
ls -la "$CERTS_DIR/"
