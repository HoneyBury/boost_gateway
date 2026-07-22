#include <nlohmann/json.hpp>
#include <openssl/bn.h>
#include <openssl/core_names.h>
#include <openssl/evp.h>
#include <openssl/pem.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <memory>
#include <stdexcept>
#include <string>
#include <system_error>
#include <unordered_map>
#include <utility>
#include <vector>

#include "v2/auth/jwt_validator.h"
#include "v2/login/login_backend_service.h"

namespace {

namespace fs = std::filesystem;
using Json = nlohmann::json;
using Clock = v2::auth::JwksKeyResolver::Clock;

struct Arguments {
    std::string jwks_uri;
    std::string allowed_host;
    fs::path old_public_key;
    fs::path old_private_key;
    fs::path new_public_key;
    fs::path new_private_key;
    fs::path state_file;
    fs::path outage_file;
    fs::path redirect_file;
    fs::path summary_path;
    std::string issuer;
    std::string audience;
};

std::string required_value(const std::unordered_map<std::string, std::string>& values,
                           const std::string& name) {
    const auto found = values.find(name);
    if (found == values.end() || found->second.empty()) {
        throw std::invalid_argument("missing required argument " + name);
    }
    return found->second;
}

Arguments parse_arguments(int argc, char** argv) {
    std::unordered_map<std::string, std::string> values;
    for (int index = 1; index < argc; index += 2) {
        if (index + 1 >= argc || std::string(argv[index]).rfind("--", 0) != 0) {
            throw std::invalid_argument("arguments must use --name value pairs");
        }
        values.emplace(argv[index], argv[index + 1]);
    }
    return {
        .jwks_uri = required_value(values, "--jwks-uri"),
        .allowed_host = required_value(values, "--allowed-host"),
        .old_public_key = required_value(values, "--old-public-key"),
        .old_private_key = required_value(values, "--old-private-key"),
        .new_public_key = required_value(values, "--new-public-key"),
        .new_private_key = required_value(values, "--new-private-key"),
        .state_file = required_value(values, "--state-file"),
        .outage_file = required_value(values, "--outage-file"),
        .redirect_file = required_value(values, "--redirect-file"),
        .summary_path = required_value(values, "--summary-path"),
        .issuer = required_value(values, "--issuer"),
        .audience = required_value(values, "--audience"),
    };
}

std::string read_text(const fs::path& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw std::runtime_error("unable to read required key file");
    }
    return {std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>()};
}

void write_text_atomic(const fs::path& path, const std::string& content) {
    fs::create_directories(path.parent_path());
    auto temporary = path;
    temporary += ".tmp";
    {
        std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
        if (!stream) {
            throw std::runtime_error("unable to write JWKS fixture state");
        }
        stream << content;
    }
    std::error_code error;
    fs::remove(path, error);
    fs::rename(temporary, path);
}

Json jwk_from_public_key(const std::string& kid, const std::string& public_key_pem) {
    using BioPtr = std::unique_ptr<BIO, decltype(&BIO_free)>;
    using KeyPtr = std::unique_ptr<EVP_PKEY, decltype(&EVP_PKEY_free)>;
    using BnPtr = std::unique_ptr<BIGNUM, decltype(&BN_free)>;

    BioPtr bio(BIO_new_mem_buf(public_key_pem.data(),
                               static_cast<int>(public_key_pem.size())),
               BIO_free);
    KeyPtr key(bio ? PEM_read_bio_PUBKEY(bio.get(), nullptr, nullptr, nullptr) : nullptr,
               EVP_PKEY_free);
    BIGNUM* raw_modulus = nullptr;
    BIGNUM* raw_exponent = nullptr;
    if (!key ||
        EVP_PKEY_get_bn_param(key.get(), OSSL_PKEY_PARAM_RSA_N, &raw_modulus) != 1 ||
        EVP_PKEY_get_bn_param(key.get(), OSSL_PKEY_PARAM_RSA_E, &raw_exponent) != 1) {
        BN_free(raw_modulus);
        BN_free(raw_exponent);
        throw std::runtime_error("unable to derive RSA JWK fixture");
    }
    BnPtr modulus(raw_modulus, BN_free);
    BnPtr exponent(raw_exponent, BN_free);
    std::string modulus_bytes(static_cast<std::size_t>(BN_num_bytes(modulus.get())), '\0');
    std::string exponent_bytes(static_cast<std::size_t>(BN_num_bytes(exponent.get())), '\0');
    BN_bn2bin(modulus.get(), reinterpret_cast<unsigned char*>(modulus_bytes.data()));
    BN_bn2bin(exponent.get(), reinterpret_cast<unsigned char*>(exponent_bytes.data()));
    return {
        {"kid", kid},
        {"kty", "RSA"},
        {"alg", "RS256"},
        {"use", "sig"},
        {"key_ops", Json::array({"verify"})},
        {"n", v2::auth::detail::base64url_encode(modulus_bytes)},
        {"e", v2::auth::detail::base64url_encode(exponent_bytes)},
    };
}

