#include "v3/cluster/raft_state_codec.h"

#include <gtest/gtest.h>

#include <nlohmann/json.hpp>
#include <openssl/evp.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <optional>
#include <ranges>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

namespace {

using v3::cluster::RaftPersistedLogEntry;
using v3::cluster::RaftPersistentState;
using v3::cluster::RaftStateCodecLimits;
using v3::cluster::RaftStateErrorCode;
using v3::cluster::RaftStateException;
using v3::cluster::RaftStateStore;

class ScopedTempDirectory {
  public:
    ScopedTempDirectory() {
        static std::atomic<std::uint64_t> sequence{0};
        const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
        path_ = std::filesystem::temp_directory_path() /
                ("boost_gateway_raft_codec_" + std::to_string(stamp) + "_" +
                 std::to_string(sequence.fetch_add(1)));
        std::error_code ec;
        if (!std::filesystem::create_directories(path_, ec) || ec) {
            throw std::runtime_error("failed to create Raft codec test directory");
        }
    }

    ~ScopedTempDirectory() {
        std::error_code ec;
        std::filesystem::remove_all(path_, ec);
    }

    ScopedTempDirectory(const ScopedTempDirectory&) = delete;
    ScopedTempDirectory& operator=(const ScopedTempDirectory&) = delete;

    [[nodiscard]] const std::filesystem::path& path() const noexcept {
        return path_;
    }

  private:
    std::filesystem::path path_;
};

std::string read_file(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input.is_open()) {
        throw std::runtime_error("failed to open test input: " + path.string());
    }
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

void write_file(const std::filesystem::path& path, std::string_view contents) {
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    if (!output.is_open()) {
        throw std::runtime_error("failed to open test output: " + path.string());
    }
    output.write(contents.data(), static_cast<std::streamsize>(contents.size()));
    if (!output) {
        throw std::runtime_error("failed to write test output: " + path.string());
    }
}

std::string sha256_hex(std::string_view bytes) {
    std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
    unsigned int digest_size = 0;
    auto* context = EVP_MD_CTX_new();
    if (context == nullptr || EVP_DigestInit_ex(context, EVP_sha256(), nullptr) != 1 ||
        EVP_DigestUpdate(context, bytes.data(), bytes.size()) != 1 ||
        EVP_DigestFinal_ex(context, digest.data(), &digest_size) != 1) {
        EVP_MD_CTX_free(context);
        throw std::runtime_error("failed to calculate test checksum");
    }
    EVP_MD_CTX_free(context);

    std::ostringstream encoded;
    encoded << std::hex << std::setfill('0');
    for (unsigned int index = 0; index < digest_size; ++index) {
        encoded << std::setw(2) << static_cast<unsigned int>(digest[index]);
    }
    return encoded.str();
}

void refresh_checksum(nlohmann::json& document) {
    document.erase("checksum_sha256");
    document["checksum_sha256"] = sha256_hex(document.dump());
}

RaftPersistentState sample_state() {
    return RaftPersistentState{
        .current_term = 7,
        .voted_for = "node-b",
        .log =
            {
                RaftPersistedLogEntry{.term = 3, .command = R"({"op":"one"})"},
                RaftPersistedLogEntry{.term = 7, .command = R"({"op":"two"})"},
            },
        .commit_index = 2,
        .last_applied = 1,
    };
}

nlohmann::json legacy_document() {
    const auto state = sample_state();
    nlohmann::json log = nlohmann::json::array();
    for (const auto& entry : state.log) {
        log.push_back({{"term", entry.term}, {"command", entry.command}});
    }
    return nlohmann::json{
        {"current_term", state.current_term},          {"voted_for", *state.voted_for},
        {"leader_id", "stale-leader-is-not-restored"}, {"commit_index", state.commit_index},
        {"last_applied", state.last_applied},          {"log", std::move(log)},
    };
}

std::vector<std::filesystem::path> files_with_marker(const std::filesystem::path& directory,
                                                     std::string_view marker) {
    std::vector<std::filesystem::path> paths;
    for (const auto& entry : std::filesystem::directory_iterator(directory)) {
        if (entry.path().filename().string().find(marker) != std::string::npos) {
            paths.push_back(entry.path());
        }
    }
    std::ranges::sort(paths);
    return paths;
}

void append_legacy_entry(nlohmann::json& document, std::uint64_t term, std::string command) {
    document["current_term"] = term;
    document["voted_for"] = nullptr;
    document["leader_id"] = "legacy-leader";
    document["log"].push_back({{"term", term}, {"command", std::move(command)}});
    document["commit_index"] = document["log"].size();
    document["last_applied"] = document["log"].size();
}

template <typename Function>
void expect_state_error(Function&& function, RaftStateErrorCode expected_code,
                        const std::filesystem::path& expected_path) {
    try {
        std::forward<Function>(function)();
        FAIL() << "expected RaftStateException";
    } catch (const RaftStateException& error) {
        EXPECT_EQ(error.code(), expected_code);
        EXPECT_EQ(error.path(), expected_path);
    } catch (const std::exception& error) {
        FAIL() << "unexpected exception type: " << error.what();
    }
}

class RaftStateCodecTest : public ::testing::Test {
  protected:
    RaftStateCodecTest()
        : state_path_(temporary_directory_.path() / "node-a.raft.json"),
          store_(state_path_, "node-a") {}

