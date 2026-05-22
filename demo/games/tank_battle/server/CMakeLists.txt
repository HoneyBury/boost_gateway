# Tank battle demo server library
add_library(tank_battle_server STATIC
    tank_simulation/tank_world.cpp
    tank_plugin/tank_plugin.cpp
)
target_include_directories(tank_battle_server
    PUBLIC
        "${CMAKE_CURRENT_SOURCE_DIR}/../.."   # for demo/games/ include path
        "${CMAKE_SOURCE_DIR}/include"
)
target_link_libraries(tank_battle_server
    PUBLIC
        project_v2
        nlohmann_json::nlohmann_json
)

# Tank battle demo executable
add_executable(tank_battle_demo
    tank_battle_main.cpp
)
target_link_libraries(tank_battle_demo
    PRIVATE
        tank_battle_server
        project_v2
)
target_include_directories(tank_battle_demo
    PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/../.."
)

if(COMMAND boost_gateway_stage_runtime_dlls)
    boost_gateway_stage_runtime_dlls(tank_battle_demo)
endif()
