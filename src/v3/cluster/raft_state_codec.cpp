#include "v3/cluster/raft_state_codec.h"

#include <nlohmann/json.hpp>
#include <openssl/evp.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <limits>
#include <map>
#include <memory>
#include <sstream>
#include <string_view>
#include <system_error>

#ifdef _WIN32
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace v3::cluster {

namespace {

using Json = nlohmann::json;

constexpr std::size_t kChecksumHexLength = 64U;
constexpr std::size_t kMaxMigrationRecordBytes = 16U * 1024U;

struct TransitionDefinition {
    std::string_view name;
    std::string_view version_key;
    std::uint64_t from_schema_version;
    std::uint64_t to_schema_version;
    std::string_view fixed_backup_suffix;
    std::string_view fixed_record_suffix;
    std::string_view history_backup_marker;
    std::string_view history_record_marker;
};

constexpr TransitionDefinition kMigrationTransition{
    .name = "migration",
    .version_key = "migration_version",
    .from_schema_version = 0U,
    .to_schema_version = 1U,
    .fixed_backup_suffix = ".v0.bak",
    .fixed_record_suffix = ".migration-v0-v1.json",
    .history_backup_marker = ".v0.history.",
    .history_record_marker = ".migration-v0-v1.history.",
};

constexpr TransitionDefinition kDowngradeTransition{
    .name = "downgrade",
    .version_key = "downgrade_version",
    .from_schema_version = 1U,
    .to_schema_version = 0U,
    .fixed_backup_suffix = ".v1.bak",
    .fixed_record_suffix = ".downgrade-v1-v0.json",
    .history_backup_marker = ".v1.history.",
    .history_record_marker = ".downgrade-v1-v0.history.",
};

struct TransitionArtifact {
    std::filesystem::path backup_path;
    std::filesystem::path record_path;
    std::string source_checksum;
    std::string target_checksum;
    std::uint64_t generation = 0;
};

std::atomic<std::uint64_t> next_temp_id{0};

[[noreturn]] void fail(RaftStateErrorCode code, const std::filesystem::path& path,
                       const std::string& detail) {
    throw RaftStateException(code, path, detail);
}

std::filesystem::path parent_directory(const std::filesystem::path& path) {
    auto parent = path.parent_path();
    return parent.empty() ? std::filesystem::path{"."} : parent;
}

bool path_exists(const std::filesystem::path& path) {
    std::error_code ec;
    const auto status = std::filesystem::symlink_status(path, ec);
    if (ec) {
        if (ec == std::errc::no_such_file_or_directory) {
            return false;
        }
        fail(RaftStateErrorCode::kIoRead, path, "cannot inspect path: " + ec.message());
    }
    return status.type() != std::filesystem::file_type::not_found;
}

void ensure_parent_directory(const std::filesystem::path& path) {
    const auto parent = parent_directory(path);
    std::error_code ec;
    std::filesystem::create_directories(parent, ec);
    if (ec) {
        fail(RaftStateErrorCode::kIoWrite, parent,
             "cannot create state directory: " + ec.message());
    }
}

std::string read_bounded_file(const std::filesystem::path& path, std::size_t max_bytes) {
    std::error_code ec;
    const auto status = std::filesystem::symlink_status(path, ec);
    if (ec) {
        fail(RaftStateErrorCode::kIoRead, path, "cannot stat state file: " + ec.message());
    }
    if (!std::filesystem::is_regular_file(status)) {
        fail(RaftStateErrorCode::kIoRead, path, "state path is not a regular file");
    }

    const auto file_size = std::filesystem::file_size(path, ec);
    if (ec) {
        fail(RaftStateErrorCode::kIoRead, path,
             "cannot determine state file size: " + ec.message());
    }
    if (file_size > max_bytes ||
        file_size > static_cast<std::uintmax_t>(std::numeric_limits<std::size_t>::max())) {
        fail(RaftStateErrorCode::kStateTooLarge, path, "state file exceeds configured size limit");
    }

    std::ifstream input(path, std::ios::binary);
    if (!input.is_open()) {
        fail(RaftStateErrorCode::kIoRead, path, "cannot open state file for reading");
    }

    std::string bytes(static_cast<std::size_t>(file_size), '\0');
    if (!bytes.empty()) {
        input.read(bytes.data(), static_cast<std::streamsize>(bytes.size()));
        if (input.gcount() != static_cast<std::streamsize>(bytes.size())) {
            fail(RaftStateErrorCode::kIoRead, path, "state file was truncated while reading");
        }
    }
    if (input.peek() != std::char_traits<char>::eof()) {
        fail(RaftStateErrorCode::kStateTooLarge, path, "state file grew while reading");
    }
    if (input.bad()) {
        fail(RaftStateErrorCode::kIoRead, path, "failed while reading state file");
    }
    return bytes;
}

std::string sha256_hex(std::string_view bytes, const std::filesystem::path& path) {
    using ContextPtr = std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)>;
    ContextPtr context(EVP_MD_CTX_new(), EVP_MD_CTX_free);
    if (!context || EVP_DigestInit_ex(context.get(), EVP_sha256(), nullptr) != 1 ||
        EVP_DigestUpdate(context.get(), bytes.data(), bytes.size()) != 1) {
        fail(RaftStateErrorCode::kChecksumMismatch, path, "cannot initialize SHA-256 checksum");
    }

    std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
    unsigned int digest_size = 0;
    if (EVP_DigestFinal_ex(context.get(), digest.data(), &digest_size) != 1 || digest_size != 32U) {
        fail(RaftStateErrorCode::kChecksumMismatch, path, "cannot finalize SHA-256 checksum");
    }

    std::ostringstream encoded;
    encoded << std::hex << std::setfill('0');
    for (unsigned int index = 0; index < digest_size; ++index) {
        encoded << std::setw(2) << static_cast<unsigned int>(digest[index]);
    }
    return encoded.str();
}

bool is_lower_hex_checksum(const std::string& value) {
    if (value.size() != kChecksumHexLength) {
        return false;
    }
    for (const char ch : value) {
        if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f'))) {
            return false;
        }
    }
    return true;
}

