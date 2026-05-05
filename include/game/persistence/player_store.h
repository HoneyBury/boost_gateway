#pragma once

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <future>
#include <memory>
#include <optional>
#include <string>

#include <nlohmann/json.hpp>

#include <boost/asio/post.hpp>
#include <boost/asio/thread_pool.hpp>

namespace game::persistence {

struct PlayerRecord {
    std::string user_id;
    std::string display_name;
    std::int64_t score = 0;
    std::int64_t last_login_ts = 0;
};

class IPlayerStore {
public:
    virtual ~IPlayerStore() = default;
    virtual std::optional<PlayerRecord> load(const std::string& user_id) = 0;
    virtual bool save(const PlayerRecord& record) = 0;
};

class JsonFilePlayerStore : public IPlayerStore {
public:
    explicit JsonFilePlayerStore(std::filesystem::path dir) : dir_(std::move(dir)) {
        std::filesystem::create_directories(dir_);
    }

    std::optional<PlayerRecord> load(const std::string& user_id) override {
        const auto path = dir_ / (user_id + ".json");
        std::ifstream input(path);
        if (!input.is_open()) return std::nullopt;

        nlohmann::json doc;
        input >> doc;
        return PlayerRecord{
            .user_id = doc.value("user_id", user_id),
            .display_name = doc.value("display_name", ""),
            .score = doc.value("score", 0),
            .last_login_ts = doc.value("last_login_ts", 0),
        };
    }

    bool save(const PlayerRecord& record) override {
        const auto path = dir_ / (record.user_id + ".json");
        nlohmann::json doc{
            {"user_id", record.user_id},
            {"display_name", record.display_name},
            {"score", record.score},
            {"last_login_ts", record.last_login_ts},
        };
        std::ofstream output(path);
        if (!output.is_open()) return false;
        output << doc.dump(2);
        return true;
    }

private:
    std::filesystem::path dir_;
};

class IBattleReplayStore {
public:
    virtual ~IBattleReplayStore() = default;
    virtual bool save_replay(const std::string& battle_id, const std::string& replay_data) = 0;
    virtual std::optional<std::string> load_replay(const std::string& battle_id) = 0;
};

class JsonFileBattleReplayStore : public IBattleReplayStore {
public:
    explicit JsonFileBattleReplayStore(std::filesystem::path dir) : dir_(std::move(dir)) {
        std::filesystem::create_directories(dir_);
    }

    bool save_replay(const std::string& battle_id, const std::string& replay_data) override {
        const auto path = dir_ / (battle_id + ".replay");
        std::ofstream output(path, std::ios::binary);
        if (!output.is_open()) return false;
        output.write(replay_data.data(), replay_data.size());
        return true;
    }

    std::optional<std::string> load_replay(const std::string& battle_id) override {
        const auto path = dir_ / (battle_id + ".replay");
        std::ifstream input(path, std::ios::binary);
        if (!input.is_open()) return std::nullopt;
        return std::string(std::istreambuf_iterator<char>(input), {});
    }

private:
    std::filesystem::path dir_;
};

}  // namespace game::persistence
