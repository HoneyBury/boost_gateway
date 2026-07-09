// v3.1.0: RedisClient implementation backed by hiredis.

#include "v3/persistence/redis_client.h"

#if __has_include(<hiredis/hiredis.h>)
#include <hiredis/hiredis.h>
#else
#include <hiredis.h>
#endif

#include <chrono>

namespace v3::persistence {

class RedisClient::Impl {
public:
    explicit Impl(Config cfg) : config_(std::move(cfg)) {}

    ~Impl() {
        if (ctx_) redisFree(ctx_);
    }

    bool is_connected() const {
        return ctx_ != nullptr && ctx_->err == 0;
    }

    bool reconnect() {
        if (ctx_) {
            redisFree(ctx_);
            ctx_ = nullptr;
        }

        struct timeval tv{};
        tv.tv_sec = config_.timeout.count() / 1000;
        tv.tv_usec = (config_.timeout.count() % 1000) * 1000;

        ctx_ = redisConnectWithTimeout(config_.host.c_str(), config_.port, tv);
        if (!ctx_ || ctx_->err) return false;

        if (!config_.password.empty()) {
            auto* reply = static_cast<redisReply*>(
                redisCommand(ctx_, "AUTH %s", config_.password.c_str()));
            if (!reply || reply->type == REDIS_REPLY_ERROR) {
                if (reply) freeReplyObject(reply);
                redisFree(ctx_);
                ctx_ = nullptr;
                return false;
            }
            freeReplyObject(reply);
        }
        return true;
    }

    std::optional<std::string> get(const std::string& key) {
        if (!ensure_connected()) return std::nullopt;
        auto* reply = cmd("GET %s", key.c_str());
        if (!reply || reply->type == REDIS_REPLY_NIL) {
            free_if(reply);
            return std::nullopt;
        }
        if (reply->type != REDIS_REPLY_STRING) {
            freeReplyObject(reply);
            return std::nullopt;
        }
        std::string result(reply->str, reply->len);
        freeReplyObject(reply);
        return result;
    }

    bool set(const std::string& key, const std::string& value) {
        if (!ensure_connected()) return false;
        auto* reply = cmd("SET %s %b", key.c_str(),
                          value.data(), value.size());
        bool ok = reply && reply->type == REDIS_REPLY_STATUS;
        free_if(reply);
        return ok;
    }

    bool del(const std::string& key) {
        if (!ensure_connected()) return false;
        auto* reply = cmd("DEL %s", key.c_str());
        // DEL returns integer count; 0 = key didn't exist (not an error)
        bool ok = reply && reply->type == REDIS_REPLY_INTEGER;
        free_if(reply);
        return ok;
    }

    bool exists(const std::string& key) {
        if (!ensure_connected()) return false;
        auto* reply = cmd("EXISTS %s", key.c_str());
        bool ok = reply && reply->type == REDIS_REPLY_INTEGER &&
                  reply->integer > 0;
        free_if(reply);
        return ok;
    }

    std::int64_t incr(const std::string& key) {
        if (!ensure_connected()) return -1;
        auto* reply = cmd("INCR %s", key.c_str());
        if (!reply || reply->type != REDIS_REPLY_INTEGER) {
            free_if(reply);
            return -1;
        }
        std::int64_t val = reply->integer;
        freeReplyObject(reply);
        return val;
    }

    bool lpush(const std::string& key, const std::string& value) {
        if (!ensure_connected()) return false;
        auto* reply = cmd("LPUSH %s %b", key.c_str(),
                          value.data(), value.size());
        bool ok = reply && reply->type == REDIS_REPLY_INTEGER;
        free_if(reply);
        return ok;
    }

    std::vector<std::string> lrange(const std::string& key,
                                     std::int64_t start, std::int64_t stop) {
        std::vector<std::string> result;
        if (!ensure_connected()) return result;
        auto* reply = cmd("LRANGE %s %lld %lld", key.c_str(),
                          static_cast<long long>(start),
                          static_cast<long long>(stop));
        if (!reply || reply->type != REDIS_REPLY_ARRAY) {
            free_if(reply);
            return result;
        }
        for (std::size_t i = 0; i < reply->elements; ++i) {
            auto* elem = reply->element[i];
            if (elem->type == REDIS_REPLY_STRING)
                result.emplace_back(elem->str, elem->len);
        }
        freeReplyObject(reply);
        return result;
    }

