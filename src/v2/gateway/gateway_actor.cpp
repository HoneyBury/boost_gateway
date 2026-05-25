#include "v2/gateway/gateway_actor.h"

#include "net/protocol.h"
#include "v2/gateway/gateway_command_parser.h"

#include <utility>

namespace v2::gateway {

GatewayActor::GatewayActor(SessionWriteSink& sink,
                           GatewayCommandSink* command_sink,
                           RateLimitPolicy rate_limit_policy,
                           AuthorizePolicy authorize_policy)
    : sink_(sink),
      command_sink_(command_sink),
      rate_limit_policy_(std::move(rate_limit_policy)),
      authorize_policy_(std::move(authorize_policy)) {}

void GatewayActor::on_message(v2::actor::Message&& message) {
    const auto* envelope = std::get_if<ClientEnvelope>(&message.payload);
    if (envelope == nullptr) {
        return;
    }
    if (rate_limit_policy_) {
        const auto limit_result = rate_limit_policy_(*envelope, envelope->session_id);
        if (!limit_result.allowed) {
            emit_error(*envelope,
                       static_cast<std::int32_t>(net::protocol::ErrorCode::kRateLimited),
                       "rate_limited,retry_after_ms=" + std::to_string(limit_result.retry_after_ms));
            return;
        }
    }

    const auto command = to_command(*envelope);
    if (!command.has_value()) {
        emit_error(*envelope,
                   static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                   "unmodeled_message");
        return;
    }

    if (!is_public_message(envelope->protocol_message_id)) {
        const auto authorized = authorize_policy_
            ? authorize_policy_(*command)
            : false;
        if (!authorized) {
            emit_error(*envelope,
                       static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                       net::protocol::to_string(net::protocol::ErrorCode::kAuthRequired));
            return;
        }
    }

    switch (command->type) {
        case GatewayCommandType::kHeartbeat:
            emit_response(*envelope,
                          net::protocol::kHeartbeatResponse,
                          static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                          "pong");
            return;
        case GatewayCommandType::kEcho:
            emit_response(*envelope,
                          net::protocol::kEchoResponse,
                          static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                          command->body);
            return;
        case GatewayCommandType::kLogin:
        case GatewayCommandType::kRegister:
        case GatewayCommandType::kRoomCreate:
        case GatewayCommandType::kRoomJoin:
        case GatewayCommandType::kRoomReady:
        case GatewayCommandType::kRoomLeave:
        case GatewayCommandType::kRoomList:
        case GatewayCommandType::kRoomDetail:
        case GatewayCommandType::kRoomKick:
        case GatewayCommandType::kRoomTransferOwner:
        case GatewayCommandType::kBattleStart:
        case GatewayCommandType::kBattleState:
        case GatewayCommandType::kReplayLoad:
        case GatewayCommandType::kMatchJoin:
        case GatewayCommandType::kMatchLeave:
        case GatewayCommandType::kMatchStatus:
        case GatewayCommandType::kLeaderboardSubmit:
        case GatewayCommandType::kLeaderboardTop:
        case GatewayCommandType::kLeaderboardRank:
            if (command_sink_ != nullptr && command_sink_->handle(*command)) {
                return;
            }
            if (command->type == GatewayCommandType::kLogin) {
                const auto login_body = parse_login_command_body(command->body);
                if (!login_body.has_value() || !validate_login_command_body(*login_body)) {
                    emit_error(*envelope,
                               static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidUserId),
                               net::protocol::to_string(net::protocol::ErrorCode::kInvalidUserId));
                    return;
                }
                emit_response(*envelope,
                              net::protocol::kLoginResponse,
                              static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                              "login_ok:" + login_body->user_id);
                return;
            }
            emit_error(*envelope,
                       static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                       "unmodeled_message");
            return;
        case GatewayCommandType::kBattleInput:
            if (command_sink_ != nullptr && command_sink_->handle(*command)) {
                return;
            }
            emit_error(*envelope,
                       static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                       "unmodeled_message");
            return;
        case GatewayCommandType::kUnknown:
            break;
    }

    emit_error(*envelope,
               static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
               "unmodeled_message");
}

