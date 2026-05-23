#pragma once

#include <cstdint>
#include <memory>
#include <optional>
#include <string>

#include "v2/data/cached_data_store.h"
#include "v3/cluster/tls_config.h"

namespace v2::battle {

class BattleBackendService {
public:
    explicit BattleBackendService(std::uint16_t port);
    ~BattleBackendService();

    void start();
    void stop();
    [[nodiscard]] std::uint16_t local_port() const;
    void set_tls_config(std::optional<v3::cluster::TlsSessionConfig> tls_config);

    /// Select the instance plugin type used for all battle instances.
    /// Supported values: "battle" (default, uses BattleInstancePlugin).
    /// For "tank_battle", enable BOOST_BUILD_TANK_DEMO and see demo/games/tank_battle/.
    void set_instance_type(const std::string& type);

    /// Set an archive store for persisting battle snapshots and results.
    void set_archive_store(std::unique_ptr<v2::data::CachedBattleDataStore> store);

    /// Convenience: creates a JsonFileBattleDataStore wrapped in a
    /// CachedBattleDataStore at the given path and sets it as the archive store.
    void set_archive_path(const std::string& path);

    /// Set a directory for async replay storage.  Creates a ReplayStorage
    /// at <path>/replays.  Replay frames are automatically saved on battle
    /// finish when this is set.
    void set_replay_storage_dir(const std::string& path);

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace v2::battle
