# Boost Gateway v3.3.2 — Environment Configuration

This directory contains the production-ready environment infrastructure for the
Boost Gateway game server framework. It covers Docker Compose orchestration,
Kubernetes deployment manifests, Prometheus/Grafana monitoring, Redis caching,
and CI/CD pipelines.

## Quick Start

```bash
# Start all 6 services + Redis + monitoring stack
docker compose -f env/docker/docker-compose.yml up -d

# Check health of the gateway
curl http://localhost:9080/health

# View Prometheus targets
open http://localhost:9090/targets

# View Grafana dashboard
open http://localhost:3000/d/boost-gateway/boost-gateway-v2-6
```

To stop everything:

```bash
docker compose -f env/docker/docker-compose.yml down -v
```

## Port Mapping

| Service              | Internal Port | External Port | Protocol | Notes                        |
|----------------------|---------------|---------------|----------|------------------------------|
| gateway              | 9201          | 9201          | TCP      | Client-facing game port      |
| gateway (HTTP mgmt)  | 9080          | 9080          | HTTP     | `/health`, `/metrics`, diagnostics |
| login backend        | 9202          | 9202          | TCP      | Token validation, session    |
| room backend         | 9302          | 9302          | TCP      | Room lifecycle               |
| battle backend       | 9303          | 9303          | TCP      | Battle simulation, ECS tick  |
| matchmaking backend  | 9304          | 9304          | TCP      | MMR-based matchmaking        |
| leaderboard backend  | 9305          | 9305          | TCP      | Ranked leaderboards          |
| Redis                | 6379          | 6379          | TCP      | Caching, leaderboard storage |
| Prometheus           | 9090          | 9090          | HTTP     | Metrics collection           |
| Grafana              | 3000          | 3000          | HTTP     | Visualization                |

## Architecture

```
                    ┌───────────┐
                    │  Clients  │
                    └─────┬─────┘
                          │ TCP 9201
                    ┌─────▼─────┐
                    │  Gateway  │  (v2_gateway_demo)
                    │  :9080    │── HTTP management (/health, /metrics)
                    └──┬───┬───┬┘
                       │   │   │
              ┌────────┤   │   ├──────────┐
              │        │   │   │          │
        ┌─────▼──┐ ┌──▼───▼─┐ ┌▼────────┐ │
        │ Login  │ │ Room   │ │ Battle  │ │
        │ :9202  │ │ :9302  │ │ :9303   │ │
        └────────┘ └────────┘ └─────────┘ │
                          ┌───────────────┘
                    ┌─────▼─────┐  ┌──────────────┐
                    │Matchmaking│  │ Leaderboard  │
                    │  :9304    │  │   :9305      │
                    └───────────┘  └──────────────┘

                    ┌───────────┐
                    │   Redis   │  (caching + leaderboard)
                    │   :6379   │
                    └───────────┘

           ┌───────────────────────────┐
           │  Prometheus :9090         │── scrapes gateway `/metrics` only
           └─────────────┬─────────────┘
                         │
           ┌─────────────▼─────────────┐
           │  Grafana :3000            │── dashboards from Prometheus
           └───────────────────────────┘
```

## Service Configuration

Each service reads its configuration from `config/` at startup. The key config
files and their environment variable overrides are:

| Service             | Config File                     | Port Env Var       |
|---------------------|---------------------------------|--------------------|
| gateway             | `config/gateway.json`           | `GATEWAY_PORT`     |
| login backend       | `config/login_backend.json`     | (CLI arg)          |
| room backend        | `config/room_backend.json`      | (CLI arg)          |
| battle backend      | `config/battle_backend.json`    | (CLI arg)          |
| matchmaking backend | — (port via env `MATCH_PORT`)   | `MATCH_PORT`       |
| leaderboard backend | — (port via env `LEADERBOARD_PORT`) | `LEADERBOARD_PORT` |

### Environment Variables

All services support the following common variables:

- `BOOST_LOG_LEVEL` — Log verbosity (`trace`, `debug`, `info`, `warn`, `error`). Default: `info`.
- `MANAGEMENT_PORT` — HTTP management port for the gateway. Default: `9080`.

## Docker Compose

The compose file at `env/docker/docker-compose.yml` orchestrates all 10
containers:

