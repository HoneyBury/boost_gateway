#include "v2/gateway/runtime.h"

#include "net/protocol.h"

#include <optional>
#include <sstream>
#include <utility>

namespace v2::gateway {

namespace {

std::vector<std::string> split(const std::string& body, char delimiter) {
    std::vector<std::string> parts;
    std::stringstream stream(body);
    std::string item;
    while (std::getline(stream, item, delimiter)) {
        parts.push_back(item);
    }
    return parts;
}

}  // namespace

v2::actor::ActorRef Runtime::create_gateway_actor() {
    return actor_system_.create_actor(std::make_unique<GatewayActor>(
        write_sink_,
        this,
        GatewayActor::RateLimitPolicy{},
        [this](const GatewayCommand& command) { return is_authenticated(command); }));
}

bool Runtime::is_authenticated(const GatewayCommand& command) const {
    return users_by_session_id_.contains(command.session_id);
}

bool Runtime::handle(const GatewayCommand& command) {
    switch (command.type) {
        case GatewayCommandType::kLogin: {
            const auto user_id = parse_login_user_id(command.body);
            if (user_id.empty()) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidUserId),
                     net::protocol::to_string(net::protocol::ErrorCode::kInvalidUserId));
                return true;
            }

            pending_login_[command.session_id] = PendingResponse{
                .session_id = command.session_id,
                .request_id = command.request_id,
            };

            auto player = get_or_create_player(user_id);

            v2::actor::Message bind;
            bind.header.kind = v2::actor::MessageKind::kUser;
            bind.payload = v2::player::BindSessionMsg{
                .session_id = command.session_id,
                .connection_id = command.session_id,
            };
            player.tell(std::move(bind));

            v2::actor::Message login;
            login.header.kind = v2::actor::MessageKind::kUser;
            login.payload = v2::player::LoginRequestMsg{
                .session_id = command.session_id,
                .user_id = user_id,
                .token = command.body,
                .display_name = parse_display_name(command.body),
            };
            player.tell(std::move(login));
            actor_system_.dispatch_all();
            return true;
        }
        case GatewayCommandType::kRoomCreate: {
            const auto user_id = session_user_id(command.session_id);
            if (user_id.empty()) {
                return false;
            }
            auto room_actor = actor_system_.create_actor(std::make_unique<v2::room::RoomActor>(*this));
            rooms_by_room_id_[command.body] = room_actor;
            pending_room_create_[command.body] = PendingResponse{
                .session_id = command.session_id,
                .request_id = command.request_id,
            };

            v2::actor::Message create;
            create.header.kind = v2::actor::MessageKind::kUser;
            create.payload = v2::room::CreateRoomMsg{
                .room_id = command.body,
                .owner_user_id = user_id,
                .owner_actor_id = players_by_user_id_.at(user_id).actor_id(),
            };
            room_actor.tell(std::move(create));

            v2::actor::Message assign;
            assign.header.kind = v2::actor::MessageKind::kUser;
            assign.payload = v2::player::RoomAssignedMsg{
                .room_actor_id = room_actor.actor_id(),
                .room_id = command.body,
            };
            players_by_user_id_.at(user_id).tell(std::move(assign));

            actor_system_.dispatch_all();
            rooms_by_session_id_[command.session_id] = command.body;
            emit(net::protocol::kRoomCreateResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 command.body);
            pending_room_create_.erase(command.body);
            return true;
        }
        case GatewayCommandType::kRoomJoin: {
            const auto user_id = session_user_id(command.session_id);
            auto room_it = rooms_by_room_id_.find(command.body);
            if (user_id.empty() || room_it == rooms_by_room_id_.end()) {
                return false;
            }
            pending_room_join_[command.body + ":" + user_id] = PendingResponse{
                .session_id = command.session_id,
                .request_id = command.request_id,
            };

            v2::actor::Message join;
            join.header.kind = v2::actor::MessageKind::kUser;
            join.payload = v2::room::JoinRoomMsg{
                .user_id = user_id,
                .player_actor_id = players_by_user_id_.at(user_id).actor_id(),
            };
            room_it->second.tell(std::move(join));

            v2::actor::Message assign;
            assign.header.kind = v2::actor::MessageKind::kUser;
            assign.payload = v2::player::RoomAssignedMsg{
                .room_actor_id = room_it->second.actor_id(),
                .room_id = command.body,
            };
            players_by_user_id_.at(user_id).tell(std::move(assign));

            actor_system_.dispatch_all();
            rooms_by_session_id_[command.session_id] = command.body;
            emit(net::protocol::kRoomJoinResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 command.body);
            pending_room_join_.erase(command.body + ":" + user_id);
            return true;
        }
        case GatewayCommandType::kRoomReady: {
            const auto user_id = session_user_id(command.session_id);
            auto room_name_it = rooms_by_session_id_.find(command.session_id);
            if (user_id.empty() || room_name_it == rooms_by_session_id_.end()) {
                return false;
            }
            const auto ready = command.body == "true";
            auto room_it = rooms_by_room_id_.find(room_name_it->second);
            if (room_it == rooms_by_room_id_.end()) {
                return false;
            }
            pending_room_ready_[room_name_it->second + ":" + user_id] = PendingResponse{
                .session_id = command.session_id,
                .request_id = command.request_id,
            };
            v2::actor::Message set_ready;
            set_ready.header.kind = v2::actor::MessageKind::kUser;
            set_ready.payload = v2::room::SetReadyMsg{
                .user_id = user_id,
                .ready = ready,
            };
            room_it->second.tell(std::move(set_ready));
            actor_system_.dispatch_all();
            emit(net::protocol::kRoomReadyResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 ready ? "true" : "false");
            pending_room_ready_.erase(room_name_it->second + ":" + user_id);
            return true;
        }
        case GatewayCommandType::kBattleStart: {
            const auto user_id = session_user_id(command.session_id);
            auto room_name_it = rooms_by_session_id_.find(command.session_id);
            if (user_id.empty() || room_name_it == rooms_by_session_id_.end()) {
                return false;
            }
            auto room_it = rooms_by_room_id_.find(room_name_it->second);
            if (room_it == rooms_by_room_id_.end()) {
                return false;
            }
            pending_battle_start_[room_name_it->second] = PendingResponse{
                .session_id = command.session_id,
                .request_id = command.request_id,
            };
            v2::actor::Message start;
            start.header.kind = v2::actor::MessageKind::kUser;
            start.payload = v2::room::StartBattleMsg{.requester_user_id = user_id};
            room_it->second.tell(std::move(start));
            actor_system_.dispatch_all();
            return true;
        }
        case GatewayCommandType::kHeartbeat:
        case GatewayCommandType::kEcho:
        case GatewayCommandType::kUnknown:
            return false;
    }

    return false;
}