template <std::size_t N>
bool has_exact_keys(const Json& value, const std::array<std::string_view, N>& keys) {
    if (!value.is_object() || value.size() != keys.size()) {
        return false;
    }
    for (const auto key : keys) {
        if (!value.contains(std::string(key))) {
            return false;
        }
    }
    return true;
}

std::uint64_t require_unsigned(const Json& object, std::string_view key,
                               const std::filesystem::path& path) {
    const auto& value = object.at(std::string(key));
    if (!value.is_number_unsigned()) {
        fail(RaftStateErrorCode::kSchemaViolation, path,
             std::string(key) + " must be an unsigned integer");
    }
    return value.get<std::uint64_t>();
}

std::string require_string(const Json& object, std::string_view key,
                           const std::filesystem::path& path) {
    const auto& value = object.at(std::string(key));
    if (!value.is_string()) {
        fail(RaftStateErrorCode::kSchemaViolation, path, std::string(key) + " must be a string");
    }
    return value.get<std::string>();
}

void validate_opaque_node_id(const std::string& node_id, const RaftStateCodecLimits& limits,
                             const std::filesystem::path& path) {
    if (node_id.empty() || node_id.size() > limits.max_node_id_bytes ||
        node_id.find('\0') != std::string::npos) {
        fail(RaftStateErrorCode::kSchemaViolation, path, "node_id is empty or oversized");
    }
}

void validate_storage_node_id(const std::string& node_id, const RaftStateCodecLimits& limits,
                              const std::filesystem::path& path) {
    validate_opaque_node_id(node_id, limits, path);
    if (node_id == "." || node_id == ".." || node_id.find('/') != std::string::npos ||
        node_id.find('\\') != std::string::npos) {
        fail(RaftStateErrorCode::kSchemaViolation, path, "node_id is unsafe for disk identity");
    }
}

void validate_state(const RaftPersistentState& state, const RaftStateCodecLimits& limits,
                    const std::filesystem::path& path) {
    if (state.voted_for.has_value()) {
        validate_opaque_node_id(*state.voted_for, limits, path);
        if (state.current_term == 0) {
            fail(RaftStateErrorCode::kInvariantViolation, path,
                 "voted_for cannot be set in term zero");
        }
    }
    if (state.log.size() > limits.max_log_entries) {
        fail(RaftStateErrorCode::kStateTooLarge, path, "log entry count exceeds configured limit");
    }
    if (state.commit_index > state.log.size()) {
        fail(RaftStateErrorCode::kInvariantViolation, path, "commit_index exceeds log size");
    }
    if (state.last_applied > state.commit_index) {
        fail(RaftStateErrorCode::kInvariantViolation, path, "last_applied exceeds commit_index");
    }
    for (const auto& entry : state.log) {
        if (entry.term == 0 || entry.term > state.current_term) {
            fail(RaftStateErrorCode::kInvariantViolation, path,
                 "log entry term is zero or exceeds current_term");
        }
        if (entry.command.size() > limits.max_command_bytes) {
            fail(RaftStateErrorCode::kStateTooLarge, path,
                 "log command exceeds configured size limit");
        }
    }
}

Json state_body(const RaftPersistentState& state, const std::string& node_id) {
    Json log = Json::array();
    for (const auto& entry : state.log) {
        log.push_back(Json{{"term", entry.term}, {"command", entry.command}});
    }
    return Json{
        {"schema_version", RaftStateStore::kCurrentSchemaVersion},
        {"node_id", node_id},
        {"current_term", state.current_term},
        {"voted_for", state.voted_for.has_value() ? Json(*state.voted_for) : Json(nullptr)},
        {"commit_index", state.commit_index},
        {"last_applied", state.last_applied},
        {"log", std::move(log)},
    };
}

std::string encode_v1(const RaftPersistentState& state, const std::string& node_id,
                      const RaftStateCodecLimits& limits, const std::filesystem::path& path) {
    validate_state(state, limits, path);
    try {
        auto document = state_body(state, node_id);
        document["checksum_sha256"] = sha256_hex(document.dump(), path);
        auto bytes = document.dump();
        if (bytes.size() > limits.max_state_bytes) {
            fail(RaftStateErrorCode::kStateTooLarge, path,
                 "encoded state exceeds configured size limit");
        }
        return bytes;
    } catch (const RaftStateException&) {
        throw;
    } catch (const Json::exception& error) {
        fail(RaftStateErrorCode::kSchemaViolation, path,
             "state cannot be encoded as canonical JSON: " + std::string(error.what()));
    }
}

