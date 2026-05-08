#include <gtest/gtest.h>

#include <memory>
#include <utility>
#include <vector>

#include "v2/battle/battle_actor.h"
#include "v2/runtime/actor_system.h"

namespace {

class RecordingBattleSink final : public v2::battle::BattleEventSink {
public:
    void push(v2::battle::BattleEvent event) override {
        events.push_back(std::move(event));
    }

    std::vector<v2::battle::BattleEvent> events;
};

}  // namespace

TEST(V2BattleActorTest, CreateBattleMarksStartedAndEmitsCreatedEvent) {
    v2::runtime::ActorSystem actor_system;
    RecordingBattleSink sink;
    auto actor = std::make_unique<v2::battle::BattleActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.payload = v2::battle::CreateBattleMsg{
        .battle_id = "battle_0001",
        .room_id = "room_alpha",
        .player_ids = {"owner", "member"},
    };
    actor_ref.tell(std::move(message));

    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    EXPECT_TRUE(actor_ptr->state().started);
    EXPECT_EQ(actor_ptr->state().battle_id, "battle_0001");
    ASSERT_EQ(sink.events.size(), 1U);
    const auto* created = std::get_if<v2::battle::BattleCreatedMsg>(&sink.events.front());
    ASSERT_NE(created, nullptr);
    EXPECT_EQ(created->room_id, "room_alpha");
}