    [[nodiscard]] nlohmann::json saved_document(const RaftPersistentState& state = sample_state()) {
        store_.save(state);
        return nlohmann::json::parse(read_file(state_path_));
    }

    void write_document(const nlohmann::json& document) {
        write_file(state_path_, document.dump(2));
    }

    ScopedTempDirectory temporary_directory_;
    std::filesystem::path state_path_;
    RaftStateStore store_;
};

TEST_F(RaftStateCodecTest, MissingStateIsTheOnlyFreshStartPath) {
    EXPECT_FALSE(store_.load_or_migrate().has_value());
    EXPECT_FALSE(std::filesystem::exists(state_path_));
    EXPECT_FALSE(std::filesystem::exists(store_.legacy_backup_path()));
    EXPECT_FALSE(std::filesystem::exists(store_.migration_record_path()));
}

TEST_F(RaftStateCodecTest, V1RoundTripIsDeterministicAndLeavesNoTemporaryFile) {
    const auto expected = sample_state();
    store_.save(expected);
    const auto first_bytes = read_file(state_path_);

    const auto loaded = store_.load_or_migrate();
    ASSERT_TRUE(loaded.has_value());
    EXPECT_FALSE(loaded->migrated_from_v0);
    EXPECT_EQ(loaded->state, expected);

    store_.save(loaded->state);
    EXPECT_EQ(read_file(state_path_), first_bytes);

    const auto document = nlohmann::json::parse(first_bytes);
    EXPECT_EQ(document.at("schema_version").get<std::uint64_t>(), 1U);
    EXPECT_EQ(document.at("node_id").get<std::string>(), "node-a");
    ASSERT_TRUE(document.at("checksum_sha256").is_string());
    EXPECT_EQ(document.at("checksum_sha256").get_ref<const std::string&>().size(), 64U);

    for (const auto& entry : std::filesystem::directory_iterator(temporary_directory_.path())) {
        EXPECT_EQ(entry.path().filename().string().find(".tmp"), std::string::npos) << entry.path();
    }
}

TEST_F(RaftStateCodecTest, LegacyV0MigratesOnceAndPreservesExactBoundedBackup) {
    const auto legacy_bytes = legacy_document().dump(2);
    write_file(state_path_, legacy_bytes);

    const auto migrated = store_.load_or_migrate();
    ASSERT_TRUE(migrated.has_value());
    EXPECT_TRUE(migrated->migrated_from_v0);
    EXPECT_EQ(migrated->state, sample_state());
    ASSERT_TRUE(std::filesystem::exists(store_.legacy_backup_path()));
    ASSERT_TRUE(std::filesystem::exists(store_.migration_record_path()));
    EXPECT_EQ(read_file(store_.legacy_backup_path()), legacy_bytes);
    const auto migration_record = nlohmann::json::parse(read_file(store_.migration_record_path()));
    EXPECT_EQ(migration_record.at("source_sha256").get<std::string>(), sha256_hex(legacy_bytes));

    const auto backup_bytes = read_file(store_.legacy_backup_path());
    const auto record_bytes = read_file(store_.migration_record_path());
    const auto second_load = store_.load_or_migrate();
    ASSERT_TRUE(second_load.has_value());
    EXPECT_FALSE(second_load->migrated_from_v0);
    EXPECT_EQ(second_load->state, sample_state());
    EXPECT_EQ(read_file(store_.legacy_backup_path()), backup_bytes);
    EXPECT_EQ(read_file(store_.migration_record_path()), record_bytes);

    std::size_t backup_count = 0;
    for (const auto& entry : std::filesystem::directory_iterator(temporary_directory_.path())) {
        if (entry.path().filename().string().ends_with(".v0.bak")) {
            ++backup_count;
        }
    }
    EXPECT_EQ(backup_count, 1U);
}

TEST_F(RaftStateCodecTest, ConflictingLegacyBackupFailsClosed) {
    write_document(legacy_document());
    write_file(store_.legacy_backup_path(), "different legacy state");

    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kMigrationConflict, store_.legacy_backup_path());
}