std::string encode_v0(const RaftPersistentState& state, const RaftStateCodecLimits& limits,
                      const std::filesystem::path& path) {
    validate_state(state, limits, path);
    Json log = Json::array();
    for (const auto& entry : state.log) {
        log.push_back(Json{{"term", entry.term}, {"command", entry.command}});
    }
    auto bytes =
        Json{
            {"current_term", state.current_term},
            {"voted_for", state.voted_for.has_value() ? Json(*state.voted_for) : Json(nullptr)},
            {"leader_id", ""},
            {"commit_index", state.commit_index},
            {"last_applied", state.last_applied},
            {"log", std::move(log)},
        }
            .dump();
    if (bytes.size() > limits.max_state_bytes) {
        fail(RaftStateErrorCode::kStateTooLarge, path,
             "encoded legacy state exceeds configured size limit");
    }
    return bytes;
}

Json parse_json(const std::string& bytes, const std::filesystem::path& path) {
    try {
        return Json::parse(bytes);
    } catch (const Json::parse_error& error) {
        fail(RaftStateErrorCode::kMalformedJson, path,
             "malformed or truncated JSON: " + std::string(error.what()));
    }
}

RaftPersistentState decode_state_fields(const Json& document, const RaftStateCodecLimits& limits,
                                        const std::filesystem::path& path) {
    RaftPersistentState state;
    state.current_term = require_unsigned(document, "current_term", path);

    const auto& voted_for = document.at("voted_for");
    if (voted_for.is_null()) {
        state.voted_for.reset();
    } else if (voted_for.is_string()) {
        state.voted_for = voted_for.get<std::string>();
    } else {
        fail(RaftStateErrorCode::kSchemaViolation, path, "voted_for must be null or a string");
    }

    state.commit_index = require_unsigned(document, "commit_index", path);
    state.last_applied = require_unsigned(document, "last_applied", path);

    const auto& log = document.at("log");
    if (!log.is_array()) {
        fail(RaftStateErrorCode::kSchemaViolation, path, "log must be an array");
    }
    if (log.size() > limits.max_log_entries) {
        fail(RaftStateErrorCode::kStateTooLarge, path, "log entry count exceeds configured limit");
    }

    constexpr std::array<std::string_view, 2> entry_keys{"term", "command"};
    state.log.reserve(log.size());
    for (const auto& item : log) {
        if (!has_exact_keys(item, entry_keys)) {
            fail(RaftStateErrorCode::kSchemaViolation, path,
                 "each log entry must contain exactly term and command");
        }
        state.log.push_back(RaftPersistedLogEntry{
            .term = require_unsigned(item, "term", path),
            .command = require_string(item, "command", path),
        });
    }
    validate_state(state, limits, path);
    return state;
}

RaftPersistentState decode_v1(const Json& document, const std::string& expected_node_id,
                              const RaftStateCodecLimits& limits,
                              const std::filesystem::path& path) {
    constexpr std::array<std::string_view, 8> keys{
        "schema_version", "node_id",      "current_term", "voted_for",
        "commit_index",   "last_applied", "log",          "checksum_sha256"};
    if (!has_exact_keys(document, keys)) {
        fail(RaftStateErrorCode::kSchemaViolation, path,
             "v1 state contains missing or unknown fields");
    }
    if (require_unsigned(document, "schema_version", path) !=
        RaftStateStore::kCurrentSchemaVersion) {
        fail(RaftStateErrorCode::kUnsupportedVersion, path,
             "unsupported Raft state schema version");
    }

    const auto node_id = require_string(document, "node_id", path);
    validate_storage_node_id(node_id, limits, path);
    if (node_id != expected_node_id) {
        fail(RaftStateErrorCode::kIdentityMismatch, path,
             "persisted node_id does not match configured node_id");
    }

    const auto checksum = require_string(document, "checksum_sha256", path);
    if (!is_lower_hex_checksum(checksum)) {
        fail(RaftStateErrorCode::kSchemaViolation, path,
             "checksum_sha256 must be 64 lowercase hexadecimal characters");
    }
    auto body = document;
    body.erase("checksum_sha256");
    if (sha256_hex(body.dump(), path) != checksum) {
        fail(RaftStateErrorCode::kChecksumMismatch, path, "Raft state checksum mismatch");
    }
    return decode_state_fields(document, limits, path);
}

RaftPersistentState decode_v0(const Json& document, const RaftStateCodecLimits& limits,
                              const std::filesystem::path& path) {
    constexpr std::array<std::string_view, 6> keys{"current_term", "voted_for",    "leader_id",
                                                   "commit_index", "last_applied", "log"};
    if (!has_exact_keys(document, keys)) {
        fail(RaftStateErrorCode::kSchemaViolation, path,
             "legacy v0 state contains missing or unknown fields");
    }
    (void)require_string(document, "leader_id", path);
    return decode_state_fields(document, limits, path);
}

