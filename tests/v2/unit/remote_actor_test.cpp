// v3.0.0 Phase 14: Remote Actor Transport + Consistent Hash tests

#include <gtest/gtest.h>
#include "v3/cluster/remote_actor.h"
#include "v3/cluster/consistent_hash.h"

using namespace v3::cluster;

// ─── RemoteActorRef ──────────────────────────────────────────────────────

TEST(RemoteActorTest, LocalRefIsLocal) {
    NodeId node{.host="10.0.0.1", .node_name="node-1"};
    RemoteActorRef ref(v2::actor::ActorRef{}, node);
    EXPECT_TRUE(ref.is_local());
    EXPECT_EQ(ref.node().node_name, "node-1");
}

TEST(RemoteActorTest, RemoteRefIsNotLocal) {
    NodeId node{.host="10.0.0.2", .node_name="node-2"};
    auto ref = RemoteActorRef::remote(42, node);
    EXPECT_FALSE(ref.is_local());
    EXPECT_EQ(ref.remote_id(), 42U);
    EXPECT_EQ(ref.node().node_name, "node-2");
    EXPECT_FALSE(ref.local_ref().has_value());
}

// ─── ActorLocationRegistry ───────────────────────────────────────────────

TEST(ActorLocationTest, RegisterAndLocate) {
    ActorLocationRegistry registry;
    NodeId node{.host="10.0.0.1", .node_name="node-1"};
    registry.register_actor(100, node);
    EXPECT_EQ(registry.actor_count(), 1U);

    auto loc = registry.locate(100);
    ASSERT_TRUE(loc.has_value());
    EXPECT_EQ(loc->node_name, "node-1");
}

TEST(ActorLocationTest, RelocateMovesActor) {
    ActorLocationRegistry registry;
    NodeId n1{.host="10.0.0.1", .node_name="node-1"};
    NodeId n2{.host="10.0.0.2", .node_name="node-2"};

    registry.register_actor(200, n1);
    registry.relocate(200, n2);

    auto loc = registry.locate(200);
    ASSERT_TRUE(loc.has_value());
    EXPECT_EQ(loc->node_name, "node-2");
}

TEST(ActorLocationTest, UnregisterRemovesActor) {
    ActorLocationRegistry registry;
    NodeId node{.host="10.0.0.1", .node_name="node-1"};
    registry.register_actor(300, node);
    registry.unregister_actor(300);
    EXPECT_FALSE(registry.locate(300).has_value());
    EXPECT_EQ(registry.actor_count(), 0U);
}

TEST(ActorLocationTest, ActorsOnNodeCount) {
    ActorLocationRegistry registry;
    NodeId n1{.host="10.0.0.1", .node_name="node-1"};
    NodeId n2{.host="10.0.0.2", .node_name="node-2"};

    registry.register_actor(1, n1);
    registry.register_actor(2, n1);
    registry.register_actor(3, n2);

    EXPECT_EQ(registry.actors_on_node(n1), 2U);
    EXPECT_EQ(registry.actors_on_node(n2), 1U);
}

// ─── Consistent Hash ─────────────────────────────────────────────────────

TEST(ConsistentHashTest, SingleNodeAlwaysReturnsSameNode) {
    ConsistentHashRing ring;
    ring.add_node("backend-1");

    // Same key should always return same node
    auto n1 = ring.lookup("room_001");
    auto n2 = ring.lookup("room_001");
    EXPECT_EQ(n1, "backend-1");
    EXPECT_EQ(n2, n1);
}

TEST(ConsistentHashTest, LookupDistributesKeysAcrossNodes) {
    ConsistentHashRing ring;
    ring.add_node("backend-1");
    ring.add_node("backend-2");
    ring.add_node("backend-3");

    // Multiple keys should be distributed
    std::map<std::string, int> counts;
    for (int i = 0; i < 1000; ++i) {
        auto node = ring.lookup("room_" + std::to_string(i));
        counts[node]++;
    }

    // Each node should get roughly 1/3 of keys (± reasonable margin)
    EXPECT_GE(counts.size(), 2U);  // at least 2 nodes got keys
    for (auto& [node, count] : counts) {
        EXPECT_GT(count, 100) << node << " has too few keys";
        EXPECT_LT(count, 600) << node << " has too many keys";
    }
}

TEST(ConsistentHashTest, VirtualNodeCount) {
    ConsistentHashRing ring(ConsistentHashRing::Config{.virtual_nodes = 100});
    ring.add_node("node-a");
    EXPECT_EQ(ring.size(), 100U);  // 100 virtual nodes

    ring.add_node("node-b");
    EXPECT_EQ(ring.size(), 200U);  // 200 virtual nodes total
}

TEST(ConsistentHashTest, RemoveNodeCleansVirtualNodes) {
    ConsistentHashRing ring(ConsistentHashRing::Config{.virtual_nodes = 50});
    ring.add_node("temp-node");
    EXPECT_EQ(ring.size(), 50U);

    ring.remove_node("temp-node");
    EXPECT_EQ(ring.size(), 0U);
}

TEST(ConsistentHashTest, RemapFractionIsOneOverN) {
    ConsistentHashRing ring(ConsistentHashRing::Config{.virtual_nodes = 150});
    ring.add_node("a");
    ring.add_node("b");
    ring.add_node("c");
    // 3 nodes → ~1/3 remap on removal
    auto fraction = ring.remap_fraction();
    EXPECT_NEAR(fraction, 1.0 / 3.0, 0.1);
}

TEST(ConsistentHashTest, LookupNReturnsReplicas) {
    ConsistentHashRing ring(ConsistentHashRing::Config{.virtual_nodes = 150});
    ring.add_node("n1");
    ring.add_node("n2");
    ring.add_node("n3");

    auto replicas = ring.lookup_n("battle_0001", 2);
    ASSERT_EQ(replicas.size(), 2U);
    EXPECT_NE(replicas[0], replicas[1]);  // different nodes
}

// ─── ShardRouter ─────────────────────────────────────────────────────────

TEST(ShardRouterTest, RoomAndBattleRouting) {
    ShardRouter router;
    router.add_backend("room-1");
    router.add_backend("room-2");

    auto r1 = router.route_room("room_alpha");
    auto r2 = router.route_room("room_alpha");
    EXPECT_EQ(r1, r2);  // same room always same node

    auto b1 = router.route_battle("battle_001");
    EXPECT_FALSE(b1.empty());
}

TEST(ShardRouterTest, AddRemoveBackend) {
    ShardRouter router;
    router.add_backend("b1");
    router.add_backend("b2");
    EXPECT_EQ(router.room_ring().size(), 300U);  // 2 × 150 virtual nodes

    router.remove_backend("b1");
    EXPECT_EQ(router.room_ring().size(), 150U);
}
