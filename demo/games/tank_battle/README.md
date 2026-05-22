# Tank Battle Demo

Multiplayer 2D tank battle game demo for validating the Boost Gateway realtime framework.

## Location

All tank-battle-specific code lives under `demo/games/tank_battle/`. No tank business logic
enters `include/`, `src/`, `sdk/`, or framework core directories.

## Directory Structure

```
demo/games/tank_battle/
  README.md
  docs/            -- game rules, protocol, acceptance criteria
  server/
    tank_simulation/  -- deterministic world, tanks, bullets, collision
    tank_plugin/      -- adapts simulation to RealtimeInstance SPI
  client_sdk_adapter/ -- thin business wrapper on top of public SDK
  tests/              -- unit and integration tests
  scripts/            -- demo verification entry points
```

## Build

```bash
cmake -B build -DBOOST_BUILD_TANK_DEMO=ON
cmake --build build --target tank_battle_demo
```

The demo is **not** built by default. Set `BOOST_BUILD_TANK_DEMO=ON` to include it.

## Flow

register/login -> list_rooms -> create/join_room -> ready ->
start_instance -> tank input/snapshot push -> disconnect/reconnect ->
settlement -> leaderboard -> leave

## Verification

```bash
python3 demo/games/tank_battle/scripts/verify_tank_battle_demo.py --build-dir build
```
