cmake_minimum_required(VERSION 3.21)

project(boost_gateway
    VERSION 3.5.1
    DESCRIPTION "Enterprise-grade C++20 realtime service framework"
    LANGUAGES CXX
)

include(CTest)
include(GNUInstallDirs)

option(ENABLE_WARNINGS "Enable compiler warnings" ON)
option(ENABLE_TESTING "Build tests" ON)
option(BOOST_BUILD_GRPC "Build gRPC support (requires protoc and grpc_cpp_plugin)" OFF)
option(BOOST_BUILD_SQLITE "Build SQLite-backed storage (requires sqlite3)" OFF)
set(BOOST_DEPENDENCY_PROVIDER "conan" CACHE STRING "Dependency provider: conan or fetchcontent")
set_property(CACHE BOOST_DEPENDENCY_PROVIDER PROPERTY STRINGS conan fetchcontent)
if(NOT BOOST_DEPENDENCY_PROVIDER STREQUAL "conan" AND
   NOT BOOST_DEPENDENCY_PROVIDER STREQUAL "fetchcontent")
  message(FATAL_ERROR
    "Unsupported BOOST_DEPENDENCY_PROVIDER=${BOOST_DEPENDENCY_PROVIDER}; "
    "expected conan or fetchcontent")
endif()

# Batch B: Auto-detect gRPC if available (does NOT enable the gRPC listener at runtime)
find_package(gRPC QUIET)
if(gRPC_FOUND AND NOT BOOST_BUILD_GRPC)
  message(STATUS "gRPC found — set BOOST_BUILD_GRPC=ON to compile gRPC gateway")
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

include(cmake/Dependencies.cmake)
include(cmake/CompilerWarnings.cmake)

add_subdirectory(src)
add_subdirectory(proto)   # Proto/gRPC code generation (no-op when BOOST_BUILD_GRPC=OFF)
add_subdirectory(sdk)
add_subdirectory(examples)
add_subdirectory(demo/games)

if(ENABLE_TESTING)
    add_subdirectory(tests)
endif()

install(DIRECTORY include/
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}"
)

install(TARGETS
    v2_gateway_demo
    v2_battle_backend
    v2_room_backend
    v2_login_backend
    v2_match_backend
    v2_leaderboard_backend
    v2_gateway_pressure
    v2_arch_benchmark
    sdk_echo_client
    sdk_full_flow_client
    example_hello_world
    echo_client
    RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
)

install(DIRECTORY config/
    DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}/config"
)

install(FILES
    README.md
    CHANGELOG.md
    LICENSE
    DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}"
)

# Conditionally install optional demo/example targets
if(TARGET tank_battle_demo)
    install(TARGETS tank_battle_demo RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}")
endif()
if(TARGET realtime_echo_plugin)
    install(TARGETS realtime_echo_plugin RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}")
endif()

install(FILES
    docs/README.md
    docs/current-state.md
    docs/project-blueprint.md
    docs/mainline-execution-plan.md
    docs/v3.5.x-maintenance-plan.md
    docs/architecture-overview.md
    docs/release-governance.md
    docs/performance-baseline.md
    docs/fixed-runner-playbook.md
    docs/tls-mtls-runbook.md
    docs/ONBOARDING.md
    DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}/docs"
)

install(DIRECTORY docs/deployment/
    DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}/docs/deployment"
)

install(DIRECTORY docs/production/
    DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}/docs/production"
)

install(DIRECTORY docs/legacy/
    DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}/docs/legacy"
)

install(DIRECTORY docs/archive/
    DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}/docs/archive"
)

install(FILES
    deploy/README.md
    DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}/deploy"
)

install(FILES
    deploy/systemd/boost-gateway.service
    deploy/systemd/boost-login-backend.service
    deploy/systemd/boost-room-backend.service
    deploy/systemd/boost-battle-backend.service
    deploy/systemd/boost-match-backend.service
    deploy/systemd/boost-leaderboard-backend.service
    DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}/deploy/systemd"
)
