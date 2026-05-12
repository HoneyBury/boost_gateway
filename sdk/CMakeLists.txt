# BoostGateway Client SDK v4.0.0 — standalone or subdirectory
option(BOOST_GATEWAY_SDK_STANDALONE "Build as standalone project" OFF)

if(BOOST_GATEWAY_SDK_STANDALONE)
    cmake_minimum_required(VERSION 3.21)
    project(boost_gateway_sdk VERSION 4.0.0 LANGUAGES CXX)
    set(CMAKE_CXX_STANDARD 20)
    set(CMAKE_CXX_STANDARD_REQUIRED ON)
    set(CMAKE_CXX_EXTENSIONS OFF)
endif()

# ── Version header ───────────────────────────────────────────────────
configure_file(
    ${CMAKE_CURRENT_SOURCE_DIR}/include/boost_gateway/sdk/version.h.in
    ${CMAKE_CURRENT_BINARY_DIR}/include/boost_gateway/sdk/version.h
)

# ── Library (standalone — no server deps) ────────────────────────────
add_library(boost_gateway_sdk SHARED
    src/client.cpp
    src/c_api.cpp
)
target_compile_definitions(boost_gateway_sdk PRIVATE BOOST_GATEWAY_SDK_EXPORTS)

target_include_directories(boost_gateway_sdk
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<BUILD_INTERFACE:${CMAKE_CURRENT_BINARY_DIR}/include>
)

# nlohmann_json
if(NOT TARGET nlohmann_json::nlohmann_json)
    include(FetchContent)
    FetchContent_Declare(nlohmann_json
        GIT_REPOSITORY https://github.com/nlohmann/json.git
        GIT_TAG v3.12.0
    )
    FetchContent_MakeAvailable(nlohmann_json)
endif()

target_link_libraries(boost_gateway_sdk PUBLIC nlohmann_json::nlohmann_json)

# Boost.Asio (header-only)
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

target_include_directories(boost_gateway_sdk PUBLIC ${boost_SOURCE_DIR})
target_compile_definitions(boost_gateway_sdk PUBLIC
    BOOST_ALL_NO_LIB BOOST_ERROR_CODE_HEADER_ONLY)
if(WIN32)
    target_compile_definitions(boost_gateway_sdk PUBLIC _WIN32_WINNT=0x0A00)
endif()

add_library(boost_gateway::sdk ALIAS boost_gateway_sdk)

# ── Install (standalone mode only) ────────────────────────────────────
if(BOOST_GATEWAY_SDK_STANDALONE)
    include(CMakePackageConfigHelpers)
    include(GNUInstallDirs)
    install(TARGETS boost_gateway_sdk EXPORT boost_gateway_sdk-targets
        ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})
    install(DIRECTORY include/ DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})
    install(FILES ${CMAKE_CURRENT_BINARY_DIR}/include/boost_gateway/sdk/version.h
        DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/boost_gateway/sdk)
    install(EXPORT boost_gateway_sdk-targets
        FILE boost_gateway_sdk-targets.cmake
        NAMESPACE boost_gateway::
        DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/boost_gateway_sdk)
    configure_package_config_file(
        ${CMAKE_CURRENT_SOURCE_DIR}/cmake/boost_gateway_sdk-config.cmake.in
        ${CMAKE_CURRENT_BINARY_DIR}/boost_gateway_sdk-config.cmake
        INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/boost_gateway_sdk)
    install(FILES ${CMAKE_CURRENT_BINARY_DIR}/boost_gateway_sdk-config.cmake
        DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/boost_gateway_sdk)
endif()

# ── Tests ────────────────────────────────────────────────────────────
add_subdirectory(tests)

# ── Examples ─────────────────────────────────────────────────────────
add_subdirectory(examples)
