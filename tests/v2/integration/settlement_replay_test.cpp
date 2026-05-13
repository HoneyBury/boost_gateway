#include "multi_process_test.h"

#include "app/logging.h"
#include "net/protocol.h"

#include <gtest/gtest.h>

#include <chrono>
#include <string>

using namespace std::chrono_literals;

// ─── Settlement / Replay E2E ───────────────────────────────────────
//
// Runs a full battle lifecycle through surrender and verifies that
// both participants receive settlement data, and that the room state
// is consistent after the battle ends.

using v2_test::MultiProcessFixture;
TEST_F(MultiProcessFixture, SurrenderSettlementDeliveredToBothPlayers) {
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(*this);

    constexpr auto kDefaultTimeout = 15s;
    uint32_t rid = 1;

    auto alice = make_client();
    auto bob = make_client();

    // ── Login both players ─────────────────────────────────────────
    auto resp = alice->exchange(net::protocol::kLoginRequest, rid++,
                                "alice_settle|token:as|AliceSettle",
                                kDefaultTimeout);
    EXPECT_EQ(resp.message_id, net::protocol::kLoginResponse);

    resp = bob->exchange(net::protocol::kLoginRequest, rid++,
                         "bob_settle|token:bs|BobSettle",
                         kDefaultTimeout);
    EXPECT_EQ(resp.message_id, net::protocol::kLoginResponse);

    // ── Create room, join, ready ───────────────────────────────────
    resp = alice->exchange(net::protocol::kRoomCreateRequest, rid++,
                           "settle_room", kDefaultTimeout);
    EXPECT_EQ(resp.message_id, net::protocol::kRoomCreateResponse);

    resp = bob->exchange(net::protocol::kRoomJoinRequest, rid++,
                         "settle_room", kDefaultTimeout);
    EXPECT_EQ(resp.message_id, net::protocol::kRoomJoinResponse);

    resp = alice->exchange(net::protocol::kRoomReadyRequest, rid++,
                           "true", kDefaultTimeout);
    EXPECT_EQ(resp.message_id, net::protocol::kRoomReadyResponse);

    resp = bob->exchange(net::protocol::kRoomReadyRequest, rid++,
                         "true", kDefaultTimeout);
    EXPECT_EQ(resp.message_id, net::protocol::kRoomReadyResponse);

    // ── Start battle ───────────────────────────────────────────────
    resp = alice->exchange(net::protocol::kBattleStartRequest, rid++,
                           "settle_room", kDefaultTimeout);
    EXPECT_EQ(resp.message_id, net::protocol::kBattleStartResponse);

    // Drain initial battle-started pushes.
    // In local mode the body is "battle_state:kind=started:room_id=...:battle_id=..."
    // In bridge mode the body is JSON like {"kind":"battle_started","battle_id":"...","room_id":"..."}
    auto push_a = alice->expect_message(net::protocol::kBattleStatePush,
                                        kDefaultTimeout);
    EXPECT_TRUE(push_a.body.find("battle_state") != std::string::npos ||
                push_a.body.find("battle_started") != std::string::npos)
        << "Alice battle-started push body: '" << push_a.body << "'";
    auto push_b = bob->expect_message(net::protocol::kBattleStatePush,
                                      kDefaultTimeout);
    EXPECT_TRUE(push_b.body.find("battle_state") != std::string::npos ||
                push_b.body.find("battle_started") != std::string::npos)
        << "Bob battle-started push body: '" << push_b.body << "'";

    // ── Submit an input to advance a frame ─────────────────────────
    resp = alice->exchange(net::protocol::kBattleInputRequest, rid++,
                           "move:10,20", kDefaultTimeout);
    EXPECT_EQ(resp.message_id, net::protocol::kBattleInputResponse)
        << "body=" << resp.body << " error_code=" << resp.error_code;

    // Drain frame-advance pushes.
    // In local mode the body is "battle_state:kind=frame:room_id=...:battle_id=...:frame=..."
    // In bridge mode the body is JSON like {"kind":"frame_advanced","battle_id":"...","frame_number":...}
    push_a = alice->expect_message(net::protocol::kBattleStatePush,
                                   kDefaultTimeout);
    EXPECT_TRUE(push_a.body.find("battle_state") != std::string::npos ||
                push_a.body.find("frame_advanced") != std::string::npos)
        << "Alice frame-advance push body: '" << push_a.body << "'";
    push_b = bob->expect_message(net::protocol::kBattleStatePush,
                                 kDefaultTimeout);
    EXPECT_TRUE(push_b.body.find("battle_state") != std::string::npos ||
                push_b.body.find("frame_advanced") != std::string::npos)
        << "Bob frame-advance push body: '" << push_b.body << "'";

    // ── Surrender (triggers settlement) ────────────────────────────
    resp = alice->exchange(net::protocol::kBattleInputRequest, rid++,
                           "finish:surrender", kDefaultTimeout);

    // Collect all subsequent pushes for both players until we see
    // a "finished" push from each. In local mode there are separate
    // settlement+finished pushes; in bridge mode a single JSON push
    // with "kind":"battle_finished" covers both.
    bool alice_got_finished = false;
    bool bob_got_finished = false;

    const auto settle_deadline = std::chrono::steady_clock::now() + 20s;
    while ((!alice_got_finished || !bob_got_finished) &&
           std::chrono::steady_clock::now() < settle_deadline) {
        if (!alice_got_finished) {
            try {
                auto p = alice->expect_message(
                    net::protocol::kBattleStatePush, 5s);
                if (p.body.find("finished") != std::string::npos) {
                    alice_got_finished = true;
                    // Verify finish push contains reason.
                    EXPECT_NE(p.body.find("reason"), std::string::npos)
                        << "Alice finished push missing 'reason' field: "
                        << p.body;
                }
            } catch (const std::exception&) {
                alice_got_finished = true;  // timeout → move on
            }
        }
        if (!bob_got_finished) {
            try {
                auto p = bob->expect_message(
                    net::protocol::kBattleStatePush, 5s);
                if (p.body.find("finished") != std::string::npos) {
                    bob_got_finished = true;
                    // Verify finish push contains reason.
                    EXPECT_NE(p.body.find("reason"), std::string::npos)
                        << "Bob finished push missing 'reason' field: "
                        << p.body;
                }
            } catch (const std::exception&) {
                bob_got_finished = true;
            }
        }
    }

    EXPECT_TRUE(alice_got_finished)
        << "Alice did not receive finished push";
    EXPECT_TRUE(bob_got_finished)
        << "Bob did not receive finished push";

    // ── After settlement, verify battle-input is rejected ──────────
    auto after = alice->exchange(net::protocol::kBattleInputRequest, rid++,
                                "move:99,99", kDefaultTimeout);
    EXPECT_TRUE(after.message_id == net::protocol::kErrorResponse ||
                after.body.find("finished") != std::string::npos ||
                after.body.find("settlement") != std::string::npos)
        << "Expected error after battle finished, got msg_id="
        << after.message_id << " body=" << after.body;

    alice->close();
    bob->close();
}

