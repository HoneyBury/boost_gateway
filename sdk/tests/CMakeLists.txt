add_executable(sdk_tests
    unit/codec_test.cpp
    unit/client_test.cpp
    unit/transport_test.cpp
    ../src/transport/tcp_transport.cpp
    ../src/transport/connection_pool.cpp
)
target_link_libraries(sdk_tests PRIVATE boost_gateway_sdk GTest::gtest_main)
if(COMMAND apply_project_warnings)
    apply_project_warnings(sdk_tests)
endif()
include(GoogleTest)
gtest_discover_tests(sdk_tests DISCOVERY_TIMEOUT 10)
