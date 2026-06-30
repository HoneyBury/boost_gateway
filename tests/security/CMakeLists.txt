# Boost Gateway — 安全测试 Targets
#
# 构建方式：
#   cmake -B build -DBOOST_BUILD_SECURITY_TESTS=ON
#   cmake --build build
#   ctest --test-dir build -R security -C Debug

option(BOOST_BUILD_SECURITY_TESTS "Build security tests" OFF)

if(BOOST_BUILD_SECURITY_TESTS)
    add_executable(security_tests
        protocol_security_test.cpp
    )
    target_include_directories(security_tests
        PRIVATE
            "${CMAKE_SOURCE_DIR}/include"
    )
    target_link_libraries(security_tests
        PRIVATE
            project_net
            project_app
            project_boost_asio
            GTest::gtest_main
    )
    apply_project_warnings(security_tests)

    include(GoogleTest)
    gtest_discover_tests(security_tests
        DISCOVERY_TIMEOUT 30
        DISCOVERY_MODE POST_BUILD
    )

    message(STATUS "Security tests are enabled (BOOST_BUILD_SECURITY_TESTS=ON)")
    message(STATUS "  security_tests — protocol security and robustness tests")

else()
    # 安全测试未启用时创建空桩目标
    add_executable(security_stub EXCLUDE_FROM_ALL
        "${CMAKE_CURRENT_SOURCE_DIR}/../unit/hello_test.cpp"
    )
    target_link_libraries(security_stub
        PRIVATE
            GTest::gtest_main
    )
endif()