bool GatewayActor::is_public_message(std::uint16_t protocol_message_id) const {
    switch (protocol_message_id) {
        case net::protocol::kHeartbeatRequest:
        case net::protocol::kLoginRequest:
        case net::protocol::kRegisterRequest:
        case net::protocol::kEchoRequest:
            return true;
        default:
            return false;
    }
}

std::optional<GatewayCommand> GatewayActor::to_command(const ClientEnvelope& envelope) const {
    GatewayCommand command;
    command.session_id = envelope.session_id;
    command.protocol_message_id = envelope.protocol_message_id;
    command.request_id = envelope.request_id;
    command.flags = envelope.flags;
    command.body = envelope.body;

    switch (envelope.protocol_message_id) {
        case net::protocol::kHeartbeatRequest:
            command.type = GatewayCommandType::kHeartbeat;
            return command;
        case net::protocol::kEchoRequest:
            command.type = GatewayCommandType::kEcho;
            return command;
        case net::protocol::kLoginRequest:
            command.type = GatewayCommandType::kLogin;
            return command;
        case net::protocol::kRegisterRequest:
            command.type = GatewayCommandType::kRegister;
            return command;
        case net::protocol::kRoomCreateRequest:
            command.type = GatewayCommandType::kRoomCreate;
            return command;
        case net::protocol::kRoomJoinRequest:
            command.type = GatewayCommandType::kRoomJoin;
            return command;
        case net::protocol::kRoomReadyRequest:
            command.type = GatewayCommandType::kRoomReady;
            return command;
        case net::protocol::kRoomLeaveRequest:
            command.type = GatewayCommandType::kRoomLeave;
            return command;
        case net::protocol::kRoomListRequest:
            command.type = GatewayCommandType::kRoomList;
            return command;
        case net::protocol::kRoomDetailRequest:
            command.type = GatewayCommandType::kRoomDetail;
            return command;
        case net::protocol::kRoomKickRequest:
            command.type = GatewayCommandType::kRoomKick;
            return command;
        case net::protocol::kRoomTransferOwnerRequest:
            command.type = GatewayCommandType::kRoomTransferOwner;
            return command;
        case net::protocol::kBattleStartRequest:
            command.type = GatewayCommandType::kBattleStart;
            return command;
        case net::protocol::kBattleInputRequest:
            command.type = GatewayCommandType::kBattleInput;
            return command;
        case net::protocol::kBattleStateRequest:
            command.type = GatewayCommandType::kBattleState;
            return command;
        case net::protocol::kReplayLoadRequest:
            command.type = GatewayCommandType::kReplayLoad;
            return command;
        case net::protocol::kMatchJoinRequest:
            command.type = GatewayCommandType::kMatchJoin;
            return command;
        case net::protocol::kMatchLeaveRequest:
            command.type = GatewayCommandType::kMatchLeave;
            return command;
        case net::protocol::kMatchStatusRequest:
            command.type = GatewayCommandType::kMatchStatus;
            return command;
        case net::protocol::kLeaderboardSubmitRequest:
            command.type = GatewayCommandType::kLeaderboardSubmit;
            return command;
        case net::protocol::kLeaderboardTopRequest:
            command.type = GatewayCommandType::kLeaderboardTop;
            return command;
        case net::protocol::kLeaderboardRankRequest:
            command.type = GatewayCommandType::kLeaderboardRank;
            return command;
        default:
            return std::nullopt;
    }
}

void GatewayActor::emit_error(const ClientEnvelope& envelope,
                              std::int32_t error_code,
                              std::string body) {
    emit_response(envelope, net::protocol::kErrorResponse, error_code, std::move(body));
}

void GatewayActor::emit_response(const ClientEnvelope& request,
                                 std::uint16_t response_message_id,
                                 std::int32_t error_code,
                                 std::string body) {
    SessionWrite write;
    write.envelope.session_id = request.session_id;
    write.envelope.protocol_message_id = response_message_id;
    write.envelope.request_id = request.request_id;
    write.envelope.error_code = error_code;
    write.envelope.flags = request.flags;
    write.envelope.body = std::move(body);
    sink_.push(std::move(write));
}

}  // namespace v2::gateway
