add_executable(project_v2_unit_tests
    unit/actor_runtime_test.cpp
    unit/bootstrap_smoke_test.cpp
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
