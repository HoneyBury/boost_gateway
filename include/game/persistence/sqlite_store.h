#pragma once

#include "game/persistence/player_store.h"

#ifdef HAS_SQLITE
#include <sqlite3.h>
#endif

#include <nlohmann/json.hpp>

#include <filesystem>
#include <memory>
#include <string>

namespace game::persistence {

#ifdef HAS_SQLITE
class SqlitePlayerStore : public IPlayerStore {
public:
    explicit SqlitePlayerStore(const std::filesystem::path& db_path) {
        sqlite3_open(db_path.string().c_str(), &db_);
        sqlite3_exec(db_, "CREATE TABLE IF NOT EXISTS players ("
                          "user_id TEXT PRIMARY KEY, display_name TEXT, "
                          "score INTEGER DEFAULT 0, last_login_ts INTEGER DEFAULT 0)",
                     nullptr, nullptr, nullptr);
    }

    ~SqlitePlayerStore() override { if (db_) sqlite3_close(db_); }

    std::optional<PlayerRecord> load(const std::string& user_id) override {
        auto stmt = prepare("SELECT display_name, score, last_login_ts FROM players WHERE user_id=?");
        if (!stmt) return std::nullopt;
        sqlite3_bind_text(stmt.get(), 1, user_id.c_str(), -1, SQLITE_TRANSIENT);

        std::optional<PlayerRecord> result;
        if (sqlite3_step(stmt.get()) == SQLITE_ROW) {
            result = PlayerRecord{
                .user_id = user_id,
                .display_name = reinterpret_cast<const char*>(sqlite3_column_text(stmt.get(), 0)),
                .score = sqlite3_column_int64(stmt.get(), 1),
                .last_login_ts = sqlite3_column_int64(stmt.get(), 2),
            };
        }
        return result;
    }

    bool save(const PlayerRecord& record) override {
        auto stmt = prepare("INSERT OR REPLACE INTO players VALUES (?,?,?,?)");
        if (!stmt) return false;
        sqlite3_bind_text(stmt.get(), 1, record.user_id.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt.get(), 2, record.display_name.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_int64(stmt.get(), 3, record.score);
        sqlite3_bind_int64(stmt.get(), 4, record.last_login_ts);
        return sqlite3_step(stmt.get()) == SQLITE_DONE;
    }

private:
    struct StmtDeleter { void operator()(sqlite3_stmt* s) { sqlite3_finalize(s); } };
    using StmtPtr = std::unique_ptr<sqlite3_stmt, StmtDeleter>;

    StmtPtr prepare(const char* sql) {
        sqlite3_stmt* stmt = nullptr;
        sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr);
        return StmtPtr(stmt);
    }

    sqlite3* db_ = nullptr;
};
#endif // HAS_SQLITE

}  // namespace game::persistence
