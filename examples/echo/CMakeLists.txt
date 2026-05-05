add_executable(echo_server
    server_main.cpp
)
target_link_libraries(echo_server
    PRIVATE
        project_game
)
apply_project_warnings(echo_server)

add_executable(echo_client
    client_main.cpp
)
target_link_libraries(echo_client
    PRIVATE
        project_net
)
apply_project_warnings(echo_client)
