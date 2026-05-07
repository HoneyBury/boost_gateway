# Multi-stage build for the boost gateway server
# Build stage
FROM ubuntu:24.04 AS builder

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
RUN ctest --preset default

# Runtime stage
FROM ubuntu:24.04 AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /src/build/default/examples/echo/echo_server /app/echo_server
COPY --from=builder /src/build/default/examples/echo/echo_client /app/echo_client
COPY --from=builder /src/build/default/examples/pressure/gateway_pressure /app/gateway_pressure
COPY config/ /app/config/

RUN mkdir -p /app/logs /app/runtime

EXPOSE 9000 9080

ENTRYPOINT ["/app/echo_server", "config/gateway.json"]
