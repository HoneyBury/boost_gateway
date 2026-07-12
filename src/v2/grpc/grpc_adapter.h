#pragma once

// grpc_adapter.h — GrpcGatewayAdapter: dual-stack adapter that wraps
// GatewayGrpcServer into the same lifecycle conventions used by existing
// TCP-based services (room_backend_service, leaderboard_service, etc.).
//
// The adapter lets the demo main function choose between TCP and gRPC at
// startup via a --grpc flag, without modifying any existing TCP code path.
//
// Usage:
//   v2::grpc::GrpcGatewayAdapter adapter(50051);
//   adapter.start();
//   // ... main loop ...
//   adapter.stop();
//
// Conditionally compiled only when BOOST_BUILD_GRPC is defined.

#ifdef BOOST_BUILD_GRPC

#include <atomic>
#include <chrono>
#include <memory>
#include <string>
#include <thread>

#include <grpcpp/grpcpp.h>
#include <nlohmann/json.hpp>
#include <spdlog/spdlog.h>

#include "v2/gateway/gateway_service_bridge.h"
#include "v2/grpc/gateway_grpc_server.h"
#include "v3/tracing/otel_exporter.h"

namespace v2::grpc {

inline std::string bridge_failure(
    const v2::gateway::GatewayServiceBridge::BackendRoutingResult& result,
    const std::string& fallback) {
  return encode_backend_failure(
      result.error,
      result.response_payload.empty() ? fallback : result.response_payload);
}

// -------------------------------------------------------------------
// GrpcGatewayAdapter: adapter between existing v2 service conventions
// and the gRPC server lifecycle.
//
// The adapter owns a GatewayGrpcServer and a dedicated thread for
// polling the gRPC completion queue. This mirrors the pattern used
// by BackendServer and other TCP-based services.
// -------------------------------------------------------------------
class GrpcGatewayAdapter {
 public:
  struct BackendOptions {
    std::optional<v2::gateway::GatewayServiceBridge::BackendConfig> login_backend_config;
    std::optional<v2::gateway::GatewayServiceBridge::BackendConfig> room_backend_config;
    std::optional<v2::gateway::GatewayServiceBridge::BackendConfig> battle_backend_config;
    std::optional<v2::gateway::GatewayServiceBridge::BackendConfig> matchmaking_backend_config;
    std::optional<v2::gateway::GatewayServiceBridge::BackendConfig> leaderboard_backend_config;
  };

  using SecurityOptions = GatewayGrpcServer::SecurityOptions;

  struct ObservabilityOptions {
    std::string otlp_export_endpoint;
    std::string service_name = "boost-gateway-grpc";
    std::size_t max_batch_size = 256;
    std::chrono::milliseconds export_interval{5000};
  };

  /// Construct a gRPC gateway adapter.
  /// @param port  gRPC server port (default 50051).
  explicit GrpcGatewayAdapter(std::uint16_t port = 50051,
                              BackendOptions backend_options = {},
                              SecurityOptions security_options = {})
      : GrpcGatewayAdapter(port,
                           std::move(backend_options),
                           std::move(security_options),
                           ObservabilityOptions{}) {}

  explicit GrpcGatewayAdapter(std::uint16_t port,
                              BackendOptions backend_options,
                              SecurityOptions security_options,
                              ObservabilityOptions observability_options)
      : configured_port_(port),
        port_(port),
        backend_options_(std::move(backend_options)),
        security_options_(std::move(security_options)),
        observability_options_(std::move(observability_options)) {}

  ~GrpcGatewayAdapter() {
    stop();
  }

  // Non-copyable, non-movable.
  GrpcGatewayAdapter(const GrpcGatewayAdapter&) = delete;
  GrpcGatewayAdapter& operator=(const GrpcGatewayAdapter&) = delete;

