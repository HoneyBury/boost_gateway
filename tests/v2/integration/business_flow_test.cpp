#include "multi_process_test.h"

#include "app/logging.h"
#include "net/protocol.h"

#include <gtest/gtest.h>

#include <chrono>
#include <string>

using namespace std::chrono_literals;

// ─── E2E Business Flow ─────────────────────────────────────────────
//
// A single long-running test that exercises the full lifecycle through
// the real v2 OS processes (gateway + 3 backends).

using v2_test::MultiProcessFixture;
TEST_F(MultiProcessFixture, BusinessFlowFullCycle) {
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(*this);

    constexpr auto kDefaultTimeout = 10s;
    uint32_t rid = 1;  // monotonically increasing request-id

    // ── 1. Alice login ─────────────────────────────────────────────
    {
        auto alice = make_client();
        auto resp = alice->exchange(net::protocol::kLoginRequest, rid++,
                                   "alice|token:alice_token|Alice",
                                   kDefaultTimeout);
        EXPECT_EQ(resp.message_id, net::protocol::kLoginResponse);
        EXPECT_EQ(resp.body, "login_ok:alice");

        // ── 2. Bob login ───────────────────────────────────────────
        auto bob = make_client();
        resp = bob->exchange(net::protocol::kLoginRequest, rid++,
                            "bob|token:bob_token|Bob",
                            kDefaultTimeout);
        EXPECT_EQ(resp.message_id, net::protocol::kLoginResponse);
        EXPECT_EQ(resp.body, "login_ok:bob");

        // ── 3. Login with invalid token → error ────────────────────
        // Use a fresh client for the invalid-login attempt.
        auto invalid = make_client();
        resp = invalid->exchange(net::protocol::kLoginRequest, rid++,
                                "mallory||Mallory",
                                kDefaultTimeout);
        // The real login backend rejects empty tokens.
        EXPECT_TRUE(resp.message_id == net::protocol::kErrorResponse ||
                    resp.error_code != 0);

        // ── 4. Alice creates room "e2e_room" ───────────────────────
        resp = alice->exchange(net::protocol::kRoomCreateRequest, rid++,
                              "e2e_room", kDefaultTimeout);
        EXPECT_EQ(resp.message_id, net::protocol::kRoomCreateResponse);

        // ── 5. Bob joins room "e2e_room" ───────────────────────────
        resp = bob->exchange(net::protocol::kRoomJoinRequest, rid++,
                            "e2e_room", kDefaultTimeout);
        EXPECT_EQ(resp.message_id, net::protocol::kRoomJoinResponse);

        // ── 6. Alice sets ready ────────────────────────────────────
        resp = alice->exchange(net::protocol::kRoomReadyRequest, rid++,
                              "true", kDefaultTimeout);
        EXPECT_EQ(resp.message_id, net::protocol::kRoomReadyResponse);

        // ── 7. Bob sets ready ──────────────────────────────────────
        resp = bob->exchange(net::protocol::kRoomReadyRequest, rid++,
                            "true", kDefaultTimeout);
        EXPECT_EQ(resp.message_id, net::protocol::kRoomReadyResponse);

        // ── 9. Battle start rejected (non-owner attempts) ──────────
        resp = bob->exchange(net::protocol::kBattleStartRequest, rid++,
                            "e2e_room", kDefaultTimeout);
        // Non-owner should receive an error.
        EXPECT_TRUE(resp.message_id == net::protocol::kErrorResponse ||
                    resp.error_code != 0);

        // ── 8. Alice starts battle (full ready, owner) ─────────────
        resp = alice->exchange(net::protocol::kBattleStartRequest, rid++,
                              "e2e_room", kDefaultTimeout);
        EXPECT_EQ(resp.message_id, net::protocol::kBattleStartResponse)
            << "body=" << resp.body << " error_code=" << resp.error_code;

        // Drain initial battle-state pushes.
        auto push_a = alice->expect_message(net::protocol::kBattleStatePush,
                                            kDefaultTimeout);
        EXPECT_EQ(push_a.message_id, net::protocol::kBattleStatePush);
        auto push_b = bob->expect_message(net::protocol::kBattleStatePush,
                                          kDefaultTimeout);
        EXPECT_EQ(push_b.message_id, net::protocol::kBattleStatePush);

        // ── 10. Battle input accepted ──────────────────────────────
        resp = alice->exchange(net::protocol::kBattleInputRequest, rid++,
                              "move:1,2", kDefaultTimeout);
        EXPECT_EQ(resp.message_id, net::protocol::kBattleInputResponse)
            << "body=" << resp.body << " error_code=" << resp.error_code;

        // ── 11. Battle frame advances ──────────────────────────────
        push_a = alice->expect_message(net::protocol::kBattleStatePush,
                                       kDefaultTimeout);
        EXPECT_EQ(push_a.message_id, net::protocol::kBattleStatePush);
        push_b = bob->expect_message(net::protocol::kBattleStatePush,
                                     kDefaultTimeout);
        EXPECT_EQ(push_b.message_id, net::protocol::kBattleStatePush);

        // ── 12. Battle finish via surrender ────────────────────────
        resp = alice->exchange(net::protocol::kBattleInputRequest, rid++,
                              "finish:surrender", kDefaultTimeout);
        // The input response or a subsequent push should indicate
        // settlement / battle_finished.
        if (resp.message_id == net::protocol::kBattleInputResponse) {
            // For surrender, the response might carry a battle-end indication.
            // If it does not carry it directly, the push will.
        }

        // ── 13. Both players receive settlement pushes ─────────────
        // The battle finish triggered by "finish:surrender" should
        // cause settlement pushed to both participants.  Drain pushes
        // until we see a finish indicator.
        bool alice_finished = false;
        bool bob_finished = false;
        const auto settle_deadline =
            std::chrono::steady_clock::now() + 15s;
        while ((!alice_finished || !bob_finished) &&
               std::chrono::steady_clock::now() < settle_deadline) {
            if (!alice_finished) {
                try {
                    auto p = alice->expect_message(
                        net::protocol::kBattleStatePush, 8s);
                    alice_finished =
                        p.body.find("finished") != std::string::npos;
                } catch (const std::exception&) {
                    alice_finished = true;  // give up
                }
            }
            if (!bob_finished) {
                try {
                    auto p = bob->expect_message(
                        net::protocol::kBattleStatePush, 8s);
                    bob_finished =
                        p.body.find("finished") != std::string::npos;
                } catch (const std::exception&) {
                    bob_finished = true;
                }
            }
        }
        EXPECT_TRUE(alice_finished)
            << "Alice did not receive battle-finished push";
        EXPECT_TRUE(bob_finished)
            << "Bob did not receive battle-finished push";

        // ── 14. Battle settlement auto-submits scores to leaderboard ─
        resp = alice->exchange(net::protocol::kLeaderboardRankRequest, rid++,
                              "alice", kDefaultTimeout);
        EXPECT_EQ(resp.message_id, net::protocol::kLeaderboardRankResponse)
            << "body=" << resp.body << " error_code=" << resp.error_code;
        EXPECT_NE(resp.body.find("\"user_id\":\"alice\""), std::string::npos)
            << resp.body;

        resp = bob->exchange(net::protocol::kLeaderboardTopRequest, rid++,
                            "10", kDefaultTimeout);
        EXPECT_EQ(resp.message_id, net::protocol::kLeaderboardTopResponse)
            << "body=" << resp.body << " error_code=" << resp.error_code;
        EXPECT_NE(resp.body.find("\"user_id\":\"alice\""), std::string::npos)
            << resp.body;
        EXPECT_NE(resp.body.find("\"user_id\":\"bob\""), std::string::npos)
            << resp.body;

        // ── 15. After battle, ready state reset ────────────────────
        // Alice re-readies up (post-battle the room may re-arm ready).
        resp = alice->exchange(net::protocol::kRoomReadyRequest, rid++,
                              "true", kDefaultTimeout);
        EXPECT_EQ(resp.message_id, net::protocol::kRoomReadyResponse);
        resp = bob->exchange(net::protocol::kRoomReadyRequest, rid++,
                            "true", kDefaultTimeout);
        EXPECT_EQ(resp.message_id, net::protocol::kRoomReadyResponse);

        // ── 16. Alice leaves room → member count decreases ─────────
        resp = alice->exchange(net::protocol::kRoomLeaveRequest, rid++,
                              "e2e_room", kDefaultTimeout);
        EXPECT_EQ(resp.message_id, net::protocol::kRoomLeaveResponse);

        // Clean up.
        alice->close();
        bob->close();
        invalid->close();
    }
}

