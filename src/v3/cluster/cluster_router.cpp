// v3.0.0 D1: ClusterRouter implementation.

#include "v3/cluster/cluster_router.h"

#include <algorithm>
#include <string>
#include <vector>

namespace v3::cluster {

ClusterRouter::ClusterRouter(HealthCheckConfig config)
    : config_(std::move(config)) {}

ClusterRouter::~ClusterRouter() = default;

void ClusterRouter::register_service(ServiceInstance instance) {
    std::lock_guard lock(mutex_);
    instance.registered_at = std::chrono::steady_clock::now();
    instance.last_heartbeat = instance.registered_at;
    if (instance.state == ServiceState::kUnknown) {
        instance.state = ServiceState::kHealthy;
    }

    auto& route = routes_[instance.service_name];
    route.service_name = instance.service_name;

    auto* existing = find_instance(instance.service_name, instance.node);
    if (existing) {
        *existing = std::move(instance);
    } else {
        route.instances.push_back(std::move(instance));
    }
}

void ClusterRouter::deregister_service(
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

std::optional<ServiceInstance> ClusterRouter::discover(
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

std::vector<ServiceInstance> ClusterRouter::discover_all(
    const std::string& service_name) {
    std::lock_guard lock(mutex_);
    auto it = routes_.find(service_name);
    if (it == routes_.end()) return {};
    return it->second.instances;
}

std::unordered_map<std::string, RouteEntry> ClusterRouter::route_table() const {
    std::lock_guard lock(mutex_);
    return routes_;
}

void ClusterRouter::run_health_checks() {
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

void ClusterRouter::mark_healthy(
    const std::string& service_name, const NodeId& node) {
    std::lock_guard lock(mutex_);
    auto* inst = find_instance(service_name, node);
    if (inst) inst->state = ServiceState::kHealthy;
}

void ClusterRouter::mark_unhealthy(
    const std::string& service_name, const NodeId& node) {
    std::lock_guard lock(mutex_);
    auto* inst = find_instance(service_name, node);
    if (inst) inst->state = ServiceState::kUnhealthy;
}

void ClusterRouter::start_drain(
    const std::string& service_name, const NodeId& node) {
    std::lock_guard lock(mutex_);
    auto* inst = find_instance(service_name, node);
    if (inst) {
        inst->state = ServiceState::kDraining;
        inst->last_heartbeat = std::chrono::steady_clock::now();
    }
}

std::size_t ClusterRouter::total_services() const {
    std::lock_guard lock(mutex_);
    return routes_.size();
}

std::size_t ClusterRouter::healthy_count(
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

std::size_t ClusterRouter::unhealthy_count(
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

ServiceInstance* ClusterRouter::find_instance(
    const std::string& service_name, const NodeId& node) {
    auto it = routes_.find(service_name);
    if (it == routes_.end()) return nullptr;
    for (auto& inst : it->second.instances) {
        if (inst.node == node) return &inst;
    }
    return nullptr;
}

}  // namespace v3::cluster
