add_executable(project_integration_tests
    gateway_integration_test.cpp
    http_management_test.cpp
)
if(BOOST_BUILD_V1_LEGACY_EXAMPLES)
    add_dependencies(project_integration_tests echo_server)
    target_compile_definitions(project_integration_tests
        PRIVATE
            PROJECT_ECHO_SERVER_PATH="$<TARGET_FILE:echo_server>"
    )
endif()
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
    DISCOVERY_MODE POST_BUILD
)