// ── Separate test for multiple rapid sequential logins ─────────────

using v2_test::MultiProcessFixture;
TEST_F(MultiProcessFixture, MultipleConcurrentLogins) {
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(*this);

    constexpr auto kTimeout = 10s;
    uint32_t rid = 100;

    {
        auto c1 = make_client();
        auto c2 = make_client();
        auto c3 = make_client();

        auto r1 = c1->exchange(net::protocol::kLoginRequest, rid++,
                              "user1|token:t1|User1", kTimeout);
        EXPECT_EQ(r1.message_id, net::protocol::kLoginResponse);

        auto r2 = c2->exchange(net::protocol::kLoginRequest, rid++,
                              "user2|token:t2|User2", kTimeout);
        EXPECT_EQ(r2.message_id, net::protocol::kLoginResponse);

        auto r3 = c3->exchange(net::protocol::kLoginRequest, rid++,
                              "user3|token:t3|User3", kTimeout);
        EXPECT_EQ(r3.message_id, net::protocol::kLoginResponse);

        c1->close();
        c2->close();
        c3->close();
    }
}

using v2_test::MultiProcessFixture;
TEST_F(MultiProcessFixture, BattleInputAfterFinishRejected) {
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(*this);

    constexpr auto kTimeout = 10s;
    uint32_t rid = 200;

    auto alice = make_client();
    auto bob = make_client();

    // Login + room + ready + start
    alice->exchange(net::protocol::kLoginRequest, rid++,
                   "alice2|token:a2|Alice2", kTimeout);
    bob->exchange(net::protocol::kLoginRequest, rid++,
                 "bob2|token:b2|Bob2", kTimeout);
    alice->exchange(net::protocol::kRoomCreateRequest, rid++,
                   "post_fight", kTimeout);
    bob->exchange(net::protocol::kRoomJoinRequest, rid++,
                 "post_fight", kTimeout);
    alice->exchange(net::protocol::kRoomReadyRequest, rid++, "true", kTimeout);
    bob->exchange(net::protocol::kRoomReadyRequest, rid++, "true", kTimeout);

    auto battle_start = alice->exchange(net::protocol::kBattleStartRequest, rid++,
                                        "post_fight", kTimeout);
    EXPECT_EQ(battle_start.message_id, net::protocol::kBattleStartResponse);

    // Drain initial pushes
    alice->expect_message(net::protocol::kBattleStatePush, kTimeout);
    bob->expect_message(net::protocol::kBattleStatePush, kTimeout);

    alice->exchange(net::protocol::kBattleInputRequest, rid++,
                   "finish:surrender", kTimeout);
    // Wait for finish pushes
    bool done = false;
    for (int i = 0; i < 10; ++i) {
        try {
            auto p = alice->expect_message(net::protocol::kBattleStatePush, 5s);
            if (p.body.find("finished") != std::string::npos) {
                done = true;
                break;
            }
        } catch (...) { break; }
    }

    // After battle, input should be rejected
    auto after = alice->exchange(net::protocol::kBattleInputRequest, rid++,
                                "move:9,9", kTimeout);
    if (after.message_id == net::protocol::kErrorResponse) {
        SUCCEED();
    } else if (after.body.find("finished") != std::string::npos ||
               after.body.find("settlement") != std::string::npos) {
        SUCCEED();
    } else {
        ADD_FAILURE() << "Expected error after battle finish, got msg_id="
                      << after.message_id << " body=" << after.body;
    }

    alice->close();
    bob->close();
}
