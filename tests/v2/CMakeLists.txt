add_executable(project_v2_unit_tests
    unit/actor_runtime_test.cpp
    unit/actor_snapshot_test.cpp
    unit/lru_cache_test.cpp
    unit/battle_actor_test.cpp
    unit/battle_runtime_world_test.cpp
    unit/battle_protocol_codec_test.cpp
    unit/battle_wire_parser_test.cpp
    unit/bootstrap_smoke_test.cpp
    unit/circuit_breaker_test.cpp
    unit/config_watcher_test.cpp
    unit/connected_flow_test.cpp
    unit/latency_histogram_test.cpp
    unit/throughput_tracker_test.cpp
    unit/data_layer_test.cpp
    unit/battle_determinism_test.cpp
    unit/battle_authoritative_test.cpp
    unit/battle_lifecycle_test.cpp
    unit/ecs_world_test.cpp
    unit/fault_injector_test.cpp
    unit/feature_flags_test.cpp
    unit/gateway_command_parser_test.cpp
    unit/gateway_bridge_test.cpp
    unit/gateway_response_parser_test.cpp
    unit/io_engine_test.cpp
    unit/json_file_store_test.cpp
    unit/player_actor_test.cpp
    unit/room_actor_test.cpp
    unit/service_boundary_test.cpp
    unit/shadow_bridge_policy_test.cpp
    unit/service_registry_test.cpp
    unit/write_behind_store_test.cpp
    unit/cached_data_store_test.cpp
    unit/aoi_system_test.cpp
    unit/aoi_spatial_grid_test.cpp
    unit/arena_test.cpp
    unit/cache_line_test.cpp
    unit/trace_context_test.cpp
    unit/diagnostics_manager_test.cpp
    unit/health_check_test.cpp
    unit/object_pool_test.cpp
    unit/rate_limiter_test.cpp
    unit/jwt_validator_test.cpp
    unit/anti_cheat_test.cpp
    unit/schema_validator_test.cpp
    unit/matchmaking_test.cpp
    unit/leaderboard_test.cpp
    unit/battle_systems_test.cpp
    unit/projectile_system_test.cpp
    unit/broadcast_test.cpp
    unit/battle_replay_test.cpp
    unit/reconnect_test.cpp
    unit/redis_persistence_test.cpp
    unit/runtime_metrics_exporter_test.cpp
    unit/proto_schema_test.cpp
    unit/cluster_router_test.cpp
    unit/remote_actor_test.cpp
    unit/raft_test.cpp
    unit/otel_persistence_test.cpp
    unit/k8s_operator_test.cpp
    unit/operator_status_test.cpp
    unit/v3_integration_test.cpp
    unit/sdk_protocol_test.cpp
    unit/error_paths_test.cpp
    unit/identity_register_test.cpp
    unit/room_lobby_test.cpp
    unit/realtime/instance_runtime_test.cpp
    unit/realtime/spi_compliance_test.cpp
    unit/parallel_system_executor_test.cpp
    unit/tank_battle_plugin_test.cpp
    "${CMAKE_SOURCE_DIR}/demo/games/tank_battle/server/tank_plugin/tank_battle_plugin.cpp"
    unit/gateway_grpc_test.cpp
)
target_link_libraries(project_v2_unit_tests
    PRIVATE
        project_v2
        boost_gateway_sdk
        GTest::gtest_main
)
target_compile_definitions(project_v2_unit_tests
    PRIVATE
        PROJECT_SOURCE_DIR="${CMAKE_SOURCE_DIR}"
)
apply_project_warnings(project_v2_unit_tests)

# ─── gRPC test dependencies (optional) ──────────────────────────────
if(BOOST_BUILD_GRPC)
    target_include_directories(project_v2_unit_tests
        PRIVATE
            "${CMAKE_SOURCE_DIR}/src/v2/grpc"
            "${CMAKE_SOURCE_DIR}/src/v3/proto"
    )
endif()

include(GoogleTest)
gtest_discover_tests(project_v2_unit_tests
    DISCOVERY_TIMEOUT ${PROJECT_GTEST_DISCOVERY_TIMEOUT}
    DISCOVERY_MODE POST_BUILD
)

add_executable(project_v2_integration_tests
    integration/backend_routing_test.cpp
    integration/demo_server_smoke_test.cpp
    integration/backend_health_test.cpp
    integration/data_layer_test.cpp
    integration/raft_e2e_test.cpp
    integration/service_bus_integrity_test.cpp
    integration/process_supervisor_test.cpp
    integration/cluster_router_e2e_test.cpp
    integration/matchmaking_e2e_test.cpp
)
target_link_libraries(project_v2_integration_tests
    PRIVATE
        project_v2
        GTest::gtest_main
)
apply_project_warnings(project_v2_integration_tests)

gtest_discover_tests(project_v2_integration_tests
    DISCOVERY_TIMEOUT ${PROJECT_GTEST_DISCOVERY_TIMEOUT}
    DISCOVERY_MODE PRE_TEST
)

# ─── Multi-process E2E tests (real OS processes) ────────────────────
# Uses native OS process APIs (no boost::filesystem dependency).
# Tests skip gracefully at runtime if service binaries are unavailable.

add_executable(project_v2_multi_process_tests
    integration/multi_process_test.cpp
    integration/business_flow_test.cpp
    integration/settlement_replay_test.cpp
)
add_dependencies(project_v2_multi_process_tests
    v2_gateway_demo
    v2_login_backend
    v2_room_backend
    v2_battle_backend
    v2_leaderboard_backend
)
target_compile_definitions(project_v2_multi_process_tests
    PRIVATE
        V2_GATEWAY_BINARY="$<TARGET_FILE:v2_gateway_demo>"
        V2_LOGIN_BINARY="$<TARGET_FILE:v2_login_backend>"
        V2_ROOM_BINARY="$<TARGET_FILE:v2_room_backend>"
        V2_BATTLE_BINARY="$<TARGET_FILE:v2_battle_backend>"
        V2_LEADERBOARD_BINARY="$<TARGET_FILE:v2_leaderboard_backend>"
)
target_link_libraries(project_v2_multi_process_tests
    PRIVATE
        project_v2
        GTest::gtest_main
)
apply_project_warnings(project_v2_multi_process_tests)
gtest_discover_tests(project_v2_multi_process_tests
    DISCOVERY_TIMEOUT ${PROJECT_GTEST_DISCOVERY_TIMEOUT}
    DISCOVERY_MODE POST_BUILD
)
