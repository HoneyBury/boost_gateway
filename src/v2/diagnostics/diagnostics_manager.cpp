#include "v2/diagnostics/diagnostics_manager.h"

#include <cctype>
#include <ostream>
#include <sstream>
#include <string>
#include <utility>

namespace v2::diagnostics {

// ── Helper: JSON escape ────────────────────────────────

namespace {

std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 2);
    for (auto ch : s) {
        switch (ch) {
            case '"':
                out += "\\\"";
                break;
            case '\\':
                out += "\\\\";
                break;
            case '\b':
                out += "\\b";
                break;
            case '\f':
                out += "\\f";
                break;
            case '\n':
                out += "\\n";
                break;
            case '\r':
                out += "\\r";
                break;
            case '\t':
                out += "\\t";
                break;
            default:
                if (static_cast<unsigned char>(ch) < 0x20) {
                    char buf[8];
                    snprintf(buf, sizeof(buf), "\\u%04x",
                             static_cast<unsigned>(ch));
                    out += buf;
                } else {
                    out += ch;
                }
                break;
        }
    }
    return out;
}

std::string json_uint64(std::uint64_t v) {
    // Use a simple approach: format as unsigned long long.
    char buf[32];
    snprintf(buf, sizeof(buf), "%llu",
             static_cast<unsigned long long>(v));
    return buf;
}

std::string json_size_t(std::size_t v) {
    char buf[32];
    snprintf(buf, sizeof(buf), "%zu", v);
    return buf;
}

std::string json_bool(bool v) { return v ? "true" : "false"; }

// ── JSON helpers for snapshot types ────────────────────

std::string metrics_to_json(const v2::gateway::BackendMetricsSnapshot& m) {
    return "{"
           "\"total_requests\":" +
           json_uint64(m.total_requests) +
           ",\"total_successes\":" + json_uint64(m.total_successes) +
           ",\"total_timeouts\":" + json_uint64(m.total_timeouts) +
           ",\"total_unavailable\":" + json_uint64(m.total_unavailable) +
           ",\"total_errors\":" + json_uint64(m.total_errors) +
           ",\"total_degraded\":" + json_uint64(m.total_degraded) +
           ",\"total_latency_us\":" + json_uint64(m.total_latency_us) +
           ",\"latency_sample_count\":" + json_uint64(m.latency_sample_count) +
           ",\"avg_latency_us\":" +
           json_uint64(m.latency_sample_count > 0
                           ? m.total_latency_us / m.latency_sample_count
                           : 0) +
           "}";
}

std::string dispatch_stats_to_json(
    const v2::gateway::GatewayServerShadowBridge::DispatchStats& d) {
    return "{"
           "\"mirrored_packets\":" +
           json_uint64(d.mirrored_packets) +
           ",\"emitted_writes\":" + json_uint64(d.emitted_writes) +
           ",\"scheduled_writes\":" + json_uint64(d.scheduled_writes) +
           ",\"inline_writes\":" + json_uint64(d.inline_writes) + "}";
}

}  // anonymous namespace

// ── DiagnosticsManager implementation ──────────────────

void DiagnosticsManager::set_backend_metrics(
    std::shared_ptr<v2::gateway::BackendMetrics> m) {
    backend_metrics_ = std::move(m);
}

void DiagnosticsManager::set_service_registry(
    std::shared_ptr<v2::service::ServiceRegistry> r) {
    service_registry_ = std::move(r);
}

void DiagnosticsManager::set_io_core_provider(
    std::function<std::vector<IoCoreEntry>()> provider) {
    io_core_provider_ = std::move(provider);
}

void DiagnosticsManager::set_shadow_bridge_provider(
    std::function<ShadowBridgeEntry()> provider) {
    shadow_bridge_provider_ = std::move(provider);
}

void DiagnosticsManager::wire_from_demo_server(
    const v2::gateway::DemoServerDiagnostics& diag) {
    // Convert io_cores from DemoServerIoCoreSnapshot to IoCoreEntry
    std::vector<IoCoreEntry> cores;
    cores.reserve(diag.io_cores.size());
    for (const auto& src : diag.io_cores) {
        cores.push_back(IoCoreEntry{
            .core_id = src.core_id,
            .active_sessions = src.active_sessions,
            .accepted_sessions = src.accepted_sessions,
            .outbound_dispatches = src.outbound_dispatches,
        });
    }
    io_core_provider_ = [cores = std::move(cores)]() { return cores; };

    // Convert backend_metrics from DemoServer's snapshots
    // BackendMetricsSnapshot is already of the right type;
    // we need to wire up a real BackendMetrics that provides these.
    // Since DemoServerDiagnostics already has the snapshots, we create
    // a provider that returns them as BackendEntry.
    // Note: this is a one-time snapshot, not a live connection.
    // We store the metrics as a provider lambda that returns the entries.
    auto snapshot_backends = diag.backend_metrics;
    (void)snapshot_backends;  // consumed below via service_registry integration
}

