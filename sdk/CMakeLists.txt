# BoostGateway Client SDK
# Standalone library that client applications link against.

add_library(boost_gateway_sdk STATIC
    src/client.cpp
)

target_include_directories(boost_gateway_sdk
    PUBLIC
        "${CMAKE_CURRENT_SOURCE_DIR}/include"
        "${CMAKE_SOURCE_DIR}/include"
)

target_link_libraries(boost_gateway_sdk
    PUBLIC
        project_net
        project_boost_asio
)

apply_project_warnings(boost_gateway_sdk)

# ── Examples ──────────────────────────────────────────────────────────
add_subdirectory(examples)

# ── Tests ────────────────────────────────────────────────────────────
if(ENABLE_TESTING)
    add_subdirectory(tests)
endif()