#ifdef _WIN32

std::string windows_error_message(DWORD error) {
    return std::system_category().message(static_cast<int>(error));
}

std::uint64_t process_id() {
    return static_cast<std::uint64_t>(GetCurrentProcessId());
}

void write_durable_temp(const std::filesystem::path& path, std::string_view bytes) {
    const HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                                    FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        fail(RaftStateErrorCode::kIoWrite, path,
             "cannot create temporary state file: " + windows_error_message(GetLastError()));
    }

    bool success = true;
    std::string detail;
    std::size_t offset = 0;
    while (offset < bytes.size()) {
        const auto remaining = bytes.size() - offset;
        const auto chunk =
            static_cast<DWORD>(std::min<std::size_t>(remaining, std::numeric_limits<DWORD>::max()));
        DWORD written = 0;
        if (!WriteFile(file, bytes.data() + offset, chunk, &written, nullptr) || written == 0) {
            success = false;
            detail = windows_error_message(GetLastError());
            break;
        }
        offset += written;
    }
    if (success && !FlushFileBuffers(file)) {
        success = false;
        detail = windows_error_message(GetLastError());
    }
    if (!CloseHandle(file) && success) {
        success = false;
        detail = windows_error_message(GetLastError());
    }
    if (!success) {
        DeleteFileW(path.c_str());
        fail(RaftStateErrorCode::kIoSync, path,
             "cannot durably write temporary state file: " + detail);
    }
}

bool install_temp(const std::filesystem::path& temporary, const std::filesystem::path& destination,
                  bool replace) {
    const DWORD flags = MOVEFILE_WRITE_THROUGH | (replace ? MOVEFILE_REPLACE_EXISTING : 0U);
    if (MoveFileExW(temporary.c_str(), destination.c_str(), flags)) {
        return true;
    }
    const auto error = GetLastError();
    if (!replace && (error == ERROR_ALREADY_EXISTS || error == ERROR_FILE_EXISTS)) {
        DeleteFileW(temporary.c_str());
        return false;
    }
    DeleteFileW(temporary.c_str());
    fail(RaftStateErrorCode::kIoRename, destination,
         "cannot atomically install state file: " + windows_error_message(error));
}

#else

std::uint64_t process_id() {
    return static_cast<std::uint64_t>(::getpid());
}

void sync_parent_directory(const std::filesystem::path& path) {
    const auto parent = parent_directory(path);
#ifdef O_DIRECTORY
    constexpr int directory_flag = O_DIRECTORY;
#else
    constexpr int directory_flag = 0;
#endif
    const int descriptor = ::open(parent.c_str(), O_RDONLY | directory_flag);
    if (descriptor < 0) {
        fail(RaftStateErrorCode::kIoSync, parent,
             "cannot open state directory for sync: " + std::string(std::strerror(errno)));
    }
    const int sync_result = ::fsync(descriptor);
    const int sync_error = errno;
    const int close_result = ::close(descriptor);
    if (sync_result != 0) {
        fail(RaftStateErrorCode::kIoSync, parent,
             "cannot sync state directory: " + std::string(std::strerror(sync_error)));
    }
    if (close_result != 0) {
        fail(RaftStateErrorCode::kIoSync, parent,
             "cannot close synced state directory: " + std::string(std::strerror(errno)));
    }
}

void write_durable_temp(const std::filesystem::path& path, std::string_view bytes) {
    const int descriptor = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL, S_IRUSR | S_IWUSR);
    if (descriptor < 0) {
        fail(RaftStateErrorCode::kIoWrite, path,
             "cannot create temporary state file: " + std::string(std::strerror(errno)));
    }

    std::size_t offset = 0;
    int failure = 0;
    while (offset < bytes.size()) {
        const auto written = ::write(descriptor, bytes.data() + offset, bytes.size() - offset);
        if (written < 0 && errno == EINTR) {
            continue;
        }
        if (written <= 0) {
            failure = errno == 0 ? EIO : errno;
            break;
        }
        offset += static_cast<std::size_t>(written);
    }
    if (failure == 0 && ::fsync(descriptor) != 0) {
        failure = errno;
    }
    if (::close(descriptor) != 0 && failure == 0) {
        failure = errno;
    }
    if (failure != 0) {
        ::unlink(path.c_str());
        fail(RaftStateErrorCode::kIoSync, path,
             "cannot durably write temporary state file: " + std::string(std::strerror(failure)));
    }
}

bool install_temp(const std::filesystem::path& temporary, const std::filesystem::path& destination,
                  bool replace) {
    if (replace) {
        if (::rename(temporary.c_str(), destination.c_str()) != 0) {
            const int error = errno;
            ::unlink(temporary.c_str());
            fail(RaftStateErrorCode::kIoRename, destination,
                 "cannot atomically replace state file: " + std::string(std::strerror(error)));
        }
        sync_parent_directory(destination);
        return true;
    }

    if (::link(temporary.c_str(), destination.c_str()) != 0) {
        const int error = errno;
        ::unlink(temporary.c_str());
        if (error == EEXIST) {
            return false;
        }
        fail(RaftStateErrorCode::kIoRename, destination,
             "cannot atomically install sidecar file: " + std::string(std::strerror(error)));
    }
    if (::unlink(temporary.c_str()) != 0) {
        fail(RaftStateErrorCode::kIoRename, temporary,
             "cannot remove linked temporary file: " + std::string(std::strerror(errno)));
    }
    sync_parent_directory(destination);
    return true;
}

