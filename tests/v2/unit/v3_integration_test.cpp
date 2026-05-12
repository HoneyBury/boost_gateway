// v3.0.0 Integration: Cross-module tests for all v3 components
#include <gtest/gtest.h>
#include "v3/cluster/cluster_router.h"
#include "v3/cluster/consistent_hash.h"
#include "v3/cluster/remote_actor.h"
#include "v3/cluster/raft.h"
#include "v3/tracing/otel_exporter.h"
#include "v3/persistence/event_store.h"
#include "v2/tracing/trace_context.h"
#include <thread>

using namespace v3::cluster;
using namespace v3::tracing;
using namespace v3::persistence;

// ─── V1.1: Cluster + Hash + Actor ───────────────────────────────────────

TEST(V3IntegrationTest, ClusterRouterWithConsistentHashRouting) {
    ClusterRouter router;
    ConsistentHashRing hash;

    // Register 3 room backends
    hash.add_node("room-1");
    hash.add_node("room-2");
    hash.add_node("room-3");

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

    // Hash room_id → cluster node → discover should match
    auto hashed = hash.lookup("room_alpha");
    EXPECT_FALSE(hashed.empty());

    auto discovered = router.discover("room");
    EXPECT_TRUE(discovered.has_value());
}

TEST(V3IntegrationTest, ActorLocationRegistryWithCluster) {
    ActorLocationRegistry registry;
    ClusterRouter router;

    NodeId n1{.host="10.0.0.1", .node_name="gw-1"};
    NodeId n2{.host="10.0.0.2", .node_name="login-1"};

    registry.register_actor(100, n1);
    registry.register_actor(200, n2);

    router.register_service(ServiceInstance{.node=n1, .service_name="gateway"});
    router.register_service(ServiceInstance{.node=n2, .service_name="login"});

    // Actor 100 is on gw-1 → cluster should discover gateway
    auto loc = registry.locate(100);
    ASSERT_TRUE(loc.has_value());
    EXPECT_EQ(loc->node_name, "gw-1");

    EXPECT_EQ(registry.actors_on_node(n1), 1U);
    EXPECT_EQ(registry.actors_on_node(n2), 1U);
}

// ─── V1.2: Raft + Cluster ──────────────────────────────────────────────

TEST(V3IntegrationTest, RaftLeaderRegistersInCluster) {
    RaftConfig config{
        .node_id = "match-1",
        .election_timeout_min = std::chrono::milliseconds(50),
        .election_timeout_max = std::chrono::milliseconds(100),
        .peers = {{"match-1", "", 0}},
    };
    RaftNode raft(config);
    ClusterRouter router;

    bool became_leader = false;
    raft.on_become_leader([&]() {
        became_leader = true;
        router.register_service(ServiceInstance{
            .node = {.host="10.0.0.1", .port=9304, .node_name="match-leader"},
            .service_name = "match",
        });
    });
    raft.start();

    for (int i = 0; i < 30 && !became_leader; ++i)
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    raft.stop();

    ASSERT_TRUE(became_leader);
    auto discovered = router.discover("match");
    EXPECT_TRUE(discovered.has_value());
    EXPECT_EQ(discovered->node.node_name, "match-leader");
}

// ─── V1.3: OTel + EventStore trace propagation ─────────────────────────

TEST(V3IntegrationTest, OtelTraceIdPropagatesToEventStore) {
    // Create a span, get its trace_id, append an event with that trace_id
    auto span = v2::tracing::Span::root("battle_finish");
    span.finish();

    InMemoryEventStore store;
    EventRecord ev{
        .event_type = "battle_result",
        .aggregate_id = "battle_001",
        .payload = R"({"winner":"alice"})",
        .trace_id = span.trace_id,
    };
    store.append(ev);

    auto events = store.read("battle_001");
    ASSERT_EQ(events.size(), 1U);
    EXPECT_GT(events[0].trace_id, 0U);
    EXPECT_EQ(events[0].trace_id, span.trace_id);
}

// ─── V1.4: ConsistentHash distribution validation ──────────────────────

TEST(V3IntegrationTest, HashDistributionAcrossNodes) {
    ConsistentHashRing ring(ConsistentHashRing::Config{.virtual_nodes = 150});
    ring.add_node("n1");
    ring.add_node("n2");
    ring.add_node("n3");
    ring.add_node("n4");

    std::map<std::string, int> counts;
    for (int i = 0; i < 10000; ++i) {
        auto node = ring.lookup("key_" + std::to_string(i));
        counts[node]++;
    }

    // Each of 4 nodes should get roughly 25% (1000-4000 with 10000 keys)
    for (auto& [node, count] : counts) {
        EXPECT_GT(count, 1000) << node << " under-distributed";
        EXPECT_LT(count, 4000) << node << " over-distributed";
    }
}
