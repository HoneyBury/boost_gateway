#pragma once
// v2.2.0 HS256 JWT Validator — self-contained, no external crypto dependencies.
// Uses nlohmann_json for payload parsing (already a project dependency).
// For RS256/ES256 upgrade path, add OpenSSL and swap the verifier.

#include <nlohmann/json.hpp>

#include <array>
#include <cstdint>
#include <cstring>
#include <optional>
#include <string>
#include <vector>

namespace v2::auth {

// ── SHA-256 (FIPS 180-4) ────────────────────────────────────────────────
namespace detail {

inline constexpr std::array<std::uint32_t, 64> kSha256K = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};

inline constexpr auto rotr32(std::uint32_t x, std::uint32_t n) noexcept
    -> std::uint32_t {
    return (x >> n) | (x << (32 - n));
}

inline void sha256_transform(std::array<std::uint32_t, 8>& h,
                             const std::uint8_t* data) {
    std::uint32_t w[64];
    for (int i = 0; i < 16; ++i) {
        w[i] = (static_cast<std::uint32_t>(data[i * 4]) << 24) |
               (static_cast<std::uint32_t>(data[i * 4 + 1]) << 16) |
               (static_cast<std::uint32_t>(data[i * 4 + 2]) << 8) |
               static_cast<std::uint32_t>(data[i * 4 + 3]);
    }
    for (int i = 16; i < 64; ++i) {
        auto s0 = rotr32(w[i - 15], 7) ^ rotr32(w[i - 15], 18) ^ (w[i - 15] >> 3);
        auto s1 = rotr32(w[i - 2], 17) ^ rotr32(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }

    auto a = h[0], b = h[1], c = h[2], d = h[3];
    auto e = h[4], f = h[5], g = h[6], hh = h[7];

    for (int i = 0; i < 64; ++i) {
        auto S1 = rotr32(e, 6) ^ rotr32(e, 11) ^ rotr32(e, 25);
        auto ch = (e & f) ^ ((~e) & g);
        auto temp1 = hh + S1 + ch + kSha256K[i] + w[i];
        auto S0 = rotr32(a, 2) ^ rotr32(a, 13) ^ rotr32(a, 22);
        auto maj = (a & b) ^ (a & c) ^ (b & c);
        auto temp2 = S0 + maj;
        hh = g; g = f; f = e; e = d + temp1;
        d = c; c = b; b = a; a = temp1 + temp2;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d;
    h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
}

inline auto sha256(const std::string& input) -> std::array<std::uint8_t, 32> {
    std::array<std::uint32_t, 8> h = {
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    std::vector<std::uint8_t> padded(input.begin(), input.end());
    std::uint64_t bit_len = padded.size() * 8;
    padded.push_back(0x80);
    while ((padded.size() + 8) % 64 != 0) padded.push_back(0);
    for (int i = 7; i >= 0; --i)
        padded.push_back(static_cast<std::uint8_t>(bit_len >> (i * 8)));

    for (std::size_t i = 0; i < padded.size(); i += 64)
        sha256_transform(h, padded.data() + i);

    std::array<std::uint8_t, 32> out;
    for (int i = 0; i < 8; ++i) {
        out[i * 4]     = static_cast<std::uint8_t>(h[i] >> 24);
        out[i * 4 + 1] = static_cast<std::uint8_t>(h[i] >> 16);
        out[i * 4 + 2] = static_cast<std::uint8_t>(h[i] >> 8);
        out[i * 4 + 3] = static_cast<std::uint8_t>(h[i]);
    }
    return out;
}

// ── HMAC-SHA256 (RFC 2104) ───────────────────────────────────────────────

inline auto hmac_sha256(const std::string& key, const std::string& message)
    -> std::array<std::uint8_t, 32> {
    constexpr std::size_t kBlockSize = 64;
    std::array<std::uint8_t, kBlockSize> ipad, opad;
    std::string key_block;
    if (key.size() > kBlockSize) {
        auto hash = sha256(key);
        key_block.assign(reinterpret_cast<const char*>(hash.data()), 32);
        key_block.resize(kBlockSize, 0);
    } else {
        key_block = key;
        key_block.resize(kBlockSize, 0);
    }
    for (std::size_t i = 0; i < kBlockSize; ++i) {
        ipad[i] = static_cast<std::uint8_t>(key_block[i]) ^ 0x36;
        opad[i] = static_cast<std::uint8_t>(key_block[i]) ^ 0x5c;
    }
    std::string inner(reinterpret_cast<const char*>(ipad.data()), kBlockSize);
    inner += message;
    auto inner_hash = sha256(inner);
    std::string outer(reinterpret_cast<const char*>(opad.data()), kBlockSize);
    outer.append(reinterpret_cast<const char*>(inner_hash.data()), 32);
    return sha256(outer);
}

// ── Base64url (RFC 4648 §5) ──────────────────────────────────────────────

inline auto base64url_decode(const std::string& input) -> std::string {
    static const std::string kTable =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    std::string out;
    int bits = 0, val = 0;
    for (auto c : input) {
        if (c == '=') break;
        auto pos = kTable.find(c);
        if (pos == std::string::npos) return {};
        val = (val << 6) | static_cast<int>(pos);
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out.push_back(static_cast<char>((val >> bits) & 0xFF));
        }
    }
    return out;
}

inline auto base64url_encode(const std::string& input) -> std::string {
    static const char kTable[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    std::string out;
    unsigned int val = 0;
    int bits = 0;
    for (auto c : input) {
        val = (val << 8) | static_cast<unsigned char>(c);
        bits += 8;
        while (bits >= 6) {
            bits -= 6;
            out.push_back(kTable[(val >> bits) & 0x3F]);
        }
    }
    if (bits > 0) out.push_back(kTable[(val << (6 - bits)) & 0x3F]);
    return out;
}

}  // namespace detail

// ── JWT Token ────────────────────────────────────────────────────────────

struct JwtHeader {
    std::string alg;  // "HS256"
    std::string typ;  // "JWT"
};

struct JwtPayload {
    std::string sub;        // subject (user_id)
    std::string iss;        // issuer
    std::string role;       // "player", "admin", "observer"
    std::string display_name;
    std::uint64_t iat = 0;  // issued at (unix timestamp)
    std::uint64_t exp = 0;  // expiration (unix timestamp)
    nlohmann::json extra;   // additional claims
};

// ── JWT Validator ────────────────────────────────────────────────────────

class JwtValidator {
public:
    struct Config {
        std::string secret;
        std::string issuer = "boost-gateway";
        bool require_expiration = false;
    };

    struct Result {
        bool valid = false;
        std::string error;
        JwtPayload payload;
    };

    explicit JwtValidator(Config config) : config_(std::move(config)) {}

    [[nodiscard]] Result validate(const std::string& token) const {
        // Split: header.payload.signature
        auto dot1 = token.find('.');
        auto dot2 = token.find('.', dot1 + 1);
        if (dot1 == std::string::npos || dot2 == std::string::npos) {
            return {false, "malformed_token", {}};
        }

        auto header_b64 = token.substr(0, dot1);
        auto payload_b64 = token.substr(dot1 + 1, dot2 - dot1 - 1);
        auto sig_b64 = token.substr(dot2 + 1);
        auto signing_input = header_b64 + "." + payload_b64;

        // Decode
        auto header_json = detail::base64url_decode(header_b64);
        auto payload_json = detail::base64url_decode(payload_b64);
        auto sig_bytes = detail::base64url_decode(sig_b64);

        // Verify signature
        auto expected = detail::hmac_sha256(config_.secret, signing_input);
        if (sig_bytes.size() != 32 ||
            std::memcmp(sig_bytes.data(), expected.data(), 32) != 0) {
            return {false, "invalid_signature", {}};
        }

        // Parse header
        auto header_doc = nlohmann::json::parse(header_json, nullptr, false);
        if (header_doc.is_discarded()) return {false, "invalid_header_json", {}};
        auto alg = header_doc.value("alg", "");
        if (alg != "HS256") return {false, "unsupported_algorithm:" + alg, {}};

        // Parse payload
        auto payload_doc = nlohmann::json::parse(payload_json, nullptr, false);
        if (payload_doc.is_discarded()) return {false, "invalid_payload_json", {}};

        JwtPayload payload;
        payload.sub = payload_doc.value("sub", "");
        payload.iss = payload_doc.value("iss", "");
        payload.role = payload_doc.value("role", "player");
        payload.display_name = payload_doc.value("name", "");
        payload.iat = payload_doc.value("iat", 0ULL);
        payload.exp = payload_doc.value("exp", 0ULL);
        payload.extra = payload_doc;

        // Validate claims
        if (payload.sub.empty()) return {false, "missing_subject", {}};
        if (!config_.issuer.empty() && payload.iss != config_.issuer)
            return {false, "invalid_issuer", {}};

        if (config_.require_expiration || payload.exp != 0) {
            auto now = std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            if (payload.exp != 0 && now > static_cast<std::int64_t>(payload.exp))
                return {false, "token_expired", {}};
        }

        return {true, "", std::move(payload)};
    }

    /// Generate a token for testing/setup purposes.
    [[nodiscard]] std::string generate(const JwtPayload& payload) const {
        nlohmann::json header{{"alg", "HS256"}, {"typ", "JWT"}};
        nlohmann::json body;
        body["sub"] = payload.sub;
        body["iss"] = config_.issuer;
        body["role"] = payload.role;
        body["name"] = payload.display_name;
        body["iat"] = payload.iat;
        if (payload.exp != 0) body["exp"] = payload.exp;
        for (auto& [k, v] : payload.extra.items()) body[k] = v;

        auto h = detail::base64url_encode(header.dump());
        auto p = detail::base64url_encode(body.dump());
        auto sig = detail::hmac_sha256(config_.secret, h + "." + p);
        return h + "." + p + "." + detail::base64url_encode(
            std::string(reinterpret_cast<const char*>(sig.data()), 32));
    }

private:
    Config config_;
};

}  // namespace v2::auth
