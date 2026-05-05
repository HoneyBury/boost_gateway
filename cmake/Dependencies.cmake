include(FetchContent)

if(POLICY CMP0169)
    cmake_policy(SET CMP0169 OLD)
endif()

set(FETCHCONTENT_QUIET OFF)

# ---------------------------------------------------------------------------
# Local third-party archive directory (for offline / internal-network builds)
# ---------------------------------------------------------------------------
set(THIRD_PARTY_DIR "${CMAKE_CURRENT_SOURCE_DIR}/third_party")

if(EXISTS "${THIRD_PARTY_DIR}")
    message(STATUS "Third-party directory found: ${THIRD_PARTY_DIR}")
else()
    message(STATUS "Third-party directory not found — fetching from remote URLs")
endif()

# ---------------------------------------------------------------------------
# fmt 11.2.0
# ---------------------------------------------------------------------------
if(EXISTS "${THIRD_PARTY_DIR}/fmt-11.2.0.tar.gz")
    message(STATUS "Using local archive: fmt-11.2.0.tar.gz")
    FetchContent_Declare(fmt URL "${THIRD_PARTY_DIR}/fmt-11.2.0.tar.gz")
else()
    FetchContent_Declare(fmt
        GIT_REPOSITORY https://github.com/fmtlib/fmt.git
        GIT_TAG 11.2.0
    )
endif()

# ---------------------------------------------------------------------------
# googletest v1.17.0
# ---------------------------------------------------------------------------
if(EXISTS "${THIRD_PARTY_DIR}/googletest-1.17.0.tar.gz")
    message(STATUS "Using local archive: googletest-1.17.0.tar.gz")
    FetchContent_Declare(googletest URL "${THIRD_PARTY_DIR}/googletest-1.17.0.tar.gz")
else()
    FetchContent_Declare(googletest
        GIT_REPOSITORY https://github.com/google/googletest.git
        GIT_TAG v1.17.0
    )
endif()

# ---------------------------------------------------------------------------
# spdlog v1.15.3
# ---------------------------------------------------------------------------
if(EXISTS "${THIRD_PARTY_DIR}/spdlog-1.15.3.tar.gz")
    message(STATUS "Using local archive: spdlog-1.15.3.tar.gz")
    FetchContent_Declare(spdlog URL "${THIRD_PARTY_DIR}/spdlog-1.15.3.tar.gz")
else()
    FetchContent_Declare(spdlog
        GIT_REPOSITORY https://github.com/gabime/spdlog.git
        GIT_TAG v1.15.3
    )
endif()

# ---------------------------------------------------------------------------
# nlohmann_json v3.12.0
# ---------------------------------------------------------------------------
if(EXISTS "${THIRD_PARTY_DIR}/nlohmann_json-3.12.0.tar.gz")
    message(STATUS "Using local archive: nlohmann_json-3.12.0.tar.gz")
    FetchContent_Declare(nlohmann_json URL "${THIRD_PARTY_DIR}/nlohmann_json-3.12.0.tar.gz")
else()
    FetchContent_Declare(nlohmann_json
        GIT_REPOSITORY https://github.com/nlohmann/json.git
        GIT_TAG v3.12.0
    )
endif()

# ---------------------------------------------------------------------------
# Boost 1.90.0
# ---------------------------------------------------------------------------
if(EXISTS "${THIRD_PARTY_DIR}/boost_1_90_0.zip")
    message(STATUS "Using local archive: boost_1_90_0.zip")
    FetchContent_Declare(Boost
        URL "${THIRD_PARTY_DIR}/boost_1_90_0.zip"
        URL_HASH SHA256=bdc79f179d1a4a60c10fe764172946d0eeafad65e576a8703c4d89d49949973c
        DOWNLOAD_EXTRACT_TIMESTAMP ON
    )
else()
    FetchContent_Declare(Boost
        URL https://archives.boost.io/release/1.90.0/source/boost_1_90_0.zip
        URL_HASH SHA256=bdc79f179d1a4a60c10fe764172946d0eeafad65e576a8703c4d89d49949973c
        DOWNLOAD_EXTRACT_TIMESTAMP ON
    )
endif()

# ---------------------------------------------------------------------------
# Make targets available
# ---------------------------------------------------------------------------
set(BUILD_GMOCK OFF CACHE BOOL "" FORCE)
set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)

FetchContent_MakeAvailable(fmt)
FetchContent_MakeAvailable(spdlog)
FetchContent_MakeAvailable(nlohmann_json)

if(ENABLE_TESTING)
    FetchContent_MakeAvailable(googletest)
endif()

FetchContent_GetProperties(Boost)

if(NOT EXISTS "${boost_SOURCE_DIR}/boost/version.hpp")
    FetchContent_Populate(Boost)
endif()

# ---------------------------------------------------------------------------
# Boost.Asio header-only interface target
# ---------------------------------------------------------------------------
add_library(project_boost_asio INTERFACE)

target_include_directories(project_boost_asio
    INTERFACE
        "${boost_SOURCE_DIR}"
)

target_compile_definitions(project_boost_asio
    INTERFACE
        BOOST_ALL_NO_LIB
        BOOST_ERROR_CODE_HEADER_ONLY
        _WIN32_WINNT=0x0A00
)
