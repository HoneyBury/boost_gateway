#pragma once

#include <string>

namespace net {

struct TlsConfig {
    bool enabled = false;
    std::string cert_chain_path;
    std::string private_key_path;
    std::string dh_params_path;
};

}  // namespace net