TEST_F(RaftStateCodecTest, ExistingEmptyOrMalformedStateFailsClosed) {
    write_file(state_path_, "");
    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kMalformedJson, state_path_);

    write_file(state_path_, R"({"current_term":7)");
    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kMalformedJson, state_path_);
}

TEST_F(RaftStateCodecTest, LegacyV0RejectsWrongTypesAndInvalidBounds) {
    auto wrong_type = legacy_document();
    wrong_type["current_term"] = "7";
    write_document(wrong_type);
    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kSchemaViolation, state_path_);

    auto invalid_commit = legacy_document();
    invalid_commit["commit_index"] = 3;
    write_document(invalid_commit);
    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kInvariantViolation, state_path_);
}

TEST_F(RaftStateCodecTest, EmptyObjectIsNotAcceptedAsLegacyState) {
    write_document(nlohmann::json::object());
    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kSchemaViolation, state_path_);
}

TEST_F(RaftStateCodecTest, V1RejectsWrongTypesMissingFieldsAndUnknownFields) {
    const auto valid = saved_document();
    std::vector<nlohmann::json> invalid_documents;

    auto wrong_term = valid;
    wrong_term["current_term"] = "7";
    refresh_checksum(wrong_term);
    invalid_documents.push_back(std::move(wrong_term));

    auto wrong_vote = valid;
    wrong_vote["voted_for"] = 42;
    refresh_checksum(wrong_vote);
    invalid_documents.push_back(std::move(wrong_vote));

    auto wrong_log = valid;
    wrong_log["log"] = nlohmann::json::object();
    refresh_checksum(wrong_log);
    invalid_documents.push_back(std::move(wrong_log));

    auto missing_command = valid;
    missing_command["log"][0].erase("command");
    refresh_checksum(missing_command);
    invalid_documents.push_back(std::move(missing_command));

    auto missing_applied = valid;
    missing_applied.erase("last_applied");
    refresh_checksum(missing_applied);
    invalid_documents.push_back(std::move(missing_applied));

    auto unknown_field = valid;
    unknown_field["unexpected"] = true;
    refresh_checksum(unknown_field);
    invalid_documents.push_back(std::move(unknown_field));

    for (const auto& invalid : invalid_documents) {
        write_document(invalid);
        expect_state_error([this]() { (void)store_.load_or_migrate(); },
                           RaftStateErrorCode::kSchemaViolation, state_path_);
    }
}

