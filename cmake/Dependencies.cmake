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
    FetchContent_Declare(fmt
        URL "${THIRD_PARTY_DIR}/fmt-11.2.0.tar.gz"
        DOWNLOAD_EXTRACT_TIMESTAMP ON
    )
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
    FetchContent_Declare(googletest
        URL "${THIRD_PARTY_DIR}/googletest-1.17.0.tar.gz"
        DOWNLOAD_EXTRACT_TIMESTAMP ON
    )
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
    FetchContent_Declare(spdlog
        URL "${THIRD_PARTY_DIR}/spdlog-1.15.3.tar.gz"
        DOWNLOAD_EXTRACT_TIMESTAMP ON
    )
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
    FetchContent_Declare(nlohmann_json
        URL "${THIRD_PARTY_DIR}/nlohmann_json-3.12.0.tar.gz"
        DOWNLOAD_EXTRACT_TIMESTAMP ON
    )
else()
    FetchContent_Declare(nlohmann_json
        GIT_REPOSITORY https://github.com/nlohmann/json.git
        GIT_TAG v3.12.0
    )
endif()

# ---------------------------------------------------------------------------
# OpenSSL (v3.0+ required for TLS/mTLS)
# ---------------------------------------------------------------------------
find_package(OpenSSL REQUIRED)
message(STATUS "OpenSSL found: ${OPENSSL_VERSION}")
message(STATUS "  include: ${OPENSSL_INCLUDE_DIR}")
message(STATUS "  ssl:     ${OPENSSL_SSL_LIBRARY}")
message(STATUS "  crypto:  ${OPENSSL_CRYPTO_LIBRARY}")

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
# hiredis v1.2.0 (Redis C client)
# ---------------------------------------------------------------------------
if(EXISTS "${THIRD_PARTY_DIR}/hiredis-1.2.0.tar.gz")
    message(STATUS "Using local archive: hiredis-1.2.0.tar.gz")
    FetchContent_Declare(hiredis
        URL "${THIRD_PARTY_DIR}/hiredis-1.2.0.tar.gz"
        DOWNLOAD_EXTRACT_TIMESTAMP ON
    )
else()
    FetchContent_Declare(hiredis
        GIT_REPOSITORY https://github.com/redis/hiredis.git
        GIT_TAG v1.2.0
    )
endif()

set(HIREDIS_SSL OFF CACHE BOOL "" FORCE)
set(HIREDIS_TEST OFF CACHE BOOL "" FORCE)
set(DISABLE_TESTS ON CACHE BOOL "" FORCE)

# hiredis requires CMake >= 3.5 policy compatibility
set(CMAKE_POLICY_VERSION_MINIMUM 3.5)

# ---------------------------------------------------------------------------
# Make targets available
# ---------------------------------------------------------------------------
set(BUILD_GMOCK OFF CACHE BOOL "" FORCE)
set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)

FetchContent_MakeAvailable(fmt)
FetchContent_MakeAvailable(spdlog)
FetchContent_MakeAvailable(nlohmann_json)
FetchContent_MakeAvailable(hiredis)

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
)

if(WIN32)
    target_compile_definitions(project_boost_asio
        INTERFACE
            _WIN32_WINNT=0x0A00
    )
    target_link_libraries(project_boost_asio
        INTERFACE
            ws2_32
            mswsock
    )
endif()
