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
        case ServiceErrorCode::kCircuitOpen:    return "circuit_open";
    }
    return "unknown";
}

}  // namespace v2::service
