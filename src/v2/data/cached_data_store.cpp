#include "v2/data/cached_data_store.h"
#include "v2/data/write_behind_store.h"

namespace v2::data {

CachedBattleDataStore::CachedBattleDataStore(
    std::shared_ptr<v2::gateway::BattleArchiveSink> delegate,
    std::size_t cache_size)
    : delegate_(std::move(delegate))
    , replay_cache_(cache_size)
    , result_cache_(cache_size)
    , snapshot_cache_(cache_size) {
    write_behind_ = std::make_unique<WriteBehindDataStore>(delegate_);
}

CachedBattleDataStore::~CachedBattleDataStore() = default;

bool CachedBattleDataStore::persist(const v2::gateway::Runtime::BattleArchive& archive) {
    return write_behind_->persist(archive);
}

// ─── Replay ───────────────────────────────────────────────────────

bool CachedBattleDataStore::save_replay(const std::string& battle_id,
                                        std::string_view replay_json) {
    auto evicted = replay_cache_.put(battle_id, std::string(replay_json));
    if (evicted.has_value()) {
        write_behind_->save_replay(evicted->first, evicted->second);
    }
    return true;
}

std::optional<std::string> CachedBattleDataStore::load_replay(
    const std::string& battle_id) {
    auto cached = replay_cache_.get(battle_id);
    if (cached != nullptr) {
        return *cached;
    }
    auto value = delegate_->load_replay(battle_id);
    if (value.has_value()) {
        replay_cache_.put(battle_id, *value);
    }
    return value;
}

// ─── Result ───────────────────────────────────────────────────────

bool CachedBattleDataStore::save_result(const std::string& battle_id,
                                        std::string_view result_json) {
    auto evicted = result_cache_.put(battle_id, std::string(result_json));
    if (evicted.has_value()) {
        write_behind_->save_result(evicted->first, evicted->second);
    }
    return true;
}

std::optional<std::string> CachedBattleDataStore::load_result(
    const std::string& battle_id) {
    auto cached = result_cache_.get(battle_id);
    if (cached != nullptr) {
        return *cached;
    }
    auto value = delegate_->load_result(battle_id);
    if (value.has_value()) {
        result_cache_.put(battle_id, *value);
    }
    return value;
}

// ─── Snapshot ─────────────────────────────────────────────────────

bool CachedBattleDataStore::save_snapshot(const std::string& battle_id,
                                          std::string_view snapshot_json) {
    auto evicted = snapshot_cache_.put(battle_id, std::string(snapshot_json));
    if (evicted.has_value()) {
        write_behind_->save_snapshot(evicted->first, evicted->second);
    }
    return true;
}

std::optional<std::string> CachedBattleDataStore::load_snapshot(
    const std::string& battle_id) {
    auto cached = snapshot_cache_.get(battle_id);
    if (cached != nullptr) {
        return *cached;
    }
    auto value = delegate_->load_snapshot(battle_id);
    if (value.has_value()) {
        snapshot_cache_.put(battle_id, *value);
    }
    return value;
}

void CachedBattleDataStore::flush() {
    replay_cache_.for_each([this](const std::string& id, const std::string& json) {
        write_behind_->save_replay(id, json);
    });
    result_cache_.for_each([this](const std::string& id, const std::string& json) {
        write_behind_->save_result(id, json);
    });
    snapshot_cache_.for_each([this](const std::string& id, const std::string& json) {
        write_behind_->save_snapshot(id, json);
    });
    write_behind_->flush();
}

}  // namespace v2::data