  /// Start the gRPC server and a background polling thread.
  bool start() {
    if (running_.load(std::memory_order_relaxed)) {
      SPDLOG_WARN("GrpcGatewayAdapter: already running");
      return false;
    }

    SPDLOG_INFO("GrpcGatewayAdapter: starting gRPC gateway on port {}", port_);

    backend_metrics_ = std::make_shared<v2::gateway::BackendMetrics>();
    bridge_ = std::make_unique<v2::gateway::GatewayServiceBridge>(
        backend_options_.login_backend_config,
        backend_options_.room_backend_config,
        backend_options_.battle_backend_config,
        backend_options_.matchmaking_backend_config,
        backend_options_.leaderboard_backend_config,
        backend_metrics_);
    if (!observability_options_.otlp_export_endpoint.empty()) {
      auto exporter = std::make_shared<v3::tracing::OtlpExporter>(
          v3::tracing::OtlpExporter::Config{
              .service_name = observability_options_.service_name,
              .export_endpoint = observability_options_.otlp_export_endpoint,
              .max_batch_size = observability_options_.max_batch_size,
              .export_interval = observability_options_.export_interval,
          });
      bridge_->set_otel_exporter(exporter);
      SPDLOG_INFO(
          "GrpcGatewayAdapter: OTLP export enabled → {}",
          observability_options_.otlp_export_endpoint);
    }

    // Create and start the server
    grpc_server_ = std::make_unique<GatewayGrpcServer>(
        configured_port_,
        [this](const std::string& user_id,
               const std::string& token,
               std::string& out_error) -> bool {
          if (!bridge_) {
            out_error = "grpc_bridge_not_configured";
            return false;
          }
          auto result = bridge_->route(
              v2::service::ServiceId::kLogin,
              "login_request",
              nlohmann::json{
                  {"user_id", user_id},
                  {"token", token},
                  {"display_name", user_id},
              }.dump(),
              user_id);
          if (!result.success) {
            out_error = result.response_payload.empty() ? "login_request_failed" : result.response_payload;
            return false;
          }
          return true;
        },
        [this](const std::string& user_id, const std::string& /*session_id*/) {
          if (!bridge_) {
            return;
          }
          (void)bridge_->route(
              v2::service::ServiceId::kLogin,
              "session_close",
              nlohmann::json{{"user_id", user_id}}.dump(),
              user_id);
        },
        [this](const std::string& user_id, const std::string& room_id, std::int32_t& out_member_count, std::string& out_error) {
          if (!bridge_) {
            out_error = "grpc_bridge_not_configured";
            return false;
          }
          auto result = bridge_->route(v2::service::ServiceId::kRoom, "room_create",
                                       nlohmann::json{{"user_id", user_id}, {"room_id", room_id}}.dump(), room_id);
          if (!result.success) {
            out_error = bridge_failure(result, "room_create_failed");
            return false;
          }
          auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
          out_member_count = doc.is_discarded() ? 0 : doc.value("member_count", 0);
          return true;
        },
        [this](const std::string& user_id, const std::string& room_id, std::int32_t& out_member_count, std::string& out_error) {
          if (!bridge_) {
            out_error = "grpc_bridge_not_configured";
            return false;
          }
          auto result = bridge_->route(v2::service::ServiceId::kRoom, "room_join",
                                       nlohmann::json{{"user_id", user_id}, {"room_id", room_id}}.dump(), room_id);
          if (!result.success) {
            out_error = bridge_failure(result, "room_join_failed");
            return false;
          }
          auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
          out_member_count = doc.is_discarded() ? 0 : doc.value("member_count", 0);
          return true;
        },
        [this](const std::string& user_id, const std::string& room_id, bool& out_was_owner, std::string& out_new_owner_id, std::string& out_error) {
          if (!bridge_) {
            out_error = "grpc_bridge_not_configured";
            return false;
          }
          auto result = bridge_->route(v2::service::ServiceId::kRoom, "room_leave",
                                       nlohmann::json{{"user_id", user_id}, {"room_id", room_id}}.dump(), room_id);
          if (!result.success) {
            out_error = bridge_failure(result, "room_leave_failed");
            return false;
          }
          auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
          out_was_owner = !doc.is_discarded() && doc.value("was_owner", false);
          out_new_owner_id = doc.is_discarded() ? std::string{} : doc.value("new_owner_id", std::string{});
          return true;
        },
        [this](const std::string& user_id, const std::string& room_id, bool ready, bool& out_all_ready, std::string& out_error) {
          if (!bridge_) {
            out_error = "grpc_bridge_not_configured";
            return false;
          }
          auto result = bridge_->route(v2::service::ServiceId::kRoom, "room_ready",
                                       nlohmann::json{{"user_id", user_id}, {"room_id", room_id}, {"ready", ready}}.dump(), room_id);
          if (!result.success) {
            out_error = bridge_failure(result, "room_ready_failed");
            return false;
          }
          auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
          out_all_ready = !doc.is_discarded() && doc.value("all_ready", false);
          return true;
        },
        [this](const std::string& user_id, std::int64_t mmr, const std::string& mode, bool& out_queued, std::string& out_error) {
          if (!bridge_) {
            out_error = "grpc_bridge_not_configured";
            return false;
          }
          auto result = bridge_->route(v2::service::ServiceId::kMatchmaking, "match_join",
                                       nlohmann::json{{"user_id", user_id}, {"mmr", mmr}, {"mode", mode}}.dump(), user_id);
          if (!result.success) {
            out_error = bridge_failure(result, "match_join_failed");
            return false;
          }
          auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
          out_queued = !doc.is_discarded() && doc.value("queued", false);
          return true;
        },
        [this](const std::string& user_id, const std::string& mode, bool& out_left, std::string& out_error) {
          if (!bridge_) {
            out_error = "grpc_bridge_not_configured";
            return false;
          }
          auto result = bridge_->route(v2::service::ServiceId::kMatchmaking, "match_leave",
                                       nlohmann::json{{"user_id", user_id}, {"mode", mode}}.dump(), user_id);
          if (!result.success) {
            out_error = bridge_failure(result, "match_leave_failed");
            return false;
          }
          auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
          out_left = !doc.is_discarded() && doc.value("left", false);
          return true;
        },
        [this](const std::string& user_id, const std::string& mode, bool& out_matched, std::string& out_match_id, std::int64_t& out_avg_mmr, std::int32_t& out_queue_size, std::string& out_error) {
          if (!bridge_) {
            out_error = "grpc_bridge_not_configured";
            return false;
          }
          auto result = bridge_->route(v2::service::ServiceId::kMatchmaking, "match_status",
                                       nlohmann::json{{"user_id", user_id}, {"mode", mode}}.dump(), user_id);
          if (!result.success) {
            out_error = bridge_failure(result, "match_status_failed");
            return false;
          }
          auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
          if (doc.is_discarded()) {
            out_error = "match_status_invalid_json";
            return false;
          }
          out_matched = doc.value("matched", false);
          out_match_id = doc.value("match_id", std::string{});
          out_avg_mmr = doc.value("avg_mmr", std::int64_t{0});
          out_queue_size = static_cast<std::int32_t>(doc.value("queue_size", 0));
          return true;
        },
        [this](const std::string& user_id, const std::string& display_name, std::int64_t score, std::int64_t& out_rank, std::string& out_error) {
          if (!bridge_) {
            out_error = "grpc_bridge_not_configured";
            return false;
          }
          auto result = bridge_->route(v2::service::ServiceId::kLeaderboard, "leaderboard_submit",
                                       nlohmann::json{{"user_id", user_id}, {"display_name", display_name}, {"score", score}}.dump(), user_id);
          if (!result.success) {
            out_error = bridge_failure(result, "leaderboard_submit_failed");
            return false;
          }
          auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
          out_rank = doc.is_discarded() ? 0 : doc.value("rank", std::int64_t{0});
          return true;
        },
        [this](std::int32_t k, std::vector<boost::gateway::v3::LeaderboardEntry>& out_entries, std::string& out_error) {
          if (!bridge_) {
            out_error = "grpc_bridge_not_configured";
            return false;
          }
          auto result = bridge_->route(v2::service::ServiceId::kLeaderboard, "leaderboard_top",
                                       nlohmann::json{{"k", k}}.dump());
          if (!result.success) {
            out_error = bridge_failure(result, "leaderboard_top_failed");
            return false;
          }
          auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
          if (doc.is_discarded() || !doc.contains("entries") || !doc["entries"].is_array()) {
            out_error = "leaderboard_top_invalid_json";
            return false;
          }
          for (const auto& entry_doc : doc["entries"]) {
            boost::gateway::v3::LeaderboardEntry entry;
            entry.set_rank(entry_doc.value("rank", std::int64_t{0}));
            entry.set_user_id(entry_doc.value("user_id", std::string{}));
            entry.set_display_name(entry_doc.value("display_name", std::string{}));
            entry.set_score(entry_doc.value("score", std::int64_t{0}));
            out_entries.push_back(std::move(entry));
          }
          return true;
        },
        [this](const std::string& user_id, std::int64_t& out_rank, std::int64_t& out_score, std::string& out_error) {
          if (!bridge_) {
            out_error = "grpc_bridge_not_configured";
            return false;
          }
          auto result = bridge_->route(v2::service::ServiceId::kLeaderboard, "leaderboard_rank",
                                       nlohmann::json{{"user_id", user_id}}.dump(), user_id);
          if (!result.success) {
            out_error = bridge_failure(result, "leaderboard_rank_failed");
            return false;
          }
          auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
          if (doc.is_discarded()) {
            out_error = "leaderboard_rank_invalid_json";
            return false;
          }
          out_rank = doc.value("rank", std::int64_t{0});
          out_score = doc.value("score", std::int64_t{0});
          return true;
        },
        [this](const std::string& battle_id, const std::string& room_id, const std::vector<std::string>& player_ids, std::uint32_t max_frames, std::string& out_error) {
          if (!bridge_) {
            out_error = "grpc_bridge_not_configured";
            return false;
          }
          auto result = bridge_->route(v2::service::ServiceId::kBattle, "battle_create",
                                       nlohmann::json{{"battle_id", battle_id}, {"room_id", room_id}, {"player_ids", player_ids}, {"max_frames", max_frames}}.dump(), room_id);
          if (!result.success) {
            out_error = bridge_failure(result, "battle_create_failed");
            return false;
          }
          return true;
        },
        [this](const std::string& user_id, const std::string& battle_id, const std::string& input_data, std::uint32_t submitted_frame, std::uint64_t& out_input_seq, std::uint32_t& out_frame_number, std::string& out_error) {
          if (!bridge_) {
            out_error = "grpc_bridge_not_configured";
            return false;
          }
          auto result = bridge_->route(v2::service::ServiceId::kBattle, "battle_input",
                                       nlohmann::json{{"user_id", user_id}, {"battle_id", battle_id}, {"input_data", input_data}, {"submitted_frame", submitted_frame}}.dump(), battle_id);
          if (!result.success) {
            out_error = bridge_failure(result, "battle_input_failed");
            return false;
          }
          auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
          if (doc.is_discarded()) {
            out_error = "battle_input_invalid_json";
            return false;
          }
          out_input_seq = doc.value("input_seq", std::uint64_t{0});
          out_frame_number = doc.value("frame_number", std::uint32_t{0});
          return true;
        },
        [this](const std::string& battle_id, std::uint32_t& out_frame_number, std::string& out_error) {
          if (!bridge_) {
            out_error = "grpc_bridge_not_configured";
            return false;
          }
          auto result = bridge_->route(v2::service::ServiceId::kBattle, "battle_state",
                                       nlohmann::json{{"battle_id", battle_id}}.dump(), battle_id);
          if (!result.success) {
            out_error = bridge_failure(result, "battle_state_failed");
            return false;
          }
          auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
          if (doc.is_discarded()) {
            out_error = "battle_state_invalid_json";
            return false;
          }
          out_frame_number = doc.value("frame_number", std::uint32_t{0});
          return true;
        },
        [this](const std::string& user_id, const std::string& battle_id, const std::string& reason, std::uint32_t& out_total_frames, std::string& out_error) {
          if (!bridge_) {
            out_error = "grpc_bridge_not_configured";
            return false;
          }
          auto result = bridge_->route(v2::service::ServiceId::kBattle, "battle_finish",
                                       nlohmann::json{{"user_id", user_id}, {"battle_id", battle_id}, {"reason", reason}}.dump(), battle_id);
          if (!result.success) {
            out_error = bridge_failure(result, "battle_finish_failed");
            return false;
          }
          auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
          if (doc.is_discarded()) {
            out_error = "battle_finish_invalid_json";
            return false;
          }
          out_total_frames = doc.value("total_frames", std::uint32_t{0});
          return true;
        }, security_options_);

    if (!grpc_server_->start()) {
      SPDLOG_ERROR("GrpcGatewayAdapter: failed to start gRPC server");
      grpc_server_.reset();
      return false;
    }
    port_ = grpc_server_->port();

    // Seed the completion queue with initial CallData instances
    grpc_server_->seed_completion_queue();

    running_.store(true, std::memory_order_release);

    // Start background polling thread for the completion queue.
    poll_thread_ = std::thread([this]() {
      SPDLOG_DEBUG("GrpcGatewayAdapter: poll thread started");
      auto* cq = grpc_server_->completion_queue();
      if (!cq) {
        SPDLOG_ERROR("GrpcGatewayAdapter: no completion queue available");
        return;
      }

      void* tag = nullptr;
      bool ok = false;

      while (cq->Next(&tag, &ok)) {
        auto* completion_tag = static_cast<GatewayGrpcServer::CompletionTag*>(tag);
        completion_tag->proceed(ok);
      }

      SPDLOG_DEBUG("GrpcGatewayAdapter: poll thread exiting");
    });

    SPDLOG_INFO("GrpcGatewayAdapter: gRPC gateway started on port {}", port_);
    return true;
  }

