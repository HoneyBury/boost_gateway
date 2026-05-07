cmake_minimum_required(VERSION 3.21)

project(boost_gateway
    VERSION 1.2.4
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

add_subdirectory(src)
add_subdirectory(examples)

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
    docs/runtime-playbook.md
    docs/development-priority.md
    docs/v1-structure-upgrade-decision.md
    docs/release-process.md
    DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}/docs"
)

install(FILES
    docs/releases/v1.2.4.md
    DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}/docs/releases"
)
