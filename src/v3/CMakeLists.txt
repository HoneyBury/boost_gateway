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

add_subdirectory(cluster)
add_subdirectory(persistence)
