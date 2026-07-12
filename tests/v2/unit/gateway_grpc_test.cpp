// gateway_grpc_test.cpp — Unit tests for the gRPC Gateway server
//
// Tests the GatewayGrpcServer end-to-end by starting an embedded gRPC
// server on an OS-assigned port and issuing RPCs through a client stub.
//
// Requires BOOST_BUILD_GRPC (gRPC libraries + protoc-generated stubs).

#ifdef BOOST_BUILD_GRPC

#include <gtest/gtest.h>

#include <grpcpp/grpcpp.h>

#include <atomic>
#include <chrono>
#include <memory>
#include <string>
#include <thread>

#include "grpc_server.h"
#include "gateway_grpc_server.h"
#include "gateway.grpc.pb.h"

namespace {

using boost::gateway::v3::Gateway;

struct GatewayGrpcTest : ::testing::Test {
    static std::string last_logout_user;
    static std::string last_logout_session;
    static int logout_call_count;
    static int room_member_count;
    static bool room_all_ready;
    static bool room_last_leave_was_owner;
    static std::string room_last_new_owner;
    static bool match_join_queued;
    static bool match_leave_left;
    static bool match_status_matched;
    static std::string match_status_id;
    static std::int64_t leaderboard_submit_rank;
    static std::int64_t leaderboard_rank_value;
    static std::int64_t leaderboard_score_value;
    static std::uint64_t battle_input_seq;
    static std::uint32_t battle_frame_number;
    static std::uint32_t battle_total_frames;

    static void SetUpTestSuite() {
        last_logout_user.clear();
        last_logout_session.clear();
        logout_call_count = 0;
        room_member_count = 0;
        room_all_ready = false;
        room_last_leave_was_owner = false;
        room_last_new_owner.clear();
        match_join_queued = false;
        match_leave_left = false;
        match_status_matched = false;
        match_status_id.clear();
        leaderboard_submit_rank = 0;
        leaderboard_rank_value = 0;
        leaderboard_score_value = 0;
        battle_input_seq = 0;
        battle_frame_number = 0;
        battle_total_frames = 0;
    }

