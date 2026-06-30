if(BOOST_BUILD_V1_LEGACY_EXAMPLES)
    add_executable(echo_server
        server_main.cpp
    )
    target_link_libraries(echo_server
        PRIVATE
            project_game
            project_v2
    )
    apply_project_warnings(echo_server)
    set_target_properties(echo_server PROPERTIES FOLDER "legacy/v1")
endif()

add_executable(echo_client
    client_main.cpp
)
target_link_libraries(echo_client
    PRIVATE
        project_net
)
apply_project_warnings(echo_client)
