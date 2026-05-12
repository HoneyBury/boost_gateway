#pragma once
// v3.0.0 D1: Cluster Router — cross-node service discovery and routing.

#include <chrono>
#include <cstdint>
#include <functional>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace v3::cluster {

// ── Node identity ──────────────────────────────────────────────────────

struct NodeId {
    std::string host;
    std::uint16_t port = 0;
    std::string node_name;  // unique per cluster

    bool operator==(const NodeId& o) const { return node_name == o.node_name; }
    bool operator<(const NodeId& o) const { return node_name < o.node_name; }
};

// ── Service instance ───────────────────────────────────────────────────

enum class ServiceState : std::uint8_t {
    kUnknown = 0,
    kHealthy = 1,
    kUnhealthy = 2,
    kDraining = 3,  // shutting down, don't route new requests
};

struct ServiceInstance {
    NodeId node;
    std::string service_name;  // "gateway", "login", "room", "battle", "match", "leaderboard"
    ServiceState state = ServiceState::kUnknown;
    std::chrono::steady_clock::time_point last_heartbeat;
    std::chrono::steady_clock::time_point registered_at;
    std::uint32_t weight = 1;  // for weighted round-robin
};

// ── Route table entry ─────────────────────────────────────────────────

struct RouteEntry {
    std::string service_name;
    std::vector<ServiceInstance> instances;  // healthy instances only
    std::uint32_t round_robin_index = 0;     // for load balancing

    [[nodiscard]] std::optional<ServiceInstance> next() const {
        if (instances.empty()) return std::nullopt;
        return instances[round_robin_index % instances.size()];
    }
};

// ─── Health check config ────────────────────────────────────────────────