std::string jwks_document(const std::vector<Json>& keys) {
    return Json{{"keys", keys}}.dump();
}

Json metrics_json(const v2::auth::JwtKeyResolverMetrics& metrics) {
    return {
        {"snapshot_available", metrics.snapshot_available},
        {"snapshot_stale", metrics.snapshot_stale},
        {"snapshot_age_seconds", metrics.snapshot_age_seconds},
        {"last_success_recorded", metrics.last_success_epoch_seconds > 0},
        {"refresh_attempts", metrics.refresh_attempts},
        {"refresh_failures", metrics.refresh_failures},
        {"unknown_kid_rejections", metrics.unknown_kid_rejections},
        {"key_count", metrics.key_count},
    };
}

v2::auth::JwtValidator signer(const std::string& public_key,
                              const std::string& private_key,
                              const std::string& kid,
                              const std::string& issuer,
                              const std::string& audience) {
    return v2::auth::JwtValidator({
        .secret = {},
        .public_key_pem = public_key,
        .private_key_pem = private_key,
        .key_resolver = {},
        .issuer = issuer,
        .audience = audience,
        .require_expiration = true,
        .signing_kid = kid,
    });
}

}  // namespace

int main(int argc, char** argv) {
    Arguments arguments;
    try {
        arguments = parse_arguments(argc, argv);
    } catch (const std::exception& error) {
        std::cerr << "jwks rotation probe argument error: " << error.what() << '\n';
        return 2;
    }

    std::vector<Json> checks;
    Json phase_metrics = Json::object();
    auto add_check = [&](std::string name, bool passed, std::string detail) {
        checks.push_back({{"name", std::move(name)},
                          {"passed", passed},
                          {"detail", std::move(detail)}});
    };

    try {
        const auto old_public = read_text(arguments.old_public_key);
        const auto old_private = read_text(arguments.old_private_key);
        const auto new_public = read_text(arguments.new_public_key);
        const auto new_private = read_text(arguments.new_private_key);
        const auto old_jwk = jwk_from_public_key("old-2026", old_public);
        const auto new_jwk = jwk_from_public_key("new-2026", new_public);

        std::error_code remove_error;
        fs::remove(arguments.outage_file, remove_error);
        fs::remove(arguments.redirect_file, remove_error);
        write_text_atomic(arguments.state_file, jwks_document({old_jwk}));

        const v2::auth::JwksHttpOptions http{
            .uri = arguments.jwks_uri,
            .allowed_hosts = {arguments.allowed_host},
            .allow_loopback_http = false,
            .connect_timeout = std::chrono::milliseconds(2000),
            .read_timeout = std::chrono::milliseconds(3000),
            .max_response_bytes = 1024U * 1024U,
        };
        auto now = Clock::now();
        auto resolver = std::make_shared<v2::auth::JwksKeyResolver>(
            v2::auth::JwksKeyResolver::Options{
                .fetcher = [http] { return v2::auth::fetch_jwks_document(http); },
                .now = [&now] { return now; },
                .ttl = std::chrono::seconds(300),
                .stale_grace = std::chrono::seconds(600),
                .minimum_refresh_interval = std::chrono::seconds(3600),
            });
        resolver->refresh_now();

        v2::auth::JwtValidator verifier({
            .secret = {},
            .public_key_pem = {},
            .private_key_pem = {},
            .key_resolver = resolver,
            .issuer = arguments.issuer,
            .audience = arguments.audience,
            .require_expiration = true,
            .signing_kid = {},
        });
        const auto epoch = static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::system_clock::now().time_since_epoch())
                .count());
        const v2::auth::JwtPayload payload{
            .sub = "rotation-probe",
            .iss = {},
            .aud = arguments.audience,
            .role = "player",
            .display_name = {},
            .iat = epoch,
            .exp = epoch + 3600,
            .extra = Json::object(),
        };
        const auto old_token = signer(old_public, old_private, "old-2026",
                                      arguments.issuer, arguments.audience)
                                   .generate(payload);
        const auto new_token = signer(new_public, new_private, "new-2026",
                                      arguments.issuer, arguments.audience)
                                   .generate(payload);
        add_check("tokens-generated-in-memory", !old_token.empty() && !new_token.empty(),
                  "two RS256 kid-specific tokens were generated without persistence");

        const auto old_initial = verifier.validate(old_token);
        add_check("old-only:old-token-accepted", old_initial.valid,
                  old_initial.valid ? "accepted" : old_initial.error);
        phase_metrics["old_only"] = metrics_json(resolver->metrics());

        write_text_atomic(arguments.state_file, jwks_document({old_jwk, new_jwk}));
        resolver->refresh_now();
        const auto old_overlap = verifier.validate(old_token);
        const auto new_overlap = verifier.validate(new_token);
        add_check("overlap:old-token-accepted", old_overlap.valid,
                  old_overlap.valid ? "accepted" : old_overlap.error);
        add_check("overlap:new-token-accepted", new_overlap.valid,
                  new_overlap.valid ? "accepted" : new_overlap.error);
        phase_metrics["overlap"] = metrics_json(resolver->metrics());

        write_text_atomic(arguments.state_file, jwks_document({new_jwk}));
        resolver->refresh_now();
        const auto new_only = verifier.validate(new_token);
        const auto retired_old = verifier.validate(old_token);
        add_check("new-only:new-token-accepted", new_only.valid,
                  new_only.valid ? "accepted" : new_only.error);
        add_check("new-only:old-token-rejected",
                  !retired_old.valid && retired_old.error == "unknown_kid",
                  retired_old.error);

        auto wrong_issuer_payload = payload;
        const auto wrong_issuer_token = signer(new_public, new_private, "new-2026",
                                               arguments.issuer + "/wrong",
                                               arguments.audience)
                                            .generate(wrong_issuer_payload);
        const auto wrong_issuer = verifier.validate(wrong_issuer_token);
        add_check("claims:issuer-remains-enforced",
                  !wrong_issuer.valid && wrong_issuer.error == "invalid_issuer",
                  wrong_issuer.error);

        auto wrong_audience_payload = payload;
        wrong_audience_payload.aud = arguments.audience + "-wrong";
        const auto wrong_audience_token = signer(new_public, new_private, "new-2026",
                                                 arguments.issuer,
                                                 arguments.audience)
                                              .generate(wrong_audience_payload);
        const auto wrong_audience = verifier.validate(wrong_audience_token);
        add_check("claims:audience-remains-enforced",
                  !wrong_audience.valid && wrong_audience.error == "invalid_audience",
                  wrong_audience.error);
        phase_metrics["new_only"] = metrics_json(resolver->metrics());

        write_text_atomic(arguments.outage_file, "outage\n");
        bool refresh_failed = false;
        try {
            resolver->refresh_now();
        } catch (const std::exception&) {
            refresh_failed = true;
        }
        add_check("outage:refresh-fails", refresh_failed,
                  refresh_failed ? "HTTPS endpoint returned controlled outage"
                                 : "refresh unexpectedly succeeded");

        now += std::chrono::seconds(301);
        const auto stale_result = verifier.validate(new_token);
        const auto stale_metrics = resolver->metrics();
        add_check("outage:stale-grace-accepts-current-key",
                  stale_result.valid && stale_metrics.snapshot_stale,
                  stale_result.valid ? "accepted from bounded stale snapshot"
                                     : stale_result.error);
        phase_metrics["stale_grace"] = metrics_json(stale_metrics);

        now += std::chrono::seconds(600);
        const auto expired_result = verifier.validate(new_token);
        add_check("outage:expired-snapshot-fails-closed",
                  !expired_result.valid && expired_result.error == "jwks_stale_expired",
                  expired_result.error);
        phase_metrics["stale_expired"] = metrics_json(resolver->metrics());

        bool startup_failed = false;
        try {
            v2::login::LoginBackendService service(v2::login::LoginBackendOptions{
                .port = 0,
                .production_auth_required = true,
                .jwt_secret = {},
                .jwt_public_key_pem = {},
                .jwt_private_key_pem = {},
                .jwt_key_ring = {},
                .jwks_http = http,
                .jwks_ttl = std::chrono::seconds(300),
                .jwks_stale_grace = std::chrono::seconds(600),
                .jwks_minimum_refresh_interval = std::chrono::seconds(30),
                .jwks_max_keys = 32,
                .jwt_issuer = arguments.issuer,
                .jwt_audience = arguments.audience,
                .tls_config = std::nullopt,
            });
        } catch (const std::exception&) {
            startup_failed = true;
        }
        add_check("outage:no-snapshot-startup-fails", startup_failed,
                  startup_failed ? "production Login Backend rejected startup"
                                 : "startup unexpectedly succeeded");

        auto static_resolver = std::make_shared<v2::auth::StaticJwtKeyResolver>(
            std::unordered_map<std::string, std::string>{{"old-2026", old_public},
                                                         {"new-2026", new_public}});
        v2::auth::JwtValidator rollback_verifier({
            .secret = {},
            .public_key_pem = {},
            .private_key_pem = {},
            .key_resolver = static_resolver,
            .issuer = arguments.issuer,
            .audience = arguments.audience,
            .require_expiration = true,
            .signing_kid = {},
        });
        const auto rollback_result = rollback_verifier.validate(new_token);
        add_check("rollback:static-key-ring-remains-valid", rollback_result.valid,
                  rollback_result.valid ? "accepted without network fallback"
                                        : rollback_result.error);

        auto insecure_http = http;
        insecure_http.uri.replace(0, 5, "http");
        bool http_rejected = false;
        try {
            static_cast<void>(v2::auth::fetch_jwks_document(insecure_http));
        } catch (const std::invalid_argument&) {
            http_rejected = true;
        }
        add_check("policy:production-http-rejected", http_rejected,
                  http_rejected ? "rejected before network access"
                                : "unsafe HTTP was accepted");

        auto wrong_host = http;
        wrong_host.allowed_hosts = {"not-localhost.invalid"};
        bool host_rejected = false;
        try {
            static_cast<void>(v2::auth::fetch_jwks_document(wrong_host));
        } catch (const std::invalid_argument&) {
            host_rejected = true;
        }
        add_check("policy:non-allowlisted-host-rejected", host_rejected,
                  host_rejected ? "rejected before network access"
                                : "non-allowlisted host was accepted");

        fs::remove(arguments.outage_file, remove_error);
        write_text_atomic(arguments.redirect_file, "redirect\n");
        bool redirect_rejected = false;
        try {
            static_cast<void>(v2::auth::fetch_jwks_document(http));
        } catch (const std::runtime_error&) {
            redirect_rejected = true;
        }
        add_check("policy:https-redirect-rejected", redirect_rejected,
                  redirect_rejected ? "302 response was not followed"
                                    : "redirect was unexpectedly accepted");
        fs::remove(arguments.redirect_file, remove_error);
    } catch (const std::exception& error) {
        add_check("probe:fatal", false, error.what());
    }

    const auto failed = static_cast<std::size_t>(
        std::count_if(checks.begin(), checks.end(),
                      [](const Json& check) { return !check.value("passed", false); }));
    const Json summary{
        {"probe_summary_version", 1},
        {"overall_pass", failed == 0},
        {"passed", failed == 0},
        {"total_checks", checks.size()},
        {"failed_checks", failed},
        {"checks", checks},
        {"phase_metrics", phase_metrics},
    };
    try {
        fs::create_directories(arguments.summary_path.parent_path());
        std::ofstream output(arguments.summary_path, std::ios::trunc);
        output << summary.dump(2) << '\n';
        if (!output) {
            throw std::runtime_error("unable to write probe summary");
        }
    } catch (const std::exception& error) {
        std::cerr << "jwks rotation probe summary error: " << error.what() << '\n';
        return 2;
    }
    std::cout << "JWKS rotation probe: " << (failed == 0 ? "PASS" : "FAIL")
              << " (" << checks.size() - failed << '/' << checks.size() << " checks)\n";
    return failed == 0 ? 0 : 1;
}
