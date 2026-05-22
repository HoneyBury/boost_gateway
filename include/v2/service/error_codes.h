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

    // Identity / Registration (-1100 .. -1199)
    kUserAlreadyExists = -1100,
    kIllegalUsername = -1101,
    kWeakCredential = -1102,
    kAccountDisabled = -1103,
    kStorageUnavailable = -1104,

    // Room / Lobby (-1200 .. -1299)
    kRoomNotFound = -1200,
    kRoomFull = -1201,
    kRoomInInstance = -1202,
    kRoomClosed = -1203,
    kNotRoomOwner = -1204,
    kNotRoomMember = -1205,
};

[[nodiscard]] std::int32_t to_client_error(ServiceErrorCode code);

[[nodiscard]] const char* to_string(ServiceErrorCode code);

}  // namespace v2::service
