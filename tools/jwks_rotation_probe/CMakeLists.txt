add_executable(jwks_rotation_probe EXCLUDE_FROM_ALL main.cpp)
target_link_libraries(jwks_rotation_probe
    PRIVATE
        project_v2
        nlohmann_json::nlohmann_json
)
apply_project_warnings(jwks_rotation_probe)
