#pragma once

#include "v3/cluster/cluster_router.h"

#include <atomic>
#include <chrono>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

namespace v2::service {

/// Automatic service registration and heartbeat management for ClusterRouter.
///
/// ServiceRegistrar wraps a local backend service registration with:
///   - Automatic registration on start()
///   - Periodic heartbeat updates on a background thread
///   - Custom health check callbacks
///   - Graceful draining and deregistration on stop()
///
/// Thread-safe: uses std::atomic for control flags and a mutex for
/// heartbeat time tracking.
///
/// Usage:
/// @code
///   auto registrar = std::make_shared<ServiceRegistrar>(
///       cluster_router, "login", "127.0.0.1", 9302);
///   registrar->set_health_check_fn([]() { return check_tcp_connect(...); });
///   registrar->start();
///   // ...
///   registrar->stop();
/// @endcode
class ServiceRegistrar {
public:
    /// Construct a registrar.
    /// @param router             The ClusterRouter to register with.
    /// @param service_name       Service type (e.g. "login", "room").
    /// @param host               Host address of this service instance.
    /// @param port               Port of this service instance.
    /// @param node_name          Unique node name (defaults to "host:port").
    /// @param heartbeat_interval Interval between heartbeat updates.
    ServiceRegistrar(
        v3::cluster::ClusterRouter& router,
        std::string service_name,
        std::string host,
        std::uint16_t port,
        std::string node_name = "",
        std::chrono::milliseconds heartbeat_interval = std::chrono::seconds(5));

    ~ServiceRegistrar();

    ServiceRegistrar(const ServiceRegistrar&) = delete;
    ServiceRegistrar& operator=(const ServiceRegistrar&) = delete;
    ServiceRegistrar(ServiceRegistrar&&) = delete;
    ServiceRegistrar& operator=(ServiceRegistrar&&) = delete;

    /// Register with ClusterRouter and start the heartbeat thread.
    void start();

    /// Mark draining, deregister, and stop the heartbeat thread.
    void stop();

    /// Set a custom health-check callback.
    /// Called on each heartbeat tick. If it returns false, the heartbeat
    /// is NOT updated and the ClusterRouter will eventually mark the
    /// instance unhealthy via its own health check cycle.
    void set_health_check_fn(std::function<bool()> fn);

    /// Returns true if the instance is currently registered.
    bool is_registered() const { return registered_.load(std::memory_order_acquire); }

    /// Returns true if the instance is registered and believed healthy.
    bool is_healthy() const;

    const std::string& service_name() const { return service_name_; }
    const std::string& host() const { return host_; }
    std::uint16_t port() const { return port_; }

private:
    void heartbeat_loop();

    v3::cluster::ClusterRouter& router_;
    std::string service_name_;
    std::string host_;
    std::uint16_t port_;
    std::string node_name_;
    std::chrono::milliseconds heartbeat_interval_;
    std::function<bool()> health_check_fn_;
    mutable std::mutex mutex_;
    std::atomic<bool> running_{false};
    std::atomic<bool> registered_{false};
    std::thread heartbeat_thread_;
    std::chrono::steady_clock::time_point last_heartbeat_;
};

}  // namespace v2::service
