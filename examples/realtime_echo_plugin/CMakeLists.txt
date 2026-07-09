# EchoRealtimePlugin Demo
#
# A minimal demo that verifies the InstancePlugin SPI contract.
# Built only when explicitly enabled (not part of default build).
#
# Enable with:
#   cmake -DBOOST_BUILD_ECHO_PLUGIN_DEMO=ON

add_executable(realtime_echo_plugin
    echo_plugin.cpp
    main.cpp
)
target_include_directories(realtime_echo_plugin
    PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}"
)
target_link_libraries(realtime_echo_plugin
    PRIVATE
        project_v2
        nlohmann_json::nlohmann_json
)
apply_project_warnings(realtime_echo_plugin)