void Runtime::push(v2::player::PlayerEvent event) {
    if (const auto* accepted = std::get_if<v2::player::LoginAcceptedMsg>(&event)) {
        users_by_session_id_[accepted->session_id] = accepted->user_id;
        auto pending = pending_login_.find(accepted->session_id);
        if (pending != pending_login_.end()) {
            emit(net::protocol::kLoginResponse,
                 accepted->session_id,
                 pending->second.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 "login_ok:" + accepted->user_id);
            pending_login_.erase(pending);
        }
        return;
    }

    if (const auto* kicked = std::get_if<v2::player::SessionKickPushMsg>(&event)) {
        emit(net::protocol::kSessionKickedPush,
             kicked->old_session_id,
             0,
             static_cast<std::int32_t>(net::protocol::ErrorCode::kDuplicateLogin),
             "duplicate_login");
        return;
    }

    if (const auto* resumed = std::get_if<v2::player::SessionResumePushMsg>(&event)) {
        emit(net::protocol::kSessionResumedPush,
             resumed->session_id,
             0,
             static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
             resumed->room_id);
    }
}

void Runtime::push(v2::room::RoomEvent event) {
    if (const auto* requested = std::get_if<v2::room::BattleStartRequestedMsg>(&event)) {
        auto pending = pending_battle_start_.find(requested->room_id);
        if (pending != pending_battle_start_.end()) {
            emit(net::protocol::kBattleStartResponse,
                 pending->second.session_id,
                 pending->second.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 "battle_started:" + requested->room_id);
            pending_battle_start_.erase(pending);
        }
        return;
    }

    if (const auto* rejected = std::get_if<v2::room::BattleStartRejectedMsg>(&event)) {
        auto pending = pending_battle_start_.find(rejected->room_id);
        if (pending != pending_battle_start_.end()) {
            auto error_code = net::protocol::ErrorCode::kAuthRequired;
            if (rejected->reason == "not_room_owner") {
                error_code = net::protocol::ErrorCode::kNotRoomOwner;
            } else if (rejected->reason == "not_enough_players") {
                error_code = net::protocol::ErrorCode::kNotEnoughPlayers;
            } else if (rejected->reason == "not_all_ready") {
                error_code = net::protocol::ErrorCode::kNotAllReady;
            }
            emit(net::protocol::kErrorResponse,
                 pending->second.session_id,
                 pending->second.request_id,
                 static_cast<std::int32_t>(error_code),
                 rejected->reason);
            pending_battle_start_.erase(pending);
        }
    }
}

v2::actor::ActorRef Runtime::get_or_create_player(const std::string& user_id) {
    auto it = players_by_user_id_.find(user_id);
    if (it != players_by_user_id_.end()) {
        return it->second;
    }
    auto actor = actor_system_.create_actor(std::make_unique<v2::player::PlayerActor>(*this));
    players_by_user_id_.emplace(user_id, actor);
    return actor;
}

std::string Runtime::parse_login_user_id(const std::string& body) const {
    const auto parts = split(body, '|');
    if (parts.empty()) {
        return {};
    }
    return parts.front();
}

std::string Runtime::parse_display_name(const std::string& body) const {
    const auto parts = split(body, '|');
    if (parts.size() < 3) {
        return {};
    }
    return parts[2];
}

std::string Runtime::session_user_id(SessionId session_id) const {
    auto it = users_by_session_id_.find(session_id);
    if (it == users_by_session_id_.end()) {
        return {};
    }
    return it->second;
}

void Runtime::emit(std::uint16_t message_id,
                   SessionId session_id,
                   std::uint32_t request_id,
                   std::int32_t error_code,
                   std::string body) {
    SessionWrite write;
    write.envelope.session_id = session_id;
    write.envelope.protocol_message_id = message_id;
    write.envelope.request_id = request_id;
    write.envelope.error_code = error_code;
    write.envelope.body = std::move(body);
    write_sink_.push(std::move(write));
}

}  // namespace v2::gateway
