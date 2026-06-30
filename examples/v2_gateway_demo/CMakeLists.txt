add_executable(v2_gateway_demo
    main.cpp
)
target_link_libraries(v2_gateway_demo
    PRIVATE
        project_v2
)
apply_project_warnings(v2_gateway_demo)
