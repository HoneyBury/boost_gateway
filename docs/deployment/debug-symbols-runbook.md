# Debug Symbols Runbook

The v3.6.2 release creates the stripped runtime and debug archive from the same
release-with-debug-info install tree. Never rebuild symbols for an existing runtime or
replace an asset attached to an immutable tag.

## Verify an offline pair

Verify the release checksums and attestations first, then run:

```bash
python3 scripts/tools/verify_debug_symbol_package.py \
  --runtime-archive boost-gateway-v3.6.2-linux-x64.tar.gz \
  --symbols-archive boost-gateway-v3.6.2-linux-x64-debug-symbols.tar.gz \
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

## Verify a macOS ARM64 pair

The macOS pair is valid only when every stripped Mach-O UUID matches its dSYM:

```bash
python3.12 scripts/tools/verify_macos_dsym_package.py \
  --runtime-archive boost-gateway-v3.6.2-macos-arm64.tar.gz \
  --symbols-archive boost-gateway-v3.6.2-macos-arm64-dsym.tar.gz \
  --candidate-revision <full-release-sha> \
  --summary-path macos-dsym-verification.json
```

Use `dwarfdump --uuid` on the incident binary and select the same UUID in
`dsym-manifest.json`. Resolve an unslid address with `dwarfdump --lookup`; for a
live ASLR address, preserve the image load address and use `atos` with the dSYM.
The command-line/server assets remain ad-hoc signed and not notarized under the
accepted macOS boundary. Strip and signing preserve the UUID.
