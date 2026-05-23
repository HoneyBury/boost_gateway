add_executable(sdk_tests
    unit/codec_test.cpp
    unit/client_test.cpp
    unit/c_api_test.cpp
    unit/transport_test.cpp
    ../src/transport/tcp_transport.cpp
    ../src/transport/connection_pool.cpp
)
target_link_libraries(sdk_tests PRIVATE boost_gateway_sdk boost_gateway_sdk_dll GTest::gtest_main)
if(COMMAND apply_project_warnings)
    apply_project_warnings(sdk_tests)
endif()
if(COMMAND boost_gateway_stage_runtime_dlls)
    boost_gateway_stage_runtime_dlls(sdk_tests)
endif()
include(GoogleTest)
gtest_discover_tests(sdk_tests
    DISCOVERY_TIMEOUT 10
    DISCOVERY_MODE POST_BUILD
)

add_executable(sdk_business_flow_tests
    sdk_integration_test.cpp
    ../src/client.cpp
    ../src/transport/tcp_transport.cpp
    ../src/transport/connection_pool.cpp
)
target_include_directories(sdk_business_flow_tests
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/..
        ${CMAKE_CURRENT_SOURCE_DIR}/../include
        ${CMAKE_CURRENT_BINARY_DIR}/../include
        ${boost_SOURCE_DIR}
)
target_compile_definitions(sdk_business_flow_tests
    PRIVATE
        BOOST_ALL_NO_LIB
        BOOST_ERROR_CODE_HEADER_ONLY
)
if(WIN32)
    target_compile_definitions(sdk_business_flow_tests PRIVATE _WIN32_WINNT=0x0A00)
endif()
target_link_libraries(sdk_business_flow_tests
    PRIVATE
        boost_gateway_sdk_dll
        nlohmann_json::nlohmann_json
        project_v2
        GTest::gtest_main
)
if(COMMAND apply_project_warnings)
    apply_project_warnings(sdk_business_flow_tests)
endif()
if(COMMAND boost_gateway_stage_runtime_dlls)
    boost_gateway_stage_runtime_dlls(sdk_business_flow_tests)
endif()
gtest_discover_tests(sdk_business_flow_tests
    DISCOVERY_TIMEOUT 10
    DISCOVERY_MODE PRE_TEST
)
