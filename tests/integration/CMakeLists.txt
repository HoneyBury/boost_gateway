add_executable(project_integration_tests
    gateway_integration_test.cpp
)
target_link_libraries(project_integration_tests
    PRIVATE
        project_game
        GTest::gtest_main
)
apply_project_warnings(project_integration_tests)

include(GoogleTest)
gtest_discover_tests(project_integration_tests)
