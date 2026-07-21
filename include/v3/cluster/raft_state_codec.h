#pragma once

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace v3::cluster {

struct RaftPersistedLogEntry {
    std::uint64_t term = 0;
    std::string command;

    bool operator==(const RaftPersistedLogEntry&) const = default;
};

struct RaftPersistentState {
    std::uint64_t current_term = 0;
    std::optional<std::string> voted_for;
    std::vector<RaftPersistedLogEntry> log;
    std::uint64_t commit_index = 0;
    std::uint64_t last_applied = 0;

    bool operator==(const RaftPersistentState&) const = default;
};

struct RaftStateCodecLimits {
    std::size_t max_state_bytes = 64U * 1024U * 1024U;
    std::size_t max_node_id_bytes = 256U;
    std::size_t max_log_entries = 100000U;
    std::size_t max_command_bytes = 1024U * 1024U;
    std::size_t max_transition_history = 8U;
};

enum class RaftStateErrorCode {
    kIoRead,
    kIoWrite,
    kIoSync,
    kIoRename,
    kStateTooLarge,
    kMalformedJson,
    kSchemaViolation,
    kUnsupportedVersion,
    kIdentityMismatch,
    kChecksumMismatch,
    kInvariantViolation,
    kMigrationConflict,
};

class RaftStateException : public std::runtime_error {
  public:
    RaftStateException(RaftStateErrorCode code, std::filesystem::path path, std::string detail);

    [[nodiscard]] RaftStateErrorCode code() const noexcept {
        return code_;
    }
    [[nodiscard]] const std::filesystem::path& path() const noexcept {
        return path_;
    }

  private:
    RaftStateErrorCode code_;
    std::filesystem::path path_;
};

struct RaftStateLoadResult {
    RaftPersistentState state;
    bool migrated_from_v0 = false;
};

struct RaftStateDowngradeResult {
    RaftPersistentState state;
    std::filesystem::path v1_backup_path;
    std::filesystem::path downgrade_record_path;
    std::uint64_t transition_generation = 0;
    bool already_downgraded = false;
};

class RaftStateStore {
  public:
    static constexpr std::uint64_t kCurrentSchemaVersion = 1;

    RaftStateStore(std::filesystem::path state_path, std::string node_id,
                   RaftStateCodecLimits limits = {});

    [[nodiscard]] std::optional<RaftStateLoadResult> load_or_migrate() const;
    void save(const RaftPersistentState& state) const;
    [[nodiscard]] RaftStateDowngradeResult downgrade_to_v0() const;

    [[nodiscard]] const std::filesystem::path& state_path() const noexcept {
        return state_path_;
    }
    [[nodiscard]] std::filesystem::path legacy_backup_path() const;
    [[nodiscard]] std::filesystem::path migration_record_path() const;
    [[nodiscard]] std::filesystem::path v1_backup_path() const;
    [[nodiscard]] std::filesystem::path downgrade_record_path() const;

  private:
    std::filesystem::path state_path_;
    std::string node_id_;
    RaftStateCodecLimits limits_;
};

} // namespace v3::cluster
