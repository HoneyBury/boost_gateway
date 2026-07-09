#pragma once
// v3.0.0 D7: TLS/mTLS configuration for inter-service communication.
// Requires OpenSSL. Feature-flagged via "v3_tls_enabled".

#include <chrono>
#include <cstdint>
#include <optional>
#include <string>

namespace v3::cluster {

// ── TLS certificate config ─────────────────────────────────────────────

struct TlsCertificateConfig {
    std::string cert_chain_path;     // PEM certificate chain
    std::string private_key_path;    // PEM private key
    std::string ca_cert_path;        // CA certificate for mTLS verification
    std::optional<std::string> private_key_password = std::nullopt;  // if encrypted
};

// ── TLS peer verification ───────────────────────────────────────────────

enum class TlsVerifyMode : std::uint8_t {
    kNone = 0,       // no verification (client only)
    kServer = 1,     // verify server certificate
    kMutual = 2,     // verify both sides (mTLS)
};

// ── TLS session config ─────────────────────────────────────────────────

struct TlsSessionConfig {
    TlsCertificateConfig cert;
    TlsVerifyMode verify_mode = TlsVerifyMode::kMutual;
    bool enable_session_resumption = true;
    std::chrono::seconds session_timeout{300};  // TLS session cache timeout

    // Cipher preferences
    std::string cipher_list = "ECDHE-ECDSA-AES256-GCM-SHA384:"
                              "ECDHE-RSA-AES256-GCM-SHA384:"
                              "ECDHE-ECDSA-AES128-GCM-SHA256:"
                              "ECDHE-RSA-AES128-GCM-SHA256";

    // Minimum TLS version
    enum class TlsVersion : std::uint8_t { k12 = 0, k13 = 1 };
    TlsVersion min_version = TlsVersion::k12;
};

// ── Connection security policy ─────────────────────────────────────────

struct SecurityPolicy {
    // Require TLS for inter-service communication (v3.0.0 default: true)
    bool require_tls = true;

    // Certificate auto-rotation interval (0 = disabled)
    std::chrono::hours cert_rotation_interval{720};  // 30 days

    // Path to certificate files (supports hot-reload via ConfigWatcher)
    TlsSessionConfig tls_config;

    // Per-service TLS enforcement (allow gradual rollout)
    struct ServiceTlsPolicy {
        bool tls_required = true;
        bool mtls_required = false;  // mTLS for sensitive services
    };
    ServiceTlsPolicy login_policy{.mtls_required = false};
    ServiceTlsPolicy room_policy;          // default: tls required
    ServiceTlsPolicy battle_policy;
    ServiceTlsPolicy match_policy;
    ServiceTlsPolicy leaderboard_policy{.mtls_required = true};  // leaderboard has PII

    [[nodiscard]] const ServiceTlsPolicy* policy_for(
        const std::string& service_name) const {
        if (service_name == "login") return &login_policy;
        if (service_name == "room") return &room_policy;
        if (service_name == "battle") return &battle_policy;
        if (service_name == "match") return &match_policy;
        if (service_name == "leaderboard") return &leaderboard_policy;
        return nullptr;
    }
};

// ── TLS configuration builder (for env/ integration) ───────────────────

inline TlsSessionConfig default_tls_config() {
    return TlsSessionConfig{
        .cert = {
            .cert_chain_path = "certs/server.crt",
            .private_key_path = "certs/server.key",
            .ca_cert_path = "certs/ca.crt",
        },
        .verify_mode = TlsVerifyMode::kMutual,
    };
}

}  // namespace v3::cluster
