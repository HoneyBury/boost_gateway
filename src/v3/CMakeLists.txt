find_package(Python3 REQUIRED COMPONENTS Interpreter)

add_library(project_v3 STATIC)
target_include_directories(project_v3
    PUBLIC
        "${CMAKE_SOURCE_DIR}/include"
)
target_link_libraries(project_v3
    PUBLIC
        project_app
    PRIVATE
        $<IF:$<TARGET_EXISTS:hiredis::hiredis>,hiredis::hiredis,hiredis>
        OpenSSL::Crypto
)
if(BOOST_BUILD_RAFT_PROTOBUF)
    target_link_libraries(project_v3 PUBLIC project_raft_proto)
    target_compile_definitions(project_v3 PUBLIC BOOST_BUILD_RAFT_PROTOBUF)
endif()
apply_project_warnings(project_v3)

add_custom_target(generate_v3_proto_cpp
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/scripts/tools/generate_proto_cpp.py"
            --proto-dir "${CMAKE_SOURCE_DIR}/proto/v3"
            --out-dir "${CMAKE_SOURCE_DIR}/src/v3/proto"
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    COMMENT "Proto C++ generation helper target"
)

add_custom_target(check_v3_proto_schema
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/scripts/gates/governance/check_v3_proto_schema.py"
            --proto-dir "${CMAKE_SOURCE_DIR}/proto/v3"
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    COMMENT "Validate v3 proto schema without requiring protoc"
)

add_custom_target(check_v3_proto_transport_contract
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/scripts/gates/governance/check_v3_proto_schema.py"
            --proto-dir "${CMAKE_SOURCE_DIR}/proto/v3"
            --require-transport-contract
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    COMMENT "Validate v3 experimental generated-transport contract"
)

add_subdirectory(cluster)
add_subdirectory(persistence)
