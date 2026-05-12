#pragma once

#include "v2/service/service_id.h"

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace v2::service {

enum class MessageKind : std::uint8_t {
    kRequest = 0,
    kResponse = 1,
    kPush = 2,
    kError = 3,
};

[[nodiscard]] constexpr const char* to_string(MessageKind kind) {
    switch (kind) {
        case MessageKind::kRequest:  return "request";
        case MessageKind::kResponse: return "response";
        case MessageKind::kPush:     return "push";
        case MessageKind::kError:    return "error";
    }
    return "unknown";
}

struct BackendEnvelope {
    std::uint64_t correlation_id = 0;
    ServiceId source_service = ServiceId::kGateway;
    ServiceId target_service = ServiceId::kGateway;
    MessageKind kind = MessageKind::kRequest;
    std::uint32_t timeout_ms = 0;
    std::int32_t error_code = 0;
    std::string payload;
    std::string message_type;
    // v2.2.0: W3C trace context for distributed tracing
    std::uint64_t trace_id = 0;
    std::uint64_t span_id = 0;
};

[[nodiscard]] std::string to_json(const BackendEnvelope& envelope);
[[nodiscard]] std::optional<BackendEnvelope> from_json(std::string_view json);

[[nodiscard]] bool is_valid(const BackendEnvelope& envelope);

[[nodiscard]] std::uint64_t generate_correlation_id();

}  // namespace v2::service
