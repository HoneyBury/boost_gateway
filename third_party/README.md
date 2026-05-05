# Third-Party Dependencies

This directory holds pre-downloaded third-party library archives for
offline / internal-network builds.

## Archive list

| Archive | Version | Source |
|---|---|---|
| `fmt-11.2.0.tar.gz` | 11.2.0 | https://github.com/fmtlib/fmt |
| `googletest-1.17.0.tar.gz` | v1.17.0 | https://github.com/google/googletest |
| `spdlog-1.15.3.tar.gz` | v1.15.3 | https://github.com/gabime/spdlog |
| `nlohmann_json-3.12.0.tar.gz` | v3.12.0 | https://github.com/nlohmann/json |
| `boost_1_90_0.zip` | 1.90.0 | https://archives.boost.io |

## For the maintainer (person with external network access)

```powershell
# Download all archives
.\third_party\download_deps.bat

# Create a single distributable package
.\third_party\package.bat

# Upload third_party.zip to the company's internal repository / file server
```

## For internal-network developers

1. Download `third_party.zip` from the company's internal repository
2. Extract it to the project root (so `third_party/` sits next to `CMakeLists.txt`)
3. Build normally:

```powershell
cmake --preset windows-msvc-debug
cmake --build --preset windows-msvc-debug
```

CMake will detect the local archives automatically and skip remote fetching.
