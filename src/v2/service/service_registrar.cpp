#include "v2/service/service_registrar.h"
#include "app/logging.h"

#include <utility>

namespace v2::service {

ServiceRegistrar::ServiceRegistrar(
    v3::cluster::ClusterRouter& router,
    std::string service_name,
    std::string host,
    std::uint16_t port,
    std::string node_name,
    std::chrono::milliseconds heartbeat_interval)
    : router_(router)
    , service_name_(std::move(service_name))
    , host_(std::move(host))
    , port_(port)
    , node_name_(std::move(node_name))
    , heartbeat_interval_(heartbeat_interval) {
    if (node_name_.empty()) {
        node_name_ = host_ + ":" + std::to_string(port_);
    }
}

ServiceRegistrar::~ServiceRegistrar() {
    stop();
}

void ServiceRegistrar::start() {
    v3::cluster::ServiceInstance instance;
    instance.node.host = host_;
    instance.node.port = port_;
    instance.node.node_name = node_name_;
    instance.service_name = service_name_;
    instance.state = v3::cluster::ServiceState::kHealthy;
    instance.registered_at = std::chrono::steady_clock::now();
    instance.last_heartbeat = instance.registered_at;

    {
        std::lock_guard<std::mutex> lock(mutex_);
        last_heartbeat_ = instance.registered_at;
    }

    router_.register_service(std::move(instance));
    registered_.store(true, std::memory_order_release);
    LOG_INFO("ServiceRegistrar: registered {} as {}:{} (node={})",
             service_name_, host_, port_, node_name_);

    // Start background heartbeat thread
    running_.store(true, std::memory_order_release);
    heartbeat_thread_ = std::thread(&ServiceRegistrar::heartbeat_loop, this);
}

void ServiceRegistrar::stop() {
    running_.store(false, std::memory_order_release);
    heartbeat_cv_.notify_all();
    if (heartbeat_thread_.joinable()) {
        heartbeat_thread_.join();
    }

    if (registered_.load(std::memory_order_acquire)) {
        v3::cluster::NodeId node;
        node.host = host_;
        node.port = port_;
        node.node_name = node_name_;

        router_.start_drain(service_name_, node);
        router_.deregister_service(service_name_, node);
        registered_.store(false, std::memory_order_release);
        LOG_INFO("ServiceRegistrar: deregistered {} node={}", service_name_, node_name_);
    }
}

void ServiceRegistrar::set_health_check_fn(std::function<bool()> fn) {
    std::lock_guard<std::mutex> lock(mutex_);
    health_check_fn_ = std::move(fn);
}

bool ServiceRegistrar::is_healthy() const {
    if (!registered_.load(std::memory_order_acquire)) {
        return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (!health_check_fn_) {
        return true;  // No health check function = assume healthy
    }
    return health_check_fn_();
}

void ServiceRegistrar::heartbeat_loop() {
    while (running_.load(std::memory_order_acquire)) {
        std::function<bool()> health_check_fn;
        {
            std::unique_lock<std::mutex> lock(mutex_);
            const bool stop_requested = heartbeat_cv_.wait_for(
                lock,
                heartbeat_interval_,
                [this]() { return !running_.load(std::memory_order_acquire); });
            if (stop_requested || !running_.load(std::memory_order_acquire)) {
                break;
            }
            health_check_fn = health_check_fn_;
        }

        bool healthy = true;
        if (health_check_fn) {
            healthy = health_check_fn();
        }

        if (!running_.load(std::memory_order_acquire)) {
            break;
        }

        if (!healthy) {
            LOG_WARN("ServiceRegistrar: {} node={} heartbeat FAILED, skipping update",
                     service_name_, node_name_);
            // Don't update heartbeat — ClusterRouter::run_health_checks()
            // will eventually mark this instance unhealthy when it detects
            // stale last_heartbeat or the health_check_fn_ itself fails.
            continue;
        }

        // Update heartbeat: mark healthy so ClusterRouter knows this instance is alive
        v3::cluster::NodeId node;
        node.host = host_;
        node.port = port_;
        node.node_name = node_name_;
        router_.mark_healthy(service_name_, node);

        {
            std::lock_guard<std::mutex> lock(mutex_);
            last_heartbeat_ = std::chrono::steady_clock::now();
        }

        LOG_DEBUG("ServiceRegistrar: {} node={} heartbeat OK", service_name_, node_name_);
    }
}

}  // namespace v2::service