struct HealthCheckConfig {
    std::chrono::milliseconds interval{5'000};
    std::chrono::milliseconds timeout{2'000};
    std::uint32_t failure_threshold = 3;    // consecutive failures → unhealthy
    std::uint32_t recovery_threshold = 2;   // consecutive successes → healthy
    std::chrono::milliseconds drain_timeout{30'000};  // max time in draining state
};

// ── Cluster Router ─────────────────────────────────────────────────────

class ClusterRouter {
public:
    using HealthCheckFn = std::function<bool(const NodeId&)>;

    explicit ClusterRouter(HealthCheckConfig config = {});
    ~ClusterRouter();

    ClusterRouter(const ClusterRouter&) = delete;
    ClusterRouter& operator=(const ClusterRouter&) = delete;

    // ── Registration ──────────────────────────────────────────────────

    /// Register a service instance with the cluster.
    void register_service(ServiceInstance instance);

    /// Deregister a service instance.
    void deregister_service(const std::string& service_name, const NodeId& node);

    // ── Discovery ─────────────────────────────────────────────────────

    /// Get a healthy instance for a service (round-robin).
    [[nodiscard]] std::optional<ServiceInstance> discover(
        const std::string& service_name);

    /// Get all healthy instances for a service.
    [[nodiscard]] std::vector<ServiceInstance> discover_all(
        const std::string& service_name);

    /// Get the full route table (for diagnostics).
    [[nodiscard]] std::unordered_map<std::string, RouteEntry> route_table() const;

    // ── Health ────────────────────────────────────────────────────────

    /// Set health check function (e.g., TCP connect to node).
    void set_health_check(HealthCheckFn fn) { health_check_fn_ = std::move(fn); }

    /// Run one health check cycle. Should be called periodically.
    void run_health_checks();

    /// Mark a service instance as healthy/unhealthy.
    void mark_healthy(const std::string& service_name, const NodeId& node);
    void mark_unhealthy(const std::string& service_name, const NodeId& node);

    // ── Draining ──────────────────────────────────────────────────────

    /// Start draining a node (stop routing new requests, wait for existing).
    void start_drain(const std::string& service_name, const NodeId& node);

    // ── Stats ─────────────────────────────────────────────────────────

    [[nodiscard]] std::size_t total_services() const;
    [[nodiscard]] std::size_t healthy_count(const std::string& service_name) const;
    [[nodiscard]] std::size_t unhealthy_count(const std::string& service_name) const;

private:
    ServiceInstance* find_instance(const std::string& service_name,
                                    const NodeId& node);

    HealthCheckConfig config_;
    HealthCheckFn health_check_fn_;
    mutable std::mutex mutex_;
    std::unordered_map<std::string, RouteEntry> routes_;
    std::unordered_map<std::string, std::uint32_t> failure_counts_;
    std::unordered_map<std::string, std::uint32_t> success_counts_;
};

// ── Implementation ──────────────────────────────────────────────────────

inline ClusterRouter::ClusterRouter(HealthCheckConfig config)
    : config_(std::move(config)) {}

inline ClusterRouter::~ClusterRouter() = default;

inline void ClusterRouter::register_service(ServiceInstance instance) {
    std::lock_guard lock(mutex_);
    instance.registered_at = std::chrono::steady_clock::now();
    instance.last_heartbeat = instance.registered_at;
    if (instance.state == ServiceState::kUnknown) {
        instance.state = ServiceState::kHealthy;
    }

    auto& route = routes_[instance.service_name];
    route.service_name = instance.service_name;

    // Replace existing or add new
    auto* existing = find_instance(instance.service_name, instance.node);
    if (existing) {
        *existing = std::move(instance);
    } else {
        route.instances.push_back(std::move(instance));
    }
}

inline void ClusterRouter::deregister_service(
    const std::string& service_name, const NodeId& node) {
    std::lock_guard lock(mutex_);
    auto it = routes_.find(service_name);
    if (it == routes_.end()) return;
    auto& instances = it->second.instances;
    instances.erase(
        std::remove_if(instances.begin(), instances.end(),
                       [&](const ServiceInstance& si) { return si.node == node; }),
        instances.end());
}

inline std::optional<ServiceInstance> ClusterRouter::discover(
    const std::string& service_name) {
    std::lock_guard lock(mutex_);
    auto it = routes_.find(service_name);
    if (it == routes_.end()) return std::nullopt;

    auto& instances = it->second.instances;
    std::vector<ServiceInstance*> healthy;
    for (auto& inst : instances) {
        if (inst.state == ServiceState::kHealthy) healthy.push_back(&inst);
    }
    if (healthy.empty()) return std::nullopt;

    auto idx = it->second.round_robin_index++ % healthy.size();
    return *healthy[idx];
}

inline std::vector<ServiceInstance> ClusterRouter::discover_all(
    const std::string& service_name) {
    std::lock_guard lock(mutex_);
    auto it = routes_.find(service_name);
    if (it == routes_.end()) return {};
    return it->second.instances;
}

inline std::unordered_map<std::string, RouteEntry> ClusterRouter::route_table() const {
    std::lock_guard lock(mutex_);
    return routes_;
}

inline void ClusterRouter::run_health_checks() {
    if (!health_check_fn_) return;

    std::lock_guard lock(mutex_);
    for (auto& [name, route] : routes_) {
        for (auto& inst : route.instances) {
            if (inst.state == ServiceState::kDraining) {
                auto elapsed = std::chrono::steady_clock::now() - inst.last_heartbeat;
                if (elapsed > config_.drain_timeout) {
                    inst.state = ServiceState::kUnhealthy;
                }
                continue;
            }

            bool alive = health_check_fn_(inst.node);
            auto key = name + ":" + inst.node.node_name;

            if (alive) {
                auto& sc = success_counts_[key];
                sc++;
                if (inst.state == ServiceState::kUnhealthy &&
                    sc >= config_.recovery_threshold) {
                    inst.state = ServiceState::kHealthy;
                    sc = 0;
                }
                failure_counts_[key] = 0;
                inst.last_heartbeat = std::chrono::steady_clock::now();
            } else {
                auto& fc = failure_counts_[key];
                fc++;
                if (fc >= config_.failure_threshold) {
                    inst.state = ServiceState::kUnhealthy;
                }
                success_counts_[key] = 0;
            }
        }
    }
}

inline void ClusterRouter::mark_healthy(
    const std::string& service_name, const NodeId& node) {
    std::lock_guard lock(mutex_);
    auto* inst = find_instance(service_name, node);
    if (inst) inst->state = ServiceState::kHealthy;
}

inline void ClusterRouter::mark_unhealthy(
    const std::string& service_name, const NodeId& node) {
    std::lock_guard lock(mutex_);
    auto* inst = find_instance(service_name, node);
    if (inst) inst->state = ServiceState::kUnhealthy;
}

inline void ClusterRouter::start_drain(
    const std::string& service_name, const NodeId& node) {
    std::lock_guard lock(mutex_);
    auto* inst = find_instance(service_name, node);
    if (inst) {
        inst->state = ServiceState::kDraining;
        inst->last_heartbeat = std::chrono::steady_clock::now();
    }
}

inline std::size_t ClusterRouter::total_services() const {
    std::lock_guard lock(mutex_);
    return routes_.size();
}

inline std::size_t ClusterRouter::healthy_count(
    const std::string& service_name) const {
    std::lock_guard lock(mutex_);
    auto it = routes_.find(service_name);
    if (it == routes_.end()) return 0;
    std::size_t count = 0;
    for (auto& inst : it->second.instances) {
        if (inst.state == ServiceState::kHealthy) ++count;
    }
    return count;
}

inline std::size_t ClusterRouter::unhealthy_count(
    const std::string& service_name) const {
    std::lock_guard lock(mutex_);
    auto it = routes_.find(service_name);
    if (it == routes_.end()) return 0;
    std::size_t count = 0;
    for (auto& inst : it->second.instances) {
        if (inst.state == ServiceState::kUnhealthy) ++count;
    }
    return count;
}

inline ServiceInstance* ClusterRouter::find_instance(
    const std::string& service_name, const NodeId& node) {
    auto it = routes_.find(service_name);
    if (it == routes_.end()) return nullptr;
    for (auto& inst : it->second.instances) {
        if (inst.node == node) return &inst;
    }
    return nullptr;
}

}  // namespace v3::cluster
