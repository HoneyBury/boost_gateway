#pragma once
// BoostGateway SDK: error codes.

#include <cstdint>

namespace boost_gateway {
namespace sdk {

enum class SdkError : std::int32_t {
    kOk = 0,
    kNotConnected = -1,
    kTimeout = -2,
    kSendFailed = -3,
    kReadFailed = -4,
    kInvalidResponse = -5,
    kMalformedBody = -6,
};

inline const char* to_string(SdkError err) {
    switch (err) {
        case SdkError::kOk: return "ok";
        case SdkError::kNotConnected: return "not_connected";
        case SdkError::kTimeout: return "timeout";
        case SdkError::kSendFailed: return "send_failed";
        case SdkError::kReadFailed: return "read_failed";
        case SdkError::kInvalidResponse: return "invalid_response";
        case SdkError::kMalformedBody: return "malformed_body";
    }
    return "unknown";
}

}  // namespace sdk
}  // namespace boost_gateway
