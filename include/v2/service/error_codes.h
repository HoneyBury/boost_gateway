#pragma once

#include <cstdint>

namespace v2::service {

enum class ServiceErrorCode : std::int32_t {
    kOk = 0,
    kTimeout = -1001,
    kUnavailable = -1002,
    kRejected = -1003,
    kInvalidRequest = -1004,
    kCircuitOpen = -1007,
    kInternalError = -1005,
    kNotImplemented = -1006,
};

[[nodiscard]] std::int32_t to_client_error(ServiceErrorCode code);

[[nodiscard]] const char* to_string(ServiceErrorCode code);

}  // namespace v2::service
