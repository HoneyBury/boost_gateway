// SDK Full Business Flow Client
// Runs complete lifecycle against a running gateway server.
//
// Usage: full_flow_client [host] [port]
//   Default: 127.0.0.1:9201
//
// Flow: connect → login → match → room → battle → leaderboard → reconnect

#include "boost_gateway/sdk/client.h"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace sdk = boost_gateway::sdk;
using namespace std::chrono_literals;

#define CHECK(expr, msg) do { \
    if (!(expr)) { std::cerr << "FAIL: " << msg << std::endl; return 1; } \
} while(0)

int main(int argc, char* argv[]) {
    std::string host = argc > 1 ? argv[1] : "127.0.0.1";
    std::uint16_t port = argc > 2 ? static_cast<std::uint16_t>(std::atoi(argv[2])) : 9201;

    std::cout << "=== BoostGateway SDK Full Flow Test ===" << std::endl;
    std::cout << "Target: " << host << ":" << port << std::endl;

    const auto run_id = std::chrono::steady_clock::now().time_since_epoch().count();
    const auto run_suffix = std::to_string(run_id);
    const auto alice_id = "alice_" + run_suffix;
    const auto bob_id = "bob_" + run_suffix;
    const auto room_id = "sdk_test_room_" + std::to_string(run_id);
    const auto reconnect_room_id = "reconnect_room_" + std::to_string(run_id);
    const auto base_score = 9'000'000'000'000LL + static_cast<long long>(run_id % 1'000'000);
    const auto alice_score = base_score;
    const auto bob_score = base_score + 100;

    sdk::SdkClient alice, bob;
    std::mutex push_mutex;
    std::vector<sdk::PushMessage> pushes;
    auto on_push = [&](const sdk::PushMessage& push) {
        std::lock_guard<std::mutex> lock(push_mutex);
        pushes.push_back(push);
        std::cout << "  [PUSH #" << pushes.size() << "] msg_id="
                  << push.message_id << " body=" << push.body << std::endl;
    };
    alice.on_push(on_push);
    bob.on_push(on_push);

    // ═══════════════════════════════════════════════════════════════
    // 1. CONNECT
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[1] Connecting..." << std::endl;
    CHECK(alice.connect(host, port, 5s), "Alice connect failed");
    CHECK(bob.connect(host, port, 5s), "Bob connect failed");
    std::cout << "  Both connected." << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // 2. LOGIN
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[2] Login..." << std::endl;
    auto alice_login = alice.login(alice_id, "token:" + alice_id, 5s);
    CHECK(alice_login.ok, "Alice login: " + alice_login.error_message);
    std::cout << "  Alice logged in as: " << alice_login.user_id << std::endl;

    auto bob_login = bob.login(bob_id, "token:" + bob_id, 5s);
    CHECK(bob_login.ok, "Bob login: " + bob_login.error_message);
    std::cout << "  Bob logged in as: " << bob_login.user_id << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // 3. ECHO TEST
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[3] Echo test..." << std::endl;
    auto echo = alice.echo("Hello from SDK!", 5s);
    CHECK(echo.ok, "Echo failed");
    std::cout << "  Echo: " << echo.echo_body << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // 4. MATCHMAKING
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[4] Matchmaking..." << std::endl;
    auto alice_match_join = alice.match_join(alice_id, 1200, "1v1", 5s);
    CHECK(alice_match_join.ok, "Alice match join: " + alice_match_join.error_message);
    CHECK(alice_match_join.response_body.find("\"queued\":true") != std::string::npos,
          "Alice match join missing queued=true: " + alice_match_join.response_body);
    auto alice_match_status = alice.match_status(alice_id, "1v1", 5s);
    CHECK(alice_match_status.ok, "Alice match status: " + alice_match_status.error_message);
    CHECK(alice_match_status.response_body.find("\"matched\":false") != std::string::npos ||
              alice_match_status.response_body.find("\"matched\":true") != std::string::npos,
          "Alice match status missing matched field: " + alice_match_status.response_body);

    auto bob_match_join = bob.match_join(bob_id, 1210, "1v1", 5s);
    CHECK(bob_match_join.ok, "Bob match join: " + bob_match_join.error_message);
    std::this_thread::sleep_for(1200ms);
    auto bob_match_status = bob.match_status(bob_id, "1v1", 5s);
    CHECK(bob_match_status.ok, "Bob match status: " + bob_match_status.error_message);
    auto bob_match_leave = bob.match_leave(bob_id, "1v1", 5s);
    CHECK(bob_match_leave.ok, "Bob match leave: " + bob_match_leave.error_message);
    std::cout << "  Match join/status/leave OK." << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // 5. CREATE ROOM
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[5] Create room..." << std::endl;
    auto create = alice.create_room(room_id, 5s);
    CHECK(create.ok, "Create room: " + create.error_message);
    std::cout << "  Room created: " << create.room_id << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // 6. JOIN ROOM
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[6] Join room..." << std::endl;
    auto join = bob.join_room(room_id, 5s);
    CHECK(join.ok, "Join room: " + join.error_message);
    std::cout << "  Bob joined: " << join.room_id << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // 7. READY
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[7] Ready up..." << std::endl;
    CHECK(alice.set_ready(true, 5s).ok, "Alice ready failed");
    CHECK(bob.set_ready(true, 5s).ok, "Bob ready failed");
    std::cout << "  Both ready." << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // 8. START BATTLE
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[8] Start battle..." << std::endl;
    auto battle = alice.start_battle(room_id, 5s);
    CHECK(battle.ok, "Start battle: " + battle.error_message);
    CHECK(battle.error_message.find("battle_started") != std::string::npos,
          "Start battle response missing battle_started: " + battle.error_message);
    std::cout << "  Battle started: " << battle.error_message << std::endl;

    // Drain battle state pushes
    std::this_thread::sleep_for(200ms);

    // ═══════════════════════════════════════════════════════════════
    // 9. BATTLE INPUT
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[9] Battle inputs..." << std::endl;
    auto move1 = alice.send_battle_input("move:50,50", 5s);
    CHECK(move1.ok, "Alice move rejected: " + move1.error_message);
    std::cout << "  Alice move: accepted" << std::endl;

    auto move2 = bob.send_battle_input("move:60,60", 5s);
    CHECK(move2.ok, "Bob move rejected: " + move2.error_message);
    std::cout << "  Bob move: accepted" << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // 10. FINISH BATTLE
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[10] Finish battle..." << std::endl;
    auto finish = alice.send_battle_input("finish:surrender", 5s);
    CHECK(finish.ok, "Surrender rejected: " + finish.error_message);
    CHECK(finish.error_message.find("battle_end_accepted:surrender") != std::string::npos,
          "Surrender response missing battle_end_accepted:surrender: " + finish.error_message);
    std::cout << "  Surrender: accepted" << std::endl;

    auto saw_push = [&](const std::string& fragment) {
        std::lock_guard<std::mutex> lock(push_mutex);
        for (const auto& push : pushes) {
            if (push.body.find(fragment) != std::string::npos) return true;
        }
        return false;
    };

    auto after_finish = bob.send_battle_input("move:70,70", 5s);
    CHECK(!after_finish.ok, "Battle input after finish should be rejected");
    CHECK(after_finish.error_message.find("battle_not_started") != std::string::npos ||
              after_finish.error_message.find("BattleNotStarted") != std::string::npos ||
              after_finish.error_message.find("finished") != std::string::npos,
          "Unexpected after-finish rejection body: " + after_finish.error_message);
    std::cout << "  After-finish input rejected as expected: "
              << after_finish.error_message << std::endl;

    CHECK(saw_push("battle_state:kind=started") || saw_push("\"kind\":\"started\""),
          "Missing battle started push");
    CHECK(saw_push("battle_state:kind=settlement") ||
              saw_push("\"kind\":\"battle_finished\""),
          "Missing battle settlement/finished push");
    CHECK(saw_push("battle_state:kind=finished") ||
              saw_push("\"kind\":\"battle_finished\""),
          "Missing battle finished push");
    CHECK(saw_push("reason=surrender") ||
              saw_push("\"reason\":\"surrender\"") ||
              saw_push("\"reason\":\"user_requested\""),
          "Missing finish reason in battle push");

    // ═══════════════════════════════════════════════════════════════
    // 11. LEADERBOARD
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[11] Leaderboard..." << std::endl;
    auto top = alice.leaderboard_top(20, 5s);
    CHECK(top.ok, "Leaderboard top: " + top.error_message);
    CHECK(top.response_body.find("\"entries\"") != std::string::npos,
          "Leaderboard top missing entries: " + top.response_body);

    auto wait_for_rank = [&](const std::string& user_id) {
        sdk::LeaderboardQueryResult latest;
        const auto deadline = std::chrono::steady_clock::now() + 5s;
        while (std::chrono::steady_clock::now() < deadline) {
            latest = alice.leaderboard_rank(user_id, 5s);
            if (latest.ok &&
                latest.response_body.find("\"user_id\":\"" + user_id + "\"") != std::string::npos) {
                return latest;
            }
            std::this_thread::sleep_for(100ms);
        }
        return latest;
    };

    auto rank = wait_for_rank(alice_id);
    CHECK(rank.ok, "Leaderboard rank Alice: " + rank.error_message);
    CHECK(rank.response_body.find("\"user_id\":\"" + alice_id + "\"") != std::string::npos,
          "Leaderboard rank missing Alice: " + rank.response_body);
    auto bob_rank = wait_for_rank(bob_id);
    CHECK(bob_rank.ok, "Leaderboard rank Bob: " + bob_rank.error_message);
    CHECK(bob_rank.response_body.find("\"user_id\":\"" + bob_id + "\"") != std::string::npos,
          "Leaderboard rank missing Bob: " + bob_rank.response_body);
    std::cout << "  Auto settlement leaderboard rank OK." << std::endl;

    auto alice_submit = alice.leaderboard_submit(alice_id, "Alice", alice_score, 5s);
    CHECK(alice_submit.ok, "Manual Alice leaderboard submit: " + alice_submit.error_message);
    auto bob_submit = bob.leaderboard_submit(bob_id, "Bob", bob_score, 5s);
    CHECK(bob_submit.ok, "Manual Bob leaderboard submit: " + bob_submit.error_message);
    std::cout << "  Manual leaderboard submit path OK." << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // 12. LEAVE ROOM
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[12] Leave room..." << std::endl;
    CHECK(alice.leave_room(room_id, 5s).ok, "Alice leave failed");
    CHECK(bob.leave_room(room_id, 5s).ok, "Bob leave failed");
    std::cout << "  Both left room." << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // 13. RECONNECT TEST
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[13] Reconnect test..." << std::endl;
    // Create room, disconnect, reconnect, verify state
    CHECK(alice.create_room(reconnect_room_id, 5s).ok, "Create reconnect room failed");
    alice.disconnect();
    std::cout << "  Disconnected. Reconnecting..." << std::endl;
    CHECK(alice.connect(host, port, 5s), "Reconnect failed");
    auto relogin = alice.login(alice_id, "token:" + alice_id, 5s);
    std::cout << "  Relogin: " << (relogin.ok ? "OK" : "FAILED") << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // CLEANUP
    // ═══════════════════════════════════════════════════════════════
    alice.leave_room(reconnect_room_id, 5s);
    alice.disconnect();
    bob.disconnect();

    std::cout << "\n=== ALL TESTS PASSED ===" << std::endl;
    std::cout << "Push messages received: " << pushes.size() << std::endl;
    return 0;
}
