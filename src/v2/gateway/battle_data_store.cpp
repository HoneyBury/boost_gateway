#include "v2/gateway/battle_data_store.h"

#include "v2/data/replay_format.h"

#include <nlohmann/json.hpp>

#include <fstream>
#include <utility>

namespace v2::gateway {

JsonFileBattleDataStore::JsonFileBattleDataStore(std::filesystem::path dir)
    : dir_(std::move(dir)),
      replay_dir_(dir_ / "replays"),
      result_dir_(dir_ / "results"),
      snapshot_dir_(dir_ / "snapshots") {
    std::filesystem::create_directories(replay_dir_);
    std::filesystem::create_directories(result_dir_);
    std::filesystem::create_directories(snapshot_dir_);
}

bool JsonFileBattleDataStore::persist(const Runtime::BattleArchive& archive) {
    nlohmann::json scores = nlohmann::json::array();
    for (const auto& score : archive.result.scores) {
        scores.push_back({
            {"user_id", score.user_id},
            {"score", score.score},
        });
    }

    nlohmann::json report{
        {"battle_id", archive.battle_id},
        {"room_id", archive.room_id},
        {"reason", archive.reason},
        {"triggering_user_id", archive.triggering_user_id},
        {"total_frames", archive.total_frames},
        {"participants", archive.participant_user_ids},
        {"winner_user_id", archive.result.winner_user_id.has_value()
                               ? nlohmann::json(*archive.result.winner_user_id)
                               : nlohmann::json(nullptr)},
        {"scores", std::move(scores)},
    };

    return save_result(archive.battle_id, report.dump(2)) &&
           save_replay(archive.battle_id, archive.replay_payload);
}

// ─── Replay ────────────────────────────────────────────────────

bool JsonFileBattleDataStore::save_replay(const std::string& battle_id,
                                          std::string_view replay_json) {
    const auto path = replay_dir_ / (battle_id + ".replay");
    const auto encoded = v2::data::encode_replay(replay_json);

    std::ofstream output(path, std::ios::binary);
    if (!output.is_open()) {
        return false;
    }
    output.write(encoded.data(), static_cast<std::streamsize>(encoded.size()));
    output.flush();
    return output.good();
}

std::optional<std::string> JsonFileBattleDataStore::load_replay(const std::string& battle_id) {
    const auto path = replay_dir_ / (battle_id + ".replay");
    std::ifstream input(path, std::ios::binary);
    if (!input.is_open()) {
        return std::nullopt;
    }
    const std::string raw(std::istreambuf_iterator<char>(input), {});

    if (auto decoded = v2::data::decode_replay(raw)) {
        return decoded;
    }

    // Backward compat: if versioned decode fails, return raw (may be old bare JSON).
    if (!raw.empty()) {
        return raw;
    }
    return std::nullopt;
}

// ─── Result ────────────────────────────────────────────────────

bool JsonFileBattleDataStore::save_result(const std::string& battle_id,
                                          std::string_view result_json) {
    const auto path = result_dir_ / (battle_id + ".result");
    const auto encoded = v2::data::encode_result(result_json);

    std::ofstream output(path, std::ios::binary);
    if (!output.is_open()) {
        return false;
    }
    output.write(encoded.data(), static_cast<std::streamsize>(encoded.size()));
    output.flush();
    return output.good();
}

std::optional<std::string> JsonFileBattleDataStore::load_result(const std::string& battle_id) {
    const auto path = result_dir_ / (battle_id + ".result");
    std::ifstream input(path, std::ios::binary);
    if (!input.is_open()) {
        return std::nullopt;
    }
    const std::string raw(std::istreambuf_iterator<char>(input), {});

    if (auto decoded = v2::data::decode_result(raw)) {
        return decoded;
    }
    if (!raw.empty()) {
        return raw;
    }
    return std::nullopt;
}

// ─── Snapshot ──────────────────────────────────────────────────

bool JsonFileBattleDataStore::save_snapshot(const std::string& battle_id,
                                            std::string_view snapshot_json) {
    const auto path = snapshot_dir_ / (battle_id + ".snapshot");
    const auto encoded = v2::data::encode_snapshot(snapshot_json);

    std::ofstream output(path, std::ios::binary);
    if (!output.is_open()) {
        return false;
    }
    output.write(encoded.data(), static_cast<std::streamsize>(encoded.size()));
    output.flush();
    return output.good();
}

std::optional<std::string> JsonFileBattleDataStore::load_snapshot(const std::string& battle_id) {
    const auto path = snapshot_dir_ / (battle_id + ".snapshot");
    std::ifstream input(path, std::ios::binary);
    if (!input.is_open()) {
        return std::nullopt;
    }
    const std::string raw(std::istreambuf_iterator<char>(input), {});

    if (auto decoded = v2::data::decode_snapshot(raw)) {
        return decoded;
    }
    if (!raw.empty()) {
        return raw;
    }
    return std::nullopt;
}

}  // namespace v2::gateway
