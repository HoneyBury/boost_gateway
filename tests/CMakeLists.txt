set(PROJECT_GTEST_DISCOVERY_TIMEOUT 30)

add_subdirectory(v2)

option(BOOST_BUILD_PERF_TESTS "Build performance benchmark tests" OFF)
if(BOOST_BUILD_PERF_TESTS)
    add_subdirectory(perf)
endif()