TEST_F(RaftStateCodecTest, FutureVersionFailsBeforeLegacyFallback) {
    auto document = saved_document();
    document["schema_version"] = 2;
    refresh_checksum(document);
    write_document(document);

    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kUnsupportedVersion, state_path_);
}

TEST_F(RaftStateCodecTest, WrongSchemaVersionTypeFailsClosed) {
    auto document = saved_document();
    document["schema_version"] = "1";
    refresh_checksum(document);
    write_document(document);

    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kSchemaViolation, state_path_);
}

TEST_F(RaftStateCodecTest, NodeIdentityMismatchFailsWithValidChecksum) {
    auto document = saved_document();
    document["node_id"] = "node-b";
    refresh_checksum(document);
    write_document(document);

    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kIdentityMismatch, state_path_);
}

TEST_F(RaftStateCodecTest, PayloadMutationFailsChecksumValidation) {
    auto document = saved_document();
    document["current_term"] = 8;
    write_document(document);

    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kChecksumMismatch, state_path_);
}

TEST_F(RaftStateCodecTest, MalformedChecksumFieldIsASchemaViolation) {
    auto document = saved_document();
    document["checksum_sha256"] = "ABC123";
    write_document(document);

    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kSchemaViolation, state_path_);
}

TEST_F(RaftStateCodecTest, CommitIndexBeyondLogFailsInvariantValidation) {
    auto document = saved_document();
    document["commit_index"] = 3;
    refresh_checksum(document);
    write_document(document);

    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kInvariantViolation, state_path_);
}

TEST_F(RaftStateCodecTest, LastAppliedBeyondCommitFailsInvariantValidation) {
    auto document = saved_document();
    document["last_applied"] = 2;
    document["commit_index"] = 1;
    refresh_checksum(document);
    write_document(document);

    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kInvariantViolation, state_path_);
}

TEST_F(RaftStateCodecTest, LogTermBeyondCurrentTermFailsInvariantValidation) {
    auto document = saved_document();
    document["log"][0]["term"] = 8;
    refresh_checksum(document);
    write_document(document);

    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kInvariantViolation, state_path_);
}

TEST_F(RaftStateCodecTest, SaveRejectsInvalidBoundsWithoutReplacingValidState) {
    const auto valid = sample_state();
    store_.save(valid);
    const auto valid_bytes = read_file(state_path_);

    auto invalid = valid;
    invalid.commit_index = static_cast<std::uint64_t>(invalid.log.size() + 1);
    expect_state_error([this, &invalid]() { store_.save(invalid); },
                       RaftStateErrorCode::kInvariantViolation, state_path_);

    EXPECT_EQ(read_file(state_path_), valid_bytes);
    const auto loaded = store_.load_or_migrate();
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(loaded->state, valid);
}

TEST_F(RaftStateCodecTest, ConfiguredSizeLimitsFailClosed) {
    const auto oversized_file_path = temporary_directory_.path() / "oversized-file.raft.json";
    write_file(oversized_file_path, "123456789");
    RaftStateStore file_store(oversized_file_path, "node-a",
                              RaftStateCodecLimits{.max_state_bytes = 8,
                                                   .max_node_id_bytes = 256,
                                                   .max_log_entries = 100000,
                                                   .max_command_bytes = 1024});
    expect_state_error([&file_store]() { (void)file_store.load_or_migrate(); },
                       RaftStateErrorCode::kStateTooLarge, oversized_file_path);

    const auto limited_log_path = temporary_directory_.path() / "limited-log.raft.json";
    RaftStateStore log_store(limited_log_path, "node-a",
                             RaftStateCodecLimits{.max_state_bytes = 64U * 1024U,
                                                  .max_node_id_bytes = 256,
                                                  .max_log_entries = 1,
                                                  .max_command_bytes = 1024});
    const auto state = sample_state();
    expect_state_error([&log_store, &state]() { log_store.save(state); },
                       RaftStateErrorCode::kStateTooLarge, limited_log_path);

    const auto limited_command_path = temporary_directory_.path() / "limited-command.raft.json";
    RaftStateStore command_store(limited_command_path, "node-a",
                                 RaftStateCodecLimits{.max_state_bytes = 64U * 1024U,
                                                      .max_node_id_bytes = 256,
                                                      .max_log_entries = 10,
                                                      .max_command_bytes = 4});
    expect_state_error([&command_store, &state]() { command_store.save(state); },
                       RaftStateErrorCode::kStateTooLarge, limited_command_path);
}

