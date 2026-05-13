#include <gtest/gtest.h>

#include "v2/service/service_registry.h"

#include <chrono>
#include <thread>

using namespace v2::service;

TEST(V2ServiceRegistryTest, RegisterAndQueryHealthy) {
    ServiceRegistry registry(std::chrono::milliseconds(100));

    registry.register_instance(ServiceId::kLogin, "127.0.0.1", 9001);

    auto healthy = registry.healthy_instances(ServiceId::kLogin);
    ASSERT_EQ(healthy.size(), 1U);
    EXPECT_EQ(healthy[0].host, "127.0.0.1");
    EXPECT_EQ(healthy[0].port, 9001U);
    EXPECT_TRUE(healthy[0].healthy);
}

TEST(V2ServiceRegistryTest, HeartbeatRefreshesTTL) {
    ServiceRegistry registry(std::chrono::milliseconds(200));

    registry.register_instance(ServiceId::kLogin, "127.0.0.1", 9001);

    // Sleep past TTL, but heartbeat midway
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    registry.heartbeat(ServiceId::kLogin, "127.0.0.1", 9001);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Instance should still exist because heartbeat refreshed TTL
    auto purged = registry.purge_expired();
    EXPECT_EQ(purged, 0U);
    EXPECT_EQ(registry.instance_count(), 1U);
}

TEST(V2ServiceRegistryTest, ExpiredInstanceRemovedByPurge) {
    ServiceRegistry registry(std::chrono::milliseconds(20));

    registry.register_instance(ServiceId::kLogin, "127.0.0.1", 9001);

    std::this_thread::sleep_for(std::chrono::milliseconds(40));

    auto purged = registry.purge_expired();
    EXPECT_EQ(purged, 1U);
    EXPECT_EQ(registry.instance_count(), 0U);
}

TEST(V2ServiceRegistryTest, MarkUnhealthyExcludesFromHealthy) {
    ServiceRegistry registry(std::chrono::milliseconds(100));

    registry.register_instance(ServiceId::kLogin, "127.0.0.1", 9001);

    registry.mark_unhealthy(ServiceId::kLogin, "127.0.0.1", 9001);

    auto healthy = registry.healthy_instances(ServiceId::kLogin);
    EXPECT_TRUE(healthy.empty());

    auto unhealthy = registry.unhealthy_instances(ServiceId::kLogin);
    ASSERT_EQ(unhealthy.size(), 1U);
    EXPECT_FALSE(unhealthy[0].healthy);
}

TEST(V2ServiceRegistryTest, HeartbeatRestoresHealth) {
    ServiceRegistry registry(std::chrono::milliseconds(100));

    registry.register_instance(ServiceId::kRoom, "127.0.0.1", 9002);
    registry.mark_unhealthy(ServiceId::kRoom, "127.0.0.1", 9002);

    // Heartbeat should auto-restore health
    registry.heartbeat(ServiceId::kRoom, "127.0.0.1", 9002);

    auto healthy = registry.healthy_instances(ServiceId::kRoom);
    ASSERT_EQ(healthy.size(), 1U);
    EXPECT_TRUE(healthy[0].healthy);
}

TEST(V2ServiceRegistryTest, DuplicateRegistrationOverwrites) {
    ServiceRegistry registry(std::chrono::milliseconds(100));

    registry.register_instance(ServiceId::kRoom, "127.0.0.1", 9002);
    // Second registration with same host:port:service_id
    registry.register_instance(ServiceId::kRoom, "127.0.0.1", 9002);

    EXPECT_EQ(registry.instance_count(), 1U);
}

TEST(V2ServiceRegistryTest, MultipleServicesIsolation) {
    ServiceRegistry registry(std::chrono::milliseconds(100));

    registry.register_instance(ServiceId::kLogin, "127.0.0.1", 9001);
    registry.register_instance(ServiceId::kRoom, "127.0.0.1", 9002);
    registry.register_instance(ServiceId::kBattle, "127.0.0.1", 9003);

    EXPECT_EQ(registry.instance_count(), 3U);

    EXPECT_EQ(registry.healthy_instances(ServiceId::kLogin).size(), 1U);
    EXPECT_EQ(registry.healthy_instances(ServiceId::kRoom).size(), 1U);
    EXPECT_EQ(registry.healthy_instances(ServiceId::kBattle).size(), 1U);

    registry.mark_unhealthy(ServiceId::kRoom, "127.0.0.1", 9002);
    EXPECT_EQ(registry.healthy_instances(ServiceId::kRoom).size(), 0U);
    EXPECT_EQ(registry.healthy_instances(ServiceId::kLogin).size(), 1U);
}