    void SetUp() override {
        last_logout_user.clear();
        last_logout_session.clear();
        logout_call_count = 0;
        room_member_count = 0;
        room_all_ready = false;
        room_last_leave_was_owner = false;
        room_last_new_owner.clear();
        match_join_queued = false;
        match_leave_left = false;
        match_status_matched = false;
        match_status_id.clear();
        leaderboard_submit_rank = 0;
        leaderboard_rank_value = 0;
        leaderboard_score_value = 0;
        battle_input_seq = 0;
        battle_frame_number = 0;
        battle_total_frames = 0;

        server = std::make_unique<v2::grpc::GatewayGrpcServer>(
            0,
            [](const std::string& user_id,
               const std::string& token,
               std::string& error) -> bool {
                if (user_id == "valid_user" && token == "valid_token")
                    return true;
                if (user_id == "empty_token_user" && !token.empty())
                    return true;
                if (user_id == "auth_ok_user")
                    return true;
                error = "invalid credentials";
                return false;
            },
            [](const std::string& user_id,
               const std::string& session_id) {
                last_logout_user = user_id;
                last_logout_session = session_id;
                ++logout_call_count;
            },
            [](const std::string&, const std::string&, std::int32_t& out_member_count, std::string&) {
                room_member_count = 1;
                out_member_count = room_member_count;
                return true;
            },
            [](const std::string&, const std::string&, std::int32_t& out_member_count, std::string&) {
                room_member_count = 2;
                out_member_count = room_member_count;
                return true;
            },
            [](const std::string&, const std::string&, bool& out_was_owner, std::string& out_new_owner_id, std::string&) {
                room_last_leave_was_owner = true;
                room_last_new_owner = "bob";
                out_was_owner = room_last_leave_was_owner;
                out_new_owner_id = room_last_new_owner;
                return true;
            },
            [](const std::string&, const std::string&, bool, bool& out_all_ready, std::string&) {
                room_all_ready = true;
                out_all_ready = room_all_ready;
                return true;
            },
            [](const std::string&, std::int64_t, const std::string&, bool& out_queued, std::string&) {
                match_join_queued = true;
                out_queued = match_join_queued;
                return true;
            },
            [](const std::string&, const std::string&, bool& out_left, std::string&) {
                match_leave_left = true;
                out_left = match_leave_left;
                return true;
            },
            [](const std::string&, const std::string&, bool& out_matched, std::string& out_match_id, std::int64_t& out_avg_mmr, std::int32_t& out_queue_size, std::string&) {
                match_status_matched = true;
                match_status_id = "match_1";
                out_matched = match_status_matched;
                out_match_id = match_status_id;
                out_avg_mmr = 1200;
                out_queue_size = 1;
                return true;
            },
            [](const std::string&, const std::string&, std::int64_t, std::int64_t& out_rank, std::string&) {
                leaderboard_submit_rank = 7;
                out_rank = leaderboard_submit_rank;
                return true;
            },
            [](std::int32_t, std::vector<boost::gateway::v3::LeaderboardEntry>& out_entries, std::string&) {
                boost::gateway::v3::LeaderboardEntry entry;
                entry.set_rank(1);
                entry.set_user_id("alice");
                entry.set_display_name("Alice");
                entry.set_score(1200);
                out_entries.push_back(entry);
                return true;
            },
            [](const std::string& user_id, std::int64_t& out_rank, std::int64_t& out_score, std::string&) {
                leaderboard_rank_value = 3;
                leaderboard_score_value = 900;
                out_rank = leaderboard_rank_value;
                out_score = leaderboard_score_value;
                return user_id == "alice";
            },
            [](const std::string&, const std::string&, const std::vector<std::string>&, std::uint32_t, std::string&) {
                return true;
            },
            [](const std::string&, const std::string&, const std::string&, std::uint32_t, std::uint64_t& out_input_seq, std::uint32_t& out_frame_number, std::string&) {
                battle_input_seq = 11;
                battle_frame_number = 3;
                out_input_seq = battle_input_seq;
                out_frame_number = battle_frame_number;
                return true;
            },
            [](const std::string&, std::uint32_t& out_frame_number, std::string&) {
                battle_frame_number = 4;
                out_frame_number = battle_frame_number;
                return true;
            },
            [](const std::string&, const std::string&, const std::string&, std::uint32_t& out_total_frames, std::string&) {
                battle_total_frames = 9;
                out_total_frames = battle_total_frames;
                return true;
            });

        ASSERT_TRUE(server->start());

        const auto target =
            "127.0.0.1:" + std::to_string(server->port());
        auto channel = grpc::CreateChannel(
            target, grpc::InsecureChannelCredentials());

        // Start CQ polling thread
        running_ = true;
        cq_thread_ = std::thread([this] {
            auto* cq = server->completion_queue();
            if (!cq) return;
            server->seed_completion_queue();
            void* tag;
            bool ok;
            while (running_ && cq->Next(&tag, &ok)) {
                auto* completion_tag =
                    static_cast<v2::grpc::GatewayGrpcServer::CompletionTag*>(tag);
                if (completion_tag) completion_tag->proceed(ok);
            }
        });

        stub = Gateway::NewStub(channel);
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    void TearDown() override {
        running_ = false;
        stub.reset();
        if (server) {
            server->shutdown();
            if (auto* cq = server->completion_queue()) {
                cq->Shutdown();
            }
        }
        if (cq_thread_.joinable())
            cq_thread_.join();
        server.reset();
    }

    boost::gateway::v3::LoginResponse do_login(
        const std::string& user_id,
        const std::string& token,
        const std::string& display_name = "") {
        grpc::ClientContext ctx;
        boost::gateway::v3::LoginRequest req;
        req.set_user_id(user_id);
        req.set_token(token);
        if (!display_name.empty())
            req.set_display_name(display_name);

        boost::gateway::v3::LoginResponse resp;
        auto status = stub->RequestLogin(&ctx, req, &resp);
        EXPECT_TRUE(status.ok()) << "Login RPC failed: "
                                  << status.error_message();
        return resp;
    }

    std::unique_ptr<v2::grpc::GatewayGrpcServer> server;
    std::unique_ptr<Gateway::Stub> stub;
    std::thread cq_thread_;
    std::atomic<bool> running_{false};
};

std::string GatewayGrpcTest::last_logout_user;
std::string GatewayGrpcTest::last_logout_session;
int GatewayGrpcTest::logout_call_count;
int GatewayGrpcTest::room_member_count;
bool GatewayGrpcTest::room_all_ready;
bool GatewayGrpcTest::room_last_leave_was_owner;
std::string GatewayGrpcTest::room_last_new_owner;
bool GatewayGrpcTest::match_join_queued;
bool GatewayGrpcTest::match_leave_left;
bool GatewayGrpcTest::match_status_matched;
std::string GatewayGrpcTest::match_status_id;
std::int64_t GatewayGrpcTest::leaderboard_submit_rank;
std::int64_t GatewayGrpcTest::leaderboard_rank_value;
std::int64_t GatewayGrpcTest::leaderboard_score_value;
std::uint64_t GatewayGrpcTest::battle_input_seq;
std::uint32_t GatewayGrpcTest::battle_frame_number;
std::uint32_t GatewayGrpcTest::battle_total_frames;

// ─── Login tests ──────────────────────────────────────────────────────

TEST_F(GatewayGrpcTest, LoginSuccess) {
    auto resp = do_login("valid_user", "valid_token", "Valid User");
    EXPECT_EQ(resp.error_code(), 0);
    EXPECT_EQ(resp.user_id(), "valid_user");
    EXPECT_EQ(resp.display_name(), "Valid User");
    EXPECT_EQ(resp.role(), "player");
    EXPECT_TRUE(resp.error_message().empty());
}

TEST_F(GatewayGrpcTest, LoginAuthFailure) {
    auto resp = do_login("valid_user", "wrong_token", "Attacker");
    EXPECT_NE(resp.error_code(), 0);
    EXPECT_EQ(resp.user_id(), "valid_user");
    EXPECT_FALSE(resp.error_message().empty());
}

TEST_F(GatewayGrpcTest, LoginUnknownUser) {
    auto resp = do_login("nonexistent", "some_token");
    EXPECT_NE(resp.error_code(), 0);
    EXPECT_FALSE(resp.error_message().empty());
}

TEST_F(GatewayGrpcTest, LoginEmptyUserId) {
    auto resp = do_login("", "valid_token");
    EXPECT_NE(resp.error_code(), 0);
    EXPECT_FALSE(resp.error_message().empty());
}

TEST_F(GatewayGrpcTest, LoginEmptyToken) {
    auto resp = do_login("valid_user", "");
    EXPECT_NE(resp.error_code(), 0);
    EXPECT_FALSE(resp.error_message().empty());
}

TEST_F(GatewayGrpcTest, LoginMultipleSessionsAccumulate) {
    do_login("valid_user", "valid_token");
    do_login("auth_ok_user", "irrelevant");
    EXPECT_GE(server->active_sessions(), 2U);
}

// ─── Logout tests ─────────────────────────────────────────────────────

TEST_F(GatewayGrpcTest, LogoutSuccess) {
    do_login("valid_user", "valid_token");

    grpc::ClientContext ctx;
    boost::gateway::v3::LogoutRequest lreq;
    lreq.set_user_id("valid_user");
    lreq.set_session_id("session_001");

    boost::gateway::v3::LogoutResponse lresp;
    auto status = stub->RequestLogout(&ctx, lreq, &lresp);

    ASSERT_TRUE(status.ok());
    EXPECT_TRUE(lresp.success());
    EXPECT_EQ(last_logout_user, "valid_user");
    EXPECT_EQ(last_logout_session, "session_001");
    EXPECT_EQ(logout_call_count, 1);
}

TEST_F(GatewayGrpcTest, LogoutDecrementsActiveSessions) {
    do_login("valid_user", "valid_token");
    do_login("auth_ok_user", "irrelevant");

    {
        grpc::ClientContext ctx;
        boost::gateway::v3::LogoutRequest lreq;
        lreq.set_user_id("valid_user");
        lreq.set_session_id("s1");

        boost::gateway::v3::LogoutResponse lresp;
        ASSERT_TRUE(stub->RequestLogout(&ctx, lreq, &lresp).ok());
    }

    EXPECT_EQ(server->active_sessions(), 1U);
}

// ─── Health tests ─────────────────────────────────────────────────────

TEST_F(GatewayGrpcTest, HealthReturnsServingStatus) {
    grpc::ClientContext ctx;
    boost::gateway::v3::HealthCheckRequest req;
    boost::gateway::v3::HealthCheckResponse resp;
    auto status = stub->Health(&ctx, req, &resp);

    ASSERT_TRUE(status.ok());
    EXPECT_EQ(resp.status(), "SERVING");
}

TEST_F(GatewayGrpcTest, ActiveSessionsAfterLogin) {
    do_login("valid_user", "valid_token");
    do_login("auth_ok_user", "irrelevant");

    EXPECT_EQ(server->active_sessions(), 2U);
}

// ─── Concurrent logins ────────────────────────────────────────────────

TEST_F(GatewayGrpcTest, ConcurrentLogins) {
    constexpr int kNumClients = 10;
    std::atomic<int> success_count{0};

    std::vector<std::thread> threads;
    for (int i = 0; i < kNumClients; ++i) {
        threads.emplace_back([this, i, &success_count] {
            auto channel = grpc::CreateChannel(
                "127.0.0.1:" + std::to_string(server->port()),
                grpc::InsecureChannelCredentials());
            auto local_stub = Gateway::NewStub(channel);

            grpc::ClientContext ctx;
            boost::gateway::v3::LoginRequest req;
            req.set_user_id("valid_user");
            req.set_token("valid_token");
            req.set_display_name("User" + std::to_string(i));

            boost::gateway::v3::LoginResponse resp;
            auto status = local_stub->RequestLogin(&ctx, req, &resp);
            if (status.ok() && resp.error_code() == 0)
                success_count.fetch_add(1, std::memory_order_relaxed);
        });
    }

    for (auto& t : threads)
        t.join();

    EXPECT_EQ(success_count.load(), kNumClients);
}

TEST_F(GatewayGrpcTest, RoomCreateSuccess) {
    grpc::ClientContext ctx;
    boost::gateway::v3::RoomCreateRequest req;
    req.set_user_id("alice");
    req.set_room_id("room_1");
    boost::gateway::v3::RoomCreateResponse resp;
    auto status = stub->RequestRoomCreate(&ctx, req, &resp);
    ASSERT_TRUE(status.ok());
    EXPECT_EQ(resp.error_code(), 0);
    EXPECT_EQ(resp.room_id(), "room_1");
    EXPECT_EQ(resp.member_count(), 1);
}

TEST_F(GatewayGrpcTest, RoomJoinSuccess) {
    grpc::ClientContext ctx;
    boost::gateway::v3::RoomJoinRequest req;
    req.set_user_id("bob");
    req.set_room_id("room_1");
    boost::gateway::v3::RoomJoinResponse resp;
    auto status = stub->RequestRoomJoin(&ctx, req, &resp);
    ASSERT_TRUE(status.ok());
    EXPECT_EQ(resp.error_code(), 0);
    EXPECT_EQ(resp.member_count(), 2);
}

TEST_F(GatewayGrpcTest, RoomLeaveSuccess) {
    grpc::ClientContext ctx;
    boost::gateway::v3::RoomLeaveRequest req;
    req.set_user_id("alice");
    req.set_room_id("room_1");
    boost::gateway::v3::RoomLeaveResponse resp;
    auto status = stub->RequestRoomLeave(&ctx, req, &resp);
    ASSERT_TRUE(status.ok());
    EXPECT_EQ(resp.error_code(), 0);
    EXPECT_TRUE(resp.was_owner());
    EXPECT_EQ(resp.new_owner_id(), "bob");
}

TEST_F(GatewayGrpcTest, RoomReadySuccess) {
    grpc::ClientContext ctx;
    boost::gateway::v3::RoomReadyRequest req;
    req.set_user_id("alice");
    req.set_room_id("room_1");
    req.set_ready(true);
    boost::gateway::v3::RoomReadyResponse resp;
    auto status = stub->RequestRoomReady(&ctx, req, &resp);
    ASSERT_TRUE(status.ok());
    EXPECT_EQ(resp.error_code(), 0);
    EXPECT_TRUE(resp.all_ready());
}

TEST_F(GatewayGrpcTest, MatchJoinSuccess) {
    grpc::ClientContext ctx;
    boost::gateway::v3::MatchJoinRequest req;
    req.set_user_id("alice");
    req.set_mmr(1200);
    req.set_mode("1v1");
    boost::gateway::v3::MatchJoinResponse resp;
    auto status = stub->RequestMatchJoin(&ctx, req, &resp);
    ASSERT_TRUE(status.ok());
    EXPECT_EQ(resp.error_code(), 0);
    EXPECT_TRUE(resp.queued());
}

TEST_F(GatewayGrpcTest, MatchLeaveSuccess) {
    grpc::ClientContext ctx;
    boost::gateway::v3::MatchLeaveRequest req;
    req.set_user_id("alice");
    req.set_mode("1v1");
    boost::gateway::v3::MatchLeaveResponse resp;
    auto status = stub->RequestMatchLeave(&ctx, req, &resp);
    ASSERT_TRUE(status.ok());
    EXPECT_EQ(resp.error_code(), 0);
    EXPECT_TRUE(resp.left());
}

TEST_F(GatewayGrpcTest, MatchStatusSuccess) {
    grpc::ClientContext ctx;
    boost::gateway::v3::MatchStatusRequest req;
    req.set_user_id("alice");
    req.set_mode("1v1");
    boost::gateway::v3::MatchStatusResponse resp;
    auto status = stub->RequestMatchStatus(&ctx, req, &resp);
    ASSERT_TRUE(status.ok());
    EXPECT_EQ(resp.error_code(), 0);
    EXPECT_TRUE(resp.matched());
    EXPECT_EQ(resp.match_id(), "match_1");
}

TEST_F(GatewayGrpcTest, LeaderboardSubmitSuccess) {
    grpc::ClientContext ctx;
    boost::gateway::v3::LeaderboardSubmitRequest req;
    req.set_user_id("alice");
    req.set_display_name("Alice");
    req.set_score(1200);
    boost::gateway::v3::LeaderboardSubmitResponse resp;
    auto status = stub->RequestLeaderboardSubmit(&ctx, req, &resp);
    ASSERT_TRUE(status.ok());
    EXPECT_EQ(resp.error_code(), 0);
    EXPECT_EQ(resp.rank(), 7);
}

TEST_F(GatewayGrpcTest, LeaderboardTopSuccess) {
    grpc::ClientContext ctx;
    boost::gateway::v3::LeaderboardTopRequest req;
    req.set_k(5);
    boost::gateway::v3::LeaderboardTopResponse resp;
    auto status = stub->RequestLeaderboardTop(&ctx, req, &resp);
    ASSERT_TRUE(status.ok());
    EXPECT_EQ(resp.error_code(), 0);
    ASSERT_EQ(resp.entries_size(), 1);
    EXPECT_EQ(resp.entries(0).user_id(), "alice");
}

TEST_F(GatewayGrpcTest, LeaderboardRankSuccess) {
    grpc::ClientContext ctx;
    boost::gateway::v3::LeaderboardRankRequest req;
    req.set_user_id("alice");
    boost::gateway::v3::LeaderboardRankResponse resp;
    auto status = stub->RequestLeaderboardRank(&ctx, req, &resp);
    ASSERT_TRUE(status.ok());
    EXPECT_EQ(resp.error_code(), 0);
    EXPECT_EQ(resp.rank(), 3);
    EXPECT_EQ(resp.score(), 900);
}

TEST_F(GatewayGrpcTest, BattleCreateSuccess) {
    grpc::ClientContext ctx;
    boost::gateway::v3::BattleCreateRequest req;
    req.set_battle_id("battle_1");
    req.set_room_id("room_1");
    req.add_player_ids("alice");
    req.add_player_ids("bob");
    req.set_max_frames(3);
    boost::gateway::v3::BattleCreateResponse resp;
    auto status = stub->RequestBattleCreate(&ctx, req, &resp);
    ASSERT_TRUE(status.ok());
    EXPECT_EQ(resp.error_code(), 0);
    EXPECT_EQ(resp.battle_id(), "battle_1");
    EXPECT_EQ(resp.player_ids_size(), 2);
}

TEST_F(GatewayGrpcTest, BattleInputSuccess) {
    grpc::ClientContext ctx;
    boost::gateway::v3::BattleInputRequest req;
    req.set_user_id("alice");
    req.set_battle_id("battle_1");
    req.set_input_data("move:1,2");
    req.set_submitted_frame(1);
    boost::gateway::v3::BattleInputResponse resp;
    auto status = stub->RequestBattleInput(&ctx, req, &resp);
    ASSERT_TRUE(status.ok());
    EXPECT_EQ(resp.error_code(), 0);
    EXPECT_EQ(resp.input_seq(), 11);
    EXPECT_EQ(resp.frame_number(), 3);
}

TEST_F(GatewayGrpcTest, BattleStateSuccess) {
    grpc::ClientContext ctx;
    boost::gateway::v3::BattleStateRequest req;
    req.set_battle_id("battle_1");
    boost::gateway::v3::BattleStateResponse resp;
    auto status = stub->RequestBattleState(&ctx, req, &resp);
    ASSERT_TRUE(status.ok());
    EXPECT_EQ(resp.error_code(), 0);
    EXPECT_EQ(resp.frame_number(), 4);
}

TEST_F(GatewayGrpcTest, BattleFinishSuccess) {
    grpc::ClientContext ctx;
    boost::gateway::v3::BattleFinishRequest req;
    req.set_user_id("alice");
    req.set_battle_id("battle_1");
    req.set_reason("finished");
    boost::gateway::v3::BattleFinishResponse resp;
    auto status = stub->RequestBattleFinish(&ctx, req, &resp);
    ASSERT_TRUE(status.ok());
    EXPECT_EQ(resp.error_code(), 0);
    EXPECT_EQ(resp.total_frames(), 9);
}

}  // anonymous namespace

#else  // !BOOST_BUILD_GRPC

#include <gtest/gtest.h>

TEST(GatewayGrpcTest, Skipped) {
    GTEST_SKIP() << "gRPC not enabled (BOOST_BUILD_GRPC not set)";
}

#endif  // BOOST_BUILD_GRPC
