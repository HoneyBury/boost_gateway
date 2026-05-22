#include "v2/service/error_codes.h"

namespace v2::service {

std::int32_t to_client_error(ServiceErrorCode code) {
    switch (code) {
        case ServiceErrorCode::kOk:
            return 0;
        case ServiceErrorCode::kTimeout:
            return -2001;  // gateway timeout
        case ServiceErrorCode::kUnavailable:
            return -2002;  // service unavailable
        case ServiceErrorCode::kRejected:
            return -2003;  // request rejected
        case ServiceErrorCode::kInvalidRequest:
            return -2004;  // bad request
        case ServiceErrorCode::kInternalError:
            return -2005;  // internal server error
        case ServiceErrorCode::kNotImplemented:
            return -2006;  // not implemented
        case ServiceErrorCode::kCircuitOpen:
            return -2007;  // circuit breaker open
        case ServiceErrorCode::kUserAlreadyExists:
            return -2100;
        case ServiceErrorCode::kIllegalUsername:
            return -2101;
        case ServiceErrorCode::kWeakCredential:
            return -2102;
        case ServiceErrorCode::kAccountDisabled:
            return -2103;
        case ServiceErrorCode::kStorageUnavailable:
            return -2104;
        case ServiceErrorCode::kRoomNotFound:
            return -2200;
        case ServiceErrorCode::kRoomFull:
            return -2201;
        case ServiceErrorCode::kRoomInInstance:
            return -2202;
        case ServiceErrorCode::kRoomClosed:
            return -2203;
        case ServiceErrorCode::kNotRoomOwner:
            return -2204;
        case ServiceErrorCode::kNotRoomMember:
            return -2205;
    }
    return -2005;  // default to internal error
}

const char* to_string(ServiceErrorCode code) {
    switch (code) {
        case ServiceErrorCode::kOk:              return "ok";
        case ServiceErrorCode::kTimeout:         return "timeout";
        case ServiceErrorCode::kUnavailable:     return "unavailable";
        case ServiceErrorCode::kRejected:        return "rejected";
        case ServiceErrorCode::kInvalidRequest:  return "invalid_request";
        case ServiceErrorCode::kInternalError:   return "internal_error";
        case ServiceErrorCode::kNotImplemented:  return "not_implemented";
        case ServiceErrorCode::kCircuitOpen:       return "circuit_open";
        case ServiceErrorCode::kUserAlreadyExists: return "user_already_exists";
        case ServiceErrorCode::kIllegalUsername:   return "illegal_username";
        case ServiceErrorCode::kWeakCredential:    return "weak_credential";
        case ServiceErrorCode::kAccountDisabled:   return "account_disabled";
        case ServiceErrorCode::kStorageUnavailable: return "storage_unavailable";
        case ServiceErrorCode::kRoomNotFound:      return "room_not_found";
        case ServiceErrorCode::kRoomFull:          return "room_full";
        case ServiceErrorCode::kRoomInInstance:    return "room_in_instance";
        case ServiceErrorCode::kRoomClosed:        return "room_closed";
        case ServiceErrorCode::kNotRoomOwner:      return "not_room_owner";
        case ServiceErrorCode::kNotRoomMember:     return "not_room_member";
    }
    return "unknown";
}

}  // namespace v2::service
