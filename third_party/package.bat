@echo off
REM Package all third-party archives into a single distributable archive.
REM
REM Usage:
REM   third_party\package.bat
REM
REM Prerequisites: run download_deps.bat first.
REM
REM Output: third_party.zip (in the project root)
REM Requires: PowerShell (bundled with Windows) for creating zip files.

setlocal enabledelayedexpansion
cd /d "%~dp0"
set "PROJECT_DIR=%cd%\.."

echo === Packaging third-party dependencies ===

REM Verify archives exist
set "MISSING="
if not exist "fmt-11.2.0.tar.gz" set "MISSING=!MISSING! fmt-11.2.0.tar.gz"
if not exist "googletest-1.17.0.tar.gz" set "MISSING=!MISSING! googletest-1.17.0.tar.gz"
if not exist "spdlog-1.15.3.tar.gz" set "MISSING=!MISSING! spdlog-1.15.3.tar.gz"
if not exist "nlohmann_json-3.12.0.tar.gz" set "MISSING=!MISSING! nlohmann_json-3.12.0.tar.gz"
if not exist "boost_1_90_0.zip" set "MISSING=!MISSING! boost_1_90_0.zip"

if not "!MISSING!"=="" (
    echo ERROR: Missing archives:!MISSING!
    echo Run 'third_party\download_deps.bat' first.
    exit /b 1
)

REM Remove old archive if exists
set "OUTPUT=%PROJECT_DIR%\third_party.zip"
if exist "%OUTPUT%" del "%OUTPUT%"

REM Create zip using PowerShell
powershell -NoProfile -Command ^
    "Compress-Archive -Path '%cd%\*' -DestinationPath '%OUTPUT%' -Force"

echo.
echo === Package created: third_party.zip ===
echo Upload this file to your internal repository or file server.
echo.
echo Other developers should:
echo   1. Download third_party.zip
echo   2. Extract it to the project root
echo   3. Run cmake --preset windows-msvc-debug as normal
endlocal