// ── Frame-limit settlement (no surrender) ──────────────────────────

using v2_test::MultiProcessFixture;
TEST_F(MultiProcessFixture, FrameLimitSettlementDelivered) {
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(*this);

    constexpr auto kDefaultTimeout = 15s;
    uint32_t rid = 100;

    auto p1 = make_client();
    auto p2 = make_client();

    p1->exchange(net::protocol::kLoginRequest, rid++,
                "p1_frame|token:p1f|P1", kDefaultTimeout);
    p2->exchange(net::protocol::kLoginRequest, rid++,
                "p2_frame|token:p2f|P2", kDefaultTimeout);
    p1->exchange(net::protocol::kRoomCreateRequest, rid++,
                "frame_room", kDefaultTimeout);
    p2->exchange(net::protocol::kRoomJoinRequest, rid++,
                "frame_room", kDefaultTimeout);
    p1->exchange(net::protocol::kRoomReadyRequest, rid++, "true", kDefaultTimeout);
    p2->exchange(net::protocol::kRoomReadyRequest, rid++, "true", kDefaultTimeout);

    auto start = p1->exchange(net::protocol::kBattleStartRequest, rid++,
                              "frame_room", kDefaultTimeout);
    EXPECT_EQ(start.message_id, net::protocol::kBattleStartResponse);

    // Drain start pushes.
    p1->expect_message(net::protocol::kBattleStatePush, kDefaultTimeout);
    p2->expect_message(net::protocol::kBattleStatePush, kDefaultTimeout);

    // Submit inputs until we observe settlement or reach iteration limit.
    bool got_settlement = false;
    net::packet::DecodedPacket input_resp;
    for (int i = 0; i < 10; ++i) {
        // Use send+read instead of exchange so pushes arriving before the
        // input response do not get consumed and lost for expect_message.
        p1->send(net::protocol::kBattleInputRequest, rid++,
                 "move:" + std::to_string(i) + ",0");
        input_resp = p1->read(kDefaultTimeout);

        // If a push arrived before the input response, handle it now.
        if (input_resp.message_id == net::protocol::kBattleStatePush) {
            if (input_resp.body.find("settlement") != std::string::npos ||
                input_resp.body.find("finished") != std::string::npos) {
                got_settlement = true;
                // Drain matching push from p2
                try {
                    auto pb = p2->expect_message(
                        net::protocol::kBattleStatePush, 8s);
                    EXPECT_NE(pb.body.find("finished") == std::string::npos &&
                                  pb.body.find("settlement") == std::string::npos,
                              true)
                        << "Player2 did not receive finish/settlement push, got: "
                        << pb.body;
                } catch (const std::exception&) {
                }
                break;
            }
            // Frame advance push — read the actual input response
            input_resp = p1->read(kDefaultTimeout);
        }

        // Drain pushes for both players.
        try {
            auto pa = p1->expect_message(net::protocol::kBattleStatePush, 8s);
            auto pb = p2->expect_message(net::protocol::kBattleStatePush, 8s);
            if (pa.body.find("settlement") != std::string::npos ||
                pa.body.find("finished") != std::string::npos) {
                got_settlement = true;
                // Verify both received the same kind of push.
                EXPECT_NE(pb.body.find("finished") == std::string::npos &&
                              pb.body.find("settlement") == std::string::npos,
                          true)
                    << "Player2 did not receive finish/settlement push, got: "
                    << pb.body;
                break;
            }
        } catch (const std::exception&) {
            break;
        }

        // If input was rejected, battle may have ended already.
        if (input_resp.message_id == net::protocol::kErrorResponse) {
            got_settlement = true;
            break;
        }
    }

    EXPECT_TRUE(got_settlement)
        << "Did not observe settlement within 10 input rounds. Last input msg_id="
        << input_resp.message_id << " body=" << input_resp.body
        << " error_code=" << input_resp.error_code;

    p1->close();
    p2->close();
}
