// v3.0.0 Phase 13: Cluster router and TLS config tests

#include <gtest/gtest.h>
#include "v3/cluster/cluster_router.h"
#include "v3/cluster/tls_config.h"
#include <thread>

using namespace v3::cluster;

// ─── Cluster Router ─────────────────────────────────────────────────────

TEST(ClusterRouterTest, RegisterAndDiscover) {
    ClusterRouter router;
    ServiceInstance login1{
        .node = {.host="10.0.0.1", .port=9202, .node_name="login-1"},
        .service_name = "login",
    };
    router.register_service(login1);

    auto discovered = router.discover("login");
    ASSERT_TRUE(discovered.has_value());
    EXPECT_EQ(discovered->node.host, "10.0.0.1");
    EXPECT_EQ(discovered->node.port, 9202U);
    EXPECT_EQ(discovered->service_name, "login");
}

TEST(ClusterRouterTest, RoundRobinRouting) {
    ClusterRouter router;
    router.register_service(ServiceInstance{
        .node = {.host="10.0.0.1", .port=9302, .node_name="room-1"},
        .service_name = "room",
    });
    router.register_service(ServiceInstance{
        .node = {.host="10.0.0.2", .port=9302, .node_name="room-2"},
        .service_name = "room",
    });
    router.register_service(ServiceInstance{
        .node = {.host="10.0.0.3", .port=9302, .node_name="room-3"},
        .service_name = "room",
    });

    // Three discovers should return three different nodes (round-robin)
    auto d1 = router.discover("room");
    auto d2 = router.discover("room");
    auto d3 = router.discover("room");
    ASSERT_TRUE(d1.has_value());
    ASSERT_TRUE(d2.has_value());
    ASSERT_TRUE(d3.has_value());
    EXPECT_NE(d1->node.node_name, d2->node.node_name);
    EXPECT_NE(d2->node.node_name, d3->node.node_name);
}

TEST(ClusterRouterTest, DeregisterRemovesNode) {
    ClusterRouter router;
    NodeId node{.host="10.0.0.1", .port=9202, .node_name="login-1"};
    router.register_service(ServiceInstance{.node=node, .service_name="login"});
    router.deregister_service("login", node);

    auto discovered = router.discover("login");
    EXPECT_FALSE(discovered.has_value());
}

TEST(ClusterRouterTest, UnhealthyNodesExcluded) {
    ClusterRouter router;
    NodeId n1{.host="10.0.0.1", .port=9302, .node_name="room-1"};
    router.register_service(ServiceInstance{.node=n1, .service_name="room"});

    router.mark_unhealthy("room", n1);
    auto d = router.discover("room");
    EXPECT_FALSE(d.has_value()) << "Unhealthy node should not be discoverable";
}

TEST(ClusterRouterTest, DrainThenUnhealthy) {
    ClusterRouter router;
    NodeId node{.host="10.0.0.1", .port=9202, .node_name="login-1"};
    router.register_service(ServiceInstance{.node=node, .service_name="login"});

    router.start_drain("login", node);
    // During drain, node is NOT discoverable (don't route new requests)
    auto d = router.discover("login");
    EXPECT_FALSE(d.has_value());
}

TEST(ClusterRouterTest, ServiceDiscoveryAcrossMultipleServices) {
    ClusterRouter router;
    router.register_service(ServiceInstance{
        .node = {.host="10.0.0.1", .node_name="gw-1"}, .service_name="gateway"});
    router.register_service(ServiceInstance{
        .node = {.host="10.0.0.2", .node_name="login-1"}, .service_name="login"});
    router.register_service(ServiceInstance{
        .node = {.host="10.0.0.3", .node_name="room-1"}, .service_name="room"});

    EXPECT_EQ(router.total_services(), 3U);
    EXPECT_TRUE(router.discover("gateway").has_value());
    EXPECT_TRUE(router.discover("login").has_value());
    EXPECT_TRUE(router.discover("room").has_value());
    EXPECT_FALSE(router.discover("battle").has_value());  // not registered
}

TEST(ClusterRouterTest, HealthCheckFlipsUnhealthyNodes) {
    ClusterRouter router;
    router.set_health_check([](const NodeId&) { return true; });  // all healthy

    NodeId node{.host="10.0.0.1", .node_name="test-1"};
    router.register_service(ServiceInstance{.node=node, .service_name="login"});
    router.mark_unhealthy("login", node);

    // Run health checks — should recover
    router.run_health_checks();  // success #1
    router.run_health_checks();  // success #2 → should flip to healthy

    auto d = router.discover("login");
    EXPECT_TRUE(d.has_value()) << "Should recover after enough health checks";
}

// ─── TLS Config ─────────────────────────────────────────────────────────

TEST(TlsConfigTest, DefaultConfigHasCorrectPaths) {
    auto config = default_tls_config();
    EXPECT_FALSE(config.cert.cert_chain_path.empty());
    EXPECT_FALSE(config.cert.private_key_path.empty());
    EXPECT_FALSE(config.cert.ca_cert_path.empty());
    EXPECT_EQ(config.verify_mode, TlsVerifyMode::kMutual);
}

TEST(TlsConfigTest, SecurityPolicyPerService) {
    SecurityPolicy policy;
    EXPECT_TRUE(policy.require_tls);

    auto* login = policy.policy_for("login");
    ASSERT_NE(login, nullptr);
    EXPECT_TRUE(login->tls_required);
    EXPECT_FALSE(login->mtls_required);

    auto* lb = policy.policy_for("leaderboard");
    ASSERT_NE(lb, nullptr);
    EXPECT_TRUE(lb->mtls_required);

    EXPECT_EQ(policy.policy_for("unknown"), nullptr);
}

TEST(TlsConfigTest, CipherListIsNotEmpty) {
    auto config = default_tls_config();
    EXPECT_FALSE(config.cipher_list.empty());
    EXPECT_NE(config.cipher_list.find("AES256"), std::string::npos);
}
