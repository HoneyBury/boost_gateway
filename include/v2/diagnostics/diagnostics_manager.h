#pragma once

#include "v2/gateway/backend_metrics.h"
#include "v2/gateway/demo_server.h"
#include "v2/gateway/gateway_server_bridge.h"
#include "v2/service/service_registry.h"

#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace v2::diagnostics {

// ── Unified snapshot types ─────────────────────────────

struct SystemSummary {
    bool overall_healthy = true;
    std::uint64_t total_active_sessions = 0;
    std::uint64_t total_accepted_sessions = 0;
    std::uint64_t total_outbound_dispatches = 0;
    std::uint32_t io_core_count = 0;
    std::size_t registered_backend_count = 0;
    std::size_t healthy_backend_count = 0;
    double messages_per_second = 0.0;
    std::chrono::steady_clock::time_point snapshot_at;
};

struct BackendEntry {
    std::string service_name;  // "login"/"room"/"battle"
    bool healthy = false;      // at least one healthy instance
    std::size_t healthy_instances = 0;
    std::size_t unhealthy_instances = 0;
    v2::gateway::BackendMetricsSnapshot metrics;
};

struct IoCoreEntry {
    std::uint32_t core_id = 0;
    std::uint64_t active_sessions = 0;
    std::uint64_t accepted_sessions = 0;
    std::uint64_t outbound_dispatches = 0;
};

struct ShadowBridgeEntry {
    bool enabled = false;
    bool emit_responses = false;
    v2::gateway::GatewayServerShadowBridge::DispatchStats dispatch_stats;
    std::uint64_t tracked_sessions = 0;
    std::uint64_t active_sessions = 0;
};

struct DiagnosticsSnapshot {
    SystemSummary summary;
    std::vector<BackendEntry> backends;
    std::vector<IoCoreEntry> io_cores;
    ShadowBridgeEntry shadow_bridge;
};

// ── Manager ────────────────────────────────────────────

class DiagnosticsManager {
public:
    DiagnosticsManager() = default;

    // Collect a full snapshot from all registered data sources.
    // All sources are optional — missing sources produce empty/default entries.
    [[nodiscard]] DiagnosticsSnapshot collect() const;

    // Serialize snapshot to human-readable text (for /metrics/diagnostics)
    [[nodiscard]] std::string to_text(const DiagnosticsSnapshot& snap) const;

    // Serialize snapshot to JSON (for /metrics/diagnostics/json)
    [[nodiscard]] std::string to_json(const DiagnosticsSnapshot& snap) const;

    // ── Data source registration ────────────────────────

    void set_backend_metrics(std::shared_ptr<v2::gateway::BackendMetrics> m);
    void set_service_registry(std::shared_ptr<v2::service::ServiceRegistry> r);
    void set_io_core_provider(std::function<std::vector<IoCoreEntry>()> provider);
    void set_shadow_bridge_provider(std::function<ShadowBridgeEntry()> provider);

    // Convenience: wire up from DemoServer + shadow bridge
    void wire_from_demo_server(const v2::gateway::DemoServerDiagnostics& diag);
    void wire_from_shadow_bridge(
        const v2::gateway::GatewayServerShadowBridge::Diagnostics& diag);

private:
    std::shared_ptr<v2::gateway::BackendMetrics> backend_metrics_;
    std::shared_ptr<v2::service::ServiceRegistry> service_registry_;
    std::function<std::vector<IoCoreEntry>()> io_core_provider_;
    std::function<ShadowBridgeEntry()> shadow_bridge_provider_;
};

}  // namespace v2::diagnostics
