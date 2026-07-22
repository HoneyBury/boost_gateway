// v3.4.0: Raft consensus E2E integration tests.
// Tests leader election, log replication, and non-leader redirect in a 3-node cluster.

#include <gtest/gtest.h>

#include "v3/cluster/raft.h"

#include <atomic>
#include <chrono>
#include <functional>
#include <memory>
#include <mutex>
#include <nlohmann/json.hpp>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

using namespace v3::cluster;

// ── Helpers ────────────────────────────────────────────────────────────

struct RaftTestNode {
    std::unique_ptr<RaftNode> node;
    std::string node_id;
    std::uint16_t port;
};

static const std::pair<const char*, std::uint16_t> kNodeInfos[] = {
    {"node1", 9201},
    {"node2", 9202},
    {"node3", 9203},
};

static std::vector<RaftNodeId> make_peers() {
    std::vector<RaftNodeId> peers;
    for (const auto& [id, port] : kNodeInfos) {
        peers.push_back({id, "127.0.0.1", port});
    }
    return peers;
}

static RaftConfig make_config(const std::string& node_id, const std::vector<RaftNodeId>& peers) {
    RaftConfig cfg;
    cfg.node_id = node_id;
    cfg.peers = peers;
    cfg.election_timeout_min = std::chrono::milliseconds(100);
    cfg.election_timeout_max = std::chrono::milliseconds(200);
    cfg.heartbeat_interval = std::chrono::milliseconds(50);
    cfg.storage_dir = ""; // in-memory for tests
    return cfg;
}

// ── Test fixture ───────────────────────────────────────────────────────

class RaftE2ETest : public ::testing::Test {
  protected:
    void SetUp() override {
        auto peers = make_peers();

        for (const auto& [id, port] : kNodeInfos) {
            nodes_[id] = RaftTestNode{
                std::make_unique<RaftNode>(make_config(id, peers)),
                id,
                port,
            };
            protobuf_capable_[id] = true;
            online_[id] = true;
        }

        // Wire up in-process RPC dispatch so nodes can communicate without
        // real network I/O.
        for (auto& [id, test_node] : nodes_) {
            test_node.node->set_rpc_sender([this](const RaftNodeId& target,
                                                  const std::string& data) -> std::string {
                auto it = nodes_.find(target.id);
                if (it == nodes_.end() || !it->second.node)
                    return {};

                const auto kind = detect_raft_rpc_kind(data);
                {
                    std::lock_guard lock(profile_mutex_);
                    if (!online_.at(target.id))
                        return {};
                    if (kind == RaftRpcKind::kCapabilityRequest &&
                        !protobuf_capable_.at(target.id)) {
                        return {};
                    }
                }
                if (kind == RaftRpcKind::kRequestVote) {
                    consensus_rpc_count_.fetch_add(1);
                    if (detect_raft_wire_format(data) != RaftWireFormat::kLegacyJson) {
                        non_legacy_consensus_rpc_count_.fetch_add(1);
                    }
                    auto args = parse_request_vote(data);
                    return serialize_request_vote_reply(it->second.node->handle_request_vote(args));
                }
                if (kind == RaftRpcKind::kAppendEntries) {
                    consensus_rpc_count_.fetch_add(1);
                    if (detect_raft_wire_format(data) != RaftWireFormat::kLegacyJson) {
                        non_legacy_consensus_rpc_count_.fetch_add(1);
                    }
                    auto args = parse_append_entries(data);
                    return serialize_append_entries_reply(
                        it->second.node->handle_append_entries(args));
                }
                if (kind == RaftRpcKind::kCapabilityRequest) {
                    return serialize_raft_capability_reply(
                        it->second.node->handle_capability_request(
                            parse_raft_capability_request(data)));
                }
                return {};
            });
        }

        // Start all nodes
        for (auto& [_, n] : nodes_) {
            n.node->start();
        }

        // Allow election to settle
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
    }

    void TearDown() override {
        for (auto& [_, n] : nodes_) {
            if (n.node)
                n.node->stop();
        }
        nodes_.clear();
    }

    std::string leader_id() const {
        for (const auto& [id, n] : nodes_) {
            if (n.node && n.node->is_leader()) {
                return id;
            }
        }
        return {};
    }

    std::vector<std::string> active_leaders() const {
        std::vector<std::string> leaders;
        for (const auto& [id, n] : nodes_) {
            if (is_online(id) && n.node && n.node->is_leader()) {
                leaders.push_back(id);
            }
        }
        return leaders;
    }

