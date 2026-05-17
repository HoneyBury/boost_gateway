# BoostGateway Client SDK v4.1.0 — standalone or subdirectory
option(BOOST_GATEWAY_SDK_STANDALONE "Build as standalone project" OFF)

# SDK version — set explicitly so version.h.in populates correctly even in
# subdirectory mode (where project() is skipped and @BoostGatewaySdk_VERSION_*@
# generator variables don't exist).
set(BOOST_GATEWAY_SDK_VERSION "4.1.0")
set(BOOST_GATEWAY_SDK_VERSION_MAJOR 4)
set(BOOST_GATEWAY_SDK_VERSION_MINOR 1)
set(BOOST_GATEWAY_SDK_VERSION_PATCH 0)

if(BOOST_GATEWAY_SDK_STANDALONE)
    cmake_minimum_required(VERSION 3.21)
    project(boost_gateway_sdk VERSION 4.1.0 LANGUAGES CXX)
    set(CMAKE_CXX_STANDARD 20)
    set(CMAKE_CXX_STANDARD_REQUIRED ON)
    set(CMAKE_CXX_EXTENSIONS OFF)
else()
    # Mirror the project()-level generator variables for configure_file().
    set(BoostGatewaySdk_VERSION_MAJOR ${BOOST_GATEWAY_SDK_VERSION_MAJOR})
    set(BoostGatewaySdk_VERSION_MINOR ${BOOST_GATEWAY_SDK_VERSION_MINOR})
    set(BoostGatewaySdk_VERSION_PATCH ${BOOST_GATEWAY_SDK_VERSION_PATCH})
    set(BoostGatewaySdk_VERSION "${BOOST_GATEWAY_SDK_VERSION}")
endif()

# ── Dependencies ────────────────────────────────────────────────────
if(NOT TARGET nlohmann_json::nlohmann_json)
    include(FetchContent)
    FetchContent_Declare(nlohmann_json
        GIT_REPOSITORY https://github.com/nlohmann/json.git
        GIT_TAG v3.12.0
    )
    FetchContent_MakeAvailable(nlohmann_json)
endif()

if(NOT DEFINED boost_SOURCE_DIR)
    include(FetchContent)
    FetchContent_Declare(Boost
        URL https://archives.boost.io/release/1.90.0/source/boost_1_90_0.zip
        URL_HASH SHA256=bdc79f179d1a4a60c10fe764172946d0eeafad65e576a8703c4d89d49949973c
    )
    FetchContent_GetProperties(Boost)
    if(NOT boost_POPULATED)
        FetchContent_Populate(Boost)
    endif()
endif()

# ── Version header ───────────────────────────────────────────────────
configure_file(
    ${CMAKE_CURRENT_SOURCE_DIR}/include/boost_gateway/sdk/version.h.in
    ${CMAKE_CURRENT_BINARY_DIR}/include/boost_gateway/sdk/version.h
)

# ── Core library (static, linked by C++ clients) ─────────────────────
add_library(boost_gateway_sdk STATIC
    src/client.cpp
    src/transport/tcp_transport.cpp
    src/transport/connection_pool.cpp
)

# ── C API shared library (for Python/C# via ctypes/DllImport) ────────
# Compile client.cpp directly here (not linked from static lib) so
# -fPIC is applied correctly on Linux for shared library compatibility.
add_library(boost_gateway_sdk_dll SHARED
    src/client.cpp
    src/c_api.cpp
)
set_target_properties(boost_gateway_sdk_dll PROPERTIES
    OUTPUT_NAME boost_gateway_sdk
)
target_compile_definitions(boost_gateway_sdk_dll PRIVATE BOOST_GATEWAY_SDK_EXPORTS)
target_link_libraries(boost_gateway_sdk_dll
    PRIVATE
        nlohmann_json::nlohmann_json
)
target_include_directories(boost_gateway_sdk_dll
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/include
        ${CMAKE_CURRENT_BINARY_DIR}/include
        ${boost_SOURCE_DIR}
)
target_compile_definitions(boost_gateway_sdk_dll
    PRIVATE
        BOOST_ALL_NO_LIB
        BOOST_ERROR_CODE_HEADER_ONLY
)
if(WIN32)
    target_compile_definitions(boost_gateway_sdk_dll PRIVATE _WIN32_WINNT=0x0A00)
endif()

target_include_directories(boost_gateway_sdk
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<BUILD_INTERFACE:${CMAKE_CURRENT_BINARY_DIR}/include>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}/boost_gateway>
)

target_link_libraries(boost_gateway_sdk PUBLIC nlohmann_json::nlohmann_json)

target_include_directories(boost_gateway_sdk PUBLIC
    $<BUILD_INTERFACE:${boost_SOURCE_DIR}>)
target_compile_definitions(boost_gateway_sdk PUBLIC
    BOOST_ALL_NO_LIB BOOST_ERROR_CODE_HEADER_ONLY)
if(WIN32)
    target_compile_definitions(boost_gateway_sdk PUBLIC _WIN32_WINNT=0x0A00)
endif()

add_library(boost_gateway::sdk ALIAS boost_gateway_sdk)

# ── Install ──────────────────────────────────────────────────────────
include(CMakePackageConfigHelpers)
include(GNUInstallDirs)

# nlohmann_json — header-only dependency, co-installed so the export set is self-contained.
install(TARGETS nlohmann_json EXPORT boost_gateway_sdk-targets)
install(DIRECTORY "${nlohmann_json_SOURCE_DIR}/include/nlohmann"
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/nlohmann)

# Library
install(TARGETS boost_gateway_sdk EXPORT boost_gateway_sdk-targets
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})

install(TARGETS boost_gateway_sdk_dll
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})

# Headers
install(DIRECTORY include/ DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/include/boost_gateway/sdk/version.h
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/boost_gateway/sdk)

# CMake package config
install(EXPORT boost_gateway_sdk-targets
    FILE boost_gateway_sdk-targets.cmake
    NAMESPACE boost_gateway::
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/boost_gateway_sdk)

configure_package_config_file(
    ${CMAKE_CURRENT_SOURCE_DIR}/cmake/boost_gateway_sdk-config.cmake.in
    ${CMAKE_CURRENT_BINARY_DIR}/boost_gateway_sdk-config.cmake
    INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/boost_gateway_sdk)

write_basic_package_version_file(
    ${CMAKE_CURRENT_BINARY_DIR}/boost_gateway_sdk-config-version.cmake
    VERSION ${BOOST_GATEWAY_SDK_VERSION}
    COMPATIBILITY SameMajorVersion)

install(FILES
    ${CMAKE_CURRENT_BINARY_DIR}/boost_gateway_sdk-config.cmake
    ${CMAKE_CURRENT_BINARY_DIR}/boost_gateway_sdk-config-version.cmake
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/boost_gateway_sdk)

# ── Tests ────────────────────────────────────────────────────────────
add_subdirectory(tests)

# ── Examples ─────────────────────────────────────────────────────────
add_subdirectory(examples)
