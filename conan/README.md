# Conan Governance

This repository prefers Conan as the dependency provider with automatic
fallback to FetchContent/third_party when Conan packages are unavailable.
The lockfile/profile flow is ready for fixed-runner validation.

Current rules:

- Default build path prefers Conan (`BOOST_USE_CONAN_DEPS=ON`), falling back to `FetchContent/third_party` when Conan packages are unavailable.
- `BOOST_USE_CONAN_DEPS=ON` is the only supported switch for enabling Conan.
- Prefer repository-local `CONAN_HOME=.conan2-local`.
- Prefer repository-managed profiles and remotes under `conan/`.
- Public `conancenter` is not a default requirement; internal mirror, warm cache,
  or `--no-remote` must remain supported.
- `scripts/generate_conan_lock.py` now defaults to a profile-specific temporary
  `CONAN_HOME` to avoid cross-runner and cross-profile state collisions.

Recommended entrypoints:

```bash
python scripts/generate_conan_lock.py --profile conan/profiles/windows-msvc-x64 --build-type Debug --without-sqlite --allow-public
conan install . --profile:host conan/profiles/windows-msvc-x64 --profile:build conan/profiles/windows-msvc-x64 --lockfile conan/locks/windows-msvc-x64-debug-nogrpc-nosqlite.lock -o "&:with_grpc=False" -o "&:with_sqlite=False" --output-folder=build/conan-debug --build=missing -s build_type=Debug
cmake -S . -B build/windows-ninja-debug-conan -G Ninja -DBOOST_USE_CONAN_DEPS=ON -DENABLE_TESTING=ON -DCMAKE_BUILD_TYPE=Debug -DCMAKE_TOOLCHAIN_FILE=build/conan-debug/build/Debug/generators/conan_toolchain.cmake
cmake --build build/windows-ninja-debug-conan --parallel --target project_v2_unit_tests
```

Linux fixed-runner example:

```bash
python scripts/generate_conan_lock.py --profile conan/profiles/linux-gcc-x64 --build-type Release --without-sqlite
conan install . --profile:host conan/profiles/linux-gcc-x64 --profile:build conan/profiles/linux-gcc-x64 --lockfile conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock -o "&:with_grpc=False" -o "&:with_sqlite=False" --output-folder=build/conan-release --build=missing -s build_type=Release
```

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
- Typical blocking signals:
  - internal mirror DNS failure such as `getaddrinfo failed`
  - public `conancenter` socket policy failure such as Windows `WinError 10013`
