find_package(Threads REQUIRED)

add_executable(v2_mailbox_benchmark
    main.cpp
)
target_include_directories(v2_mailbox_benchmark
    PRIVATE
        "${PROJECT_SOURCE_DIR}/include"
)
target_link_libraries(v2_mailbox_benchmark
    PRIVATE
        Threads::Threads
)
apply_project_warnings(v2_mailbox_benchmark)