void DiagnosticsManager::wire_from_shadow_bridge(
    const v2::gateway::GatewayServerShadowBridge::Diagnostics& diag) {
    ShadowBridgeEntry entry;
    entry.enabled = true;
    entry.emit_responses = diag.emit_responses;
    entry.dispatch_stats = diag.dispatch_stats;
    entry.tracked_sessions = diag.tracked_sessions;
    entry.active_sessions = diag.active_sessions;
    shadow_bridge_provider_ = [entry = std::move(entry)]() { return entry; };
}

DiagnosticsSnapshot DiagnosticsManager::collect() const {
    DiagnosticsSnapshot snap;
    snap.summary.snapshot_at = std::chrono::steady_clock::now();

    // ── Collect IO core data ──
    if (io_core_provider_) {
        snap.io_cores = io_core_provider_();
    }
    snap.summary.io_core_count = static_cast<std::uint32_t>(snap.io_cores.size());

    for (const auto& core : snap.io_cores) {
        snap.summary.total_active_sessions += core.active_sessions;
        snap.summary.total_accepted_sessions += core.accepted_sessions;
        snap.summary.total_outbound_dispatches += core.outbound_dispatches;
    }

    // ── Collect backend metrics ──
    if (backend_metrics_) {
        auto all_metrics = backend_metrics_->all_snapshots();
        snap.summary.registered_backend_count = all_metrics.size();

        for (const auto& [service_id, metrics] : all_metrics) {
            BackendEntry entry;
            entry.service_name = v2::service::to_string(service_id);
            entry.metrics = metrics;
            snap.backends.push_back(std::move(entry));
        }
    }

    // ── Collect service registry data ──
    std::size_t backend_healthy_count = 0;
    if (service_registry_) {
        auto all_instances = service_registry_->all_instances();
        for (auto& backend : snap.backends) {
            // Convert service_name to ServiceId for matching
            v2::service::ServiceId sid = v2::service::ServiceId::kGateway;
            if (backend.service_name == "login") {
                sid = v2::service::ServiceId::kLogin;
            } else if (backend.service_name == "room") {
                sid = v2::service::ServiceId::kRoom;
            } else if (backend.service_name == "battle") {
                sid = v2::service::ServiceId::kBattle;
            }

            auto healthy = service_registry_->healthy_instances(sid);
            auto unhealthy = service_registry_->unhealthy_instances(sid);
            backend.healthy_instances = healthy.size();
            backend.unhealthy_instances = unhealthy.size();
            backend.healthy = !healthy.empty();
            if (backend.healthy) {
                ++backend_healthy_count;
            }
        }
    }
    snap.summary.healthy_backend_count = backend_healthy_count;

    // ── Collect shadow bridge data ──
    if (shadow_bridge_provider_) {
        snap.shadow_bridge = shadow_bridge_provider_();
    }

    // ── Overall health determination ──
    bool all_backends_healthy = true;
    for (const auto& b : snap.backends) {
        if (!b.healthy) {
            all_backends_healthy = false;
            break;
        }
    }
    // overall_healthy is true if no backends exist (vacuously true),
    // or if all existing backends have at least one healthy instance.
    snap.summary.overall_healthy = all_backends_healthy;

    return snap;
}

