# Perf test — gRPC vs TCP protocol benchmark.
#
# Requires BOOST_BUILD_GRPC=ON for the gRPC benchmark path.
# The TCP benchmark uses the existing v2::service::BackendConnection
# and works regardless of gRPC availability.

add_executable(grpc_vs_tcp_perf_test
    grpc_vs_tcp_perf_test.cpp
)

target_link_libraries(grpc_vs_tcp_perf_test
    PRIVATE
        project_v2
)

if(BOOST_BUILD_GRPC)
    target_include_directories(grpc_vs_tcp_perf_test
        PRIVATE
            ${CMAKE_SOURCE_DIR}/src
    )
    target_compile_definitions(grpc_vs_tcp_perf_test
        PRIVATE
            BOOST_BUILD_GRPC
    )
    target_link_libraries(grpc_vs_tcp_perf_test
        PRIVATE
            project_proto
    )
endif()

apply_project_warnings(grpc_vs_tcp_perf_test)