#endif

std::filesystem::path temporary_path_for(const std::filesystem::path& destination) {
    const auto suffix =
        ".tmp." + std::to_string(process_id()) + "." + std::to_string(next_temp_id.fetch_add(1));
    auto temporary = destination;
    temporary += suffix;
    return temporary;
}

bool durable_write(const std::filesystem::path& destination, std::string_view bytes, bool replace) {
    ensure_parent_directory(destination);
    for (int attempt = 0; attempt < 100; ++attempt) {
        const auto temporary = temporary_path_for(destination);
        try {
            write_durable_temp(temporary, bytes);
            return install_temp(temporary, destination, replace);
        } catch (const RaftStateException& error) {
            if (error.code() != RaftStateErrorCode::kIoWrite || !path_exists(temporary)) {
                throw;
            }
        }
    }
    fail(RaftStateErrorCode::kIoWrite, destination,
         "cannot allocate a unique temporary state file");
}

std::filesystem::path with_suffix(const std::filesystem::path& path, std::string_view suffix) {
    auto result = path;
    result += suffix;
    return result;
}

std::string transition_record(const TransitionDefinition& definition, const std::string& node_id,
                              std::string_view source_bytes, std::string_view target_bytes,
                              std::uint64_t generation, const std::filesystem::path& path) {
    Json record{
        {std::string(definition.version_key), generation == 0 ? 1U : 2U},
        {"from_schema_version", definition.from_schema_version},
        {"to_schema_version", definition.to_schema_version},
        {"node_id", node_id},
        {"source_sha256", sha256_hex(source_bytes, path)},
        {"target_sha256", sha256_hex(target_bytes, path)},
    };
    if (generation != 0) {
        record["generation"] = generation;
    }
    return record.dump();
}

std::string transition_id(const TransitionDefinition& definition, const std::string& node_id,
                          std::string_view source_bytes, std::string_view target_bytes,
                          const std::filesystem::path& path) {
    const auto identity = std::string(definition.name) + "\n" + node_id + "\n" +
                          sha256_hex(source_bytes, path) + "\n" + sha256_hex(target_bytes, path);
    return sha256_hex(identity, path);
}

struct HistoryPaths {
    std::filesystem::path backup_path;
    std::filesystem::path record_path;
};

std::map<std::string, HistoryPaths> list_history_paths(const std::filesystem::path& state_path,
                                                       const TransitionDefinition& definition) {
    std::map<std::string, HistoryPaths> histories;
    const auto base = state_path.filename().string();
    const auto backup_prefix = base + std::string(definition.history_backup_marker);
    const auto record_prefix = base + std::string(definition.history_record_marker);
    const auto directory = parent_directory(state_path);
    std::error_code ec;
    std::filesystem::directory_iterator iterator(directory, ec);
    if (ec) {
        fail(RaftStateErrorCode::kIoRead, directory,
             "cannot inspect transition history: " + ec.message());
    }
    for (const auto& entry : iterator) {
        const auto filename = entry.path().filename().string();
        std::string id;
        bool backup = false;
        if (filename.starts_with(backup_prefix) && filename.ends_with(".bak")) {
            id = filename.substr(backup_prefix.size(), filename.size() - backup_prefix.size() - 4U);
            backup = true;
        } else if (filename.starts_with(record_prefix) && filename.ends_with(".json")) {
            id = filename.substr(record_prefix.size(), filename.size() - record_prefix.size() - 5U);
        } else {
            continue;
        }
        if (!is_lower_hex_checksum(id)) {
            fail(RaftStateErrorCode::kMigrationConflict, entry.path(),
                 "transition history filename has an invalid content id");
        }
        auto& paths = histories[id];
        if (backup) {
            paths.backup_path = entry.path();
        } else {
            paths.record_path = entry.path();
        }
    }
    return histories;
}

