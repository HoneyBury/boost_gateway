#pragma once

#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace v2::auth {

struct JwtKeyResolution {
    std::string public_key_pem;
    std::string error;

    [[nodiscard]] bool valid() const noexcept { return !public_key_pem.empty(); }
};

struct JwtKeyResolverMetrics {
    bool snapshot_available = false;
    bool snapshot_stale = false;
    std::int64_t snapshot_age_seconds = -1;
    std::int64_t last_success_epoch_seconds = 0;
    std::uint64_t refresh_attempts = 0;
    std::uint64_t refresh_failures = 0;
    std::uint64_t unknown_kid_rejections = 0;
    std::size_t key_count = 0;
};

class JwtKeyResolver {
public:
    virtual ~JwtKeyResolver() = default;
    [[nodiscard]] virtual JwtKeyResolution resolve(const std::string& kid) = 0;
    [[nodiscard]] virtual JwtKeyResolverMetrics metrics() const = 0;
};

class StaticJwtKeyResolver final : public JwtKeyResolver {
public:
    explicit StaticJwtKeyResolver(std::unordered_map<std::string, std::string> keys);
    [[nodiscard]] JwtKeyResolution resolve(const std::string& kid) override;
    [[nodiscard]] JwtKeyResolverMetrics metrics() const override;

private:
    std::unordered_map<std::string, std::string> keys_;
};

struct JwksHttpOptions {
    std::string uri;
    std::vector<std::string> allowed_hosts;
    bool allow_loopback_http = false;
    std::chrono::milliseconds connect_timeout{2000};
    std::chrono::milliseconds read_timeout{3000};
    std::size_t max_response_bytes = 1024U * 1024U;
};

[[nodiscard]] std::string fetch_jwks_document(const JwksHttpOptions& options);

class JwksKeyResolver final : public JwtKeyResolver {
public:
    using Clock = std::chrono::system_clock;
    using Fetcher = std::function<std::string()>;
    using Now = std::function<Clock::time_point()>;

    struct Options {
        Fetcher fetcher;
        Now now = [] { return Clock::now(); };
        std::chrono::seconds ttl{300};
        std::chrono::seconds stale_grace{900};
        std::chrono::seconds minimum_refresh_interval{30};
        std::size_t max_response_bytes = 1024U * 1024U;
        std::size_t max_keys = 32;
        std::size_t max_key_bytes = 16U * 1024U;
    };

    explicit JwksKeyResolver(Options options);
    ~JwksKeyResolver() override;

    JwksKeyResolver(const JwksKeyResolver&) = delete;
    JwksKeyResolver& operator=(const JwksKeyResolver&) = delete;

    void refresh_now();
    [[nodiscard]] JwtKeyResolution resolve(const std::string& kid) override;
    [[nodiscard]] JwtKeyResolverMetrics metrics() const override;

private:
    struct Snapshot {
        std::unordered_map<std::string, std::string> keys;
        Clock::time_point fetched_at;
    };

    void request_refresh();
    void worker_loop();
    [[nodiscard]] std::shared_ptr<const Snapshot> fetch_snapshot();

    Options options_;
    mutable std::mutex mutex_;
    std::shared_ptr<const Snapshot> snapshot_;
    Clock::time_point last_attempt_{};
    Clock::time_point last_success_{};
    std::uint64_t refresh_attempts_ = 0;
    std::uint64_t refresh_failures_ = 0;
    std::uint64_t unknown_kid_rejections_ = 0;

    std::mutex refresh_mutex_;
    std::mutex worker_mutex_;
    std::condition_variable worker_cv_;
    bool refresh_requested_ = false;
    bool stopping_ = false;
    std::thread worker_;
};

} // namespace v2::auth
