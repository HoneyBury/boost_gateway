#include "v2/service/service_manifest.h"

#include <vector>

namespace v2::service {

const std::vector<ServiceManifest>& all_manifests() {
    static const std::vector<ServiceManifest> manifests = {
        gateway_manifest(),
        login_manifest(),
        room_manifest(),
        battle_manifest(),
    };
    return manifests;
}

ServiceManifest gateway_manifest() {
    return ServiceManifest{
        .service_id = ServiceId::kGateway,
        .description = "Client ingress: session, rate-limiting, protocol codec, outbound writes",
        .owned_state = {"session", "ingress_rate_limit", "client_connection"},
        .handled_messages = {"login_request", "room_create", "room_join", "room_ready",
                             "room_list", "room_detail", "battle_start", "battle_input",
                             "battle_state", "battle_end", "frame_ack"},
        .read_only_state = {},
    };
}

ServiceManifest login_manifest() {
    return ServiceManifest{
        .service_id = ServiceId::kLogin,
        .description = "Authentication: token validation, player identity, session binding",
        .owned_state = {"player_auth", "player_profile"},
        .handled_messages = {"login_request", "register_account", "token_validate",
                             "session_bind", "session_close"},
        .read_only_state = {},
    };
}

ServiceManifest room_manifest() {
    return ServiceManifest{
        .service_id = ServiceId::kRoom,
        .description = "Room management: create, join, ready, list/detail, admin, battle initiation",
        .owned_state = {"room", "room_membership", "readiness"},
        .handled_messages = {"room_create", "room_join", "room_ready", "room_list",
                             "room_detail", "room_kick", "room_transfer_owner",
                             "room_start_battle"},
        .read_only_state = {"player_auth"},
    };
}

ServiceManifest battle_manifest() {
    return ServiceManifest{
        .service_id = ServiceId::kBattle,
        .description = "Battle engine: frames, input, scoring, replay, lifecycle",
        .owned_state = {"battle", "frame", "replay", "score", "battle_lifecycle"},
        .handled_messages = {"battle_start", "battle_input", "battle_state", "replay_load",
                             "battle_end", "frame_ack", "tick", "player_disconnect"},
        .read_only_state = {"room_membership"},
    };
}

ServiceId owner_of(const std::string& state_name) {
    for (const auto& manifest : all_manifests()) {
        for (const auto& owned : manifest.owned_state) {
            if (owned == state_name) {
                return manifest.service_id;
            }
        }
    }
    return ServiceId::kGateway;
}

ServiceId handler_of(const std::string& message_type) {
    // Check backends first so they take priority over gateway (which also lists
    // these messages as handled, but only as a forward/proxy).
    const auto& manifests = all_manifests();
    for (auto it = manifests.rbegin(); it != manifests.rend(); ++it) {
        for (const auto& handled : it->handled_messages) {
            if (handled == message_type) {
                return it->service_id;
            }
        }
    }
    return ServiceId::kGateway;
}

}  // namespace v2::service