TEST_F(RaftStateCodecTest, OrphanedMigrationSidecarFailsClosed) {
    write_file(store_.legacy_backup_path(), legacy_document().dump());

    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kMigrationConflict, state_path_);
}

TEST_F(RaftStateCodecTest, V1RequiresCompleteMigrationSidecarPair) {
    store_.save(sample_state());
    write_file(store_.legacy_backup_path(), legacy_document().dump());

    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kMigrationConflict, store_.migration_record_path());
}

TEST_F(RaftStateCodecTest, DowngradeProducesEquivalentV0WithExactV1BackupAndRecord) {
    store_.save(sample_state());
    const auto v1_bytes = read_file(state_path_);

    const auto result = store_.downgrade_to_v0();
    EXPECT_FALSE(result.already_downgraded);
    EXPECT_EQ(result.state, sample_state());
    EXPECT_EQ(read_file(store_.v1_backup_path()), v1_bytes);

    const auto legacy = nlohmann::json::parse(read_file(state_path_));
    auto expected_legacy = legacy_document();
    expected_legacy["leader_id"] = "";
    EXPECT_EQ(legacy, expected_legacy);
    EXPECT_EQ(legacy.at("leader_id").get<std::string>(), "");
    EXPECT_FALSE(legacy.contains("schema_version"));
    EXPECT_FALSE(legacy.contains("checksum_sha256"));

    const auto record = nlohmann::json::parse(read_file(store_.downgrade_record_path()));
    EXPECT_EQ(record.at("downgrade_version").get<std::uint64_t>(), 1U);
    EXPECT_EQ(record.at("from_schema_version").get<std::uint64_t>(), 1U);
    EXPECT_EQ(record.at("to_schema_version").get<std::uint64_t>(), 0U);
    EXPECT_EQ(record.at("node_id").get<std::string>(), "node-a");
    EXPECT_EQ(record.at("source_sha256").get<std::string>(), sha256_hex(v1_bytes));
    EXPECT_EQ(record.at("target_sha256").get<std::string>(), sha256_hex(read_file(state_path_)));

    const auto repeated = store_.downgrade_to_v0();
    EXPECT_TRUE(repeated.already_downgraded);
    EXPECT_EQ(repeated.state, sample_state());
}

TEST_F(RaftStateCodecTest, DowngradeAcceptsAndPreservesValidMigrationSidecars) {
    const auto legacy_bytes = legacy_document().dump();
    write_file(state_path_, legacy_bytes);
    ASSERT_TRUE(store_.load_or_migrate()->migrated_from_v0);
    const auto original_backup = read_file(store_.legacy_backup_path());
    const auto original_record = read_file(store_.migration_record_path());

    EXPECT_NO_THROW((void)store_.downgrade_to_v0());
    EXPECT_EQ(read_file(store_.legacy_backup_path()), original_backup);
    EXPECT_EQ(read_file(store_.migration_record_path()), original_record);
}

TEST_F(RaftStateCodecTest, DowngradeRejectsCorruptV1WithoutCreatingArtifacts) {
    auto document = saved_document();
    document["current_term"] = 8;
    write_document(document);
    const auto corrupt_bytes = read_file(state_path_);

    expect_state_error([this]() { (void)store_.downgrade_to_v0(); },
                       RaftStateErrorCode::kChecksumMismatch, state_path_);
    EXPECT_EQ(read_file(state_path_), corrupt_bytes);
    EXPECT_FALSE(std::filesystem::exists(store_.v1_backup_path()));
    EXPECT_FALSE(std::filesystem::exists(store_.downgrade_record_path()));
}

