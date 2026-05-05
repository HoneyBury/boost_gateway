@echo off
REM Download all third-party dependencies for offline/internal-network builds.
REM
REM Usage:
REM   third_party\download_deps.bat
REM
REM Requires curl (bundled with Windows 10+).

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === Downloading third-party dependencies ===

REM --- fmt 11.2.0 ---
if exist "fmt-11.2.0.tar.gz" (
    echo [skip] fmt-11.2.0.tar.gz already exists
) else (
    echo [fetch] fmt-11.2.0.tar.gz
    curl -fSL -o "fmt-11.2.0.tar.gz" ^
        "https://github.com/fmtlib/fmt/archive/refs/tags/11.2.0.tar.gz"
    if errorlevel 1 exit /b 1
)

REM --- googletest v1.17.0 ---
if exist "googletest-1.17.0.tar.gz" (
    echo [skip] googletest-1.17.0.tar.gz already exists
) else (
    echo [fetch] googletest-1.17.0.tar.gz
    curl -fSL -o "googletest-1.17.0.tar.gz" ^
        "https://github.com/google/googletest/archive/refs/tags/v1.17.0.tar.gz"
    if errorlevel 1 exit /b 1
)

REM --- spdlog v1.15.3 ---
if exist "spdlog-1.15.3.tar.gz" (
    echo [skip] spdlog-1.15.3.tar.gz already exists
) else (
    echo [fetch] spdlog-1.15.3.tar.gz
    curl -fSL -o "spdlog-1.15.3.tar.gz" ^
        "https://github.com/gabime/spdlog/archive/refs/tags/v1.15.3.tar.gz"
    if errorlevel 1 exit /b 1
)

REM --- nlohmann_json v3.12.0 ---
if exist "nlohmann_json-3.12.0.tar.gz" (
    echo [skip] nlohmann_json-3.12.0.tar.gz already exists
) else (
    echo [fetch] nlohmann_json-3.12.0.tar.gz
    curl -fSL -o "nlohmann_json-3.12.0.tar.gz" ^
        "https://github.com/nlohmann/json/archive/refs/tags/v3.12.0.tar.gz"
    if errorlevel 1 exit /b 1
)

REM --- Boost 1.90.0 ---
if exist "boost_1_90_0.zip" (
    echo [skip] boost_1_90_0.zip already exists
) else (
    echo [fetch] boost_1_90_0.zip
    curl -fSL -o "boost_1_90_0.zip" ^
        "https://archives.boost.io/release/1.90.0/source/boost_1_90_0.zip"
    if errorlevel 1 exit /b 1
)

echo.
echo === All dependencies downloaded to third_party\ ===
echo Next: run 'third_party\package.bat' to create a distributable archive.
endlocal
