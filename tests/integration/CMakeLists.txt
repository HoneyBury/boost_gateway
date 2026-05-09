add_executable(project_integration_tests
    gateway_integration_test.cpp
    http_management_test.cpp
)
target_link_libraries(project_integration_tests
    PRIVATE
        project_game
        project_v2
        GTest::gtest_main
)
apply_project_warnings(project_integration_tests)

include(GoogleTest)
gtest_discover_tests(project_integration_tests
    DISCOVERY_TIMEOUT ${PROJECT_GTEST_DISCOVERY_TIMEOUT}
)
