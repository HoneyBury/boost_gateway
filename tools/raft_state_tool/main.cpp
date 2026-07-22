#include "v3/cluster/raft_state_codec.h"

#include <nlohmann/json.hpp>

#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

struct Options {
    std::filesystem::path state_path;
    std::string node_id;
    std::filesystem::path summary_path;
};

Options parse_options(int argc, char* argv[]) {
    if (argc < 2 || std::string(argv[1]) != "downgrade") {
        throw std::invalid_argument(
            "usage: raft_state_tool downgrade --state PATH --node-id ID [--summary PATH]");
    }
    Options options;
    for (int index = 2; index < argc; ++index) {
        const std::string option = argv[index];
        if (index + 1 >= argc) {
            throw std::invalid_argument("missing value for " + option);
        }
        const std::string value = argv[++index];
        if (option == "--state") {
            options.state_path = value;
        } else if (option == "--node-id") {
            options.node_id = value;
        } else if (option == "--summary") {
            options.summary_path = value;
        } else {
            throw std::invalid_argument("unknown option: " + option);
        }
    }
    if (options.state_path.empty() || options.node_id.empty()) {
        throw std::invalid_argument("--state and --node-id are required");
    }
    return options;
}

void write_summary(const std::filesystem::path& path, const nlohmann::json& summary) {
    if (path.empty()) {
        return;
    }
    if (const auto parent = path.parent_path(); !parent.empty()) {
        std::filesystem::create_directories(parent);
    }
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    if (!output.is_open()) {
        throw std::runtime_error("cannot write summary: " + path.string());
    }
    output << summary.dump(2) << '\n';
    if (!output) {
        throw std::runtime_error("failed while writing summary: " + path.string());
    }
}

} // namespace

int main(int argc, char* argv[]) {
    std::filesystem::path summary_path;
    try {
        const auto options = parse_options(argc, argv);
        summary_path = options.summary_path;
        const v3::cluster::RaftStateStore store(options.state_path, options.node_id);
        const auto result = store.downgrade_to_v0();
        const nlohmann::json summary{
            {"summary_version", 2},
            {"overall_pass", true},
            {"passed", true},
            {"operation", "raft_state_v1_to_v0"},
            {"node_id", options.node_id},
            {"state_path", options.state_path.string()},
            {"v1_backup_path", result.v1_backup_path.string()},
            {"downgrade_record_path", result.downgrade_record_path.string()},
            {"transition_generation", result.transition_generation},
            {"already_downgraded", result.already_downgraded},
            {"current_term", result.state.current_term},
            {"commit_index", result.state.commit_index},
            {"last_applied", result.state.last_applied},
            {"log_entries", result.state.log.size()},
        };
        write_summary(summary_path, summary);
        std::cout << summary.dump() << std::endl;
        return 0;
    } catch (const std::exception& error) {
        const nlohmann::json summary{
            {"summary_version", 2},  {"overall_pass", false},
            {"passed", false},       {"operation", "raft_state_v1_to_v0"},
            {"error", error.what()},
        };
        try {
            write_summary(summary_path, summary);
        } catch (const std::exception&) {
        }
        std::cerr << summary.dump() << std::endl;
        return 1;
    }
}