TEST_F(RaftStateCodecTest, DowngradeRejectsUntrackedV0AndConflictingBackup) {
    write_document(legacy_document());
    expect_state_error([this]() { (void)store_.downgrade_to_v0(); },
                       RaftStateErrorCode::kMigrationConflict, state_path_);

    store_.save(sample_state());
    write_file(store_.v1_backup_path(), "conflicting backup");
    expect_state_error([this]() { (void)store_.downgrade_to_v0(); },
                       RaftStateErrorCode::kMigrationConflict, store_.v1_backup_path());
}

TEST_F(RaftStateCodecTest, DowngradeIdempotenceRejectsLegacyStateModifiedAfterConversion) {
    store_.save(sample_state());
    (void)store_.downgrade_to_v0();
    auto legacy = nlohmann::json::parse(read_file(state_path_));
    legacy["current_term"] = 8;
    write_document(legacy);

    expect_state_error([this]() { (void)store_.downgrade_to_v0(); },
                       RaftStateErrorCode::kMigrationConflict, store_.downgrade_record_path());
}

TEST_F(RaftStateCodecTest, RepeatedUpgradeRollbackCyclesUseContentAddressedHistory) {
    write_document(legacy_document());
    ASSERT_TRUE(store_.load_or_migrate()->migrated_from_v0);
    const auto first_downgrade = store_.downgrade_to_v0();
    EXPECT_EQ(first_downgrade.v1_backup_path, store_.v1_backup_path());

    std::filesystem::path previous_downgrade_record = store_.downgrade_record_path();
    for (std::uint64_t cycle = 2; cycle <= 4; ++cycle) {
        auto legacy = nlohmann::json::parse(read_file(state_path_));
        append_legacy_entry(legacy, 7U + cycle, "{\"op\":\"cycle-" + std::to_string(cycle) + "\"}");
        write_document(legacy);

        const auto migrated = store_.load_or_migrate();
        ASSERT_TRUE(migrated.has_value());
        EXPECT_TRUE(migrated->migrated_from_v0);
        EXPECT_EQ(migrated->state.commit_index, cycle + 1U);

        const auto downgraded = store_.downgrade_to_v0();
        EXPECT_FALSE(downgraded.already_downgraded);
        EXPECT_NE(downgraded.downgrade_record_path, previous_downgrade_record);
        EXPECT_NE(downgraded.v1_backup_path, store_.v1_backup_path());
        EXPECT_EQ(downgraded.transition_generation, cycle - 1U);
        previous_downgrade_record = downgraded.downgrade_record_path;

        const auto repeated = store_.downgrade_to_v0();
        EXPECT_TRUE(repeated.already_downgraded);
        EXPECT_EQ(repeated.downgrade_record_path, downgraded.downgrade_record_path);

        const auto migration_records =
            files_with_marker(temporary_directory_.path(), ".migration-v0-v1.history.");
        ASSERT_EQ(migration_records.size(), cycle - 1U);
        std::uint64_t maximum_generation = 0;
        for (const auto& path : migration_records) {
            const auto migration_record = nlohmann::json::parse(read_file(path));
            maximum_generation = std::max(maximum_generation,
                                          migration_record.at("generation").get<std::uint64_t>());
        }
        EXPECT_EQ(maximum_generation, cycle - 1U);

        const auto record = nlohmann::json::parse(read_file(downgraded.downgrade_record_path));
        EXPECT_EQ(record.at("downgrade_version").get<std::uint64_t>(), 2U);
        EXPECT_EQ(record.at("generation").get<std::uint64_t>(), cycle - 1U);
    }

    EXPECT_EQ(files_with_marker(temporary_directory_.path(), ".v0.history.").size(), 3U);
    EXPECT_EQ(files_with_marker(temporary_directory_.path(), ".v1.history.").size(), 3U);
}