  /// Gracefully stop the gRPC server.
  void stop() {
    if (!running_.exchange(false, std::memory_order_acq_rel)) {
      return;
    }

    SPDLOG_INFO("GrpcGatewayAdapter: stopping gRPC gateway");

    if (grpc_server_) {
      grpc_server_->shutdown();
    }

    auto* cq = grpc_server_ ? grpc_server_->completion_queue() : nullptr;
    if (cq) {
      cq->Shutdown();
    }

    if (poll_thread_.joinable()) {
      poll_thread_.join();
    }

    if (bridge_) {
      if (auto exporter = bridge_->get_otel_exporter()) {
        (void)exporter->flush();
      }
    }

    grpc_server_.reset();
    bridge_.reset();
    backend_metrics_.reset();
    SPDLOG_INFO("GrpcGatewayAdapter: gRPC gateway stopped");
  }

  /// Get the local port.
  std::uint16_t port() const noexcept { return port_; }

  /// Whether the adapter is currently running.
  bool is_running() const noexcept {
    return running_.load(std::memory_order_relaxed);
  }

  /// Number of active sessions tracked by the gRPC server.
  std::uint32_t active_sessions() const noexcept {
    return grpc_server_ ? grpc_server_->active_sessions() : 0;
  }

  [[nodiscard]] v2::gateway::BackendMetricsSnapshot backend_metrics_snapshot(
      v2::service::ServiceId service) const {
    return backend_metrics_ ? backend_metrics_->snapshot(service)
                            : v2::gateway::BackendMetricsSnapshot{};
  }

  [[nodiscard]] GatewayGrpcServer::BattleStateStreamMetricsSnapshot
  battle_state_stream_metrics() const noexcept {
    return grpc_server_ ? grpc_server_->battle_state_stream_metrics()
                        : GatewayGrpcServer::BattleStateStreamMetricsSnapshot{};
  }

 private:
  std::uint16_t configured_port_;
  std::uint16_t port_;
  BackendOptions backend_options_;
  SecurityOptions security_options_;
  ObservabilityOptions observability_options_;
  std::shared_ptr<v2::gateway::BackendMetrics> backend_metrics_;
  std::unique_ptr<GatewayGrpcServer> grpc_server_;
  std::unique_ptr<v2::gateway::GatewayServiceBridge> bridge_;
  std::thread poll_thread_;
  std::atomic<bool> running_{false};
};

}  // namespace v2::grpc

#endif  // BOOST_BUILD_GRPC
