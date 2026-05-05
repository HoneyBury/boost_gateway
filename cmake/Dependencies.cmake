include(FetchContent)

if(POLICY CMP0169)
    cmake_policy(SET CMP0169 OLD)
endif()

set(FETCHCONTENT_QUIET OFF)

FetchContent_Declare(
    fmt
    GIT_REPOSITORY https://github.com/fmtlib/fmt.git
    GIT_TAG 11.2.0
)

FetchContent_Declare(
    googletest
    GIT_REPOSITORY https://github.com/google/googletest.git
    GIT_TAG v1.17.0
)

FetchContent_Declare(
    spdlog
    GIT_REPOSITORY https://github.com/gabime/spdlog.git
    GIT_TAG v1.15.3
)

FetchContent_Declare(
    Boost
    URL https://archives.boost.io/release/1.90.0/source/boost_1_90_0.zip
    URL_HASH SHA256=bdc79f179d1a4a60c10fe764172946d0eeafad65e576a8703c4d89d49949973c
    DOWNLOAD_EXTRACT_TIMESTAMP ON
)

set(BUILD_GMOCK OFF CACHE BOOL "" FORCE)
set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)

FetchContent_MakeAvailable(fmt)
FetchContent_MakeAvailable(spdlog)

if(ENABLE_TESTING)
    FetchContent_MakeAvailable(googletest)
endif()

FetchContent_GetProperties(Boost)

if(NOT EXISTS "${boost_SOURCE_DIR}/boost/version.hpp")
    FetchContent_Populate(Boost)
endif()

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
