# Demo games — built only when explicitly enabled.
# Each demo must be independent of the default build and production gates.

option(BOOST_BUILD_TANK_DEMO "Build tank battle demo" OFF)

if(BOOST_BUILD_TANK_DEMO)
    add_subdirectory(tank_battle/server)
    add_subdirectory(tank_battle/tests)
endif()
