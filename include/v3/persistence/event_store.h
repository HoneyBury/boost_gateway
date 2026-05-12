#pragma once
// v3.0.0 D10: Event-sourced persistence layer.
// Immutable append-only event log with file-based storage.
// Supports battle results, player state, and replay data persistence.

#include <nlohmann/json.hpp>

#include <chrono>
#include <cstdint>
#include <fstream>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

namespace v3::persistence {

// ── Event record ───────────────────────────────────────────────────────

struct EventRecord {
    std::uint64_t sequence = 0;     // monotonically increasing
    std::string event_type;         // "battle_result", "login", "room_created"
    std::string aggregate_id;       // e.g., battle_id, user_id
    std::string payload;            // JSON payload
    std::uint64_t timestamp_ms = 0; // epoch millis
    std::uint64_t trace_id = 0;     // W3C trace context
};

// ── EventStore interface ───────────────────────────────────────────────

class IEventStore {
public:
    virtual ~IEventStore() = default;

    /// Append an event to the log.
    virtual bool append(EventRecord event) = 0;

    /// Read events for an aggregate, from a given sequence.
    virtual std::vector<EventRecord> read(
        const std::string& aggregate_id,
        std::uint64_t from_sequence = 0,
        std::uint64_t max_count = 100) = 0;

    /// Get the latest sequence number for an aggregate.
    [[nodiscard]] virtual std::uint64_t latest_sequence(
        const std::string& aggregate_id) const = 0;

    /// Get all events of a specific type.
    virtual std::vector<EventRecord> read_by_type(
        const std::string& event_type,
        std::uint64_t max_count = 100) = 0;

    /// Get total event count.
    [[nodiscard]] virtual std::uint64_t total_events() const = 0;
};

// ── FileEventStore ────────────────────────────────────────────────────

class FileEventStore : public IEventStore {
public:
    struct Config {
        std::string data_dir = "data/events";    // storage directory
        std::size_t max_file_size_mb = 64;       // rotate files at 64MB
        bool fsync_on_write = true;              // durability guarantee
    };

    explicit FileEventStore(Config config);
    ~FileEventStore();

    FileEventStore(const FileEventStore&) = delete;
    FileEventStore& operator=(const FileEventStore&) = delete;

    bool append(EventRecord event) override;
    std::vector<EventRecord> read(
        const std::string& aggregate_id,
        std::uint64_t from_sequence = 0,
        std::uint64_t max_count = 100) override;
    [[nodiscard]] std::uint64_t latest_sequence(
        const std::string& aggregate_id) const override;
    std::vector<EventRecord> read_by_type(
        const std::string& event_type,
        std::uint64_t max_count = 100) override;
    [[nodiscard]] std::uint64_t total_events() const override;

private:
    std::string event_file_path() const;
    std::string index_path(const std::string& aggregate_id) const;
    void ensure_directory() const;

    Config config_;
    mutable std::mutex mutex_;
    std::atomic<std::uint64_t> next_sequence_{1};
    std::atomic<std::uint64_t> total_{0};
};

// ── InMemoryEventStore (for testing) ──────────────────────────────────

class InMemoryEventStore : public IEventStore {
public:
    bool append(EventRecord event) override {
        std::lock_guard lock(mutex_);
        event.sequence = next_seq_++;
        events_.push_back(std::move(event));
        return true;
    }

    std::vector<EventRecord> read(
        const std::string& aggregate_id,
        std::uint64_t from_sequence = 0,
        std::uint64_t max_count = 100) override {
        std::lock_guard lock(mutex_);
        std::vector<EventRecord> result;
        for (const auto& e : events_) {
            if (e.aggregate_id == aggregate_id &&
                e.sequence >= from_sequence &&
                result.size() < max_count) {
                result.push_back(e);
            }
        }
        return result;
    }

    [[nodiscard]] std::uint64_t latest_sequence(
        const std::string& aggregate_id) const override {
        std::lock_guard lock(mutex_);
        std::uint64_t max_seq = 0;
        for (const auto& e : events_) {
            if (e.aggregate_id == aggregate_id && e.sequence > max_seq)
                max_seq = e.sequence;
        }
        return max_seq;
    }

    std::vector<EventRecord> read_by_type(
        const std::string& event_type,
        std::uint64_t max_count = 100) override {
        std::lock_guard lock(mutex_);
        std::vector<EventRecord> result;
        for (const auto& e : events_) {
            if (e.event_type == event_type && result.size() < max_count) {
                result.push_back(e);
            }
        }
        return result;
    }

