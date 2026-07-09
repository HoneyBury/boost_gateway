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

if(PROJECT_DEPENDENCY_PROVIDER STREQUAL "conan")
    set(BOOST_GATEWAY_SDK_DEPENDENCY_PROVIDER "conan")
else()
    set(BOOST_GATEWAY_SDK_DEPENDENCY_PROVIDER "fetchcontent_fallback")
endif()

# ── Dependencies ────────────────────────────────────────────────────
if(NOT TARGET nlohmann_json::nlohmann_json AND NOT BOOST_USE_CONAN_DEPS)
    include(FetchContent)
    FetchContent_Declare(nlohmann_json
        GIT_REPOSITORY https://github.com/nlohmann/json.git
        GIT_TAG v3.12.0
    )
    FetchContent_MakeAvailable(nlohmann_json)
endif()

set(BOOST_GATEWAY_SDK_JSON_INCLUDE_ROOT "")
foreach(_boost_gateway_sdk_json_candidate IN LISTS nlohmann_json_INCLUDE_DIRS nlohmann_json_INCLUDE_DIR)
    if(EXISTS "${_boost_gateway_sdk_json_candidate}/nlohmann/json.hpp")
        set(BOOST_GATEWAY_SDK_JSON_INCLUDE_ROOT "${_boost_gateway_sdk_json_candidate}")
        break()
    endif()
endforeach()
if(NOT BOOST_GATEWAY_SDK_JSON_INCLUDE_ROOT AND DEFINED nlohmann_json_SOURCE_DIR AND EXISTS "${nlohmann_json_SOURCE_DIR}/include/nlohmann/json.hpp")
    set(BOOST_GATEWAY_SDK_JSON_INCLUDE_ROOT "${nlohmann_json_SOURCE_DIR}/include")
elseif(NOT BOOST_GATEWAY_SDK_JSON_INCLUDE_ROOT AND TARGET nlohmann_json::nlohmann_json)
    get_target_property(_boost_gateway_sdk_json_include_dirs nlohmann_json::nlohmann_json INTERFACE_INCLUDE_DIRECTORIES)
    foreach(_boost_gateway_sdk_json_dir IN LISTS _boost_gateway_sdk_json_include_dirs)
        if(EXISTS "${_boost_gateway_sdk_json_dir}/nlohmann/json.hpp")
            set(BOOST_GATEWAY_SDK_JSON_INCLUDE_ROOT "${_boost_gateway_sdk_json_dir}")
            break()
        endif()
    endforeach()
endif()

if(NOT DEFINED boost_SOURCE_DIR AND NOT TARGET boost::headers AND NOT BOOST_USE_CONAN_DEPS)
    include(FetchContent)
    FetchContent_Declare(Boost
        URL https://archives.boost.io/release/1.86.0/source/boost_1_86_0.zip
        URL_HASH SHA256=cd20a5694e753683e1dc2ee10e2d1bb11704e65893ebcc6ced234ba68e5d8646
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
)
if(TARGET project_boost_asio)
    target_link_libraries(boost_gateway_sdk_dll PRIVATE project_boost_asio)
elseif(TARGET boost::headers)
    target_link_libraries(boost_gateway_sdk_dll PRIVATE boost::headers)
else()
    target_include_directories(boost_gateway_sdk_dll PRIVATE ${boost_SOURCE_DIR})
    target_compile_definitions(boost_gateway_sdk_dll
        PRIVATE
            BOOST_ALL_NO_LIB
            BOOST_ERROR_CODE_HEADER_ONLY
    )
endif()

target_include_directories(boost_gateway_sdk
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<BUILD_INTERFACE:${CMAKE_CURRENT_BINARY_DIR}/include>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}/boost_gateway>
)

if(TARGET project_boost_asio)
    target_link_libraries(boost_gateway_sdk PRIVATE $<BUILD_INTERFACE:project_boost_asio>)
    target_compile_definitions(boost_gateway_sdk PUBLIC
        BOOST_ALL_NO_LIB BOOST_ERROR_CODE_HEADER_ONLY)
elseif(TARGET boost::headers)
    target_link_libraries(boost_gateway_sdk PUBLIC boost::headers)
else()
    target_include_directories(boost_gateway_sdk PUBLIC
        $<BUILD_INTERFACE:${boost_SOURCE_DIR}>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>)
    target_compile_definitions(boost_gateway_sdk PUBLIC
        BOOST_ALL_NO_LIB BOOST_ERROR_CODE_HEADER_ONLY)
endif()

add_library(boost_gateway::sdk ALIAS boost_gateway_sdk)

# ── Install ──────────────────────────────────────────────────────────
include(CMakePackageConfigHelpers)
include(GNUInstallDirs)

# nlohmann_json — header-only dependency, co-installed so the SDK package is self-contained.
if(BOOST_GATEWAY_SDK_JSON_INCLUDE_ROOT)
    install(DIRECTORY "${BOOST_GATEWAY_SDK_JSON_INCLUDE_ROOT}/nlohmann"
        DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/nlohmann)
else()
    message(WARNING "Could not resolve nlohmann_json include root for SDK install packaging")
endif()

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
if(ENABLE_TESTING OR BOOST_GATEWAY_SDK_STANDALONE)
    add_subdirectory(tests)
endif()

# ── Examples ─────────────────────────────────────────────────────────
add_subdirectory(examples)
