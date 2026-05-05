add_executable(project_unit_tests
    hello_test.cpp
    packet_codec_test.cpp
    message_dispatcher_test.cpp
    service_registration_test.cpp
)
target_link_libraries(project_unit_tests
    PRIVATE
        project_game
        project_hello
        GTest::gtest_main
)
apply_project_warnings(project_unit_tests)

include(GoogleTest)
gtest_discover_tests(project_unit_tests)