    bool wait_until(const std::function<bool()>& predicate,
                    std::chrono::milliseconds timeout = std::chrono::seconds(3)) const {
        const auto deadline = std::chrono::steady_clock::now() + timeout;
        while (std::chrono::steady_clock::now() < deadline) {
            if (predicate())
                return true;
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
        return predicate();
    }

    void set_protobuf_capable(const std::string& node_id, bool capable) {
        std::lock_guard lock(profile_mutex_);
        protobuf_capable_.at(node_id) = capable;
    }

    void set_online(const std::string& node_id, bool online) {
        std::lock_guard lock(profile_mutex_);
        online_.at(node_id) = online;
    }

    bool is_protobuf_capable(const std::string& node_id) const {
        std::lock_guard lock(profile_mutex_);
        return protobuf_capable_.at(node_id);
    }

    bool is_online(const std::string& node_id) const {
        std::lock_guard lock(profile_mutex_);
        return online_.at(node_id);
    }

    void refresh_capable_nodes() {
        std::vector<std::string> capable_nodes;
        {
            std::lock_guard lock(profile_mutex_);
            for (const auto& [id, capable] : protobuf_capable_) {
                if (capable && online_.at(id))
                    capable_nodes.push_back(id);
            }
        }
        for (const auto& id : capable_nodes) {
            nodes_.at(id).node->refresh_peer_capabilities();
        }
    }

    std::unordered_map<std::string, RaftTestNode> nodes_;
    mutable std::mutex profile_mutex_;
    std::unordered_map<std::string, bool> protobuf_capable_;
    std::unordered_map<std::string, bool> online_;
    std::atomic<std::uint64_t> consensus_rpc_count_{0};
    std::atomic<std::uint64_t> non_legacy_consensus_rpc_count_{0};
};

// ── Test cases ─────────────────────────────────────────────────────────

TEST_F(RaftE2ETest, E2E_HasLeader) {
    // After election timeout, at least one node should be leader.
    auto lid = leader_id();
    EXPECT_FALSE(lid.empty());
}

TEST_F(RaftE2ETest, E2E_OnlyOneLeader) {
    // Exactly one node should report as leader.
    std::size_t count = 0;
    for (const auto& [_, n] : nodes_) {
        if (n.node && n.node->is_leader())
            ++count;
    }
    EXPECT_EQ(count, 1);
}

TEST_F(RaftE2ETest, E2E_LeaderRedirect) {
    // Non-leader nodes should know who the leader is.
    auto actual_leader = leader_id();
    ASSERT_FALSE(actual_leader.empty());

    // Find a non-leader node.
    std::string non_leader;
    for (const auto& [id, n] : nodes_) {
        if (id != actual_leader) {
            non_leader = id;
            break;
        }
    }
    ASSERT_FALSE(non_leader.empty());

    auto reported_leader = nodes_[non_leader].node->leader_id();
    EXPECT_FALSE(reported_leader.empty());
    EXPECT_EQ(reported_leader, actual_leader);
}

TEST_F(RaftE2ETest, E2E_AppendAndCommit) {
    // Appending a command to the leader should advance commit_index.
    auto lid = leader_id();
    ASSERT_FALSE(lid.empty());

    auto& leader = *nodes_[lid].node;
    auto before = leader.commit_index();

    ASSERT_TRUE(leader.append_command("test_command_123"));

    // Wait for the commit to propagate.
    std::this_thread::sleep_for(std::chrono::milliseconds(200));

    EXPECT_GT(leader.commit_index(), before);
}

TEST_F(RaftE2ETest, E2E_LeaderStepDownOnHigherTerm) {
    // A leader receiving a RequestVote with a higher term must step down.
    auto lid = leader_id();
    ASSERT_FALSE(lid.empty());

    auto& leader = *nodes_[lid].node;
    auto current_term = leader.current_term();

    RequestVoteArgs higher_term_args;
    higher_term_args.term = current_term + 1;
    higher_term_args.candidate_id = "phantom";
    higher_term_args.last_log_term = 0;
    higher_term_args.last_log_index = 0;

    leader.handle_request_vote(higher_term_args);

    EXPECT_FALSE(leader.is_leader());

    // Allow new election to settle.
    std::this_thread::sleep_for(std::chrono::milliseconds(500));

    auto new_leader = leader_id();
    EXPECT_FALSE(new_leader.empty());
}

TEST_F(RaftE2ETest, E2E_LogReplication) {
    // Entries appended to the leader should be replicated to all followers.
    auto lid = leader_id();
    ASSERT_FALSE(lid.empty());

    ASSERT_TRUE(nodes_[lid].node->append_command("cmd_1"));
    ASSERT_TRUE(nodes_[lid].node->append_command("cmd_2"));
    ASSERT_TRUE(nodes_[lid].node->append_command("cmd_3"));

    // Wait for replication.
    std::this_thread::sleep_for(std::chrono::milliseconds(300));

    auto leader_log_index = nodes_[lid].node->last_log_index();
    EXPECT_GE(leader_log_index, 3);

    for (const auto& [id, n] : nodes_) {
        if (id == lid)
            continue;
        EXPECT_EQ(n.node->last_log_index(), leader_log_index)
            << "Follower " << id << " log index does not match leader";
    }
}

TEST_F(RaftE2ETest, E2E_MixedVersionRollingUpgradeAndRollbackKeepsLegacyWriterAndCommittedLog) {
    std::vector<std::pair<std::uint64_t, std::string>> committed;
    const auto append_and_wait = [&](const std::string& command) {
        const auto leaders = active_leaders();
        if (leaders.size() != 1U)
            return false;
        auto& leader = *nodes_.at(leaders.front()).node;
        const auto index = leader.last_log_index() + 1U;
        if (!leader.append_command(command))
            return false;
        if (!wait_until([&] {
                for (const auto& [id, test_node] : nodes_) {
                    if (!is_online(id))
                        continue;
                    if (test_node.node->commit_index() < index ||
                        test_node.node->last_log_index() < index ||
                        test_node.node->log_command(index) != command) {
                        return false;
                    }
                }
                return true;
            })) {
            return false;
        }
        committed.emplace_back(index, command);
        return true;
    };

    for (const auto& [id, _] : kNodeInfos) {
        set_protobuf_capable(id, false);
    }
    refresh_capable_nodes();
    ASSERT_TRUE(append_and_wait("mixed-v0"));

    for (const auto& [id, _] : kNodeInfos) {
        set_protobuf_capable(id, true);
        refresh_capable_nodes();
        ASSERT_TRUE(append_and_wait(std::string("upgrade-") + id));
        for (const auto& [candidate_id, candidate] : nodes_) {
            if (candidate_id == id || !is_protobuf_capable(candidate_id))
                continue;
            if (std::string{id} != "node3") {
                EXPECT_FALSE(candidate.node->all_voting_peers_support_protobuf());
            }
        }
    }
    for (const auto& [_, test_node] : nodes_) {
        EXPECT_TRUE(test_node.node->all_voting_peers_support_protobuf());
    }

    const auto leaders_before_restart = active_leaders();
    ASSERT_EQ(leaders_before_restart.size(), 1U);
    const auto restarted_leader = leaders_before_restart.front();
    set_online(restarted_leader, false);
    nodes_.at(restarted_leader).node->stop();
    ASSERT_TRUE(wait_until([&] {
        const auto leaders = active_leaders();
        return leaders.size() == 1U && leaders.front() != restarted_leader;
    }));
    ASSERT_TRUE(append_and_wait("leader-switch"));

    nodes_.at(restarted_leader).node->start();
    set_online(restarted_leader, true);
    ASSERT_TRUE(wait_until([&] {
        const auto& restarted = *nodes_.at(restarted_leader).node;
        const auto& [index, command] = committed.back();
        return restarted.commit_index() >= index && restarted.last_log_index() >= index &&
               restarted.log_command(index) == command && active_leaders().size() == 1U;
    }));

    for (auto it = std::rbegin(kNodeInfos); it != std::rend(kNodeInfos); ++it) {
        set_protobuf_capable(it->first, false);
        refresh_capable_nodes();
        for (const auto& [id, _] : nodes_) {
            if (is_protobuf_capable(id)) {
                EXPECT_FALSE(nodes_.at(id).node->all_voting_peers_support_protobuf());
            }
        }
        ASSERT_TRUE(append_and_wait(std::string("rollback-") + it->first));
    }

    for (const auto& [_, test_node] : nodes_) {
        for (const auto& [index, command] : committed) {
            EXPECT_GE(test_node.node->commit_index(), index);
            EXPECT_EQ(test_node.node->log_command(index), command);
        }
    }
    EXPECT_GT(consensus_rpc_count_.load(), 0U);
    EXPECT_EQ(non_legacy_consensus_rpc_count_.load(), 0U);
}