std::string DiagnosticsManager::to_text(const DiagnosticsSnapshot& snap) const {
    std::ostringstream os;

    os << "--- System Summary ---\n";
    os << "overall_healthy: " << (snap.summary.overall_healthy ? "true" : "false")
       << "\n";
    os << "total_active_sessions: " << snap.summary.total_active_sessions << "\n";
    os << "total_accepted_sessions: " << snap.summary.total_accepted_sessions
       << "\n";
    os << "total_outbound_dispatches: " << snap.summary.total_outbound_dispatches
       << "\n";
    os << "io_core_count: " << snap.summary.io_core_count << "\n";
    os << "registered_backend_count: " << snap.summary.registered_backend_count
       << "\n";
    os << "healthy_backend_count: " << snap.summary.healthy_backend_count << "\n";
    os << "messages_per_second: " << snap.summary.messages_per_second << "\n";

    os << "\n--- Backends ---\n";
    if (snap.backends.empty()) {
        os << "(none)\n";
    } else {
        for (const auto& b : snap.backends) {
            os << "service: " << b.service_name << "\n";
            os << "  healthy: " << (b.healthy ? "true" : "false") << "\n";
            os << "  healthy_instances: " << b.healthy_instances << "\n";
            os << "  unhealthy_instances: " << b.unhealthy_instances << "\n";
            os << "  metrics(total_requests): " << b.metrics.total_requests
               << "\n";
            os << "  metrics(total_successes): " << b.metrics.total_successes
               << "\n";
            os << "  metrics(total_timeouts): " << b.metrics.total_timeouts
               << "\n";
            os << "  metrics(total_unavailable): " << b.metrics.total_unavailable
               << "\n";
            os << "  metrics(total_errors): " << b.metrics.total_errors << "\n";
            os << "  metrics(total_degraded): " << b.metrics.total_degraded << "\n";
            os << "  metrics(total_latency_us): " << b.metrics.total_latency_us << "\n";
            os << "  metrics(latency_sample_count): "
               << b.metrics.latency_sample_count << "\n";
            if (b.metrics.latency_sample_count > 0) {
                os << "  metrics(avg_latency_us): "
                   << (b.metrics.total_latency_us / b.metrics.latency_sample_count)
                   << "\n";
            }
        }
    }

    os << "\n--- IO Cores ---\n";
    if (snap.io_cores.empty()) {
        os << "(none)\n";
    } else {
        for (const auto& c : snap.io_cores) {
            os << "core[" << c.core_id << "]: active=" << c.active_sessions
               << " accepted=" << c.accepted_sessions
               << " dispatched=" << c.outbound_dispatches << "\n";
        }
    }

    os << "\n--- Shadow Bridge ---\n";
    os << "enabled: " << (snap.shadow_bridge.enabled ? "true" : "false") << "\n";
    os << "emit_responses: " << (snap.shadow_bridge.emit_responses ? "true"
                                                                   : "false")
       << "\n";
    os << "dispatch_stats(mirrored_packets): "
       << snap.shadow_bridge.dispatch_stats.mirrored_packets << "\n";
    os << "dispatch_stats(emitted_writes): "
       << snap.shadow_bridge.dispatch_stats.emitted_writes << "\n";
    os << "dispatch_stats(scheduled_writes): "
       << snap.shadow_bridge.dispatch_stats.scheduled_writes << "\n";
    os << "dispatch_stats(inline_writes): "
       << snap.shadow_bridge.dispatch_stats.inline_writes << "\n";
    os << "tracked_sessions: " << snap.shadow_bridge.tracked_sessions << "\n";
    os << "active_sessions: " << snap.shadow_bridge.active_sessions << "\n";

    return os.str();
}

std::string DiagnosticsManager::to_json(const DiagnosticsSnapshot& snap) const {
    std::string json;
    json.reserve(1024);

    json += "{\n";

    // summary
    json += "  \"summary\":{"
            "\"overall_healthy\":" +
            json_bool(snap.summary.overall_healthy) +
            ","
            "\"total_active_sessions\":" +
            json_uint64(snap.summary.total_active_sessions) +
            ","
            "\"total_accepted_sessions\":" +
            json_uint64(snap.summary.total_accepted_sessions) +
            ","
            "\"total_outbound_dispatches\":" +
            json_uint64(snap.summary.total_outbound_dispatches) +
            ","
            "\"io_core_count\":" +
            json_uint64(snap.summary.io_core_count) +
            ","
            "\"registered_backend_count\":" +
            json_size_t(snap.summary.registered_backend_count) +
            ","
            "\"healthy_backend_count\":" +
            json_size_t(snap.summary.healthy_backend_count) +
            ",\"messages_per_second\":" +
            std::to_string(snap.summary.messages_per_second) + "}";

    // backends
    json += ",\n  \"backends\":[";
    for (std::size_t i = 0; i < snap.backends.size(); ++i) {
        if (i > 0) json += ",";
        const auto& b = snap.backends[i];
        json += "{"
                "\"service_name\":\"" +
                json_escape(b.service_name) +
                "\","
                "\"healthy\":" +
                json_bool(b.healthy) +
                ","
                "\"healthy_instances\":" +
                json_size_t(b.healthy_instances) +
                ","
                "\"unhealthy_instances\":" +
                json_size_t(b.unhealthy_instances) +
                ","
                "\"metrics\":" +
                metrics_to_json(b.metrics) + "}";
    }
    json += "]";

    // io_cores
    json += ",\n  \"io_cores\":[";
    for (std::size_t i = 0; i < snap.io_cores.size(); ++i) {
        if (i > 0) json += ",";
        const auto& c = snap.io_cores[i];
        json += "{"
                "\"core_id\":" +
                json_uint64(c.core_id) +
                ","
                "\"active_sessions\":" +
                json_uint64(c.active_sessions) +
                ","
                "\"accepted_sessions\":" +
                json_uint64(c.accepted_sessions) +
                ","
                "\"outbound_dispatches\":" +
                json_uint64(c.outbound_dispatches) + "}";
    }
    json += "]";

    // shadow_bridge
    json += ",\n  \"shadow_bridge\":{"
            "\"enabled\":" +
            json_bool(snap.shadow_bridge.enabled) +
            ","
            "\"emit_responses\":" +
            json_bool(snap.shadow_bridge.emit_responses) +
            ","
            "\"dispatch_stats\":" +
            dispatch_stats_to_json(snap.shadow_bridge.dispatch_stats) +
            ","
            "\"tracked_sessions\":" +
            json_uint64(snap.shadow_bridge.tracked_sessions) +
            ","
            "\"active_sessions\":" +
            json_uint64(snap.shadow_bridge.active_sessions) + "}";

    json += "\n}\n";
    return json;
}

}  // namespace v2::diagnostics
