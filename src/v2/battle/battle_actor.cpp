#include "v2/battle/battle_actor.h"

#include <utility>

namespace v2::battle {

void BattleActor::on_message(v2::actor::Message&& message) {
    const auto* create = std::get_if<CreateBattleMsg>(&message.payload);
    if (create == nullptr) {
        return;
    }

    state_.battle_id = create->battle_id;
    state_.room_id = create->room_id;
    state_.player_ids = create->player_ids;
    state_.started = true;

    sink_.push(BattleCreatedMsg{
        .battle_id = state_.battle_id,
        .room_id = state_.room_id,
        .player_ids = state_.player_ids,
    });
}

}  // namespace v2::battle
