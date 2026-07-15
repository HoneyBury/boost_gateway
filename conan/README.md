# Conan Governance

This repository uses Conan as its strict default dependency provider. Missing
packages or generated CMake config files are configuration errors; there is no
implicit fallback. The lockfile/profile flow is the fixed-runner contract.

Current rules:

- The default build path is strict Conan (`BOOST_DEPENDENCY_PROVIDER=conan`).
- `BOOST_DEPENDENCY_PROVIDER=fetchcontent` is an explicit development-only source mode; CI, release and evidence workflows must never use it.
- For local development, `CONAN_HOME=.conan2-local` is convenient. Fixed-runner
  workflows use an OS-safe persistent namespace under `/opt/boost-gateway/conan`;
  do not share native Conan packages between Ubuntu releases.
- Prefer repository-managed profiles and remotes under `conan/`.
- Public `conancenter` is not a default requirement; internal mirror, warm cache,
  or `--no-remote` must remain supported.
- `scripts/generate_conan_lock.py` now defaults to a profile-specific temporary
  `CONAN_HOME` to avoid cross-runner and cross-profile state collisions.
- All development, cache warm-up and fixed-runner evidence use Conan `2.8.1`
  from an isolated virtual environment. Do not install Conan into the system
  Python or reuse an unpinned global `conan` executable.
- Every repository workflow that invokes `conan install` follows the same
  venv contract, including GitHub-hosted CI and release. The helper validates
  Python 3.12 and Conan `2.8.1`; workflows must expose that venv through
  `GITHUB_PATH`, not discover a global `conan` executable. CI cache keys carry
  `conan-2.8.1` and never use a broad restore key across Conan versions.

Recommended entrypoints (Linux/macOS mainline):

```bash
python3 scripts/tools/ensure_conan_venv.py --conan-version 2.8.1
source .venv/conan-2.8.1/bin/activate
export CONAN_HOME="$PWD/.conan2-local"
python scripts/generate_conan_lock.py --profile conan/profiles/linux-gcc-x64 --build-type Debug --without-sqlite --allow-public
conan install . --profile:host conan/profiles/linux-gcc-x64 --profile:build conan/profiles/linux-gcc-x64 --lockfile conan/locks/linux-gcc-x64-debug-nogrpc-nosqlite.lock -o "&:with_grpc=False" -o "&:with_sqlite=False" --output-folder=build/conan-debug --build=missing -s build_type=Debug
cmake -S . -B build/linux-ninja-debug-conan -G Ninja -DBOOST_DEPENDENCY_PROVIDER=conan -DENABLE_TESTING=ON -DCMAKE_BUILD_TYPE=Debug -DCMAKE_TOOLCHAIN_FILE=build/conan-debug/build/Debug/generators/conan_toolchain.cmake
cmake --build build/linux-ninja-debug-conan --parallel --target project_v2_unit_tests
```

Linux fixed-runner example:

```bash
conan_venv=/opt/boost-gateway/tools/conan-2.8.1-py3.12
python3.12 scripts/tools/ensure_conan_venv.py --venv "$conan_venv" --conan-version 2.8.1
export PATH="$conan_venv/bin:$PATH"
cache_env="$(mktemp)"
python scripts/tools/resolve_runner_cache.py --build-type Release \
  --profile conan/profiles/linux-gcc-x64 \
  --lockfile conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock \
  --github-env "$cache_env"
set -a; . "$cache_env"; set +a
python3.12 scripts/tools/ensure_conan_venv.py --venv "$conan_venv" --conan-version 2.8.1 --offline
python scripts/bootstrap_conan.py --conan-home "$CONAN_HOME" --no-remote
conan install . --profile:host conan/profiles/linux-gcc-x64 --profile:build conan/profiles/linux-gcc-x64 --lockfile conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock -o "&:with_grpc=False" -o "&:with_sqlite=False" --output-folder=build/conan-release-offline --build=never --no-remote -s build_type=Release
cmake -S . -B build/release -G Ninja -DBOOST_DEPENDENCY_PROVIDER=conan -DENABLE_TESTING=OFF -DCMAKE_BUILD_TYPE=Release -DCMAKE_TOOLCHAIN_FILE=build/conan-release-offline/build/Release/generators/conan_toolchain.cmake
cmake --build build/release --parallel
python scripts/tools/prepare_docker_runtime_context.py --build-dir build/release
```

### 新 runner 的 Conan 缓存初始化

`resolve_runner_cache.py` creates `/opt/boost-gateway/conan/<ubuntu-release>-gcc<version>-<arch>-<build-type>/...`
and exports `CONAN_HOME`; it also assigns sccache a matching namespace. The
Conan key includes `conanfile.py`, profile, lockfile, repository remote files,
remote overrides, Conan/GCC versions, OS release, architecture and build type.
Warm each namespace using the fixed-runner example, then use `--no-remote` for
evidence work. Docker images are a separate cache and may cross Ubuntu 22.04
and 24.04 x64 runners when imported as `linux/amd64` images.

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
- Cache keys include `conanfile.py`, profile, remotes config and overrides,
  lockfile hash, Conan/GCC versions, Ubuntu release, architecture and build type.
- Offline evidence must use `--no-remote --build=never` after the local Conan
  cache has been pre-warmed with all required packages and source archives.
  `--build=missing` is only for an explicit warm-up with an approved remote.
- Windows profile/lockfile examples remain in git history only; current mainline
  no longer treats Windows as an active Conan validation target.
- Typical blocking signals:
  - internal mirror DNS failure such as `getaddrinfo failed`
  - public `conancenter` socket policy failure or offline registry access denial
