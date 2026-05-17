// v3.1.0: RedisEventStore implementation.
// Events are stored as JSON strings in Redis lists per aggregate.
// Global ordering is maintained via the es:{prefix}:next_seq counter.

#include "v3/persistence/redis_event_store.h"

#include <nlohmann/json.hpp>

#include <chrono>

namespace v3::persistence {

class RedisEventStore::Impl {
public:
    explicit Impl(Config cfg) : config_(std::move(cfg)), client_(config_.redis) {}

    bool append(EventRecord event) {
        if (!ensure_redis()) return false;

        if (event.timestamp_ms == 0) {
            event.timestamp_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
        }

        auto seq = client_.incr(seq_key());
        if (seq < 0) return false;
        event.sequence = static_cast<std::uint64_t>(seq);

        nlohmann::json doc{
            {"seq", event.sequence},
            {"type", event.event_type},
            {"aggregate_id", event.aggregate_id},
            {"payload", event.payload},
            {"ts", event.timestamp_ms},
            {"trace_id", event.trace_id},
        };
        std::string json = doc.dump();

        client_.lpush(agg_key(event.aggregate_id), json);
        client_.lpush(type_key(event.event_type), json);
        return true;
    }

    std::vector<EventRecord> read(const std::string& aggregate_id,
                                   std::uint64_t from_sequence,
                                   std::uint64_t max_count) {
        std::vector<EventRecord> result;
        if (!ensure_redis()) return result;

        // LPUSH puts newest first, so LRANGE 0 -1 returns newest to oldest.
        // Read all then filter and reverse.
        auto raw = client_.lrange(agg_key(aggregate_id), 0, -1);
        for (auto it = raw.rbegin(); it != raw.rend(); ++it) {
            if (result.size() >= max_count) break;
            auto rec = parse(*it);
            if (rec && rec->sequence >= from_sequence)
                result.push_back(std::move(*rec));
        }
        return result;
    }

    std::uint64_t latest_sequence(const std::string& aggregate_id) const {
        if (!redis_available()) return 0;
        // Newest entry is at index 0 (LPUSH).
        auto raw = const_cast<Impl*>(this)->client_.lrange(
            agg_key(aggregate_id), 0, 0);
        if (raw.empty()) return 0;
        auto rec = parse(raw[0]);
        return rec ? rec->sequence : 0;
    }

    std::vector<EventRecord> read_by_type(const std::string& event_type,
                                           std::uint64_t max_count) {
        std::vector<EventRecord> result;
        if (!ensure_redis()) return result;

        auto raw = client_.lrange(type_key(event_type), 0,
                                   static_cast<std::int64_t>(max_count) - 1);
        for (auto it = raw.rbegin(); it != raw.rend(); ++it) {
            auto rec = parse(*it);
            if (rec) result.push_back(std::move(*rec));
        }
        return result;
    }

    std::uint64_t total_events() const {
        if (!redis_available()) return 0;
        auto val = const_cast<Impl*>(this)->client_.get(seq_key());
        if (!val) return 0;
        try { return std::stoull(*val); }
        catch (...) { return 0; }
    }

    RedisClient& client() { return client_; }
    bool redis_available() const {
        if (client_.is_connected()) return true;
        return const_cast<Impl*>(this)->ensure_redis();
    }

private:
    bool ensure_redis() {
        if (client_.is_connected()) return true;
        return client_.reconnect();
    }

    std::string seq_key() const { return config_.key_prefix + ":" + "next_seq"; }
    std::string agg_key(const std::string& id) const {
        return config_.key_prefix + ":" + id;
    }
    std::string type_key(const std::string& type) const {
        return config_.key_prefix + ":by_type:" + type;
    }

    static std::optional<EventRecord> parse(const std::string& json) {
        auto doc = nlohmann::json::parse(json, nullptr, false);
        if (doc.is_discarded()) return std::nullopt;
        EventRecord rec;
        rec.sequence = doc.value("seq", 0ULL);
        rec.event_type = doc.value("type", "");
        rec.aggregate_id = doc.value("aggregate_id", "");
        rec.payload = doc.value("payload", "");
        rec.timestamp_ms = doc.value("ts", 0ULL);
        rec.trace_id = doc.value("trace_id", 0ULL);
        return rec;
    }

    Config config_;
    RedisClient client_;
};

// ── PIMPL forwarding ──────────────────────────────────────────────────────

RedisEventStore::RedisEventStore(Config config)
    : impl_(std::make_unique<Impl>(std::move(config))) {}

RedisEventStore::~RedisEventStore() = default;

bool RedisEventStore::append(EventRecord event) {
    return impl_->append(std::move(event));
}

std::vector<EventRecord> RedisEventStore::read(
    const std::string& aggregate_id,
    std::uint64_t from_sequence,
    std::uint64_t max_count) {
    return impl_->read(aggregate_id, from_sequence, max_count);
}

std::uint64_t RedisEventStore::latest_sequence(
    const std::string& aggregate_id) const {
    return impl_->latest_sequence(aggregate_id);
}

std::vector<EventRecord> RedisEventStore::read_by_type(
    const std::string& event_type,
    std::uint64_t max_count) {
    return impl_->read_by_type(event_type, max_count);
}

std::uint64_t RedisEventStore::total_events() const {
    return impl_->total_events();
}

RedisClient& RedisEventStore::client() { return impl_->client(); }

bool RedisEventStore::redis_available() const {
    return impl_->redis_available();
}

}  // namespace v3::persistence
