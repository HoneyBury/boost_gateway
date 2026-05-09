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
    EXPECT_EQ(actor_ptr->state().lifecycle, v2::battle::BattleLifecycleState::kRunning);
    EXPECT_EQ(actor_ptr->state().battle_id, "battle_0001");
    ASSERT_EQ(actor_ptr->state().participants.size(), 2U);
    EXPECT_EQ(actor_ptr->state().participants[0].user_id, "owner");
    ASSERT_EQ(sink.events.size(), 1U);
    const auto* created = std::get_if<v2::battle::BattleCreatedMsg>(&sink.events.front());
    ASSERT_NE(created, nullptr);
    EXPECT_EQ(created->room_id, "room_alpha");
}

TEST(V2BattleActorTest, SubmitInputEmitsAcceptedEvent) {
    v2::runtime::ActorSystem actor_system;
    RecordingBattleSink sink;
    auto actor = std::make_unique<v2::battle::BattleActor>(sink);
    auto actor_ref = actor_system.create_actor(std::move(actor));

    v2::actor::Message create;
    create.header.kind = v2::actor::MessageKind::kUser;
    create.payload = v2::battle::CreateBattleMsg{
        .battle_id = "battle_0001",
        .room_id = "room_alpha",
        .player_ids = {"owner", "member"},
    };
    actor_ref.tell(std::move(create));

    v2::actor::Message input;
    input.header.kind = v2::actor::MessageKind::kUser;
    input.payload = v2::battle::SubmitBattleInputMsg{
        .user_id = "owner",
        .request_id = 77,
        .input_data = "move:1,2",
    };
    actor_ref.tell(std::move(input));

    EXPECT_EQ(actor_system.dispatch_all(), 2U);
    ASSERT_EQ(sink.events.size(), 2U);
    const auto* accepted = std::get_if<v2::battle::BattleInputAcceptedMsg>(&sink.events[1]);
    ASSERT_NE(accepted, nullptr);
    EXPECT_EQ(accepted->request_id, 77U);
    EXPECT_EQ(accepted->input_seq, 1U);
}

TEST(V2BattleActorTest, PlayerDisconnectFinishesBattle) {
    v2::runtime::ActorSystem actor_system;
    RecordingBattleSink sink;
    auto actor = std::make_unique<v2::battle::BattleActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    v2::actor::Message create;
    create.header.kind = v2::actor::MessageKind::kUser;
    create.payload = v2::battle::CreateBattleMsg{
        .battle_id = "battle_0001",
        .room_id = "room_alpha",
        .player_ids = {"owner", "member"},
    };
    actor_ref.tell(std::move(create));

    v2::actor::Message disconnected;
    disconnected.header.kind = v2::actor::MessageKind::kUser;
    disconnected.payload = v2::battle::PlayerDisconnectedMsg{.user_id = "owner"};
    actor_ref.tell(std::move(disconnected));

    EXPECT_EQ(actor_system.dispatch_all(), 2U);
    EXPECT_EQ(actor_ptr->state().lifecycle, v2::battle::BattleLifecycleState::kFinished);
    ASSERT_EQ(sink.events.size(), 3U);
    const auto* settlement = std::get_if<v2::battle::BattleSettlementPreparedMsg>(&sink.events[1]);
    ASSERT_NE(settlement, nullptr);
    EXPECT_EQ(settlement->reason, v2::battle::BattleFinishReason::kPlayerDisconnected);
    const auto* finished = std::get_if<v2::battle::BattleFinishedMsg>(&sink.events[2]);
    ASSERT_NE(finished, nullptr);
    EXPECT_EQ(finished->reason, v2::battle::BattleFinishReason::kPlayerDisconnected);
    EXPECT_EQ(finished->triggering_user_id, "owner");
}

TEST(V2BattleActorTest, TickAdvancesFrameAndCanFinishNormally) {
    v2::runtime::ActorSystem actor_system;
    RecordingBattleSink sink;
    auto actor = std::make_unique<v2::battle::BattleActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    v2::actor::Message create;
    create.header.kind = v2::actor::MessageKind::kUser;
    create.payload = v2::battle::CreateBattleMsg{
        .battle_id = "battle_0001",
        .room_id = "room_alpha",
        .player_ids = {"owner", "member"},
        .max_frames = 3,
    };
    actor_ref.tell(std::move(create));

    for (int i = 0; i < 3; ++i) {
        v2::actor::Message tick;
        tick.header.kind = v2::actor::MessageKind::kUser;
        tick.payload = v2::battle::TickBattleMsg{.trigger = "test_tick"};
        actor_ref.tell(std::move(tick));
    }

    EXPECT_EQ(actor_system.dispatch_all(), 4U);
    EXPECT_EQ(actor_ptr->state().frame_number, 3U);
    EXPECT_EQ(actor_ptr->state().lifecycle, v2::battle::BattleLifecycleState::kFinished);
    ASSERT_EQ(sink.events.size(), 6U);
    const auto* frame = std::get_if<v2::battle::BattleFrameAdvancedMsg>(&sink.events[1]);
    ASSERT_NE(frame, nullptr);
    EXPECT_EQ(frame->frame_number, 1U);
    const auto* settlement = std::get_if<v2::battle::BattleSettlementPreparedMsg>(&sink.events[4]);
    ASSERT_NE(settlement, nullptr);
    EXPECT_EQ(settlement->reason, v2::battle::BattleFinishReason::kFrameLimitReached);
    const auto* finished = std::get_if<v2::battle::BattleFinishedMsg>(&sink.events.back());
    ASSERT_NE(finished, nullptr);
    EXPECT_EQ(finished->reason, v2::battle::BattleFinishReason::kFrameLimitReached);
}

TEST(V2BattleActorTest, EndBattleMessageFinishesWithRequestedReason) {
    v2::runtime::ActorSystem actor_system;
    RecordingBattleSink sink;
    auto actor = std::make_unique<v2::battle::BattleActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    v2::actor::Message create;
    create.header.kind = v2::actor::MessageKind::kUser;
    create.payload = v2::battle::CreateBattleMsg{
        .battle_id = "battle_0001",
        .room_id = "room_alpha",
        .player_ids = {"owner", "member"},
    };
    actor_ref.tell(std::move(create));

    v2::actor::Message end;
    end.header.kind = v2::actor::MessageKind::kUser;
    end.payload = v2::battle::EndBattleMsg{
        .reason = v2::battle::BattleFinishReason::kSurrender,
        .triggering_user_id = "owner",
    };
    actor_ref.tell(std::move(end));

    EXPECT_EQ(actor_system.dispatch_all(), 2U);
    EXPECT_EQ(actor_ptr->state().lifecycle, v2::battle::BattleLifecycleState::kFinished);
    ASSERT_EQ(sink.events.size(), 3U);
    const auto* settlement = std::get_if<v2::battle::BattleSettlementPreparedMsg>(&sink.events[1]);
    ASSERT_NE(settlement, nullptr);
    EXPECT_EQ(settlement->reason, v2::battle::BattleFinishReason::kSurrender);
    const auto* finished = std::get_if<v2::battle::BattleFinishedMsg>(&sink.events[2]);
    ASSERT_NE(finished, nullptr);
    EXPECT_EQ(finished->reason, v2::battle::BattleFinishReason::kSurrender);
    EXPECT_EQ(finished->triggering_user_id, "owner");
}
