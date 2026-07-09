if(ENABLE_TESTING AND BOOST_BUILD_TANK_DEMO)
    add_executable(tank_battle_simulation_test
        simulation_test.cpp
    )
    target_include_directories(tank_battle_simulation_test
        PRIVATE
            "${CMAKE_SOURCE_DIR}/include"
            "${CMAKE_SOURCE_DIR}"  # for demo/games/ include path
            "${CMAKE_CURRENT_SOURCE_DIR}/../server"
    )
    target_link_libraries(tank_battle_simulation_test
        PRIVATE
            tank_battle_server
            GTest::gtest_main
            nlohmann_json::nlohmann_json
    )
    apply_project_warnings(tank_battle_simulation_test)
    gtest_discover_tests(tank_battle_simulation_test
        DISCOVERY_TIMEOUT 30
    )

    # Performance smoke test (standalone, not GTest)
    add_executable(tank_battle_perf_test
        perf_test.cpp
    )
    target_include_directories(tank_battle_perf_test
        PRIVATE
            "${CMAKE_SOURCE_DIR}/include"
            "${CMAKE_SOURCE_DIR}"
            "${CMAKE_CURRENT_SOURCE_DIR}/../server"
    )
    target_link_libraries(tank_battle_perf_test
        PRIVATE
            tank_battle_server
            project_v2
            nlohmann_json::nlohmann_json
    )
    apply_project_warnings(tank_battle_perf_test)

    # E2E closed-loop test (GTest)
    add_executable(tank_battle_e2e_test
        e2e_test.cpp
    )
    target_include_directories(tank_battle_e2e_test
        PRIVATE
            "${CMAKE_SOURCE_DIR}/include"
            "${CMAKE_SOURCE_DIR}"
            "${CMAKE_CURRENT_SOURCE_DIR}/../server"
    )
    target_link_libraries(tank_battle_e2e_test
        PRIVATE
            tank_battle_server
            project_v2
            GTest::gtest_main
            nlohmann_json::nlohmann_json
    )
    apply_project_warnings(tank_battle_e2e_test)
    gtest_discover_tests(tank_battle_e2e_test
        DISCOVERY_TIMEOUT 30
    )
endif()
