cmake_minimum_required(VERSION 3.21)

project(cpp_scaffold
    VERSION 0.1.0
    DESCRIPTION "A modern C++ starter scaffold"
    LANGUAGES CXX
)

include(CTest)

option(ENABLE_WARNINGS "Enable compiler warnings" ON)
option(ENABLE_TESTING "Build tests" ON)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

include(cmake/Dependencies.cmake)
include(cmake/CompilerWarnings.cmake)

add_library(hello_lib
    src/hello.cpp
    src/logging.cpp
    src/session.cpp
)

target_include_directories(hello_lib
    PUBLIC
        "${CMAKE_CURRENT_SOURCE_DIR}/include"
)

target_link_libraries(hello_lib
    PUBLIC
        project_boost_asio
        fmt::fmt
        spdlog::spdlog
)

target_compile_definitions(hello_lib
    PUBLIC
        $<$<CONFIG:Debug>:SPDLOG_ACTIVE_LEVEL=SPDLOG_LEVEL_TRACE>
        $<$<NOT:$<CONFIG:Debug>>:SPDLOG_ACTIVE_LEVEL=SPDLOG_LEVEL_INFO>
)

apply_project_warnings(hello_lib)

add_executable(hello_world
    src/main.cpp
)

target_link_libraries(hello_world
    PRIVATE
        hello_lib
)

apply_project_warnings(hello_world)

add_executable(echo_server
    src/echo_server.cpp
)

target_link_libraries(echo_server
    PRIVATE
        hello_lib
        project_boost_asio
)

apply_project_warnings(echo_server)

add_executable(echo_client
    src/echo_client.cpp
)

target_link_libraries(echo_client
    PRIVATE
        hello_lib
        project_boost_asio
)

apply_project_warnings(echo_client)

if(ENABLE_TESTING)
    add_subdirectory(tests)
endif()
