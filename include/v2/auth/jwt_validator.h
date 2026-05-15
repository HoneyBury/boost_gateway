#pragma once
// v2.2.0 JWT Validator.
// Supports HS256 and RS256 verification, plus optional token generation
// for tests and local setup flows.

#include <nlohmann/json.hpp>
#include <openssl/bio.h>
#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <openssl/pem.h>

#include <array>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace v2::auth {

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
        auto s1 = rotr32(e, 6) ^ rotr32(e, 11) ^ rotr32(e, 25);
        auto ch = (e & f) ^ ((~e) & g);
        auto temp1 = hh + s1 + ch + kSha256K[i] + w[i];
        auto s0 = rotr32(a, 2) ^ rotr32(a, 13) ^ rotr32(a, 22);
        auto maj = (a & b) ^ (a & c) ^ (b & c);
        auto temp2 = s0 + maj;
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
    for (int i = 7; i >= 0; --i) {
        padded.push_back(static_cast<std::uint8_t>(bit_len >> (i * 8)));
    }

    for (std::size_t i = 0; i < padded.size(); i += 64) {
        sha256_transform(h, padded.data() + i);
    }

    std::array<std::uint8_t, 32> out;
    for (int i = 0; i < 8; ++i) {
        out[i * 4] = static_cast<std::uint8_t>(h[i] >> 24);
        out[i * 4 + 1] = static_cast<std::uint8_t>(h[i] >> 16);
        out[i * 4 + 2] = static_cast<std::uint8_t>(h[i] >> 8);
        out[i * 4 + 3] = static_cast<std::uint8_t>(h[i]);
    }
    return out;
}

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

inline auto base64url_decode(const std::string& input) -> std::string {
    static const std::string kTable =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    std::string out;
    int bits = 0;
    int val = 0;
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

using BioPtr = std::unique_ptr<BIO, decltype(&BIO_free)>;
using MdCtxPtr = std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)>;
using PKeyPtr = std::unique_ptr<EVP_PKEY, decltype(&EVP_PKEY_free)>;

inline auto load_public_key(const std::string& pem) -> PKeyPtr {
    if (pem.empty()) return {nullptr, EVP_PKEY_free};
    BioPtr bio(BIO_new_mem_buf(pem.data(), static_cast<int>(pem.size())), BIO_free);
    if (!bio) return {nullptr, EVP_PKEY_free};
    return PKeyPtr(PEM_read_bio_PUBKEY(bio.get(), nullptr, nullptr, nullptr),
                   EVP_PKEY_free);
}

inline auto load_private_key(const std::string& pem) -> PKeyPtr {
    if (pem.empty()) return {nullptr, EVP_PKEY_free};
    BioPtr bio(BIO_new_mem_buf(pem.data(), static_cast<int>(pem.size())), BIO_free);
    if (!bio) return {nullptr, EVP_PKEY_free};
    return PKeyPtr(
        PEM_read_bio_PrivateKey(bio.get(), nullptr, nullptr, nullptr),
        EVP_PKEY_free);
}

inline bool verify_rs256(const std::string& public_key_pem,
                         const std::string& signing_input,
                         const std::string& signature) {
    auto pkey = load_public_key(public_key_pem);
    if (!pkey) return false;

    MdCtxPtr ctx(EVP_MD_CTX_new(), EVP_MD_CTX_free);
    if (!ctx) return false;
    if (EVP_DigestVerifyInit(ctx.get(), nullptr, EVP_sha256(), nullptr,
                             pkey.get()) != 1) {
        return false;
    }
    if (EVP_DigestVerifyUpdate(ctx.get(), signing_input.data(),
                               signing_input.size()) != 1) {
        return false;
    }
    return EVP_DigestVerifyFinal(
               ctx.get(),
               reinterpret_cast<const unsigned char*>(signature.data()),
               signature.size()) == 1;
}

inline auto sign_rs256(const std::string& private_key_pem,
                       const std::string& signing_input) -> std::string {
    auto pkey = load_private_key(private_key_pem);
    if (!pkey) return {};

    MdCtxPtr ctx(EVP_MD_CTX_new(), EVP_MD_CTX_free);
    if (!ctx) return {};
    if (EVP_DigestSignInit(ctx.get(), nullptr, EVP_sha256(), nullptr,
                           pkey.get()) != 1) {
        return {};
    }
    if (EVP_DigestSignUpdate(ctx.get(), signing_input.data(),
                             signing_input.size()) != 1) {
        return {};
    }

    std::size_t sig_len = 0;
    if (EVP_DigestSignFinal(ctx.get(), nullptr, &sig_len) != 1) return {};
    std::string signature(sig_len, '\0');
    if (EVP_DigestSignFinal(
            ctx.get(),
            reinterpret_cast<unsigned char*>(signature.data()),
            &sig_len) != 1) {
        return {};
    }
    signature.resize(sig_len);
    return signature;
}

inline bool constant_time_equal(const std::string& lhs, const std::string& rhs) {
    if (lhs.size() != rhs.size()) return false;
    return CRYPTO_memcmp(lhs.data(), rhs.data(), lhs.size()) == 0;
}

inline bool audience_matches(const nlohmann::json& payload_doc,
                             const std::string& expected_audience) {
    if (expected_audience.empty()) return true;
    auto it = payload_doc.find("aud");
    if (it == payload_doc.end()) return false;
    if (it->is_string()) {
        return it->get<std::string>() == expected_audience;
    }
    if (it->is_array()) {
        for (const auto& entry : *it) {
            if (entry.is_string() &&
                entry.get<std::string>() == expected_audience) {
                return true;
            }
        }
    }
    return false;
}

}  // namespace detail

