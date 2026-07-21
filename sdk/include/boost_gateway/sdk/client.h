#pragma once
// BoostGateway SDK: main client class.
// High-level API for connecting to a BoostGateway server, sending requests,
// receiving responses, and handling server push messages.

#include "boost_gateway/sdk/error.h"
#include "boost_gateway/sdk/types.h"

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>

namespace boost_gateway {
namespace sdk {

class SdkClient {
public:
    SdkClient();
    ~SdkClient();

    SdkClient(const SdkClient&) = delete;
    SdkClient& operator=(const SdkClient&) = delete;

    // ── Connection ──────────────────────────────────────────────────

    /// Connect to a gateway server. Returns true on success.
    bool connect(const std::string& host, std::uint16_t port,
                 std::chrono::milliseconds timeout = std::chrono::seconds(5));

    /// Disconnect from the server.
    void disconnect();

    /// Check if currently connected.
    [[nodiscard]] bool is_connected() const;

    // ── Authentication ──────────────────────────────────────────────

    /// Login with user_id and token.
    LoginResult login(const std::string& user_id, const std::string& token,
                      std::chrono::milliseconds timeout = std::chrono::seconds(5));

    RegisterResult register_account(const std::string& user_id,
                                    const std::string& credential,
                                    const std::string& display_name = "",
                                    std::chrono::milliseconds timeout = std::chrono::seconds(5));

    // ── Room ────────────────────────────────────────────────────────

    RoomResult create_room(const std::string& room_id,
                           std::chrono::milliseconds timeout = std::chrono::seconds(5));

    RoomResult join_room(const std::string& room_id,
                         std::chrono::milliseconds timeout = std::chrono::seconds(5));

    RoomResult leave_room(const std::string& room_id,
                          std::chrono::milliseconds timeout = std::chrono::seconds(5));

    RoomResult set_ready(bool ready,
                         std::chrono::milliseconds timeout = std::chrono::seconds(5));

    RoomQueryResult room_list(std::size_t page = 1,
                              std::size_t page_size = 20,
                              const std::string& status = "",
                              std::chrono::milliseconds timeout = std::chrono::seconds(5));

    RoomQueryResult room_detail(const std::string& room_id,
                                std::chrono::milliseconds timeout = std::chrono::seconds(5));

    RoomQueryResult room_kick(const std::string& target_user_id,
                              std::chrono::milliseconds timeout = std::chrono::seconds(5));

    RoomQueryResult room_transfer_owner(const std::string& new_owner_id,
                                        std::chrono::milliseconds timeout = std::chrono::seconds(5));

    // ── Battle ──────────────────────────────────────────────────────

    BattleStartResult start_battle(const std::string& room_id,
                                    std::chrono::milliseconds timeout = std::chrono::seconds(5));

    BattleInputResult send_battle_input(const std::string& input_data,
                                         std::chrono::milliseconds timeout = std::chrono::seconds(5));

    BattleStateResult battle_state(const std::string& battle_id,
                                   std::chrono::milliseconds timeout = std::chrono::seconds(5));

    ReplayLoadResult replay_load(const std::string& battle_id,
                                 std::chrono::milliseconds timeout = std::chrono::seconds(5));

    // ── Matchmaking ─────────────────────────────────────────────────

    MatchResult match_join(const std::string& user_id,
                           std::int64_t mmr = 1000,
                           const std::string& mode = "1v1",
                           std::chrono::milliseconds timeout = std::chrono::seconds(5));

    MatchResult match_leave(const std::string& user_id,
                            const std::string& mode = "1v1",
                            std::chrono::milliseconds timeout = std::chrono::seconds(5));

    MatchResult match_status(const std::string& user_id,
                             const std::string& mode = "1v1",
                             std::chrono::milliseconds timeout = std::chrono::seconds(5));

    // ── Leaderboard ─────────────────────────────────────────────────

    LeaderboardSubmitResult leaderboard_submit(const std::string& user_id,
                                               const std::string& display_name,
                                               std::int64_t score,
                                               std::chrono::milliseconds timeout = std::chrono::seconds(5));

    LeaderboardQueryResult leaderboard_top(std::size_t k = 10,
                                           std::chrono::milliseconds timeout = std::chrono::seconds(5));

    LeaderboardQueryResult leaderboard_rank(const std::string& user_id,
                                            std::chrono::milliseconds timeout = std::chrono::seconds(5));

    // ── Echo (test) ─────────────────────────────────────────────────

    EchoResult echo(const std::string& body,
                    std::chrono::milliseconds timeout = std::chrono::seconds(5));

    // ── Async API (v3.4.0) ─────────────────────────────────────────────────

    /// Async connect. Callback fires on completion.
    void async_connect(const std::string& host, std::uint16_t port,
                       std::function<void(bool)> callback,
                       std::chrono::milliseconds timeout = std::chrono::seconds(5));

    /// Async login. Callback fires with result.
    void async_login(const std::string& user_id, const std::string& token,
                     std::function<void(LoginResult)> callback,
                     std::chrono::milliseconds timeout = std::chrono::seconds(5));

    /// Async create_room.
    void async_create_room(const std::string& room_id,
                           std::function<void(RoomResult)> callback,
                           std::chrono::milliseconds timeout = std::chrono::seconds(5));

    /// Async join_room.
    void async_join_room(const std::string& room_id,
                         std::function<void(RoomResult)> callback,
                         std::chrono::milliseconds timeout = std::chrono::seconds(5));

    /// Async battle input.
    void async_send_battle_input(const std::string& input_data,
                                 std::function<void(BattleInputResult)> callback,
                                 std::chrono::milliseconds timeout = std::chrono::seconds(5));

    /// Set async push callback (called from background receive thread).
    void on_async_push(std::function<void(const std::string&)> callback);

    /// Set async disconnect callback.
    void on_async_disconnect(std::function<void()> callback);

    // ── Callbacks ───────────────────────────────────────────────────

    /// Set callback for server push messages (kicked, resumed, room state, battle state).
    void on_push(PushCallback callback);

    /// Set callback for unexpected disconnection.
    void on_disconnect(DisconnectCallback callback);

    // ── Heartbeat ───────────────────────────────────────────────────

    /// Start automatic heartbeat. Call after login.
    void start_heartbeat(std::chrono::seconds interval = std::chrono::seconds(15));

    void stop_heartbeat();

private:
    class Impl;
    std::shared_ptr<Impl> impl_;
};

}  // namespace sdk
}  // namespace boost_gateway
