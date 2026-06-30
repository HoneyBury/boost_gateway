add_executable(v2_arch_benchmark
    main.cpp
)
target_link_libraries(v2_arch_benchmark
    PRIVATE
        project_v2
)
apply_project_warnings(v2_arch_benchmark)
