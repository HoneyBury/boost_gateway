cmake_minimum_required(VERSION 3.21)

project(boost_gateway
    VERSION 3.3.2
    DESCRIPTION "A high-performance C++ game server framework"
    LANGUAGES CXX
)

include(CTest)
include(GNUInstallDirs)

option(ENABLE_WARNINGS "Enable compiler warnings" ON)
option(ENABLE_TESTING "Build tests" ON)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

include(cmake/Dependencies.cmake)
include(cmake/CompilerWarnings.cmake)

function(boost_gateway_stage_runtime_dlls target_name)
    if(NOT WIN32)
        return()
    endif()
    if(NOT TARGET "${target_name}")
        return()
    endif()
    add_custom_command(TARGET "${target_name}" POST_BUILD
        COMMAND "${CMAKE_COMMAND}" -E copy -t "$<TARGET_FILE_DIR:${target_name}>"
                $<TARGET_RUNTIME_DLLS:${target_name}>
        COMMAND_EXPAND_LISTS
    )
endfunction()

add_subdirectory(src)
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
    example_hello_world
    echo_server
    echo_client
    gateway_pressure
    login_server
    room_server
    battle_server
    login_demo
    room_demo
    battle_demo
    admin_demo
    v2_gateway_demo
    v2_battle_backend
    v2_room_backend
    v2_login_backend
    v2_match_backend
    v2_leaderboard_backend
    v2_gateway_pressure
    v2_arch_benchmark
    RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
)

install(DIRECTORY config/
    DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}/config"
)

install(FILES
    README.md
    CHANGELOG.md
    DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}"
)

install(FILES
    docs/README.md
    docs/current-state.md
    docs/reliability-matrix.md
    docs/runtime-playbook.md
    docs/development-priority.md
    docs/v1-structure-upgrade-decision.md
    docs/release-process.md
    docs/v2-roadmap.md
    docs/v2-design.md
    docs/v2-startup-checklist.md
    docs/v2-next-phases.md
    docs/v2-runtime.md
    docs/v2-protocol-bridge.md
    docs/v2-player-lifecycle.md
    docs/v2-room-lifecycle.md
    docs/v2-gateway-cutover-criteria.md
    docs/v2-service-split-plan.md
    docs/v2-enterprise-roadmap.md
    docs/architecture-acceptance-criteria.md
    docs/architecture-baseline-r2.md
    docs/actor-concurrency-model-r3.md
    docs/communication-contract-r4.md
    docs/performance-baseline.md
    docs/v3-production-readiness-plan.md
    docs/v3-release-checklist.md
    DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}/docs"
)

install(FILES
    docs/releases/v1.2.5.md
    DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}/docs/releases"
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
