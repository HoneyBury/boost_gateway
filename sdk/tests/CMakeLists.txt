add_executable(sdk_tests
    unit/codec_test.cpp
    unit/client_test.cpp
    unit/c_api_test.cpp
    unit/transport_test.cpp
    ../src/transport/tcp_transport.cpp
    ../src/transport/connection_pool.cpp
)
target_link_libraries(sdk_tests PRIVATE boost_gateway_sdk boost_gateway_sdk_dll GTest::gtest_main)
if(TARGET project_boost_asio)
    target_link_libraries(sdk_tests PRIVATE project_boost_asio)
elseif(TARGET boost::headers)
    target_link_libraries(sdk_tests PRIVATE boost::headers)
endif()
if(COMMAND apply_project_warnings)
    apply_project_warnings(sdk_tests)
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
)
if(TARGET project_boost_asio)
    target_link_libraries(sdk_business_flow_tests PRIVATE project_boost_asio)
elseif(TARGET boost::headers)
    target_link_libraries(sdk_business_flow_tests PRIVATE boost::headers)
else()
    target_include_directories(sdk_business_flow_tests PRIVATE ${boost_SOURCE_DIR})
    target_compile_definitions(sdk_business_flow_tests
        PRIVATE
            BOOST_ALL_NO_LIB
            BOOST_ERROR_CODE_HEADER_ONLY
    )
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
gtest_discover_tests(sdk_business_flow_tests
    DISCOVERY_TIMEOUT 10
    DISCOVERY_MODE PRE_TEST
)