    std::int64_t llen(const std::string& key) {
        if (!ensure_connected()) return -1;
        auto* reply = cmd("LLEN %s", key.c_str());
        if (!reply || reply->type != REDIS_REPLY_INTEGER) {
            free_if(reply);
            return -1;
        }
        std::int64_t val = reply->integer;
        freeReplyObject(reply);
        return val;
    }

    bool hset(const std::string& key, const std::string& field,
              const std::string& value) {
        if (!ensure_connected()) return false;
        auto* reply = cmd("HSET %s %s %s",
                          key.c_str(),
                          field.c_str(),
                          value.c_str());
        bool ok = reply && reply->type == REDIS_REPLY_INTEGER;
        free_if(reply);
        return ok;
    }

    std::optional<std::string> hget(const std::string& key,
                                    const std::string& field) {
        if (!ensure_connected()) return std::nullopt;
        auto* reply = cmd("HGET %s %s",
                          key.c_str(),
                          field.c_str());
        if (!reply || reply->type == REDIS_REPLY_NIL) {
            free_if(reply);
            return std::nullopt;
        }
        if (reply->type != REDIS_REPLY_STRING) {
            freeReplyObject(reply);
            return std::nullopt;
        }
        std::string result(reply->str, reply->len);
        freeReplyObject(reply);
        return result;
    }

    bool zadd(const std::string& key, double score, const std::string& member) {
        if (!ensure_connected()) return false;
        auto* reply = cmd("ZADD %s %f %s",
                          key.c_str(),
                          score,
                          member.c_str());
        bool ok = reply && reply->type == REDIS_REPLY_INTEGER;
        free_if(reply);
        return ok;
    }

    std::vector<std::pair<std::string, double>>
    zrange_with_scores(const std::string& key,
                        std::int64_t start, std::int64_t stop) {
        std::vector<std::pair<std::string, double>> result;
        if (!ensure_connected()) return result;
        auto* reply = cmd("ZRANGE %s %lld %lld WITHSCORES",
                          key.c_str(),
                          static_cast<long long>(start),
                          static_cast<long long>(stop));
        if (!reply || reply->type != REDIS_REPLY_ARRAY) {
            free_if(reply);
            return result;
        }
        // Elements come in pairs: member, score, member, score...
        for (std::size_t i = 0; i + 1 < reply->elements; i += 2) {
            std::string member(reply->element[i]->str,
                               reply->element[i]->len);
            double score = std::strtod(reply->element[i + 1]->str, nullptr);
            result.emplace_back(std::move(member), score);
        }
        freeReplyObject(reply);
        return result;
    }

    std::int64_t zcard(const std::string& key) {
        if (!ensure_connected()) return -1;
        auto* reply = cmd("ZCARD %s", key.c_str());
        if (!reply || reply->type != REDIS_REPLY_INTEGER) {
            free_if(reply);
            return -1;
        }
        std::int64_t val = reply->integer;
        freeReplyObject(reply);
        return val;
    }

    std::vector<std::pair<std::string, double>>
    zrevrange_with_scores(const std::string& key,
                          std::int64_t start, std::int64_t stop) {
        std::vector<std::pair<std::string, double>> result;
        if (!ensure_connected()) return result;
        auto* reply = cmd("ZREVRANGE %s %lld %lld WITHSCORES",
                          key.c_str(),
                          static_cast<long long>(start),
                          static_cast<long long>(stop));
        if (!reply || reply->type != REDIS_REPLY_ARRAY) {
            free_if(reply);
            return result;
        }
        for (std::size_t i = 0; i + 1 < reply->elements; i += 2) {
            std::string member(reply->element[i]->str,
                               reply->element[i]->len);
            double score = std::strtod(reply->element[i + 1]->str, nullptr);
            result.emplace_back(std::move(member), score);
        }
        freeReplyObject(reply);
        return result;
    }

