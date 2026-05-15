// v3.0.0 Phase 15: Raft leader election tests
// v3.2.0: Multi-node cluster verification with in-memory RPC.

#include <gtest/gtest.h>
#include "v3/cluster/raft.h"

#include <nlohmann/json.hpp>

#include <atomic>
#include <chrono>
#include <filesystem>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>

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

TEST(RaftTest, AppendEntriesSerializationRoundTripCarriesLogFields) {
    AppendEntriesArgs args;
    args.term = 7;
    args.leader_id = "leader-1";
    args.prev_log_index = 3;
    args.prev_log_term = 6;
    args.leader_commit = 2;
    args.entries = {
        LogEntry{.term = 7, .command = R"({"op":"join"})"},
        LogEntry{.term = 7, .command = R"({"op":"match"})"},
    };

    auto encoded = serialize_append_entries(args);
    auto decoded = parse_append_entries(encoded);
    EXPECT_EQ(decoded.term, 7U);
    EXPECT_EQ(decoded.leader_id, "leader-1");
    EXPECT_EQ(decoded.prev_log_index, 3U);
    EXPECT_EQ(decoded.prev_log_term, 6U);
    EXPECT_EQ(decoded.leader_commit, 2U);
    ASSERT_EQ(decoded.entries.size(), 2U);
    EXPECT_EQ(decoded.entries[0].command, R"({"op":"join"})");
    EXPECT_EQ(decoded.entries[1].term, 7U);
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

// ── v3.2.0: Multi-node Raft cluster with in-memory RPC ────────────────

namespace {

// In-memory RPC sender that dispatches to the target node via registry.
auto make_rpc_sender(std::unordered_map<std::string, RaftNode*>& registry,
                     std::mutex& registry_mutex) {
    return [&registry, &registry_mutex](const RaftNodeId& target,
                                         const std::string& data) -> std::string {
        // Look up target under the registry lock, then release before dispatch.
        RaftNode* target_node = nullptr;
        {
            std::lock_guard lock(registry_mutex);
            auto it = registry.find(target.id);
            if (it != registry.end()) target_node = it->second;
        }
        if (!target_node) return {};

        try {
            auto j = nlohmann::json::parse(data, nullptr, false);
            if (j.is_discarded()) return {};

            std::string msg_type = j.value("type", "");

            if (msg_type == "request_vote") {
                auto args = parse_request_vote(data);
                return serialize_request_vote_reply(
                    target_node->handle_request_vote(args));
            }
            if (msg_type == "append_entries") {
                auto args = parse_append_entries(data);
                return serialize_append_entries_reply(
                    target_node->handle_append_entries(args));
            }
        } catch (const std::exception&) {
            // Log swallowed — RPC timeout is the signal.
        }
        return {};
    };
}

}  // namespace

TEST(RaftClusterTest, ThreeNodeClusterElectsSingleLeader) {
    std::unordered_map<std::string, std::unique_ptr<RaftNode>> node_store;
    std::unordered_map<std::string, RaftNode*> registry;
    std::mutex registry_mutex;

    std::atomic<int> leaders{0};
    std::mutex leader_mutex;

    for (int i = 1; i <= 3; ++i) {
        auto id = "n" + std::to_string(i);
        RaftConfig cfg{
            .node_id = id,
            .election_timeout_min = std::chrono::milliseconds(150),
            .election_timeout_max = std::chrono::milliseconds(300),
            .heartbeat_interval = std::chrono::milliseconds(50),
            .peers = {
                {"n1", "", 0},
                {"n2", "", 0},
                {"n3", "", 0},
            },
        };
        auto node = std::make_unique<RaftNode>(std::move(cfg));
        registry[id] = node.get();

        node->on_become_leader([&leaders, &leader_mutex, id]() {
            std::lock_guard lock(leader_mutex);
            leaders++;
        });

        node->set_rpc_sender(make_rpc_sender(registry, registry_mutex));
        node_store[id] = std::move(node);
    }

    // Start all nodes
    for (auto& [_, node] : node_store) {
        node->start();
    }

    // Wait for leader election (up to 3 seconds)
    for (int i = 0; i < 60; ++i) {
        if (leaders.load() >= 1) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }

    EXPECT_EQ(leaders.load(), 1) << "Exactly one leader should be elected";

    // Stop all
    for (auto& [_, node] : node_store) {
        node->stop();
    }
}

TEST(RaftClusterTest, LeaderStepDownOnHigherTerm) {
    std::unordered_map<std::string, std::unique_ptr<RaftNode>> node_store;
    std::unordered_map<std::string, RaftNode*> registry;
    std::mutex registry_mutex;

    std::atomic<int> leader_elected{0};
    std::atomic<int> leader_stepped_down{0};

    for (int i = 1; i <= 2; ++i) {
        auto id = "n" + std::to_string(i);
        RaftConfig cfg{
            .node_id = id,
            .election_timeout_min = std::chrono::milliseconds(100),
            .election_timeout_max = std::chrono::milliseconds(200),
            .heartbeat_interval = std::chrono::milliseconds(50),
            .peers = {{"n1", "", 0}, {"n2", "", 0}},
        };
        auto node = std::make_unique<RaftNode>(std::move(cfg));
        registry[id] = node.get();

        node->on_become_leader([&leader_elected, id]() {
            leader_elected++;
        });
        node->on_step_down([&leader_stepped_down]() {
            leader_stepped_down++;
        });

        node->set_rpc_sender(make_rpc_sender(registry, registry_mutex));
        node_store[id] = std::move(node);
    }

    for (auto& [_, node] : node_store) node->start();

    for (int i = 0; i < 40 && leader_elected.load() < 1; ++i)
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    ASSERT_GE(leader_elected.load(), 1);

    // Send a heartbeat with higher term to all nodes to force step down
    AppendEntriesArgs hb{.term = 100, .leader_id = "external"};
    for (auto& [_, node] : node_store) {
        node->handle_append_entries(hb);
    }

    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    EXPECT_GE(leader_stepped_down.load(), 1)
        << "Leader should step down on higher term";

    for (auto& [_, node] : node_store) node->stop();
}

TEST(RaftClusterTest, LeaderReplicatesCommittedLogToFollowers) {
    std::unordered_map<std::string, std::unique_ptr<RaftNode>> node_store;
    std::unordered_map<std::string, RaftNode*> registry;
    std::mutex registry_mutex;

    for (int i = 1; i <= 3; ++i) {
        auto id = "n" + std::to_string(i);
        RaftConfig cfg{
            .node_id = id,
            .election_timeout_min = std::chrono::milliseconds(120),
            .election_timeout_max = std::chrono::milliseconds(240),
            .heartbeat_interval = std::chrono::milliseconds(40),
            .peers = {{"n1", "", 0}, {"n2", "", 0}, {"n3", "", 0}},
        };
        auto node = std::make_unique<RaftNode>(std::move(cfg));
        registry[id] = node.get();
        node->set_rpc_sender(make_rpc_sender(registry, registry_mutex));
        node_store[id] = std::move(node);
    }

    for (auto& [_, node] : node_store) {
        node->start();
    }

    RaftNode* leader = nullptr;
    for (int i = 0; i < 80 && !leader; ++i) {
        for (auto& [_, node] : node_store) {
            if (node->is_leader()) {
                leader = node.get();
                break;
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    ASSERT_NE(leader, nullptr);

    EXPECT_TRUE(leader->append_command(R"({"op":"promote_match","id":"m-1"})"));

    for (int i = 0; i < 40; ++i) {
        bool replicated = true;
        for (auto& [_, node] : node_store) {
            if (node->log_size() != 1U || node->commit_index() != 1U) {
                replicated = false;
                break;
            }
        }
        if (replicated) {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }

    for (auto& [id, node] : node_store) {
        EXPECT_EQ(node->log_size(), 1U) << id;
        EXPECT_EQ(node->commit_index(), 1U) << id;
        EXPECT_EQ(node->log_command(1), R"({"op":"promote_match","id":"m-1"})") << id;
    }

    for (auto& [_, node] : node_store) {
        node->stop();
    }
}

TEST(RaftTest, RequestVoteRejectsOutdatedCandidateLog) {
    RaftNode node(RaftConfig{
        .node_id = "leader-like",
        .election_timeout_min = std::chrono::milliseconds(50),
        .election_timeout_max = std::chrono::milliseconds(100),
        .peers = {{"leader-like", "", 0}},
    });
    node.start();
    for (int i = 0; i < 30 && !node.is_leader(); ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    ASSERT_TRUE(node.is_leader());
    ASSERT_TRUE(node.append_command("cmd-1"));

    RequestVoteArgs stale_candidate{
        .term = node.current_term() + 1,
        .candidate_id = "candidate-x",
        .last_log_term = 0,
        .last_log_index = 0,
    };
    auto reply = node.handle_request_vote(stale_candidate);
    EXPECT_FALSE(reply.vote_granted);
    EXPECT_EQ(reply.term, node.current_term());

    node.stop();
}

TEST(RaftTest, PersistentLogAndCommitStateRestoreAfterRestart) {
    const auto storage_root =
        std::filesystem::temp_directory_path() / "boost_raft_persist_test";
    std::error_code ec;
    std::filesystem::remove_all(storage_root, ec);
    std::filesystem::create_directories(storage_root, ec);

    {
        RaftNode node(RaftConfig{
            .node_id = "persist-node",
            .storage_dir = storage_root.string(),
            .election_timeout_min = std::chrono::milliseconds(50),
            .election_timeout_max = std::chrono::milliseconds(100),
            .peers = {{"persist-node", "", 0}},
        });
        node.start();
        for (int i = 0; i < 30 && !node.is_leader(); ++i) {
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
        ASSERT_TRUE(node.is_leader());
        ASSERT_TRUE(node.append_command(R"({"op":"persist","id":"cmd-1"})"));
        ASSERT_EQ(node.commit_index(), 1U);
        ASSERT_EQ(node.last_applied(), 1U);
        node.stop();
    }

    {
        RaftNode recovered(RaftConfig{
            .node_id = "persist-node",
            .storage_dir = storage_root.string(),
            .peers = {{"persist-node", "", 0}},
        });
        EXPECT_EQ(recovered.current_term(), 1U);
        EXPECT_EQ(recovered.log_size(), 1U);
        EXPECT_EQ(recovered.commit_index(), 1U);
        EXPECT_EQ(recovered.last_applied(), 1U);
        EXPECT_EQ(recovered.log_command(1), R"({"op":"persist","id":"cmd-1"})");
        EXPECT_EQ(recovered.state(), RaftState::kFollower);
    }

    std::filesystem::remove_all(storage_root, ec);
}