    [[nodiscard]] std::uint64_t total_events() const override {
        std::lock_guard lock(mutex_);
        return events_.size();
    }

private:
    mutable std::mutex mutex_;
    std::vector<EventRecord> events_;
    std::uint64_t next_seq_ = 1;
};

// ── FileEventStore implementation ──────────────────────────────────────

inline FileEventStore::FileEventStore(Config config)
    : config_(std::move(config)) {
    ensure_directory();
}

inline FileEventStore::~FileEventStore() = default;

inline std::string FileEventStore::event_file_path() const {
    return config_.data_dir + "/events.jsonl";
}

inline std::string FileEventStore::index_path(
    const std::string& aggregate_id) const {
    return config_.data_dir + "/" + aggregate_id + ".idx";
}

inline void FileEventStore::ensure_directory() const {
    std::filesystem::create_directories(config_.data_dir);
}

inline bool FileEventStore::append(EventRecord event) {
    std::lock_guard lock(mutex_);
    event.sequence = next_sequence_++;
    if (event.timestamp_ms == 0) {
        event.timestamp_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();
    }

    nlohmann::json doc{
        {"seq", event.sequence},
        {"type", event.event_type},
        {"aggregate_id", event.aggregate_id},
        {"payload", event.payload},
        {"ts", event.timestamp_ms},
        {"trace_id", event.trace_id},
    };

    std::ofstream file(event_file_path(), std::ios::app);
    if (!file.is_open()) return false;
    file << doc.dump() << "\n";
    if (config_.fsync_on_write) file.flush();
    total_++;
    return true;
}

inline std::vector<EventRecord> FileEventStore::read(
    const std::string& aggregate_id,
    std::uint64_t from_sequence,
    std::uint64_t max_count) {
    std::lock_guard lock(mutex_);
    std::vector<EventRecord> result;
    std::ifstream file(event_file_path());
    if (!file.is_open()) return result;

    std::string line;
    while (std::getline(file, line) && result.size() < max_count) {
        auto doc = nlohmann::json::parse(line, nullptr, false);
        if (doc.is_discarded()) continue;
        if (doc.value("aggregate_id", "") != aggregate_id) continue;
        if (doc.value("seq", 0ULL) < from_sequence) continue;

        EventRecord rec;
        rec.sequence = doc["seq"].get<std::uint64_t>();
        rec.event_type = doc["type"].get<std::string>();
        rec.aggregate_id = doc["aggregate_id"].get<std::string>();
        rec.payload = doc["payload"].get<std::string>();
        rec.timestamp_ms = doc.value("ts", 0ULL);
        rec.trace_id = doc.value("trace_id", 0ULL);
        result.push_back(std::move(rec));
    }
    return result;
}

inline std::uint64_t FileEventStore::latest_sequence(
    const std::string& aggregate_id) const {
    std::lock_guard lock(mutex_);
    std::uint64_t max_seq = 0;
    std::ifstream file(event_file_path());
    if (!file.is_open()) return 0;

    std::string line;
    while (std::getline(file, line)) {
        auto doc = nlohmann::json::parse(line, nullptr, false);
        if (doc.is_discarded()) continue;
        if (doc.value("aggregate_id", "") == aggregate_id) {
            auto seq = doc.value("seq", 0ULL);
            if (seq > max_seq) max_seq = seq;
        }
    }
    return max_seq;
}

inline std::vector<EventRecord> FileEventStore::read_by_type(
    const std::string& event_type,
    std::uint64_t max_count) {
    std::lock_guard lock(mutex_);
    std::vector<EventRecord> result;
    std::ifstream file(event_file_path());
    if (!file.is_open()) return result;

    std::string line;
    while (std::getline(file, line) && result.size() < max_count) {
        auto doc = nlohmann::json::parse(line, nullptr, false);
        if (doc.is_discarded()) continue;
        if (doc.value("type", "") != event_type) continue;

        EventRecord rec;
        rec.sequence = doc["seq"].get<std::uint64_t>();
        rec.event_type = doc["type"].get<std::string>();
        rec.aggregate_id = doc["aggregate_id"].get<std::string>();
        rec.payload = doc["payload"].get<std::string>();
        rec.timestamp_ms = doc.value("ts", 0ULL);
        result.push_back(std::move(rec));
    }
    return result;
}

inline std::uint64_t FileEventStore::total_events() const {
    return total_.load();
}

}  // namespace v3::persistence
