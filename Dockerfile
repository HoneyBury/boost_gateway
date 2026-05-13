# Multi-stage build for BoostAsioDemo v2.0.0
# Build stage
FROM ubuntu:24.04 AS builder

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    ninja-build \
    curl \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

COPY CMakeLists.txt CMakePresets.json ./
COPY cmake/ cmake/
COPY include/ include/
COPY src/ src/
COPY examples/ examples/
COPY tests/ tests/
COPY third_party/ third_party/
COPY config/ config/

RUN cmake --preset default
RUN cmake --build --preset default --parallel

# Runtime stage
FROM ubuntu:24.04 AS runtime

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN mkdir -p /app/bin /app/logs /app/runtime /app/config

# v2 binaries (primary)
COPY --from=builder /src/build/default/examples/v2_gateway_demo/v2_gateway_demo /app/bin/
COPY --from=builder /src/build/default/examples/v2_login_backend/v2_login_backend /app/bin/
COPY --from=builder /src/build/default/examples/v2_room_backend/v2_room_backend /app/bin/
COPY --from=builder /src/build/default/examples/v2_battle_backend/v2_battle_backend /app/bin/
COPY --from=builder /src/build/default/examples/v2_match_backend/v2_match_backend /app/bin/
COPY --from=builder /src/build/default/examples/v2_leaderboard_backend/v2_leaderboard_backend /app/bin/

# v1 binaries (reference / legacy)
COPY --from=builder /src/build/default/examples/echo/echo_server /app/bin/
COPY --from=builder /src/build/default/examples/echo/echo_client /app/bin/
COPY --from=builder /src/build/default/examples/pressure/gateway_pressure /app/bin/

COPY config/ /app/config/

EXPOSE 9000 9080 9202 9302 9303

# Default: v2 gateway demo in standalone mode
# Args appended to ENTRYPOINT (e.g., --management-port 9080)
ENTRYPOINT ["/app/bin/v2_gateway_demo"]
CMD []
