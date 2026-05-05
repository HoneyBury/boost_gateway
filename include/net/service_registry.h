#pragma once

#include "net/protocol.h"
#include "net/service_router.h"

#include <chrono>
#include <cstdint>
#include <memory>
#include <mutex>
#include <shared_mutex>
#include <string>
#include <vector>

namespace net {

struct ServiceInstance {
    ServiceId service_id;
    std::string host;
    std::uint16_t port;
    bool healthy = true;
    std::chrono::steady_clock::time_point last_health_check;
};

class ServiceRegistry {
public:
    void register_instance(ServiceInstance instance) {
        std::unique_lock lock(mutex_);
        instances_.push_back(std::move(instance));
    }

    void mark_healthy(const std::string& host, std::uint16_t port, bool healthy) {
        std::unique_lock lock(mutex_);
        for (auto& inst : instances_) {
            if (inst.host == host && inst.port == port) {
                inst.healthy = healthy;
                inst.last_health_check = std::chrono::steady_clock::now();
            }
        }
    }

    [[nodiscard]] std::vector<ServiceInstance> healthy_instances(ServiceId service_id) const {
        std::shared_lock lock(mutex_);
        std::vector<ServiceInstance> result;
        for (const auto& inst : instances_) {
            if (inst.service_id == service_id && inst.healthy) {
                result.push_back(inst);
            }
        }
        return result;
    }

    [[nodiscard]] std::vector<ServiceInstance> all_instances() const {
        std::shared_lock lock(mutex_);
        return instances_;
    }

    [[nodiscard]] std::size_t instance_count() const {
        std::shared_lock lock(mutex_);
        return instances_.size();
    }

private:
    mutable std::shared_mutex mutex_;
    std::vector<ServiceInstance> instances_;
};

}  // namespace net