TransitionArtifact validate_transition_pair(const TransitionDefinition& definition,
                                            const std::filesystem::path& backup_path,
                                            const std::filesystem::path& record_path,
                                            const std::string& expected_node_id,
                                            const RaftStateCodecLimits& limits, bool history,
                                            std::string_view history_id = {}) {
    const auto backup = read_bounded_file(backup_path, limits.max_state_bytes);
    const auto record_bytes = read_bounded_file(record_path, kMaxMigrationRecordBytes);
    const auto record = parse_json(record_bytes, record_path);
    const std::size_t expected_keys = history ? 7U : 6U;
    if (!record.is_object() || record.size() != expected_keys ||
        !record.contains(std::string(definition.version_key)) ||
        !record.contains("from_schema_version") || !record.contains("to_schema_version") ||
        !record.contains("node_id") || !record.contains("source_sha256") ||
        !record.contains("target_sha256") || (history && !record.contains("generation")) ||
        require_unsigned(record, definition.version_key, record_path) != (history ? 2U : 1U) ||
        require_unsigned(record, "from_schema_version", record_path) !=
            definition.from_schema_version ||
        require_unsigned(record, "to_schema_version", record_path) !=
            definition.to_schema_version ||
        require_string(record, "node_id", record_path) != expected_node_id) {
        fail(RaftStateErrorCode::kMigrationConflict, record_path,
             std::string(definition.name) + " record does not match this node or transition");
    }
    const auto generation = history ? require_unsigned(record, "generation", record_path) : 0U;
    if (history && generation == 0) {
        fail(RaftStateErrorCode::kMigrationConflict, record_path,
             "transition history generation must be positive");
    }
    const auto source_checksum = require_string(record, "source_sha256", record_path);
    const auto target_checksum = require_string(record, "target_sha256", record_path);
    if (!is_lower_hex_checksum(source_checksum) || !is_lower_hex_checksum(target_checksum) ||
        sha256_hex(backup, backup_path) != source_checksum) {
        fail(RaftStateErrorCode::kMigrationConflict, record_path,
             std::string(definition.name) + " backup or record checksum is invalid");
    }
    if (history) {
        const auto expected_id = sha256_hex(std::string(definition.name) + "\n" + expected_node_id +
                                                "\n" + source_checksum + "\n" + target_checksum,
                                            record_path);
        if (history_id != expected_id) {
            fail(RaftStateErrorCode::kMigrationConflict, record_path,
                 "transition history content id does not match its record");
        }
    }
    return TransitionArtifact{
        .backup_path = backup_path,
        .record_path = record_path,
        .source_checksum = source_checksum,
        .target_checksum = target_checksum,
        .generation = generation,
    };
}

std::vector<TransitionArtifact> validate_transition_artifacts(
    const std::filesystem::path& state_path, const TransitionDefinition& definition,
    const std::string& expected_node_id, const RaftStateCodecLimits& limits,
    std::string_view allowed_orphan_backup_id = {}) {
    std::vector<TransitionArtifact> artifacts;
    const auto fixed_backup = with_suffix(state_path, definition.fixed_backup_suffix);
    const auto fixed_record = with_suffix(state_path, definition.fixed_record_suffix);
    const bool fixed_backup_exists = path_exists(fixed_backup);
    const bool fixed_record_exists = path_exists(fixed_record);
    if (fixed_backup_exists != fixed_record_exists) {
        fail(RaftStateErrorCode::kMigrationConflict, fixed_record,
             std::string(definition.name) + " sidecars must form a complete pair");
    }
    if (fixed_backup_exists) {
        artifacts.push_back(validate_transition_pair(definition, fixed_backup, fixed_record,
                                                     expected_node_id, limits, false));
    }
    const auto histories = list_history_paths(state_path, definition);
    if (!histories.empty() && !fixed_backup_exists) {
        fail(RaftStateErrorCode::kMigrationConflict, state_path,
             std::string(definition.name) + " history exists without the fixed first pair");
    }
    std::size_t allowed_orphans = 0;
    for (const auto& [id, paths] : histories) {
        if (paths.backup_path.empty() || paths.record_path.empty()) {
            if (!paths.backup_path.empty() && paths.record_path.empty() &&
                id == allowed_orphan_backup_id) {
                ++allowed_orphans;
                continue;
            }
            const auto& failure_path =
                paths.record_path.empty() ? paths.backup_path : paths.record_path;
            fail(RaftStateErrorCode::kMigrationConflict, failure_path,
                 std::string(definition.name) + " history contains an orphaned sidecar");
        }
        artifacts.push_back(validate_transition_pair(
            definition, paths.backup_path, paths.record_path, expected_node_id, limits, true, id));
    }
    if (artifacts.size() + allowed_orphans > limits.max_transition_history) {
        fail(RaftStateErrorCode::kMigrationConflict, state_path,
             std::string(definition.name) + " history exceeds the configured bound");
    }
    std::vector<std::uint64_t> generations;
    for (const auto& artifact : artifacts) {
        if (artifact.generation != 0) {
            generations.push_back(artifact.generation);
        }
    }
    std::sort(generations.begin(), generations.end());
    for (std::size_t index = 0; index < generations.size(); ++index) {
        if (generations[index] != index + 1U) {
            fail(RaftStateErrorCode::kMigrationConflict, state_path,
                 std::string(definition.name) +
                     " history generations must be unique and contiguous");
        }
    }
    return artifacts;
}