struct JwtHeader {
    std::string alg;
    std::string typ;
    std::string kid;
};

struct JwtPayload {
    std::string sub;
    std::string iss;
    std::string aud;
    std::string role;
    std::string display_name;
    std::uint64_t iat = 0;
    std::uint64_t exp = 0;
    nlohmann::json extra;
};

class JwtValidator {
public:
    struct Config {
        std::string secret;
        std::string public_key_pem;
        std::string private_key_pem;
        std::string issuer = "boost-gateway";
        std::string audience;
        bool require_expiration = false;
    };

    struct Result {
        bool valid = false;
        std::string error;
        JwtPayload payload;
    };

    explicit JwtValidator(Config config) : config_(std::move(config)) {}

    [[nodiscard]] Result validate(const std::string& token) const {
        auto dot1 = token.find('.');
        auto dot2 = token.find('.', dot1 == std::string::npos ? dot1 : dot1 + 1);
        if (dot1 == std::string::npos || dot2 == std::string::npos) {
            return {false, "malformed_token", {}};
        }

        const auto header_b64 = token.substr(0, dot1);
        const auto payload_b64 = token.substr(dot1 + 1, dot2 - dot1 - 1);
        const auto sig_b64 = token.substr(dot2 + 1);
        const auto signing_input = header_b64 + "." + payload_b64;

        const auto header_json = detail::base64url_decode(header_b64);
        const auto payload_json = detail::base64url_decode(payload_b64);
        const auto sig_bytes = detail::base64url_decode(sig_b64);

        auto header_doc = nlohmann::json::parse(header_json, nullptr, false);
        if (header_doc.is_discarded()) return {false, "invalid_header_json", {}};

        const auto alg = header_doc.value("alg", "");
        if (alg == "HS256") {
            if (config_.secret.empty()) {
                return {false, "missing_hs256_secret", {}};
            }
            const auto expected = detail::hmac_sha256(config_.secret, signing_input);
            const std::string expected_sig(
                reinterpret_cast<const char*>(expected.data()), expected.size());
            if (!detail::constant_time_equal(sig_bytes, expected_sig)) {
                return {false, "invalid_signature", {}};
            }
        } else if (alg == "RS256") {
            if (config_.public_key_pem.empty()) {
                return {false, "missing_rs256_public_key", {}};
            }
            if (!detail::verify_rs256(config_.public_key_pem, signing_input, sig_bytes)) {
                return {false, "invalid_signature", {}};
            }
        } else {
            return {false, "unsupported_algorithm:" + alg, {}};
        }

        auto payload_doc = nlohmann::json::parse(payload_json, nullptr, false);
        if (payload_doc.is_discarded()) return {false, "invalid_payload_json", {}};

        JwtPayload payload;
        payload.sub = payload_doc.value("sub", "");
        payload.iss = payload_doc.value("iss", "");
        payload.role = payload_doc.value("role", "player");
        payload.display_name = payload_doc.value("name", "");
        payload.iat = payload_doc.value("iat", 0ULL);
        payload.exp = payload_doc.value("exp", 0ULL);
        if (payload_doc.contains("aud") && payload_doc["aud"].is_string()) {
            payload.aud = payload_doc["aud"].get<std::string>();
        }
        payload.extra = payload_doc;

        if (payload.sub.empty()) return {false, "missing_subject", {}};
        if (!config_.issuer.empty() && payload.iss != config_.issuer) {
            return {false, "invalid_issuer", {}};
        }
        if (!detail::audience_matches(payload_doc, config_.audience)) {
            return {false, "invalid_audience", {}};
        }

        if (config_.require_expiration || payload.exp != 0) {
            const auto now = std::chrono::duration_cast<std::chrono::seconds>(
                                 std::chrono::system_clock::now().time_since_epoch())
                                 .count();
            if (payload.exp == 0) {
                return {false, "missing_expiration", {}};
            }
            if (now > static_cast<std::int64_t>(payload.exp)) {
                return {false, "token_expired", {}};
            }
        }

        return {true, "", std::move(payload)};
    }

    [[nodiscard]] std::string generate(const JwtPayload& payload) const {
        const auto use_rs256 = !config_.private_key_pem.empty();
        if (use_rs256 && config_.public_key_pem.empty()) {
            return {};
        }
        if (!use_rs256 && config_.secret.empty()) {
            return {};
        }

        nlohmann::json header{
            {"alg", use_rs256 ? "RS256" : "HS256"},
            {"typ", "JWT"},
        };
        nlohmann::json body;
        body["sub"] = payload.sub;
        body["iss"] = config_.issuer;
        body["role"] = payload.role;
        body["name"] = payload.display_name;
        body["iat"] = payload.iat;
        if (!payload.aud.empty()) body["aud"] = payload.aud;
        if (payload.exp != 0) body["exp"] = payload.exp;
        for (auto& [k, v] : payload.extra.items()) body[k] = v;

        const auto h = detail::base64url_encode(header.dump());
        const auto p = detail::base64url_encode(body.dump());
        const auto signing_input = h + "." + p;

        std::string signature;
        if (use_rs256) {
            signature = detail::sign_rs256(config_.private_key_pem, signing_input);
        } else {
            const auto sig = detail::hmac_sha256(config_.secret, signing_input);
            signature.assign(reinterpret_cast<const char*>(sig.data()), sig.size());
        }
        if (signature.empty()) return {};
        return h + "." + p + "." + detail::base64url_encode(signature);
    }

private:
    Config config_;
};

}  // namespace v2::auth
