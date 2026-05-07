# 发布流程（v1.x）

## 目标

在 `v1.x` 维护分支内，发布流程只做三件事：

1. 将阶段收束结果从 `develop` 合并到 `main`
2. 用 GitHub Actions 在 Linux / macOS / Windows 上完成构建、测试、打包
3. 以 `v*` tag 触发 GitHub Release，上传三平台产物与 `SHA256SUMS.txt`

## 分支与版本

- 日常集成：`develop`
- 稳定发布：`main`
- 当前维护发布版：`v1.2.4`
- `v2.0.0` 相关设计仍停留在文档草案，不进入当前发布流程

## 发布前检查

发布前至少确认：

- `cmake --preset default`
- `cmake --build --preset default`
- `ctest --preset default`
- Windows 预设 `windows-msvc-debug` / `windows-msvc-release` 可配置
- GitHub Windows runner 走 `windows-ninja-debug` / `windows-ninja-release`，避免依赖固定 Visual Studio generator 名称
- `README.md`、`CHANGELOG.md`、`docs/development-log.md`、`docs/development-priority.md` 已同步
- `docs/v1-structure-upgrade-decision.md` 仍明确当前分支不进入 `v2.0.0`

## CI

`.github/workflows/ci.yml` 负责：

- `develop` / `main` push
- 所有 PR
- Linux / macOS：`default` preset
- Windows：`windows-ninja-debug` preset（配合 `msvc-dev-cmd`）
- Ubuntu 主干 push 后补做 Docker build smoke test

## Release

`.github/workflows/release.yml` 负责：

- `v*` tag push 或手工触发
- Linux / macOS：`release` preset
- Windows：`windows-ninja-release` preset（配合 `msvc-dev-cmd`）
- `ctest` 后执行 `cmake --install`
- 生成三平台压缩包：
  - `boost-gateway-<tag>-linux-x86_64.tar.gz`
  - `boost-gateway-<tag>-macos.tar.gz`
  - `boost-gateway-<tag>-windows-x64.zip`
- 汇总 `SHA256SUMS.txt`
- 创建 GitHub Release 并上传所有产物

## 归档内容

每个 release 安装目录至少包含：

- `bin/`：示例与演示可执行文件
- `include/`：公开头文件
- `share/boost_gateway/config/`：配置样例
- `share/boost_gateway/docs/`：运行与维护文档
- 根目录 `README.md`、`CHANGELOG.md`

## 推荐操作顺序

1. 在 `develop` 完成收束并提交
2. `git switch main`
3. `git merge --no-ff develop`
4. `git tag -a v1.2.4 -m "Release v1.2.4"`
5. `git push origin main develop --follow-tags`

push tag 后，GitHub Release 流水线会自动生成正式发布产物。