TransitionArtifact write_transition_artifacts(const std::filesystem::path& state_path,
                                              const TransitionDefinition& definition,
                                              const std::string& node_id,
                                              std::string_view source_bytes,
                                              std::string_view target_bytes,
                                              const RaftStateCodecLimits& limits) {
    const auto source_checksum = sha256_hex(source_bytes, state_path);
    const auto target_checksum = sha256_hex(target_bytes, state_path);
    const auto id = transition_id(definition, node_id, source_bytes, target_bytes, state_path);
    const auto fixed_backup = with_suffix(state_path, definition.fixed_backup_suffix);
    const auto fixed_record = with_suffix(state_path, definition.fixed_record_suffix);

    const bool fixed_backup_exists = path_exists(fixed_backup);
    const bool fixed_record_exists = path_exists(fixed_record);
    if (fixed_backup_exists && !fixed_record_exists) {
        if (read_bounded_file(fixed_backup, limits.max_state_bytes) != source_bytes) {
            fail(RaftStateErrorCode::kMigrationConflict, fixed_backup,
                 std::string(definition.name) + " has a conflicting incomplete backup");
        }
        const auto record =
            transition_record(definition, node_id, source_bytes, target_bytes, 0, fixed_record);
        (void)durable_write(fixed_record, record, false);
    } else if (!fixed_backup_exists && fixed_record_exists) {
        fail(RaftStateErrorCode::kMigrationConflict, fixed_record,
             std::string(definition.name) + " record exists without its backup");
    } else if (!fixed_backup_exists) {
        const auto record =
            transition_record(definition, node_id, source_bytes, target_bytes, 0, fixed_record);
        (void)durable_write(fixed_backup, source_bytes, false);
        (void)durable_write(fixed_record, record, false);
    }

    auto artifacts = validate_transition_artifacts(state_path, definition, node_id, limits, id);
    for (const auto& artifact : artifacts) {
        if (artifact.source_checksum == source_checksum &&
            artifact.target_checksum == target_checksum) {
            return artifact;
        }
    }

    auto histories = list_history_paths(state_path, definition);
    const auto history = histories.find(id);
    std::uint64_t generation = 1;
    for (const auto& artifact : artifacts) {
        generation = std::max(generation, artifact.generation + 1U);
    }
    const auto backup_path =
        with_suffix(state_path, std::string(definition.history_backup_marker) + id + ".bak");
    const auto record_path =
        with_suffix(state_path, std::string(definition.history_record_marker) + id + ".json");

    if (history != histories.end() && !history->second.record_path.empty()) {
        fail(RaftStateErrorCode::kMigrationConflict, history->second.record_path,
             std::string(definition.name) + " history record exists without a valid pair");
    }
    if (history != histories.end() && !history->second.backup_path.empty()) {
        if (read_bounded_file(history->second.backup_path, limits.max_state_bytes) !=
            source_bytes) {
            fail(RaftStateErrorCode::kMigrationConflict, history->second.backup_path,
                 std::string(definition.name) + " history backup conflicts with current source");
        }
    } else {
        if (artifacts.size() >= limits.max_transition_history) {
            fail(RaftStateErrorCode::kMigrationConflict, state_path,
                 std::string(definition.name) + " history reached its configured bound");
        }
        (void)durable_write(backup_path, source_bytes, false);
    }
    const auto record =
        transition_record(definition, node_id, source_bytes, target_bytes, generation, record_path);
    (void)durable_write(record_path, record, false);
    return validate_transition_pair(definition, backup_path, record_path, node_id, limits, true,
                                    id);
}

bool has_transition_sidecars(const std::filesystem::path& state_path,
                             const TransitionDefinition& definition) {
    return path_exists(with_suffix(state_path, definition.fixed_backup_suffix)) ||
           path_exists(with_suffix(state_path, definition.fixed_record_suffix)) ||
           !list_history_paths(state_path, definition).empty();
}

RaftStateDowngradeResult verify_downgraded_v0(const Json& document, std::string_view state_bytes,
                                              const std::filesystem::path& state_path,
                                              const std::string& expected_node_id,
                                              const RaftStateCodecLimits& limits) {
    const auto artifacts =
        validate_transition_artifacts(state_path, kDowngradeTransition, expected_node_id, limits);
    if (artifacts.empty()) {
        fail(RaftStateErrorCode::kMigrationConflict, state_path,
             "legacy state was not produced by the supported downgrade tool");
    }
    const auto target_checksum = sha256_hex(state_bytes, state_path);
    for (const auto& artifact : artifacts) {
        if (artifact.target_checksum != target_checksum) {
            continue;
        }
        const auto backup = read_bounded_file(artifact.backup_path, limits.max_state_bytes);
        const auto backup_document = parse_json(backup, artifact.backup_path);
        const auto backup_state =
            decode_v1(backup_document, expected_node_id, limits, artifact.backup_path);
        const auto legacy_state = decode_v0(document, limits, state_path);
        if (backup_state != legacy_state) {
            fail(RaftStateErrorCode::kMigrationConflict, artifact.record_path,
                 "downgraded legacy state is not equivalent to its v1 backup");
        }
        return RaftStateDowngradeResult{
            .state = legacy_state,
            .v1_backup_path = artifact.backup_path,
            .downgrade_record_path = artifact.record_path,
            .transition_generation = artifact.generation,
            .already_downgraded = true,
        };
    }
    fail(RaftStateErrorCode::kMigrationConflict,
         with_suffix(state_path, kDowngradeTransition.fixed_record_suffix),
         "legacy state does not match any supported downgrade record");
}

} // namespace

RaftStateException::RaftStateException(RaftStateErrorCode code, std::filesystem::path path,
                                       std::string detail)
    : std::runtime_error("Raft state error at " + path.string() + ": " + detail), code_(code),
      path_(std::move(path)) {}

