add_library(project_app STATIC)
target_include_directories(project_app
    PUBLIC
        "${CMAKE_SOURCE_DIR}/include"
)
target_link_libraries(project_app
    PUBLIC
        fmt::fmt
        nlohmann_json::nlohmann_json
        spdlog::spdlog
)
target_compile_definitions(project_app
    PUBLIC
        $<$<CONFIG:Debug>:SPDLOG_ACTIVE_LEVEL=SPDLOG_LEVEL_TRACE>
        $<$<NOT:$<CONFIG:Debug>>:SPDLOG_ACTIVE_LEVEL=SPDLOG_LEVEL_INFO>
)
apply_project_warnings(project_app)

add_library(project_hello STATIC)
target_include_directories(project_hello
    PUBLIC
        "${CMAKE_SOURCE_DIR}/include"
)
target_link_libraries(project_hello
    PUBLIC
        fmt::fmt
)
apply_project_warnings(project_hello)

add_library(project_net STATIC)
target_include_directories(project_net
    PUBLIC
        "${CMAKE_SOURCE_DIR}/include"
)
target_link_libraries(project_net
    PUBLIC
        project_app
        project_boost_asio
)
apply_project_warnings(project_net)

add_library(project_game STATIC)
target_include_directories(project_game
    PUBLIC
        "${CMAKE_SOURCE_DIR}/include"
)
target_link_libraries(project_game
    PUBLIC
        project_net
        nlohmann_json::nlohmann_json
)
apply_project_warnings(project_game)

add_subdirectory(app)
add_subdirectory(hello)
add_subdirectory(net)
add_subdirectory(game)
add_subdirectory(v3)
add_subdirectory(v2)

target_link_libraries(project_game
    PUBLIC
        project_v2
)
