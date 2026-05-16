add_library(project_v3 STATIC)
target_include_directories(project_v3
    PUBLIC
        "${CMAKE_SOURCE_DIR}/include"
    PRIVATE
        "${hiredis_SOURCE_DIR}"
)
target_link_libraries(project_v3
    PUBLIC
        project_app
    PRIVATE
        hiredis
)
apply_project_warnings(project_v3)

add_custom_target(generate_v3_proto_cpp
    COMMAND python "${CMAKE_SOURCE_DIR}/scripts/generate_proto_cpp.py"
            --proto-dir "${CMAKE_SOURCE_DIR}/proto/v3"
            --out-dir "${CMAKE_SOURCE_DIR}/src/v3/proto"
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    COMMENT "Proto C++ generation helper target"
)

add_subdirectory(cluster)
add_subdirectory(persistence)
