# P1 Commit Slices

Date: 2026-05-15

This repository is currently carrying multiple parallel streams in one working
tree. The next cleanup step should be to split them into small, verifiable
commits with the following order.

## Recommended commit order

1. `build: recover offline dependency resolution and Windows test runtime staging`
   - `cmake/Dependencies.cmake`
   - `CMakeLists.txt`
   - `sdk/tests/CMakeLists.txt`
   - `tests/**/CMakeLists.txt`
   - `examples/**/CMakeLists.txt`
   - `third_party/README.md`
   - `third_party/bootstrap_from_build_cache.*`

2. `raft: add backend RPC wiring plus persisted log recovery`
   - `include/v3/cluster/raft.h`
   - `src/v3/cluster/raft.cpp`
   - `src/v2/match/matchmaking_service.cpp`
   - `src/v2/leaderboard/leaderboard_service.cpp`
   - `examples/v2_match_backend/main.cpp`
   - `examples/v2_leaderboard_backend/main.cpp`
   - `tests/v2/unit/raft_test.cpp`
   - relevant Raft integration tests

3. `gateway: harden bridge routing, TLS gating, and observability behavior`
   - `include/v2/gateway/gateway_service_bridge.h`
   - `src/v2/gateway/gateway_service_bridge.cpp`
   - `src/v2/gateway/demo_server.cpp`
   - `include/v3/tracing/otel_exporter.h`
   - backend routing / service bus integration tests

4. `auth: expand JWT support to RS256 and backend validation`
   - `include/v2/auth/jwt_validator.h`
   - `include/v2/login/login_backend_service.h`
   - `src/v2/login/login_backend_service.cpp`
   - JWT / service bus tests

5. `operator: scaffold controller tests, configmaps, and TLS placeholder secret`
   - `../plans/k8s-operator-implementation.md`
   - `operator/boostgateway-operator/**`
   - `tests/v2/unit/k8s_operator_test.cpp`

6. `docs: refresh roadmap and execution notes`
   - this file
   - roadmap / implementation notes updated by the previous commits

## Rules for staging

- Do not mix third-party vendoring changes with code changes.
- Verify each commit with the smallest relevant test subset before staging the
  next one.
- Keep generated or scaffolded operator files in a dedicated commit so the diff
  stays reviewable.