```bash
# Full stack
docker compose -f env/docker/docker-compose.yml up -d

# Selective start
docker compose -f env/docker/docker-compose.yml up -d gateway redis

# Scale backends
docker compose -f env/docker/docker-compose.yml up -d --scale room-backend=3
```

### Dockerfiles

- **`env/docker/Dockerfile.gateway`** — Builds only the gateway binary in a
  multi-stage build on the current Ubuntu runtime base.

- **`env/docker/Dockerfile.backend`** — Generic image for all 5 backend services
  (login, room, battle, matchmaking, leaderboard). The `SERVICE_BINARY`
  build argument selects which binary to include; the `SERVICE_PORT` env
  variable controls the listening port at runtime.

## Kubernetes

Manifests in `env/k8s/` target Kubernetes 1.25+:

```bash
kubectl apply -f env/k8s/namespace.yaml
kubectl apply -f env/k8s/
kubectl -n boost-gateway get pods
```

Each backend has its own manifest (`login-backend-deployment.yaml`,
`room-backend-deployment.yaml`, `battle-backend-deployment.yaml`,
`matchmaking-backend-deployment.yaml`, `leaderboard-backend-deployment.yaml`).
Backend probes use TCP socket checks because backend services expose the custom
game backend protocol rather than HTTP.

## Monitoring

### Prometheus

The Prometheus config (`env/monitoring/prometheus.yml`) scrapes gateway
`/metrics` every 15 seconds. Backends currently expose TCP service ports and
file-oriented metrics configuration, not HTTP `/metrics` endpoints.
Do not configure Prometheus to scrape backend TCP ports as HTTP targets until
backend HTTP management endpoints are implemented.

Alert rules live at `env/monitoring/prometheus-alerts.yml` and are loaded by
Prometheus through `rule_files`. The default rules cover gateway scrape down,
backend route errors/timeouts inferred from gateway RED counters, leaderboard
Redis-dependent failures, rate-limit spikes, active-session pressure, and
optional process-exporter RSS/fd signals.

### Grafana

The dashboard (`env/monitoring/grafana-dashboard.json`) provides RED-metrics
visualization:

- **Rate**: gateway accepted sessions, packets/sec, bytes/sec, backend request rates
- **Errors**: blocked packets, backend route errors/timeouts, leaderboard Redis-dependent failures
- **Capacity**: active sessions, optional process RSS/fd when an exporter is configured

Import it into Grafana via **Configuration > Data Sources > Add data source >
Prometheus**, then **+ Import** the JSON file.

## Redis

The Redis configuration (`env/redis/redis.conf`) is tuned for the gateway's
workload:

- Max memory: 256 MB (`maxmemory 256mb`)
- Eviction: `allkeys-lru` (least-recently-used)
- Persistence: RDB snapshots every 5 minutes (or 100 writes)
- Used for: leaderboard data caching, session cache, write-behind store

## CI/CD

The GitHub Actions pipeline (`env/cicd/github-actions.yml`) runs on push to
`develop` or `main`, and on version tags (`v*`):

1. **Build** — CMake configure + build across ubuntu-latest, macos-latest,
   windows-2022
2. **Test** — ctest with output on failure
3. **Docker** — Build and push images to GitHub Container Registry (main branch
   only)
4. **Deploy** — Example kubectl apply step for Kubernetes (tagged releases only)

## Prerequisites

- **Docker** 24+ with Compose v2 (`docker compose` plugin)
- **Kubernetes** 1.25+ cluster (for k8s manifests); `kubectl` configured
- **Prometheus** operator or standalone Prometheus (for monitoring)
- **Grafana** 10+ (for dashboards)
- **GitHub** repository with Actions enabled (for CI/CD)

## Related Documentation

| Document | Path | Description |
|---|---|---|
| Deployment Guide | `deploy/README.md` | systemd setup, manual install |
| Project README | `README.md` | Build from source, testing |
| Config Files | `config/*.json` | Service configuration schemas |
| Prometheus Alerts | `env/monitoring/prometheus-alerts.yml` | Production alerting rules |
| Grafana Dashboard | `env/monitoring/grafana-dashboard.json` | Production dashboard backed by current gateway metrics |
| Release Process | `docs/release-process.md` | Versioning and publishing |
| V2 Roadmap | `docs/v2-roadmap.md` | Architecture evolution |
