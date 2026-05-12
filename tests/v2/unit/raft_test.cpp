// v3.0.0 Phase 15: Raft leader election tests

#include <gtest/gtest.h>
#include "v3/cluster/raft.h"
#include <thread>

using namespace v3::cluster;

TEST(RaftTest, InitialStateIsFollower) {
    RaftConfig config{.node_id = "node-1"};
    RaftNode node(config);
    EXPECT_EQ(node.state(), RaftState::kFollower);
    EXPECT_FALSE(node.is_leader());
    EXPECT_EQ(node.current_term(), 0U);
}

TEST(RaftTest, QuorumSize) {
    RaftConfig config{
        .node_id = "node-1",
        .peers = {
            {"node-1", "", 0},
            {"node-2", "", 0},
            {"node-3", "", 0},
            {"node-4", "", 0},
            {"node-5", "", 0},
        },
    };
    RaftNode node(config);
    EXPECT_EQ(node.quorum_size(), 3U);  // 5/2 + 1 = 3
    EXPECT_EQ(node.peer_count(), 5U);
}

TEST(RaftTest, SingleNodeBecomesLeaderImmediately) {
    RaftConfig config{
        .node_id = "solo",
        .election_timeout_min = std::chrono::milliseconds(50),
        .election_timeout_max = std::chrono::milliseconds(100),
        .peers = {{"solo", "", 0}},
    };
    RaftNode node(config);

    bool became_leader = false;
    node.on_become_leader([&]() { became_leader = true; });
    node.start();

    // Wait for election
    for (int i = 0; i < 30 && !became_leader; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }

    node.stop();
    EXPECT_TRUE(became_leader);
    EXPECT_TRUE(node.is_leader());
}

TEST(RaftTest, RequestVoteRejectsLowerTerm) {
    RaftNode node(RaftConfig{.node_id = "voter"});
    node.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    // Pretend we're at term 5
    RequestVoteArgs args{.term = 5, .candidate_id = "candidate-1"};
    auto reply = node.handle_request_vote(args);
    EXPECT_TRUE(reply.vote_granted);
    EXPECT_GE(reply.term, 5U);

    // Lower term should be rejected
    RequestVoteArgs old_args{.term = 3, .candidate_id = "candidate-2"};
    auto old_reply = node.handle_request_vote(old_args);
    EXPECT_FALSE(old_reply.vote_granted);

    node.stop();
}

TEST(RaftTest, AppendEntriesFromLeaderResetsElectionTimeout) {
    RaftNode node(RaftConfig{
        .node_id = "follower-1",
        .election_timeout_min = std::chrono::milliseconds(500),
        .election_timeout_max = std::chrono::milliseconds(1000),
    });
    node.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    EXPECT_EQ(node.state(), RaftState::kFollower);

    // Receive heartbeat from leader (prevents election)
    AppendEntriesArgs hb{.term = 1, .leader_id = "leader-1"};
    auto reply = node.handle_append_entries(hb);
    EXPECT_TRUE(reply.success);

    // Should still be follower (election timer reset)
    EXPECT_EQ(node.state(), RaftState::kFollower);
    EXPECT_EQ(node.leader_id(), "leader-1");

    node.stop();
}

TEST(RaftTest, HigherTermForcesStepDown) {
    RaftNode node(RaftConfig{
        .node_id = "step-down-test",
        .election_timeout_min = std::chrono::milliseconds(100),
        .election_timeout_max = std::chrono::milliseconds(200),
        .peers = {{"step-down-test", "", 0}},
    });

    bool leader = false, stepped_down = false;
    node.on_become_leader([&]() { leader = true; });
    node.on_step_down([&]() { stepped_down = true; });
    node.start();

    // Wait to become leader
    for (int i = 0; i < 30 && !leader; ++i)
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    EXPECT_TRUE(leader);

    // Receive heartbeat from higher term → step down
    AppendEntriesArgs hb{.term = 10, .leader_id = "new-leader"};
    node.handle_append_entries(hb);
    EXPECT_TRUE(stepped_down);

    node.stop();
}

TEST(RaftTest, TwoNodesWithThreePeersElectLeader) {
    // 3-node cluster: quorum is 2. With internal simulated votes from all
    // peers, the candidate should receive enough votes to become leader.
    RaftConfig config{
        .node_id = "node-a",
        .election_timeout_min = std::chrono::milliseconds(50),
        .election_timeout_max = std::chrono::milliseconds(150),
        .peers = {{"node-a", "", 0}, {"node-b", "", 0}, {"node-c", "", 0}},
    };
    RaftNode node_a(config);

    bool a_leader = false;
    node_a.on_become_leader([&]() { a_leader = true; });
    node_a.start();

    // Wait up to ~3s for election to complete
    for (int i = 0; i < 60 && !a_leader; ++i)
        std::this_thread::sleep_for(std::chrono::milliseconds(50));

    node_a.stop();
    // Single node with 3 peers: quorum is 2, self-vote is 1,
    // internal simulated votes from 2 peers = enough to win
    EXPECT_TRUE(a_leader) << "Should win election with internal peer votes";
}

TEST(RaftTest, LeaderIdIsTracked) {
    RaftNode node(RaftConfig{.node_id = "tracker"});
    node.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    AppendEntriesArgs hb{.term = 5, .leader_id = "leader-5"};
    node.handle_append_entries(hb);
    EXPECT_EQ(node.leader_id(), "leader-5");

    node.stop();
}
