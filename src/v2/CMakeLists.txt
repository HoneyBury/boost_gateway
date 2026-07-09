add_library(project_v2 STATIC
    actor/actor.cpp
    actor/actor_ref.cpp
    aoi/aoi_grid.cpp
    aoi/aoi_system.cpp
    aoi/spatial_grid.cpp
    battle/battle_actor.cpp
    battle/battle_snapshot.cpp
    battle/runtime_world.cpp
    battle/game_systems.cpp
    common/module_marker.cpp
    diagnostics/diagnostics_manager.cpp
    diagnostics/health_check.cpp
    diagnostics/runtime_metrics_exporter.cpp
    data/replay_format.cpp
    data/write_behind_store.cpp
    data/cached_data_store.cpp
    ecs/parallel_system_executor.cpp
    ecs/world.cpp
    gateway/battle_data_store.cpp
    service/backend_envelope.cpp
    service/envelope_adapter.cpp
    service/backend_connection.cpp
    service/circuit_breaker.cpp
    service/backend_frame_codec.cpp
    service/backend_server.cpp
    service/error_codes.cpp
    service/service_manifest.cpp
    service/service_registry.cpp
    service/service_registrar.cpp
    gateway/gateway_service_bridge.cpp
    gateway/battle_protocol_codec.cpp
    gateway/battle_wire_parser.cpp
    gateway/gateway_command_parser.cpp
    config/config_watcher.cpp
    gateway/demo_server.cpp
    gateway/gateway_actor.cpp
    gateway/gateway_response_parser.cpp
    gateway/gateway_server_bridge.cpp
    gateway/runtime.cpp
    gateway/session_adapter.cpp
    io/io_engine.cpp
    memory/arena.cpp
    perf/perf_counter.cpp
    player/player_actor.cpp
    realtime/instance_runtime.cpp
    room/room_actor.cpp
    room/room_backend_service.cpp
    login/login_backend_service.cpp
    battle/battle_backend_service.cpp
    battle/battle_instance_plugin.cpp
    # battle/tank_battle_plugin.cpp -- moved to demo/games/tank_battle/ (Batch A)
    leaderboard/leaderboard_service.cpp
    match/matchmaking_service.cpp
    security/anti_cheat.cpp
    test/fault_injector.cpp
    runtime/actor_system.cpp
    persistence/writebehind.cpp
    persistence/replay_storage.cpp
    persistence/storage_engine_sqlite.cpp
    persistence/player_data.cpp
)
target_include_directories(project_v2
    PUBLIC
        "${CMAKE_SOURCE_DIR}/include"
)
target_link_libraries(project_v2
    PUBLIC
        project_app
        project_net
        project_v3
        OpenSSL::SSL
        OpenSSL::Crypto
)
apply_project_warnings(project_v2)

# SQLite-backed persistence (opt-in)
# Enable with: cmake -B build -DBOOST_BUILD_SQLITE=ON
# Batch C: SQLite auto-detect — when enabled, gracefully degrades if not found
if(BOOST_BUILD_SQLITE)
  find_package(sqlite3 QUIET)
  if(sqlite3_FOUND)
    target_compile_definitions(project_v2 PRIVATE HAS_SQLITE)
    target_link_libraries(project_v2 PUBLIC sqlite3)
    message(STATUS "SQLite found — persistence with SQLite enabled")
  else()
    message(WARNING "BOOST_BUILD_SQLITE=ON but sqlite3 not found — "
                    "persistence will compile without SQLite support")
  endif()
endif()

# gRPC gateway (experimental, disabled by default).
# Enable with: cmake -B build -DBOOST_BUILD_GRPC=ON
# Test with: ctest -C Debug -R "gateway_grpc"
#
# Batch B gap analysis (2026-05-23): gRPC GatewayServer implements 3 RPCs
# (Login/Logout/Health). Missing vs TCP Gateway: Room Create/Join/Leave/Ready/
# StartBattle, Battle Input/Finish/FramePush(streaming), Match Join/Leave/Status,
# Leaderboard Submit/Top/Rank, Session Kick/Resume, Rate Limiting, RBAC, TLS.
# gRPC remains experimental — compile when available, but do NOT start listener
# by default (use --enable-grpc or config to activate at runtime).
# ─── gRPC server sources (conditional on BOOST_BUILD_GRPC) ─────────────────
if(BOOST_BUILD_GRPC)
  target_sources(project_v2 PRIVATE
    grpc/gateway_grpc_server.cpp
  )

  target_link_libraries(project_v2
    PUBLIC
      project_proto
  )

  target_compile_definitions(project_v2
    PRIVATE
      BOOST_BUILD_GRPC
  )

  # Ensure proto/gRPC stubs are generated before compiling gRPC sources.
  add_dependencies(project_v2 generate_v3_proto generate_v3_grpc)
endif()