TEST_F(RaftStateCodecTest, TransitionHistoryBoundFailsBeforeReplacingLegacyState) {
    const auto bounded_path = temporary_directory_.path() / "bounded.raft.json";
    RaftStateStore bounded_store(bounded_path, "node-a",
                                 RaftStateCodecLimits{.max_state_bytes = 64U * 1024U * 1024U,
                                                      .max_node_id_bytes = 256U,
                                                      .max_log_entries = 100000U,
                                                      .max_command_bytes = 1024U * 1024U,
                                                      .max_transition_history = 2U});
    write_file(bounded_path, legacy_document().dump());
    ASSERT_TRUE(bounded_store.load_or_migrate()->migrated_from_v0);
    (void)bounded_store.downgrade_to_v0();

    auto second = nlohmann::json::parse(read_file(bounded_path));
    append_legacy_entry(second, 8U, R"({"op":"second"})");
    write_file(bounded_path, second.dump());
    ASSERT_TRUE(bounded_store.load_or_migrate()->migrated_from_v0);
    (void)bounded_store.downgrade_to_v0();

    auto third = nlohmann::json::parse(read_file(bounded_path));
    append_legacy_entry(third, 9U, R"({"op":"third"})");
    const auto third_bytes = third.dump();
    write_file(bounded_path, third_bytes);
    expect_state_error([&bounded_store]() { (void)bounded_store.load_or_migrate(); },
                       RaftStateErrorCode::kMigrationConflict, bounded_path);
    EXPECT_EQ(read_file(bounded_path), third_bytes);
}

TEST_F(RaftStateCodecTest, InterruptedHistoryBackupIsCompletedOnRetry) {
    write_document(legacy_document());
    ASSERT_TRUE(store_.load_or_migrate()->migrated_from_v0);
    (void)store_.downgrade_to_v0();

    auto legacy = nlohmann::json::parse(read_file(state_path_));
    append_legacy_entry(legacy, 8U, R"({"op":"retry"})");
    const auto retry_source = legacy.dump();
    write_file(state_path_, retry_source);
    ASSERT_TRUE(store_.load_or_migrate()->migrated_from_v0);

    auto history_records =
        files_with_marker(temporary_directory_.path(), ".migration-v0-v1.history.");
    ASSERT_EQ(history_records.size(), 1U);
    const auto record_path = history_records.front();
    ASSERT_TRUE(std::filesystem::remove(record_path));
    write_file(state_path_, retry_source);

    const auto recovered = store_.load_or_migrate();
    ASSERT_TRUE(recovered.has_value());
    EXPECT_TRUE(recovered->migrated_from_v0);
    EXPECT_TRUE(std::filesystem::exists(record_path));
    EXPECT_EQ(recovered->state.commit_index, 3U);
}

TEST_F(RaftStateCodecTest, CorruptTransitionHistoryBlocksLaterMigration) {
    write_document(legacy_document());
    ASSERT_TRUE(store_.load_or_migrate()->migrated_from_v0);
    (void)store_.downgrade_to_v0();

    auto legacy = nlohmann::json::parse(read_file(state_path_));
    append_legacy_entry(legacy, 8U, R"({"op":"history"})");
    write_document(legacy);
    ASSERT_TRUE(store_.load_or_migrate()->migrated_from_v0);
    (void)store_.downgrade_to_v0();

    const auto history_records =
        files_with_marker(temporary_directory_.path(), ".migration-v0-v1.history.");
    ASSERT_EQ(history_records.size(), 1U);
    auto corrupt = nlohmann::json::parse(read_file(history_records.front()));
    corrupt["source_sha256"] = std::string(64U, '0');
    write_file(history_records.front(), corrupt.dump());

    auto next = nlohmann::json::parse(read_file(state_path_));
    append_legacy_entry(next, 9U, R"({"op":"blocked"})");
    write_document(next);
    expect_state_error([this]() { (void)store_.load_or_migrate(); },
                       RaftStateErrorCode::kMigrationConflict, history_records.front());
}

} // namespace