RaftStateStore::RaftStateStore(std::filesystem::path state_path, std::string node_id,
                               RaftStateCodecLimits limits)
    : state_path_(std::move(state_path)), node_id_(std::move(node_id)), limits_(limits) {
    if (state_path_.empty()) {
        fail(RaftStateErrorCode::kSchemaViolation, state_path_, "state path cannot be empty");
    }
    if (limits_.max_state_bytes == 0 || limits_.max_node_id_bytes == 0 ||
        limits_.max_log_entries == 0 || limits_.max_command_bytes == 0 ||
        limits_.max_transition_history == 0) {
        fail(RaftStateErrorCode::kSchemaViolation, state_path_, "codec limits must be non-zero");
    }
    validate_storage_node_id(node_id_, limits_, state_path_);
}

std::filesystem::path RaftStateStore::legacy_backup_path() const {
    auto backup = state_path_;
    backup += ".v0.bak";
    return backup;
}

std::filesystem::path RaftStateStore::migration_record_path() const {
    auto record = state_path_;
    record += ".migration-v0-v1.json";
    return record;
}

std::filesystem::path RaftStateStore::v1_backup_path() const {
    auto backup = state_path_;
    backup += ".v1.bak";
    return backup;
}

std::filesystem::path RaftStateStore::downgrade_record_path() const {
    auto record = state_path_;
    record += ".downgrade-v1-v0.json";
    return record;
}

std::optional<RaftStateLoadResult> RaftStateStore::load_or_migrate() const {
    if (!path_exists(state_path_)) {
        if (has_transition_sidecars(state_path_, kMigrationTransition) ||
            has_transition_sidecars(state_path_, kDowngradeTransition)) {
            fail(RaftStateErrorCode::kMigrationConflict, state_path_,
                 "state file is missing while migration sidecars exist");
        }
        return std::nullopt;
    }

    const auto source_bytes = read_bounded_file(state_path_, limits_.max_state_bytes);
    const auto document = parse_json(source_bytes, state_path_);
    if (!document.is_object()) {
        fail(RaftStateErrorCode::kSchemaViolation, state_path_,
             "Raft state root must be an object");
    }

    if (document.contains("schema_version")) {
        const auto version = require_unsigned(document, "schema_version", state_path_);
        if (version != kCurrentSchemaVersion) {
            fail(RaftStateErrorCode::kUnsupportedVersion, state_path_,
                 "unsupported Raft state schema version " + std::to_string(version));
        }
        auto state = decode_v1(document, node_id_, limits_, state_path_);
        (void)validate_transition_artifacts(state_path_, kMigrationTransition, node_id_, limits_);
        (void)validate_transition_artifacts(state_path_, kDowngradeTransition, node_id_, limits_);
        return RaftStateLoadResult{.state = std::move(state), .migrated_from_v0 = false};
    }

    auto state = decode_v0(document, limits_, state_path_);
    const auto target_bytes = encode_v1(state, node_id_, limits_, state_path_);
    (void)validate_transition_artifacts(state_path_, kDowngradeTransition, node_id_, limits_);
    (void)write_transition_artifacts(state_path_, kMigrationTransition, node_id_, source_bytes,
                                     target_bytes, limits_);
    (void)durable_write(state_path_, target_bytes, true);
    return RaftStateLoadResult{.state = std::move(state), .migrated_from_v0 = true};
}

void RaftStateStore::save(const RaftPersistentState& state) const {
    const auto bytes = encode_v1(state, node_id_, limits_, state_path_);
    (void)durable_write(state_path_, bytes, true);
}

RaftStateDowngradeResult RaftStateStore::downgrade_to_v0() const {
    if (!path_exists(state_path_)) {
        fail(RaftStateErrorCode::kIoRead, state_path_, "state file does not exist");
    }
    const auto source_bytes = read_bounded_file(state_path_, limits_.max_state_bytes);
    const auto document = parse_json(source_bytes, state_path_);
    if (!document.is_object()) {
        fail(RaftStateErrorCode::kSchemaViolation, state_path_,
             "Raft state root must be an object");
    }

    if (!document.contains("schema_version")) {
        return verify_downgraded_v0(document, source_bytes, state_path_, node_id_, limits_);
    }
    const auto version = require_unsigned(document, "schema_version", state_path_);
    if (version != kCurrentSchemaVersion) {
        fail(RaftStateErrorCode::kUnsupportedVersion, state_path_,
             "unsupported Raft state schema version " + std::to_string(version));
    }

    const auto state = decode_v1(document, node_id_, limits_, state_path_);
    (void)validate_transition_artifacts(state_path_, kMigrationTransition, node_id_, limits_);
    const auto target_bytes = encode_v0(state, limits_, state_path_);
    const auto artifact = write_transition_artifacts(state_path_, kDowngradeTransition, node_id_,
                                                     source_bytes, target_bytes, limits_);
    (void)durable_write(state_path_, target_bytes, true);
    return RaftStateDowngradeResult{
        .state = state,
        .v1_backup_path = artifact.backup_path,
        .downgrade_record_path = artifact.record_path,
        .transition_generation = artifact.generation,
        .already_downgraded = false,
    };
}

} // namespace v3::cluster
