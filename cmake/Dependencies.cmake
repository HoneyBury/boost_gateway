include(FetchContent)

if(POLICY CMP0169)
    cmake_policy(SET CMP0169 OLD)
endif()

set(FETCHCONTENT_QUIET OFF)

# ---------------------------------------------------------------------------
# Local third-party archive directory (for offline / internal-network builds)
# ---------------------------------------------------------------------------
set(THIRD_PARTY_DIR "${CMAKE_CURRENT_SOURCE_DIR}/third_party")
set(THIRD_PARTY_CACHE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/build/_deps")
set(THIRD_PARTY_BINARY_CACHE_DIR "${CMAKE_BINARY_DIR}/_deps")

set(PROJECT_DEPENDENCY_PROVIDER "fetchcontent_fallback")
set(PROJECT_CONAN_DEPENDENCY_MISSING "")
set(PROJECT_CONAN_FIRST_DEPENDENCIES "fmt;spdlog;nlohmann_json;hiredis;Boost")
set(PROJECT_CONAN_BOOST_TARGET "")
set(PROJECT_CONAN_DUAL_TRACK_DEPENDENCIES "OpenSSL")
set(PROJECT_CONAN_EXPERIMENTAL_DEPENDENCIES "protobuf;grpc;sqlite3")

if(BOOST_USE_CONAN_DEPS)
    message(STATUS "BOOST_USE_CONAN_DEPS=ON — probing Conan-provided dependencies before fallback")
endif()

if(EXISTS "${THIRD_PARTY_DIR}")
    message(STATUS "Third-party directory found: ${THIRD_PARTY_DIR}")
else()
    message(STATUS "Third-party directory not found — fetching from remote URLs")
endif()

function(project_try_local_source dep_name local_dir sentinel out_var)
    if(EXISTS "${local_dir}/${sentinel}")
        message(STATUS "Using local source directory: ${dep_name} -> ${local_dir}")
        set(${out_var} "${local_dir}" PARENT_SCOPE)
    endif()
endfunction()

function(project_resolve_source_dir dep_name sentinel out_var)
    set(_resolved "")
    project_try_local_source("${dep_name}" "${THIRD_PARTY_DIR}/${dep_name}-src" "${sentinel}" _resolved)
    if(NOT _resolved)
        project_try_local_source("${dep_name}" "${THIRD_PARTY_CACHE_DIR}/${dep_name}-src" "${sentinel}" _resolved)
    endif()
    if(NOT _resolved)
        project_try_local_source("${dep_name}" "${THIRD_PARTY_BINARY_CACHE_DIR}/${dep_name}-src" "${sentinel}" _resolved)
    endif()
    set(${out_var} "${_resolved}" PARENT_SCOPE)
endfunction()

function(project_record_missing_conan_dependency dep_name)
    if(PROJECT_CONAN_DEPENDENCY_MISSING)
        set(PROJECT_CONAN_DEPENDENCY_MISSING "${PROJECT_CONAN_DEPENDENCY_MISSING},${dep_name}" PARENT_SCOPE)
    else()
        set(PROJECT_CONAN_DEPENDENCY_MISSING "${dep_name}" PARENT_SCOPE)
    endif()
endfunction()

function(project_configure_local_openssl_root candidate_root out_var)
    set(_configured FALSE)
    if(NOT EXISTS "${candidate_root}")
        set(${out_var} FALSE PARENT_SCOPE)
        return()
    endif()

    set(_include_dir "${candidate_root}/include")
    if(NOT EXISTS "${_include_dir}/openssl/ssl.h")
        set(${out_var} FALSE PARENT_SCOPE)
        return()
    endif()

    set(_lib_candidates
        "${candidate_root}/lib"
        "${candidate_root}/lib64"
        "${candidate_root}/lib/x86_64-linux-gnu"
        "${candidate_root}/lib/aarch64-linux-gnu"
        "${candidate_root}/bin"
    )
    find_library(_ssl_library
        NAMES ssl libssl
        PATHS ${_lib_candidates}
        NO_DEFAULT_PATH
    )
    find_library(_crypto_library
        NAMES crypto libcrypto
        PATHS ${_lib_candidates}
        NO_DEFAULT_PATH
    )
    if(_ssl_library AND _crypto_library)
        set(OPENSSL_ROOT_DIR "${candidate_root}" CACHE PATH "OpenSSL root directory" FORCE)
        set(OPENSSL_INCLUDE_DIR "${_include_dir}" CACHE PATH "OpenSSL include directory" FORCE)
        set(OPENSSL_SSL_LIBRARY "${_ssl_library}" CACHE FILEPATH "OpenSSL SSL library" FORCE)
        set(OPENSSL_CRYPTO_LIBRARY "${_crypto_library}" CACHE FILEPATH "OpenSSL Crypto library" FORCE)
        set(_configured TRUE)
    endif()
    unset(_ssl_library CACHE)
    unset(_crypto_library CACHE)
    set(${out_var} "${_configured}" PARENT_SCOPE)
endfunction()

function(project_ensure_openssl)
    if(TARGET OpenSSL::SSL AND TARGET OpenSSL::Crypto)
        message(STATUS "OpenSSL target already available")
        return()
    endif()

    # Conan exports OpenSSL config packages. Try CONFIG first so a Conan
    # toolchain remains authoritative when BOOST_USE_CONAN_DEPS=ON.
    find_package(OpenSSL CONFIG QUIET)
    if(TARGET OpenSSL::SSL AND TARGET OpenSSL::Crypto)
        message(STATUS "Using OpenSSL from CMake config package")
        return()
    endif()

    find_package(OpenSSL QUIET)
    if(TARGET OpenSSL::SSL AND TARGET OpenSSL::Crypto)
        message(STATUS "Using system OpenSSL: ${OPENSSL_VERSION}")
        return()
    endif()

    set(_local_roots
        "${THIRD_PARTY_DIR}/openssl"
        "${THIRD_PARTY_DIR}/openssl-src"
        "${THIRD_PARTY_CACHE_DIR}/openssl-src"
        "${THIRD_PARTY_BINARY_CACHE_DIR}/openssl-src"
    )
    foreach(_local_root IN LISTS _local_roots)
        project_configure_local_openssl_root("${_local_root}" _openssl_local_configured)
        if(_openssl_local_configured)
            message(STATUS "Using local OpenSSL installation: ${_local_root}")
            find_package(OpenSSL REQUIRED)
            return()
        endif()
    endforeach()

    message(FATAL_ERROR
        "OpenSSL was not found. Install libssl-dev/openssl-devel, enable "
        "Conan with BOOST_USE_CONAN_DEPS=ON, set OPENSSL_ROOT_DIR, or provide "
        "a local OpenSSL install under third_party/openssl with include/ and lib/."
    )
endfunction()

if(BOOST_USE_CONAN_DEPS)
    find_package(fmt CONFIG QUIET)
    if(NOT fmt_FOUND)
        project_record_missing_conan_dependency("fmt")
    endif()
    find_package(spdlog CONFIG QUIET)
    if(NOT spdlog_FOUND)
        project_record_missing_conan_dependency("spdlog")
    endif()
    find_package(nlohmann_json CONFIG QUIET)
    if(NOT nlohmann_json_FOUND)
        project_record_missing_conan_dependency("nlohmann_json")
    endif()
    find_package(OpenSSL CONFIG QUIET)
    if(NOT OpenSSL_FOUND)
        project_record_missing_conan_dependency("OpenSSL")
    endif()
    find_package(hiredis CONFIG QUIET)
    if(NOT hiredis_FOUND)
        project_record_missing_conan_dependency("hiredis")
    endif()
    find_package(Boost CONFIG QUIET)
    if(TARGET Boost::headers)
        set(PROJECT_CONAN_BOOST_TARGET "Boost::headers")
    elseif(TARGET boost::headers)
        set(PROJECT_CONAN_BOOST_TARGET "boost::headers")
    elseif(TARGET boost::boost)
        set(PROJECT_CONAN_BOOST_TARGET "boost::boost")
    else()
        project_record_missing_conan_dependency("Boost")
    endif()
    if(ENABLE_TESTING)
        find_package(GTest CONFIG QUIET)
        if(NOT GTest_FOUND)
            project_record_missing_conan_dependency("GTest")
        endif()
    endif()
    if(fmt_FOUND AND spdlog_FOUND AND nlohmann_json_FOUND AND OpenSSL_FOUND AND hiredis_FOUND AND PROJECT_CONAN_BOOST_TARGET)
        set(PROJECT_DEPENDENCY_PROVIDER "conan")
        message(STATUS "Using Conan-provided dependency graph")
        message(STATUS "Conan-first dependencies: ${PROJECT_CONAN_FIRST_DEPENDENCIES}")
        message(STATUS "Conan dual-track dependencies: ${PROJECT_CONAN_DUAL_TRACK_DEPENDENCIES}")
        message(STATUS "Conan experimental dependencies: ${PROJECT_CONAN_EXPERIMENTAL_DEPENDENCIES}")
    else()
        message(WARNING "BOOST_USE_CONAN_DEPS=ON but not all Conan packages were found "
                        "(${PROJECT_CONAN_DEPENDENCY_MISSING}) — falling back to FetchContent/third_party")
    endif()
endif()

if(NOT PROJECT_DEPENDENCY_PROVIDER STREQUAL "conan")
    # ---------------------------------------------------------------------------
    # fmt 11.2.0
    # ---------------------------------------------------------------------------
    project_resolve_source_dir("fmt" "CMakeLists.txt" FMT_SOURCE_DIR)
    if(FMT_SOURCE_DIR)
        FetchContent_Declare(fmt
            SOURCE_DIR "${FMT_SOURCE_DIR}"
        )
    elseif(EXISTS "${THIRD_PARTY_DIR}/fmt-11.2.0.tar.gz")
        message(STATUS "Using local archive: fmt-11.2.0.tar.gz")
        FetchContent_Declare(fmt
            URL "${THIRD_PARTY_DIR}/fmt-11.2.0.tar.gz"
            DOWNLOAD_EXTRACT_TIMESTAMP ON
        )
    else()
        FetchContent_Declare(fmt
            GIT_REPOSITORY https://github.com/fmtlib/fmt.git
            GIT_TAG 11.2.0
            GIT_SHALLOW TRUE
        )
    endif()

    # ---------------------------------------------------------------------------
    # googletest v1.15.0
    # ---------------------------------------------------------------------------
    project_resolve_source_dir("googletest" "CMakeLists.txt" GOOGLETEST_SOURCE_DIR)
    if(GOOGLETEST_SOURCE_DIR)
        FetchContent_Declare(googletest
            SOURCE_DIR "${GOOGLETEST_SOURCE_DIR}"
        )
    elseif(EXISTS "${THIRD_PARTY_DIR}/googletest-1.15.0.tar.gz")
        message(STATUS "Using local archive: googletest-1.15.0.tar.gz")
        FetchContent_Declare(googletest
            URL "${THIRD_PARTY_DIR}/googletest-1.15.0.tar.gz"
            DOWNLOAD_EXTRACT_TIMESTAMP ON
        )
    else()
        FetchContent_Declare(googletest
            GIT_REPOSITORY https://github.com/google/googletest.git
            GIT_TAG v1.15.0
            GIT_SHALLOW TRUE
        )
    endif()

    # ---------------------------------------------------------------------------
    # spdlog v1.15.3
    # ---------------------------------------------------------------------------
    project_resolve_source_dir("spdlog" "CMakeLists.txt" SPDLOG_SOURCE_DIR)
    if(SPDLOG_SOURCE_DIR)
        FetchContent_Declare(spdlog
            SOURCE_DIR "${SPDLOG_SOURCE_DIR}"
        )
    elseif(EXISTS "${THIRD_PARTY_DIR}/spdlog-1.15.3.tar.gz")
        message(STATUS "Using local archive: spdlog-1.15.3.tar.gz")
        FetchContent_Declare(spdlog
            URL "${THIRD_PARTY_DIR}/spdlog-1.15.3.tar.gz"
            DOWNLOAD_EXTRACT_TIMESTAMP ON
        )
    else()
        FetchContent_Declare(spdlog
            GIT_REPOSITORY https://github.com/gabime/spdlog.git
            GIT_TAG v1.15.3
            GIT_SHALLOW TRUE
        )
    endif()

    # ---------------------------------------------------------------------------
    # nlohmann_json v3.12.0
    # ---------------------------------------------------------------------------
    project_resolve_source_dir("nlohmann_json" "CMakeLists.txt" NLOHMANN_JSON_SOURCE_DIR)
    if(NLOHMANN_JSON_SOURCE_DIR)
        FetchContent_Declare(nlohmann_json
            SOURCE_DIR "${NLOHMANN_JSON_SOURCE_DIR}"
        )
    elseif(EXISTS "${THIRD_PARTY_DIR}/nlohmann_json-3.12.0.tar.gz")
        message(STATUS "Using local archive: nlohmann_json-3.12.0.tar.gz")
        FetchContent_Declare(nlohmann_json
            URL "${THIRD_PARTY_DIR}/nlohmann_json-3.12.0.tar.gz"
            DOWNLOAD_EXTRACT_TIMESTAMP ON
        )
    else()
        FetchContent_Declare(nlohmann_json
            GIT_REPOSITORY https://github.com/nlohmann/json.git
            GIT_TAG v3.12.0
            GIT_SHALLOW TRUE
        )
    endif()

    # ---------------------------------------------------------------------------
    # OpenSSL (v3.0+ required for TLS/mTLS)
    # ---------------------------------------------------------------------------
    project_ensure_openssl()
    message(STATUS "OpenSSL found: ${OPENSSL_VERSION}")
    message(STATUS "  include: ${OPENSSL_INCLUDE_DIR}")
    message(STATUS "  ssl:     ${OPENSSL_SSL_LIBRARY}")
    message(STATUS "  crypto:  ${OPENSSL_CRYPTO_LIBRARY}")

    # ---------------------------------------------------------------------------
    # Boost 1.86.0
    # ---------------------------------------------------------------------------
    project_resolve_source_dir("boost" "boost/version.hpp" BOOST_SOURCE_CACHE_DIR)
    if(BOOST_SOURCE_CACHE_DIR)
        FetchContent_Declare(Boost
            SOURCE_DIR "${BOOST_SOURCE_CACHE_DIR}"
        )
    elseif(EXISTS "/usr/include/boost/version.hpp")
        message(STATUS "Using system Boost headers: /usr/include")
        FetchContent_Declare(Boost
            SOURCE_DIR "/usr/include"
        )
    elseif(EXISTS "${THIRD_PARTY_DIR}/boost_1_86_0.zip")
        message(STATUS "Using local archive: boost_1_86_0.zip")
        FetchContent_Declare(Boost
            URL "${THIRD_PARTY_DIR}/boost_1_86_0.zip"
            URL_HASH SHA256=cd20a5694e753683e1dc2ee10e2d1bb11704e65893ebcc6ced234ba68e5d8646
            DOWNLOAD_EXTRACT_TIMESTAMP ON
        )
    else()
        FetchContent_Declare(Boost
            URL https://archives.boost.io/release/1.86.0/source/boost_1_86_0.zip
            URL_HASH SHA256=cd20a5694e753683e1dc2ee10e2d1bb11704e65893ebcc6ced234ba68e5d8646
            DOWNLOAD_EXTRACT_TIMESTAMP ON
        )
    endif()

    # ---------------------------------------------------------------------------
    # hiredis v1.2.0 (Redis C client)
    # ---------------------------------------------------------------------------
    project_resolve_source_dir("hiredis" "CMakeLists.txt" HIREDIS_SOURCE_DIR)
    if(HIREDIS_SOURCE_DIR)
        FetchContent_Declare(hiredis
            SOURCE_DIR "${HIREDIS_SOURCE_DIR}"
        )
    elseif(EXISTS "${THIRD_PARTY_DIR}/hiredis-1.2.0.tar.gz")
        message(STATUS "Using local archive: hiredis-1.2.0.tar.gz")
        FetchContent_Declare(hiredis
            URL "${THIRD_PARTY_DIR}/hiredis-1.2.0.tar.gz"
            DOWNLOAD_EXTRACT_TIMESTAMP ON
        )
    else()
        FetchContent_Declare(hiredis
            GIT_REPOSITORY https://github.com/redis/hiredis.git
            GIT_TAG v1.2.0
            GIT_SHALLOW TRUE
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
endif()

# ---------------------------------------------------------------------------
# Boost.Asio header-only interface target
# ---------------------------------------------------------------------------
add_library(project_boost_asio INTERFACE)

if(PROJECT_DEPENDENCY_PROVIDER STREQUAL "conan")
    target_link_libraries(project_boost_asio
        INTERFACE
            ${PROJECT_CONAN_BOOST_TARGET}
    )
else()
    target_include_directories(project_boost_asio
        INTERFACE
            "${boost_SOURCE_DIR}"
    )

    target_compile_definitions(project_boost_asio
        INTERFACE
            BOOST_ALL_NO_LIB
            BOOST_ERROR_CODE_HEADER_ONLY
    )
endif()
