add_executable(v2_gateway_pressure
    main.cpp
)
target_link_libraries(v2_gateway_pressure
    PRIVATE
        project_v2
)
apply_project_warnings(v2_gateway_pressure)
if(COMMAND boost_gateway_stage_runtime_dlls)
    boost_gateway_stage_runtime_dlls(v2_gateway_pressure)
endif()
