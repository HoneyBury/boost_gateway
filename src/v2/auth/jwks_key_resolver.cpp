#include "v2/auth/jwks_key_resolver.h"

#include <boost/asio/connect.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/ssl/context.hpp>
#include <boost/asio/ssl/host_name_verification.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/http.hpp>
#include <boost/beast/ssl.hpp>

#include <nlohmann/json.hpp>

#include <openssl/bio.h>
#include <openssl/bn.h>
#include <openssl/core_names.h>
#include <openssl/evp.h>
#include <openssl/param_build.h>
#include <openssl/pem.h>
#include <openssl/ssl.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <memory>
#include <stdexcept>
#include <string_view>

namespace v2::auth {
namespace {

namespace asio = boost::asio;
namespace beast = boost::beast;
namespace http = beast::http;
namespace ssl = asio::ssl;
using tcp = asio::ip::tcp;
using Json = nlohmann::json;

struct ParsedUri {
    std::string scheme;
    std::string host;
    std::string port;
    std::string target;
};

std::string lowercase(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

ParsedUri parse_uri(const std::string& uri) {
    const auto delimiter = uri.find("://");
    if (delimiter == std::string::npos || uri.find('#') != std::string::npos) {
        throw std::invalid_argument("JWKS URI is malformed");
    }
    ParsedUri parsed;
    parsed.scheme = lowercase(uri.substr(0, delimiter));
    const auto authority_begin = delimiter + 3;
    const auto path_begin = uri.find('/', authority_begin);
    const auto authority = uri.substr(authority_begin, path_begin - authority_begin);
    parsed.target = path_begin == std::string::npos ? "/" : uri.substr(path_begin);
    if (authority.empty() || authority.find('@') != std::string::npos || parsed.target.empty()) {
        throw std::invalid_argument("JWKS URI authority is invalid");
    }

    if (authority.front() == '[') {
        const auto close = authority.find(']');
        if (close == std::string::npos) {
            throw std::invalid_argument("JWKS URI IPv6 host is malformed");
        }
        parsed.host = lowercase(authority.substr(1, close - 1));
        if (close + 1 < authority.size()) {
            if (authority[close + 1] != ':') {
                throw std::invalid_argument("JWKS URI port is malformed");
            }
            parsed.port = authority.substr(close + 2);
        }
    } else {
        const auto colon = authority.rfind(':');
        if (colon != std::string::npos) {
            parsed.host = lowercase(authority.substr(0, colon));
            parsed.port = authority.substr(colon + 1);
        } else {
            parsed.host = lowercase(authority);
        }
    }
    if (parsed.host.empty()) {
        throw std::invalid_argument("JWKS URI host is empty");
    }
    if (parsed.port.empty()) {
        parsed.port = parsed.scheme == "https" ? "443" : "80";
    }
    if (!std::all_of(parsed.port.begin(), parsed.port.end(), [](unsigned char ch) {
            return std::isdigit(ch) != 0;
        })) {
        throw std::invalid_argument("JWKS URI port is invalid");
    }
    return parsed;
}

bool is_loopback_host(const std::string& host) {
    return host == "127.0.0.1" || host == "localhost" || host == "::1";
}

void validate_http_options(const JwksHttpOptions& options, const ParsedUri& uri) {
    if (uri.scheme != "https" &&
        !(uri.scheme == "http" && options.allow_loopback_http && is_loopback_host(uri.host))) {
        throw std::invalid_argument("JWKS URI must use HTTPS");
    }
    const auto allowed = std::any_of(options.allowed_hosts.begin(), options.allowed_hosts.end(),
                                     [&](const std::string& host) {
                                         return lowercase(host) == uri.host;
                                     });
    if (!allowed) {
        throw std::invalid_argument("JWKS URI host is not allowlisted");
    }
    if (options.max_response_bytes == 0 || options.max_response_bytes > 16U * 1024U * 1024U) {
        throw std::invalid_argument("JWKS response limit is invalid");
    }
}

template <typename Stream>
std::string read_response(Stream& stream, const ParsedUri& uri,
                          const JwksHttpOptions& options) {
    http::request<http::empty_body> request{http::verb::get, uri.target, 11};
    request.set(http::field::host, uri.host);
    request.set(http::field::user_agent, "BoostGateway-JWKS/3.6");
    request.set(http::field::accept, "application/json");
    beast::get_lowest_layer(stream).expires_after(options.read_timeout);
    http::write(stream, request);

    beast::flat_buffer buffer;
    http::response_parser<http::string_body> parser;
    parser.body_limit(options.max_response_bytes);
    beast::get_lowest_layer(stream).expires_after(options.read_timeout);
    http::read(stream, buffer, parser);
    const auto response = parser.release();
    if (response.result_int() >= 300 && response.result_int() < 400) {
        throw std::runtime_error("JWKS redirect responses are forbidden");
    }
    if (response.result() != http::status::ok) {
        throw std::runtime_error("JWKS endpoint returned HTTP " +
                                 std::to_string(response.result_int()));
    }
    return response.body();
}

std::vector<unsigned char> decode_base64url(const std::string& input) {
    if (input.empty() || input.find('=') != std::string::npos) {
        throw std::invalid_argument("JWK base64url value is empty or padded");
    }
    std::string encoded = input;
    for (char& ch : encoded) {
        if (ch == '-')
            ch = '+';
        else if (ch == '_')
            ch = '/';
        else if (!std::isalnum(static_cast<unsigned char>(ch)) && ch != '+' && ch != '/')
            throw std::invalid_argument("JWK base64url value is malformed");
    }
    const auto remainder = encoded.size() % 4;
    if (remainder == 1) {
        throw std::invalid_argument("JWK base64url value has invalid length");
    }
    encoded.append((4 - remainder) % 4, '=');
    std::vector<unsigned char> decoded((encoded.size() / 4) * 3);
    const auto count = EVP_DecodeBlock(decoded.data(),
                                       reinterpret_cast<const unsigned char*>(encoded.data()),
                                       static_cast<int>(encoded.size()));
    if (count < 0) {
        throw std::invalid_argument("JWK base64url decode failed");
    }
    auto size = static_cast<std::size_t>(count);
    if (!encoded.empty() && encoded.back() == '=')
        --size;
    if (encoded.size() > 1 && encoded[encoded.size() - 2] == '=')
        --size;
    decoded.resize(size);
    return decoded;
}

std::string rsa_jwk_to_pem(const Json& key, std::size_t max_key_bytes) {
    const auto modulus = decode_base64url(key.at("n").get<std::string>());
    const auto exponent = decode_base64url(key.at("e").get<std::string>());
    if (modulus.empty() || exponent.empty() || modulus.size() > max_key_bytes ||
        exponent.size() > 8) {
        throw std::invalid_argument("JWK RSA material is outside supported bounds");
    }

    using BnPtr = std::unique_ptr<BIGNUM, decltype(&BN_free)>;
    BnPtr n(BN_bin2bn(modulus.data(), static_cast<int>(modulus.size()), nullptr), BN_free);
    BnPtr e(BN_bin2bn(exponent.data(), static_cast<int>(exponent.size()), nullptr), BN_free);
    if (!n || !e || BN_num_bits(n.get()) < 2048 || !BN_is_odd(e.get()) || BN_is_one(e.get())) {
        throw std::invalid_argument("JWK RSA key strength or exponent is invalid");
    }

    using ParamBuildPtr = std::unique_ptr<OSSL_PARAM_BLD, decltype(&OSSL_PARAM_BLD_free)>;
    using ParamPtr = std::unique_ptr<OSSL_PARAM, decltype(&OSSL_PARAM_free)>;
    using CtxPtr = std::unique_ptr<EVP_PKEY_CTX, decltype(&EVP_PKEY_CTX_free)>;
    using KeyPtr = std::unique_ptr<EVP_PKEY, decltype(&EVP_PKEY_free)>;
    ParamBuildPtr builder(OSSL_PARAM_BLD_new(), OSSL_PARAM_BLD_free);
    if (!builder || OSSL_PARAM_BLD_push_BN(builder.get(), OSSL_PKEY_PARAM_RSA_N, n.get()) != 1 ||
        OSSL_PARAM_BLD_push_BN(builder.get(), OSSL_PKEY_PARAM_RSA_E, e.get()) != 1) {
        throw std::runtime_error("failed to construct RSA JWK parameters");
    }
    ParamPtr params(OSSL_PARAM_BLD_to_param(builder.get()), OSSL_PARAM_free);
    CtxPtr context(EVP_PKEY_CTX_new_from_name(nullptr, "RSA", nullptr), EVP_PKEY_CTX_free);
    EVP_PKEY* raw_key = nullptr;
    if (!params || !context || EVP_PKEY_fromdata_init(context.get()) != 1 ||
        EVP_PKEY_fromdata(context.get(), &raw_key, EVP_PKEY_PUBLIC_KEY, params.get()) != 1) {
        throw std::runtime_error("failed to construct RSA public key from JWK");
    }
    KeyPtr public_key(raw_key, EVP_PKEY_free);

    using BioPtr = std::unique_ptr<BIO, decltype(&BIO_free)>;
    BioPtr output(BIO_new(BIO_s_mem()), BIO_free);
    if (!output || PEM_write_bio_PUBKEY(output.get(), public_key.get()) != 1) {
        throw std::runtime_error("failed to encode RSA JWK as PEM");
    }
    char* bytes = nullptr;
    const auto size = BIO_get_mem_data(output.get(), &bytes);
    if (size <= 0 || static_cast<std::size_t>(size) > max_key_bytes) {
        throw std::runtime_error("encoded RSA public key is outside supported bounds");
    }
    return std::string(bytes, static_cast<std::size_t>(size));
}

std::unordered_map<std::string, std::string> parse_jwks(const std::string& document,
                                                        const JwksKeyResolver::Options& options) {
    if (document.empty() || document.size() > options.max_response_bytes) {
        throw std::invalid_argument("JWKS document is empty or oversized");
    }
    const auto root = Json::parse(document);
    if (!root.is_object() || root.size() != 1 || !root.contains("keys") ||
        !root.at("keys").is_array() || root.at("keys").empty() ||
        root.at("keys").size() > options.max_keys) {
        throw std::invalid_argument("JWKS root or key count is invalid");
    }

    std::unordered_map<std::string, std::string> keys;
    for (const auto& key : root.at("keys")) {
        if (!key.is_object() || !key.contains("kid") || !key.at("kid").is_string() ||
            !key.contains("kty") || !key.at("kty").is_string() ||
            !key.contains("n") || !key.at("n").is_string() ||
            !key.contains("e") || !key.at("e").is_string()) {
            throw std::invalid_argument("JWKS key is missing required RSA fields");
        }
        const auto kid = key.at("kid").get<std::string>();
        if (kid.empty() || kid.size() > 256 || key.at("kty").get<std::string>() != "RSA" ||
            (key.contains("alg") && key.at("alg").get<std::string>() != "RS256") ||
            (key.contains("use") && key.at("use").get<std::string>() != "sig") ||
            key.contains("d") || key.contains("p") || key.contains("q")) {
            throw std::invalid_argument("JWKS key metadata is incompatible with RS256 verification");
        }
        if (key.contains("key_ops")) {
            if (!key.at("key_ops").is_array() || key.at("key_ops").size() != 1 ||
                !key.at("key_ops").front().is_string() ||
                key.at("key_ops").front().get<std::string>() != "verify") {
                throw std::invalid_argument("JWKS key_ops must contain only verify");
            }
        }
        auto [_, inserted] = keys.emplace(kid, rsa_jwk_to_pem(key, options.max_key_bytes));
        if (!inserted) {
            throw std::invalid_argument("JWKS contains duplicate kid");
        }
    }
    return keys;
}

} // namespace

StaticJwtKeyResolver::StaticJwtKeyResolver(std::unordered_map<std::string, std::string> keys)
    : keys_(std::move(keys)) {
    if (keys_.empty()) {
        throw std::invalid_argument("static JWT key ring is empty");
    }
    for (const auto& [kid, pem] : keys_) {
        if (kid.empty() || kid.size() > 256 || pem.empty() || pem.size() > 16U * 1024U) {
            throw std::invalid_argument("static JWT key ring entry is invalid");
        }
        BIO* raw_bio = BIO_new_mem_buf(pem.data(), static_cast<int>(pem.size()));
        if (!raw_bio) {
            throw std::invalid_argument("static JWT key ring entry is invalid");
        }
        std::unique_ptr<BIO, decltype(&BIO_free)> bio(raw_bio, BIO_free);
        std::unique_ptr<EVP_PKEY, decltype(&EVP_PKEY_free)> key(
            PEM_read_bio_PUBKEY(bio.get(), nullptr, nullptr, nullptr), EVP_PKEY_free);
        if (!key || EVP_PKEY_base_id(key.get()) != EVP_PKEY_RSA) {
            throw std::invalid_argument("static JWT key ring entry is not an RSA public key");
        }
    }
}

JwtKeyResolution StaticJwtKeyResolver::resolve(const std::string& kid) {
    if (kid.empty())
        return {{}, "missing_kid"};
    const auto found = keys_.find(kid);
    if (found == keys_.end())
        return {{}, "unknown_kid"};
    return {found->second, {}};
}

JwtKeyResolverMetrics StaticJwtKeyResolver::metrics() const {
    return JwtKeyResolverMetrics{
        .snapshot_available = true,
        .snapshot_stale = false,
        .snapshot_age_seconds = 0,
        .key_count = keys_.size(),
    };
}

std::string fetch_jwks_document(const JwksHttpOptions& options) {
    const auto uri = parse_uri(options.uri);
    validate_http_options(options, uri);

    asio::io_context io;
    tcp::resolver resolver(io);
    const auto endpoints = resolver.resolve(uri.host, uri.port);
    if (uri.scheme == "https") {
        ssl::context context(ssl::context::tls_client);
        context.set_default_verify_paths();
        beast::ssl_stream<beast::tcp_stream> stream(io, context);
        if (SSL_set_tlsext_host_name(stream.native_handle(), uri.host.c_str()) != 1) {
            throw std::runtime_error("failed to set JWKS TLS server name");
        }
        stream.set_verify_mode(ssl::verify_peer);
        stream.set_verify_callback(ssl::host_name_verification(uri.host));
        beast::get_lowest_layer(stream).expires_after(options.connect_timeout);
        beast::get_lowest_layer(stream).connect(endpoints);
        beast::get_lowest_layer(stream).expires_after(options.connect_timeout);
        stream.handshake(ssl::stream_base::client);
        auto body = read_response(stream, uri, options);
        beast::error_code ec;
        stream.shutdown(ec);
        return body;
    }

    beast::tcp_stream stream(io);
    stream.expires_after(options.connect_timeout);
    stream.connect(endpoints);
    auto body = read_response(stream, uri, options);
    beast::error_code ec;
    stream.socket().shutdown(tcp::socket::shutdown_both, ec);
    return body;
}

JwksKeyResolver::JwksKeyResolver(Options options) : options_(std::move(options)) {
    if (!options_.fetcher || !options_.now || options_.ttl <= std::chrono::seconds::zero() ||
        options_.stale_grace < std::chrono::seconds::zero() ||
        options_.minimum_refresh_interval < std::chrono::seconds::zero() ||
        options_.max_keys == 0 || options_.max_response_bytes == 0 || options_.max_key_bytes == 0) {
        throw std::invalid_argument("JWKS resolver options are invalid");
    }
    worker_ = std::thread([this] { worker_loop(); });
}

JwksKeyResolver::~JwksKeyResolver() {
    {
        std::lock_guard lock(worker_mutex_);
        stopping_ = true;
    }
    worker_cv_.notify_all();
    if (worker_.joinable())
        worker_.join();
}

std::shared_ptr<const JwksKeyResolver::Snapshot> JwksKeyResolver::fetch_snapshot() {
    const auto document = options_.fetcher();
    auto snapshot = std::make_shared<Snapshot>();
    snapshot->keys = parse_jwks(document, options_);
    snapshot->fetched_at = options_.now();
    return snapshot;
}

void JwksKeyResolver::refresh_now() {
    std::lock_guard refresh_lock(refresh_mutex_);
    {
        std::lock_guard lock(mutex_);
        ++refresh_attempts_;
        last_attempt_ = options_.now();
    }
    try {
        auto next = fetch_snapshot();
        std::lock_guard lock(mutex_);
        snapshot_ = std::move(next);
        last_success_ = snapshot_->fetched_at;
    } catch (...) {
        std::lock_guard lock(mutex_);
        ++refresh_failures_;
        throw;
    }
}

void JwksKeyResolver::request_refresh() {
    const auto now = options_.now();
    {
        std::lock_guard lock(mutex_);
        if (last_attempt_ != Clock::time_point{} &&
            now - last_attempt_ < options_.minimum_refresh_interval) {
            return;
        }
    }
    {
        std::lock_guard lock(worker_mutex_);
        refresh_requested_ = true;
    }
    worker_cv_.notify_one();
}

void JwksKeyResolver::worker_loop() {
    std::unique_lock lock(worker_mutex_);
    while (!stopping_) {
        worker_cv_.wait_for(lock, options_.ttl, [&] { return stopping_ || refresh_requested_; });
        if (stopping_)
            break;
        refresh_requested_ = false;
        lock.unlock();
        try {
            refresh_now();
        } catch (const std::exception&) {
        }
        lock.lock();
    }
}

JwtKeyResolution JwksKeyResolver::resolve(const std::string& kid) {
    if (kid.empty())
        return {{}, "missing_kid"};
    const auto now = options_.now();
    bool unknown = false;
    {
        std::lock_guard lock(mutex_);
        if (!snapshot_)
            return {{}, "jwks_unavailable"};
        const auto age = now - snapshot_->fetched_at;
        if (age > options_.ttl + options_.stale_grace)
            return {{}, "jwks_stale_expired"};
        const auto found = snapshot_->keys.find(kid);
        if (found != snapshot_->keys.end())
            return {found->second, {}};
        ++unknown_kid_rejections_;
        unknown = true;
    }
    if (unknown)
        request_refresh();
    return {{}, "unknown_kid"};
}

JwtKeyResolverMetrics JwksKeyResolver::metrics() const {
    std::lock_guard lock(mutex_);
    JwtKeyResolverMetrics result;
    result.refresh_attempts = refresh_attempts_;
    result.refresh_failures = refresh_failures_;
    result.unknown_kid_rejections = unknown_kid_rejections_;
    if (!snapshot_)
        return result;
    const auto age = std::chrono::duration_cast<std::chrono::seconds>(
        options_.now() - snapshot_->fetched_at);
    result.snapshot_available = true;
    result.snapshot_stale = age > options_.ttl;
    result.snapshot_age_seconds = std::max<std::int64_t>(0, age.count());
    result.last_success_epoch_seconds = std::chrono::duration_cast<std::chrono::seconds>(
        last_success_.time_since_epoch()).count();
    result.key_count = snapshot_->keys.size();
    return result;
}

} // namespace v2::auth
