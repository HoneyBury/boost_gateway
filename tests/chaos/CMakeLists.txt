# Boost Gateway — 混沌测试 Targets
#
# 构建方式：
#   cmake -B build -DBOOST_BUILD_CHAOS=ON
#   cmake --build build
#   ctest --test-dir build -R chaos -C Debug
#
# 混沌测试会启动真实的 Gateway 服务器并注入故障，因此：
#   - 需要网络栈支持（127.0.0.1 回环）
#   - 稳定性测试运行时约 60 秒

if(BOOST_BUILD_CHAOS)
    # ── Gateway 混沌测试 ────────────────────────────────────────────
    add_executable(chaos_gateway_tests
        gateway_chaos_test.cpp
    )
    target_include_directories(chaos_gateway_tests
        PRIVATE
            "${CMAKE_CURRENT_SOURCE_DIR}"  # 可找到 chaos_test_framework.h
    )
    target_link_libraries(chaos_gateway_tests
        PRIVATE
            project_game
            project_net
            project_app
            project_boost_asio
            GTest::gtest_main
    )
    apply_project_warnings(chaos_gateway_tests)

    include(GoogleTest)
    gtest_discover_tests(chaos_gateway_tests
        DISCOVERY_TIMEOUT 60
        DISCOVERY_MODE POST_BUILD
    )

    # ── 稳定性混沌测试（长期运行）───────────────────────────────────
    add_executable(chaos_stability_tests
        stability_chaos_test.cpp
    )
    target_include_directories(chaos_stability_tests
        PRIVATE
            "${CMAKE_CURRENT_SOURCE_DIR}"
    )
    target_link_libraries(chaos_stability_tests
        PRIVATE
            project_game
            project_net
            project_app
            project_boost_asio
            GTest::gtest_main
    )
    apply_project_warnings(chaos_stability_tests)

    # 稳定性测试需要更长的超时时间
    gtest_discover_tests(chaos_stability_tests
        DISCOVERY_TIMEOUT 120
        DISCOVERY_MODE POST_BUILD
    )

    message(STATUS "Chaos tests are enabled (BOOST_BUILD_CHAOS=ON)")
    message(STATUS "  chaos_gateway_tests     — gateway-specific chaos scenarios")
    message(STATUS "  chaos_stability_tests   — 60s long-running with random fault injection")

else()
    # 混沌未启用时创建空桩目标
    add_executable(chaos_stub EXCLUDE_FROM_ALL
        "${CMAKE_CURRENT_SOURCE_DIR}/../unit/hello_test.cpp"
    )
    target_link_libraries(chaos_stub
        PRIVATE
            GTest::gtest_main
    )
endif()
