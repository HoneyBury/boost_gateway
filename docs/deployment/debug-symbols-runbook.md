# Linux Debug Symbols Runbook

The v3.6 candidate creates the stripped runtime and debug archive from the same
`RelWithDebInfo` install tree. Never rebuild symbols for an existing runtime or
replace an asset attached to an immutable tag.

## Verify an offline pair

Verify the release checksums and attestations first, then run:

```bash
python3 scripts/tools/verify_debug_symbol_package.py \
  --runtime-archive boost-gateway-3.6.0-linux-x64.tar.gz \
  --symbols-archive boost-gateway-3.6.0-linux-x64-debug-symbols.tar.gz \
  --candidate-revision <full-release-sha> \
  --summary-path debug-symbol-verification.json
```

The verifier rejects missing files, hash/build-id drift, unstripped runtime
DWARF, missing debuglink, or an address that cannot resolve to a source line.

## Resolve an address

Read `debug-symbol-manifest.json`, locate the record whose `build_id` matches
the affected executable, and use its `debug_path`:

```bash
readelf -n /opt/boost-gateway/bin/v2_gateway_demo
addr2line -f -C -e symbols/bin/v2_gateway_demo.debug 0xADDRESS
```

Keep runtime logs, build-id, immutable release tag, full candidate SHA and the
verification summary together in the incident record. Do not install the whole
symbols archive on production nodes and do not upload cores that may contain
credentials or user data without applying the incident data-retention policy.
