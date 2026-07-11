# Conan Governance

This repository prefers Conan as the dependency provider with automatic
fallback to FetchContent/third_party when Conan packages are unavailable.
The lockfile/profile flow is ready for fixed-runner validation.

Current rules:

- Default build path prefers Conan (`BOOST_USE_CONAN_DEPS=ON`), falling back to `FetchContent/third_party` when Conan packages are unavailable.
- `BOOST_USE_CONAN_DEPS=ON` is the only supported switch for enabling Conan.
- For local development, `CONAN_HOME=.conan2-local` is convenient. Fixed-runner
  workflows deliberately use `${{ github.workspace }}/../.conan2-local`, which
  survives checkout replacement and is pre-warmed on the runner.
- Prefer repository-managed profiles and remotes under `conan/`.
- Public `conancenter` is not a default requirement; internal mirror, warm cache,
  or `--no-remote` must remain supported.
- `scripts/generate_conan_lock.py` now defaults to a profile-specific temporary
  `CONAN_HOME` to avoid cross-runner and cross-profile state collisions.

Recommended entrypoints (Linux/macOS mainline):

```bash
export CONAN_HOME=$PWD/.conan2-local
python scripts/generate_conan_lock.py --profile conan/profiles/linux-gcc-x64 --build-type Debug --without-sqlite --allow-public
conan install . --profile:host conan/profiles/linux-gcc-x64 --profile:build conan/profiles/linux-gcc-x64 --lockfile conan/locks/linux-gcc-x64-debug-nogrpc-nosqlite.lock -o "&:with_grpc=False" -o "&:with_sqlite=False" --output-folder=build/conan-debug --build=missing -s build_type=Debug
cmake -S . -B build/linux-ninja-debug-conan -G Ninja -DBOOST_USE_CONAN_DEPS=ON -DENABLE_TESTING=ON -DCMAKE_BUILD_TYPE=Debug -DCMAKE_TOOLCHAIN_FILE=build/conan-debug/build/Debug/generators/conan_toolchain.cmake
cmake --build build/linux-ninja-debug-conan --parallel --target project_v2_unit_tests
```

Linux fixed-runner example:

```bash
python scripts/generate_conan_lock.py --profile conan/profiles/linux-gcc-x64 --build-type Release --without-sqlite
conan install . --profile:host conan/profiles/linux-gcc-x64 --profile:build conan/profiles/linux-gcc-x64 --lockfile conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock -o "&:with_grpc=False" -o "&:with_sqlite=False" --output-folder=build/conan-release --build=missing -s build_type=Release
```

### 新 runner 的 Conan 缓存初始化

固定 runner 的 Conan home 是 checkout 的同级目录：
`${GITHUB_WORKSPACE}/../.conan2-local`（Actions 中对应
`${{ github.workspace }}/../.conan2-local`）。换新机器时，先让第一次
`conan install` 从配置的远端完整拉取依赖；成功后把这个已填充的 Conan
home 复制到该固定路径，并确保 runner 清理 checkout 时不要删除它。之后
`conan-validate.yml`、`release.yml`、长稳、性能和 production evidence
工作流都会复用这份缓存，避免每个 workflow 重复从远端下载。

```bash
export RUNNER_CONAN_HOME="$GITHUB_WORKSPACE/../.conan2-local"
mkdir -p "$RUNNER_CONAN_HOME"
rsync -a /path/to/seeded/.conan2-local/ "$RUNNER_CONAN_HOME/"
export CONAN_HOME="$RUNNER_CONAN_HOME"
```

`ci.yml` 是有意保留的例外：它运行在 GitHub-hosted runner 上，使用
checkout 内的 `.conan2-local` 和 `actions/cache`；`production-readiness.yml`
只下载并汇聚已有 artifact，不执行 Conan。

The repository-managed Linux profile intentionally pins `compiler.version` so
the fixed-runner lockfile stays reproducible. If the Ubuntu runner image moves
to a different GCC major, update `conan/profiles/linux-gcc-x64` and refresh the
lockfile in the same change.

Lockfile policy:

- Generate lockfiles per build type and option set before promoting Conan to a
  default CI dependency source.
- Store generated lockfiles under `conan/locks/`.
- Cache keys should include `conanfile.py`, `conan/profiles/**`, remotes config,
  and the relevant lockfile hash.
- `--no-remote` can only generate lockfiles after the local Conan cache has been
  pre-warmed with all required packages. Otherwise lock generation must use an
  internal/public remote.
- Windows profile/lockfile examples remain in git history only; current mainline
  no longer treats Windows as an active Conan validation target.
- Typical blocking signals:
  - internal mirror DNS failure such as `getaddrinfo failed`
  - public `conancenter` socket policy failure or offline registry access denial
