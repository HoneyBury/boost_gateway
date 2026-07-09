# 第三方依赖

本目录用于离线 / 内网构建，支持两种本地依赖形态：

- 压缩包：`fmt-11.2.0.tar.gz`、`boost_1_90_0.zip` 等
- 源码目录：`fmt-src/`、`googletest-src/`、`spdlog-src/`、`nlohmann_json-src/`、`boost-src/`、`hiredis-src/`
- 本地安装目录：`openssl/`，要求包含 `include/openssl/ssl.h` 和 `lib/` 或 `lib64/` 下的 `libssl` / `libcrypto`

`cmake/Dependencies.cmake` 的依赖查找优先级：

1. `third_party/*-src`
2. `build/_deps/*-src`
3. `build/<preset>/_deps/*-src`
4. `third_party/*.tar.gz|*.zip`
5. 远程下载

OpenSSL 是例外：它不通过 FetchContent 从源码编译，查找顺序为 Conan config package、系统安装、
`OPENSSL_ROOT_DIR` 显式路径、本目录下的 `openssl/` 或 `openssl-src/` 本地安装目录。这样可以同时支持
Conan、系统包和内网预装 OpenSSL。

## 依赖列表

| 本地名称 | 版本 | 来源 |
|---|---|---|
| `fmt-11.2.0.tar.gz` / `fmt-src/` | 11.2.0 | https://github.com/fmtlib/fmt |
| `googletest-1.17.0.tar.gz` / `googletest-src/` | v1.17.0 | https://github.com/google/googletest |
| `spdlog-1.15.3.tar.gz` / `spdlog-src/` | v1.15.3 | https://github.com/gabime/spdlog |
| `nlohmann_json-3.12.0.tar.gz` / `nlohmann_json-src/` | v3.12.0 | https://github.com/nlohmann/json |
| `boost_1_90_0.zip` / `boost-src/` | 1.90.0 | https://archives.boost.io |
| `hiredis-1.2.0.tar.gz` / `hiredis-src/` | v1.2.0 | https://github.com/redis/hiredis |
| `openssl/` / `openssl-src/` | 3.0+ | https://www.openssl.org |

## 外网机器

首次下载可执行：

```powershell
.\third_party\download_deps.bat
.\third_party\package.bat
```

然后把生成的 `third_party.zip` 上传到内部文件服务器。

## 从已有构建缓存固化源码目录

如果本机已经存在旧的 `build/_deps/*-src` 缓存，但没有压缩包，可直接执行：

```powershell
.\third_party\bootstrap_from_build_cache.bat
```

Linux/macOS：

```bash
./third_party/bootstrap_from_build_cache.sh
```

脚本会把现有缓存复制到 `third_party/*-src`，后续 configure 可直接离线使用。

## 内网开发机器

1. 下载并解压 `third_party.zip` 到项目根目录；或执行 `bootstrap_from_build_cache` 固化本机已有缓存
2. 正常构建：

```powershell
cmake --preset windows-msvc-debug
cmake --build --preset windows-msvc-debug
ctest --preset windows-msvc-debug --output-on-failure
```

CMake 配置阶段会输出 `Using local source directory:` 或 `Using local archive:`。
只有在本地源码目录和压缩包都不存在时，才会回退到远程 URL 下载。
