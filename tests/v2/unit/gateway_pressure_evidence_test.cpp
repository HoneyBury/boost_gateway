#include "../../../examples/v2_gateway_pressure/load_evidence.h"
#include "../../../examples/v2_gateway_pressure/final_message_counts.h"
#include "../../../examples/v2_gateway_pressure/stall_watchdog_policy.h"
#include "../../../examples/v2_gateway_pressure/termination_policy.h"

#include <gtest/gtest.h>

#include <chrono>

namespace {

TEST(GatewayPressureEvidenceTest, MeasurementStartsOnlyAfterEveryClientAuthenticates) {
    using Evidence = v2::gateway_pressure::LoadEvidence;
    const auto started = Evidence::Clock::time_point{};
    Evidence evidence(2, started);

    evidence.on_started();
    evidence.on_tcp_connected();
    EXPECT_FALSE(evidence.on_authenticated(started + std::chrono::seconds(1)));
    evidence.on_started();
    evidence.on_tcp_connected();
    EXPECT_TRUE(evidence.on_authenticated(started + std::chrono::seconds(2)));

    evidence.finish_measurement(started + std::chrono::seconds(7));
    const auto snapshot = evidence.snapshot(started + std::chrono::seconds(9));
    EXPECT_EQ(snapshot.target_clients, 2U);
    EXPECT_EQ(snapshot.started_clients, 2U);
    EXPECT_EQ(snapshot.tcp_connected_clients, 2U);
    EXPECT_EQ(snapshot.authenticated_clients, 2U);
    EXPECT_EQ(snapshot.peak_active_clients, 2U);
    EXPECT_TRUE(snapshot.ramp_completed);
    EXPECT_DOUBLE_EQ(snapshot.ramp_up_seconds, 2.0);
    EXPECT_DOUBLE_EQ(snapshot.steady_state_elapsed_seconds, 5.0);
}

TEST(GatewayPressureEvidenceTest, CancellationDoesNotMasqueradeAsConnection) {
    using Evidence = v2::gateway_pressure::LoadEvidence;
    Evidence evidence(2, Evidence::Clock::time_point{});

    evidence.on_started();
    evidence.on_terminal(false, true, true);
    const auto snapshot = evidence.snapshot(Evidence::Clock::time_point{} + std::chrono::seconds(1));

    EXPECT_EQ(snapshot.started_clients, 1U);
    EXPECT_EQ(snapshot.tcp_connected_clients, 0U);
    EXPECT_EQ(snapshot.authenticated_clients, 0U);
    EXPECT_EQ(snapshot.cancelled_clients, 1U);
    EXPECT_EQ(snapshot.cancelled_before_connect, 1U);
    EXPECT_FALSE(snapshot.measurement_started);
}

TEST(GatewayPressureEvidenceTest, FinalMessageCountsUseOnePostIoSnapshot) {
    const auto echo = v2::gateway_pressure::final_message_counts(1234, 0);
    EXPECT_EQ(echo.response_messages, 1234U);
    EXPECT_EQ(echo.push_messages, 0U);
    EXPECT_EQ(echo.total_messages, echo.response_messages);

    const auto battle = v2::gateway_pressure::final_message_counts(100, 250);
    EXPECT_EQ(battle.response_messages, 100U);
    EXPECT_EQ(battle.push_messages, 250U);
    EXPECT_EQ(battle.total_messages,
              battle.response_messages + battle.push_messages);
}

TEST(GatewayPressureEvidenceTest, StallWatchdogDefersToRampAndDurationLifecycles) {
    using Action = v2::gateway_pressure::StallWatchdogAction;
    using State = v2::gateway_pressure::StallWatchdogState;

    EXPECT_EQ(v2::gateway_pressure::stall_watchdog_action(State{}), Action::kRearm);
    EXPECT_EQ(v2::gateway_pressure::stall_watchdog_action({
                  .measurement_started = true,
                  .duration_timer_expected = true,
              }),
              Action::kRearm);
    EXPECT_EQ(v2::gateway_pressure::stall_watchdog_action({
                  .measurement_started = true,
                  .duration_timer_expected = true,
                  .duration_deadline_armed = true,
                  .duration_deadline_elapsed = true,
              }),
              Action::kStopMonitoring);
    EXPECT_EQ(v2::gateway_pressure::stall_watchdog_action({
                  .measurement_started = true,
                  .duration_timer_expected = true,
                  .duration_deadline_armed = true,
              }),
              Action::kStopForStall);
    EXPECT_EQ(v2::gateway_pressure::stall_watchdog_action({
                  .measurement_started = true,
                  .progress_changed = true,
              }),
              Action::kRearm);
    EXPECT_EQ(v2::gateway_pressure::stall_watchdog_action({
                  .measurement_started = true,
              }),
              Action::kStopForStall);
    EXPECT_EQ(v2::gateway_pressure::stall_watchdog_action({
                  .lifecycle_finished = true,
              }),
              Action::kStopMonitoring);
}

TEST(GatewayPressureEvidenceTest, BattleErrorsAfterGlobalCompletionAreTeardown) {
    EXPECT_TRUE(v2::gateway_pressure::is_expected_shutdown_error(true, true));
    EXPECT_FALSE(v2::gateway_pressure::is_expected_shutdown_error(true, false));
    EXPECT_FALSE(v2::gateway_pressure::is_expected_shutdown_error(false, true));
}

}  // namespace
