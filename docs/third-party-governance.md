# Third-Party Governance

Date: 2026-05-15

This repository now carries enough vendored and cached dependencies that the
team needs explicit rules for what stays in-tree and what should move to a
mirror or release artifact.

## Current policy

- Keep source-vendored dependencies only when they are required for offline
  configure/build on the primary Windows developer path.
- Prefer cached archives or module caches over full source trees when the build
  only needs extraction, not patching.
- Keep operator Go modules in the local `go-modcache` path instead of vendoring
  `vendor/` into the repository.
- Do not add new third-party trees under `third_party/` unless the dependency is
  on the critical path for `cmake --preset windows-msvc-debug`.

## Priority cleanup

1. Classify each existing dependency as one of:
   `vendored-source`, `cached-archive`, `toolchain-cache`, or `external-only`.
2. Move large unmodified sources to cached archives or an internal mirror when
   the build no longer requires source-level patching.
3. Keep the smallest possible bootstrap set in-tree:
   `boost`, `fmt`, `spdlog`, `nlohmann/json`, `googletest`, `hiredis`, and the
   operator Go module cache.
4. Document the restore command for every cache-backed dependency so a clean
   machine can rebuild without guessing.

## Enforcement

- Any PR that introduces a new vendored dependency should state why an internal
  mirror, package manager, or cached archive is insufficient.
- Any PR that updates a vendored tree should also update the corresponding
  bootstrap or restore instructions.
- CI should keep one offline-oriented build lane so accidental network
  dependencies are caught early.

## Operational checks

- `bash scripts/inspect_dependency_layout.sh`
- `bash third_party/download_deps.sh`
- `bash third_party/bootstrap_from_build_cache.sh`

The first command is the lightweight audit step. The latter two are the restore
paths a clean machine should be able to execute without reading build scripts.