    std::optional<std::int64_t> zrevrank(const std::string& key,
                                         const std::string& member) {
        if (!ensure_connected()) return std::nullopt;
        auto* reply = cmd("ZREVRANK %s %s",
                          key.c_str(),
                          member.c_str());
        if (!reply || reply->type != REDIS_REPLY_INTEGER) {
            free_if(reply);
            return std::nullopt;
        }
        std::int64_t rank = reply->integer;
        freeReplyObject(reply);
        return rank;
    }

    std::optional<double> zscore(const std::string& key,
                                 const std::string& member) {
        if (!ensure_connected()) return std::nullopt;
        auto* reply = cmd("ZSCORE %s %s",
                          key.c_str(),
                          member.c_str());
        if (!reply || reply->type == REDIS_REPLY_NIL) {
            free_if(reply);
            return std::nullopt;
        }
        if (reply->type != REDIS_REPLY_STRING) {
            freeReplyObject(reply);
            return std::nullopt;
        }
        double score = std::strtod(reply->str, nullptr);
        freeReplyObject(reply);
        return score;
    }

private:
    bool ensure_connected() {
        if (is_connected()) return true;
        return reconnect();
    }

    redisReply* cmd(const char* format, ...) {
        va_list ap;
        va_start(ap, format);
        auto* reply = static_cast<redisReply*>(
            redisvCommand(ctx_, format, ap));
        va_end(ap);
        return reply;
    }

    static void free_if(redisReply* reply) {
        if (reply) freeReplyObject(reply);
    }

    Config config_;
    redisContext* ctx_ = nullptr;
};

// ── PIMPL forwarding ──────────────────────────────────────────────────────

RedisClient::RedisClient(Config config)
    : impl_(std::make_unique<Impl>(std::move(config))) {}

RedisClient::~RedisClient() = default;

RedisClient::RedisClient(RedisClient&&) noexcept = default;
RedisClient& RedisClient::operator=(RedisClient&&) noexcept = default;

bool RedisClient::is_connected() const { return impl_->is_connected(); }
bool RedisClient::reconnect() { return impl_->reconnect(); }

std::optional<std::string> RedisClient::get(const std::string& key) {
    return impl_->get(key);
}

bool RedisClient::set(const std::string& key, const std::string& value) {
    return impl_->set(key, value);
}

bool RedisClient::del(const std::string& key) { return impl_->del(key); }
bool RedisClient::exists(const std::string& key) { return impl_->exists(key); }
std::int64_t RedisClient::incr(const std::string& key) { return impl_->incr(key); }

bool RedisClient::lpush(const std::string& key, const std::string& value) {
    return impl_->lpush(key, value);
}

std::vector<std::string> RedisClient::lrange(const std::string& key,
                                              std::int64_t start,
                                              std::int64_t stop) {
    return impl_->lrange(key, start, stop);
}

std::int64_t RedisClient::llen(const std::string& key) {
    return impl_->llen(key);
}

bool RedisClient::zadd(const std::string& key, double score,
                        const std::string& member) {
    return impl_->zadd(key, score, member);
}

std::vector<std::pair<std::string, double>>
RedisClient::zrange_with_scores(const std::string& key,
                                 std::int64_t start, std::int64_t stop) {
    return impl_->zrange_with_scores(key, start, stop);
}

std::int64_t RedisClient::zcard(const std::string& key) {
    return impl_->zcard(key);
}

bool RedisClient::hset(const std::string& key, const std::string& field,
                        const std::string& value) {
    return impl_->hset(key, field, value);
}

std::optional<std::string> RedisClient::hget(const std::string& key,
                                              const std::string& field) {
    return impl_->hget(key, field);
}

std::vector<std::pair<std::string, double>>
RedisClient::zrevrange_with_scores(const std::string& key,
                                   std::int64_t start, std::int64_t stop) {
    return impl_->zrevrange_with_scores(key, start, stop);
}

std::optional<std::int64_t> RedisClient::zrevrank(const std::string& key,
                                                   const std::string& member) {
    return impl_->zrevrank(key, member);
}

std::optional<double> RedisClient::zscore(const std::string& key,
                                           const std::string& member) {
    return impl_->zscore(key, member);
}

}  // namespace v3::persistence
