#include "v2/io/mailbox.h"

#include <algorithm>
#include <barrier>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;

std::uint64_t now_ns() {
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count());
}

struct Options {
    std::size_t iterations = 10000;
    std::string output_path;
};

struct LatencyStats {
    std::size_t samples = 0;
    double min_us = 0.0;
    double p50_us = 0.0;
    double p90_us = 0.0;
    double p99_us = 0.0;
    double max_us = 0.0;
    double throughput_ops_per_sec = 0.0;
};

LatencyStats make_stats(std::vector<double> samples, double elapsed_seconds, std::size_t operations) {
    LatencyStats stats;
    stats.samples = samples.size();
    stats.throughput_ops_per_sec = elapsed_seconds > 0.0
        ? static_cast<double>(operations) / elapsed_seconds
        : 0.0;
    if (samples.empty()) {
        return stats;
    }

    std::sort(samples.begin(), samples.end());
    const auto pick = [&samples](double percentile) {
        const auto last_index = static_cast<double>(samples.size() - 1);
        const auto index = static_cast<std::size_t>(std::min(last_index, last_index * percentile));
        return samples[index];
    };
    stats.min_us = samples.front();
    stats.p50_us = pick(0.50);
    stats.p90_us = pick(0.90);
    stats.p99_us = pick(0.99);
    stats.max_us = samples.back();
    return stats;
}

LatencyStats run_mpsc_mailbox_fan_in(std::size_t iterations) {
    constexpr std::size_t kProducers = 4;
    const auto items_per_producer = std::max<std::size_t>(1, iterations / kProducers);
    const auto total_items = items_per_producer * kProducers;
    v2::io::MpscQueue<std::uint64_t> queue(1024);
    std::barrier start(static_cast<std::ptrdiff_t>(kProducers + 1));
    std::vector<std::vector<double>> producer_samples(kProducers);
    std::vector<std::thread> producers;
    producers.reserve(kProducers);

    for (std::size_t producer = 0; producer < kProducers; ++producer) {
        producer_samples[producer].reserve(items_per_producer);
        producers.emplace_back([&, producer]() {
            start.arrive_and_wait();
            for (std::size_t sequence = 0; sequence < items_per_producer; ++sequence) {
                const auto value = (static_cast<std::uint64_t>(producer) << 32U) |
                                   static_cast<std::uint64_t>(sequence);
                const auto op_begin = now_ns();
                while (!queue.try_enqueue(value)) {
                    std::this_thread::yield();
                }
                producer_samples[producer].push_back(
                    static_cast<double>(now_ns() - op_begin) / 1000.0);
            }
        });
    }

    std::vector<std::uint64_t> received_values;
    received_values.reserve(total_items);
    const auto begin = Clock::now();
    start.arrive_and_wait();
    while (received_values.size() < total_items) {
        if (auto value = queue.try_dequeue()) {
            received_values.push_back(*value);
        } else {
            std::this_thread::yield();
        }
    }
    for (auto& producer : producers) {
        producer.join();
    }
    const auto elapsed = std::chrono::duration<double>(Clock::now() - begin).count();

    std::vector<std::size_t> next_sequence(kProducers, 0);
    std::vector<bool> seen(total_items, false);
    for (const auto value : received_values) {
        const auto producer = static_cast<std::size_t>(value >> 32U);
        const auto sequence = static_cast<std::size_t>(value & 0xffffffffULL);
        if (producer >= kProducers || sequence != next_sequence[producer]++) {
            throw std::runtime_error("MPSC mailbox producer FIFO verification failed");
        }
        const auto index = producer * items_per_producer + sequence;
        if (index >= seen.size() || seen[index]) {
            throw std::runtime_error("MPSC mailbox exactly-once verification failed");
        }
        seen[index] = true;
    }
    if (!std::all_of(seen.begin(), seen.end(), [](bool value) { return value; })) {
        throw std::runtime_error("MPSC mailbox delivery verification failed");
    }

    std::vector<double> samples;
    samples.reserve(total_items);
    for (auto& producer : producer_samples) {
        samples.insert(samples.end(), producer.begin(), producer.end());
    }
    return make_stats(std::move(samples), elapsed, received_values.size());
}

Options parse_args(int argc, char** argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        const auto require_value = [&](const char* name) -> std::string {
            if (i + 1 >= argc) {
                throw std::runtime_error(std::string("missing value for ") + name);
            }
            return argv[++i];
        };

        if (arg == "--iterations") {
            options.iterations = static_cast<std::size_t>(std::stoull(require_value("--iterations")));
        } else if (arg == "--output") {
            options.output_path = require_value("--output");
        } else if (arg == "--help") {
            std::cout << "Usage: v2_mailbox_benchmark [--iterations N] [--output path]\n";
            std::exit(0);
        } else {
            throw std::runtime_error("unknown argument: " + arg);
        }
    }
    return options;
}

std::string build_json(const Options& options, const LatencyStats& stats) {
    std::ostringstream out;
    out << "{\n"
        << "  \"tool\": \"v2_mailbox_benchmark\",\n"
        << "  \"iterations\": " << options.iterations << ",\n"
        << "  \"results\": [\n"
        << "    {\n"
        << "      \"name\": \"mpsc_mailbox_four_producer_fan_in\",\n"
        << "      \"samples\": " << stats.samples << ",\n"
        << "      \"min_us\": " << stats.min_us << ",\n"
        << "      \"p50_us\": " << stats.p50_us << ",\n"
        << "      \"p90_us\": " << stats.p90_us << ",\n"
        << "      \"p99_us\": " << stats.p99_us << ",\n"
        << "      \"max_us\": " << stats.max_us << ",\n"
        << "      \"throughput_ops_per_sec\": " << stats.throughput_ops_per_sec << "\n"
        << "    }\n"
        << "  ]\n"
        << "}\n";
    return out.str();
}

}  // namespace

int main(int argc, char** argv) {
    try {
        const auto options = parse_args(argc, argv);
        const auto json = build_json(options, run_mpsc_mailbox_fan_in(options.iterations));
        if (!options.output_path.empty()) {
            std::ofstream output(options.output_path, std::ios::binary);
            if (!output) {
                std::cerr << "failed to open output: " << options.output_path << "\n";
                return 2;
            }
            output << json;
        }
        std::cout << json;
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "v2_mailbox_benchmark failed: " << error.what() << "\n";
        return 1;
    }
}
