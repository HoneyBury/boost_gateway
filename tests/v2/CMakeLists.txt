add_executable(project_v2_unit_tests
    unit/actor_runtime_test.cpp
    unit/battle_archive_test.cpp
    unit/battle_actor_test.cpp
    unit/battle_protocol_codec_test.cpp
    unit/battle_wire_parser_test.cpp
    unit/bootstrap_smoke_test.cpp
    unit/connected_flow_test.cpp
    unit/gateway_command_parser_test.cpp
    unit/gateway_bridge_test.cpp
    unit/player_actor_test.cpp
    unit/room_actor_test.cpp
    unit/shadow_bridge_policy_test.cpp
)
target_link_libraries(project_v2_unit_tests
    PRIVATE
        project_v2
        GTest::gtest_main
)
apply_project_warnings(project_v2_unit_tests)

include(GoogleTest)
gtest_discover_tests(project_v2_unit_tests
    DISCOVERY_TIMEOUT ${PROJECT_GTEST_DISCOVERY_TIMEOUT}
)

add_executable(project_v2_integration_tests
    integration/demo_server_smoke_test.cpp
)
target_link_libraries(project_v2_integration_tests
    PRIVATE
        project_v2
        GTest::gtest_main
)
apply_project_warnings(project_v2_integration_tests)

gtest_discover_tests(project_v2_integration_tests
    DISCOVERY_TIMEOUT ${PROJECT_GTEST_DISCOVERY_TIMEOUT}
)
