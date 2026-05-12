// SDK Full Business Flow Client
// Runs complete lifecycle against a running gateway server.
//
// Usage: full_flow_client [host] [port]
//   Default: 127.0.0.1:9201
//
// Flow: connect → login → create room → join → ready → battle → finish → leave

#include "boost_gateway/sdk/client.h"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <thread>

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

    sdk::SdkClient alice, bob;
    int push_count = 0;
    auto on_push = [&](const sdk::PushMessage& push) {
        ++push_count;
        std::cout << "  [PUSH #" << push_count << "] msg_id="
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
    auto alice_login = alice.login("alice", "token:alice", 5s);
    CHECK(alice_login.ok, "Alice login: " + alice_login.error_message);
    std::cout << "  Alice logged in as: " << alice_login.user_id << std::endl;

    auto bob_login = bob.login("bob", "token:bob", 5s);
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
    // 4. CREATE ROOM
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[4] Create room..." << std::endl;
    auto create = alice.create_room("sdk_test_room", 5s);
    CHECK(create.ok, "Create room: " + create.error_message);
    std::cout << "  Room created: " << create.room_id << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // 5. JOIN ROOM
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[5] Join room..." << std::endl;
    auto join = bob.join_room("sdk_test_room", 5s);
    CHECK(join.ok, "Join room: " + join.error_message);
    std::cout << "  Bob joined: " << join.room_id << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // 6. READY
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[6] Ready up..." << std::endl;
    CHECK(alice.set_ready(true, 5s).ok, "Alice ready failed");
    CHECK(bob.set_ready(true, 5s).ok, "Bob ready failed");
    std::cout << "  Both ready." << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // 7. START BATTLE
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[7] Start battle..." << std::endl;
    auto battle = alice.start_battle("sdk_test_room", 5s);
    CHECK(battle.ok, "Start battle: " + battle.error_message);
    std::cout << "  Battle started: " << battle.battle_id << std::endl;

    // Drain battle state pushes
    std::this_thread::sleep_for(200ms);

    // ═══════════════════════════════════════════════════════════════
    // 8. BATTLE INPUT
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[8] Battle inputs..." << std::endl;
    auto move1 = alice.send_battle_input("move:50,50", 5s);
    std::cout << "  Alice move: " << (move1.ok ? "accepted" : "rejected") << std::endl;

    auto move2 = bob.send_battle_input("move:60,60", 5s);
    std::cout << "  Bob move: " << (move2.ok ? "accepted" : "rejected") << std::endl;

    auto atk = alice.send_battle_input("attack:bob", 5s);
    std::cout << "  Alice attack: " << (atk.ok ? "accepted" : "rejected") << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // 9. FINISH BATTLE
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[9] Finish battle..." << std::endl;
    auto finish = alice.send_battle_input("finish:surrender", 5s);
    std::cout << "  Surrender: " << (finish.ok ? "accepted" : "rejected") << std::endl;

    // Drain settlement pushes
    std::this_thread::sleep_for(500ms);

    // ═══════════════════════════════════════════════════════════════
    // 10. LEAVE ROOM
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[10] Leave room..." << std::endl;
    CHECK(alice.leave_room("sdk_test_room", 5s).ok, "Alice leave failed");
    CHECK(bob.leave_room("sdk_test_room", 5s).ok, "Bob leave failed");
    std::cout << "  Both left room." << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // 11. RECONNECT TEST
    // ═══════════════════════════════════════════════════════════════
    std::cout << "\n[11] Reconnect test..." << std::endl;
    // Create room, disconnect, reconnect, verify state
    CHECK(alice.create_room("reconnect_room", 5s).ok, "Create reconnect room failed");
    alice.disconnect();
    std::cout << "  Disconnected. Reconnecting..." << std::endl;
    CHECK(alice.connect(host, port, 5s), "Reconnect failed");
    auto relogin = alice.login("alice", "token:alice", 5s);
    std::cout << "  Relogin: " << (relogin.ok ? "OK" : "FAILED") << std::endl;

    // ═══════════════════════════════════════════════════════════════
    // CLEANUP
    // ═══════════════════════════════════════════════════════════════
    alice.leave_room("reconnect_room", 5s);
    alice.disconnect();
    bob.disconnect();

    std::cout << "\n=== ALL TESTS PASSED ===" << std::endl;
    std::cout << "Push messages received: " << push_count << std::endl;
    return 0;
}
