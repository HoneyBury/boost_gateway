#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <future>
#include <iostream>
#include <memory>
#include <mutex>
#include <numeric>
#include <string>
#include <thread>
#include <vector>

#include <nlohmann/json.hpp>
#include <spdlog/spdlog.h>

#include "v2/service/backend_connection.h"
#include "v2/service/backend_server.h"

#ifdef BOOST_BUILD_GRPC
#include <grpcpp/grpcpp.h>

#include "gateway.grpc.pb.h"
#include "v2/grpc/gateway_grpc_server.h"
#endif

namespace {

using Clock = std::chrono::steady_clock;

constexpr std::uint16_t kPayloadSize = 128;
constexpr int kConcurrencyLevels[] = {1, 10, 100, 1000};
constexpr int kBenchmarkIterations = 100;

struct TimestampedResult {
    std::int64_t latency_us = 0;
    bool success = false;
};

struct BenchmarkOutput {
    std::string protocol;
    int concurrency = 0;
    double avg_latency_us = 0.0;
    std::int64_t p99_latency_us = 0;
    double throughput_req_s = 0.0;
    int success_count = 0;
    int failure_count = 0;
};

std::int64_t elapsed_us(const Clock::time_point& start, const Clock::time_point& end) {
    return std::chrono::duration_cast<std::chrono::microseconds>(end - start).count();
}

std::int64_t compute_p99(std::vector<std::int64_t>& latencies) {
    if (latencies.empty()) return 0;
    std::sort(latencies.begin(), latencies.end());
    const size_t idx = static_cast<size_t>(
        std::ceil(0.99 * static_cast<double>(latencies.size())) - 1);
    return latencies[std::min(idx, latencies.size() - 1)];
}

std::string make_token_payload(int worker_id, int iteration) {
    std::string payload = "token_" + std::to_string(worker_id) + "_" + std::to_string(iteration);
    if (payload.size() < kPayloadSize) {
        payload.append(kPayloadSize - payload.size(), 'x');
    }
    return payload;
}

std::string make_user_id(int worker_id) {
    return "perf_user_" + std::to_string(worker_id);
}

v2::service::BackendServer make_login_backend() {
    v2::service::BackendServer::HandlerMap handlers;
    handlers["login_request"] = [](const v2::service::BackendEnvelope& req) {
        auto doc = nlohmann::json::parse(req.payload, nullptr, false);
        v2::service::BackendEnvelope resp;
        if (doc.is_discarded() || !doc.contains("user_id") || !doc.contains("token")) {
            resp.kind = v2::service::MessageKind::kError;
            resp.error_code = -1004;
            resp.payload = R"({"status":"error","reason":"invalid_json"})";
            return resp;
        }

        resp.kind = v2::service::MessageKind::kResponse;
        resp.payload = nlohmann::json{
            {"status", "ok"},
            {"user_id", doc.value("user_id", "")},
            {"display_name", doc.value("display_name", doc.value("user_id", ""))},
            {"role", "player"},
        }.dump();
        return resp;
    };
    return v2::service::BackendServer(0, std::move(handlers));
}

void run_tcp_benchmark(std::uint16_t port,
                       int concurrency,
                       std::vector<TimestampedResult>& results) {
    const int iterations_per_worker =
        std::max(1, kBenchmarkIterations / std::max(1, concurrency));

    results.clear();
    results.reserve(static_cast<size_t>(concurrency * iterations_per_worker));

    std::mutex results_mutex;
    std::atomic<int> ready{0};
    std::atomic<bool> start{false};
    std::vector<std::thread> workers;
    workers.reserve(static_cast<size_t>(concurrency));

    for (int worker = 0; worker < concurrency; ++worker) {
        workers.emplace_back([&, worker]() {
            v2::service::BackendConnection conn(v2::service::BackendConnectionOptions{
                .host = "127.0.0.1",
                .port = port,
                .timeout = std::chrono::milliseconds(3000),
                .connect_timeout = std::chrono::milliseconds(1000),
                .tls_enabled = false,
            });

            std::vector<TimestampedResult> local_results;
            local_results.reserve(static_cast<size_t>(iterations_per_worker));

            const bool connected = conn.connect();
            ready.fetch_add(1, std::memory_order_release);
            while (!start.load(std::memory_order_acquire)) {
                std::this_thread::yield();
            }

            if (!connected) {
                for (int i = 0; i < iterations_per_worker; ++i) {
                    local_results.push_back({0, false});
                }
            } else {
                for (int i = 0; i < iterations_per_worker; ++i) {
                    const auto start_tp = Clock::now();
                    v2::service::BackendEnvelope req;
                    req.correlation_id = v2::service::generate_correlation_id();
                    req.target_service = v2::service::ServiceId::kLogin;
                    req.kind = v2::service::MessageKind::kRequest;
                    req.message_type = "login_request";
                    req.payload = nlohmann::json{
                        {"user_id", make_user_id(worker)},
                        {"token", make_token_payload(worker, i)},
                        {"display_name", make_user_id(worker)},
                    }.dump();
                    auto resp = conn.send_request(req);
                    const auto end_tp = Clock::now();
                    local_results.push_back(
                        {elapsed_us(start_tp, end_tp),
                         resp.has_value() && resp->kind == v2::service::MessageKind::kResponse});
                }
            }

            std::scoped_lock lock(results_mutex);
            results.insert(results.end(), local_results.begin(), local_results.end());
        });
    }

    while (ready.load(std::memory_order_acquire) < concurrency) {
        std::this_thread::yield();
    }
    start.store(true, std::memory_order_release);

    for (auto& worker : workers) {
        worker.join();
    }
}

#ifdef BOOST_BUILD_GRPC
class GrpcServerRunner {
public:
    explicit GrpcServerRunner(std::uint16_t port)
        : server_(std::make_unique<v2::grpc::GatewayGrpcServer>(
              port,
              [](const std::string& user_id, const std::string& token, std::string& error) {
                  if (user_id.empty() || token.empty()) {
                      error = "invalid credentials";
                      return false;
                  }
                  return true;
              },
              [](const std::string&, const std::string&) {})) {}

    bool start() {
        if (!server_->start()) {
            return false;
        }
        running_.store(true, std::memory_order_release);
        cq_thread_ = std::thread([this]() {
            auto* cq = server_->completion_queue();
            if (!cq) {
                return;
            }
            server_->seed_completion_queue();
            void* tag = nullptr;
            bool ok = false;
            while (cq->Next(&tag, &ok)) {
                auto* completion_tag =
                    static_cast<v2::grpc::GatewayGrpcServer::CompletionTag*>(tag);
                if (completion_tag != nullptr) {
                    completion_tag->proceed(ok);
                }
            }
        });
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
        return true;
    }

    void stop() {
        running_.store(false, std::memory_order_release);
        if (server_) {
            server_->shutdown();
            if (auto* cq = server_->completion_queue()) {
                cq->Shutdown();
            }
        }
        if (cq_thread_.joinable()) {
            cq_thread_.join();
        }
    }

    ~GrpcServerRunner() { stop(); }

    std::uint16_t port() const {
        return server_ ? server_->port() : 0;
    }

private:
    std::unique_ptr<v2::grpc::GatewayGrpcServer> server_;
    std::thread cq_thread_;
    std::atomic<bool> running_{false};
};

void run_grpc_benchmark(std::uint16_t port,
                        int concurrency,
                        std::vector<TimestampedResult>& results) {
    const int iterations_per_worker =
        std::max(1, kBenchmarkIterations / std::max(1, concurrency));

    results.clear();
    results.reserve(static_cast<size_t>(concurrency * iterations_per_worker));

    std::mutex results_mutex;
    std::atomic<int> ready{0};
    std::atomic<bool> start{false};
    std::vector<std::thread> workers;
    workers.reserve(static_cast<size_t>(concurrency));

    const auto target = "127.0.0.1:" + std::to_string(port);
    for (int worker = 0; worker < concurrency; ++worker) {
        workers.emplace_back([&, worker]() {
            auto channel = grpc::CreateChannel(target, grpc::InsecureChannelCredentials());
            auto stub = boost::gateway::v3::Gateway::NewStub(channel);

            std::vector<TimestampedResult> local_results;
            local_results.reserve(static_cast<size_t>(iterations_per_worker));
            ready.fetch_add(1, std::memory_order_release);
            while (!start.load(std::memory_order_acquire)) {
                std::this_thread::yield();
            }

            for (int i = 0; i < iterations_per_worker; ++i) {
                grpc::ClientContext ctx;
                boost::gateway::v3::LoginRequest req;
                req.set_user_id(make_user_id(worker));
                req.set_token(make_token_payload(worker, i));
                req.set_display_name(make_user_id(worker));

                boost::gateway::v3::LoginResponse resp;
                const auto start_tp = Clock::now();
                const auto status = stub->RequestLogin(&ctx, req, &resp);
                const auto end_tp = Clock::now();

                local_results.push_back(
                    {elapsed_us(start_tp, end_tp),
                     status.ok() && resp.error_code() == 0});
            }

            std::scoped_lock lock(results_mutex);
            results.insert(results.end(), local_results.begin(), local_results.end());
        });
    }

    while (ready.load(std::memory_order_acquire) < concurrency) {
        std::this_thread::yield();
    }
    start.store(true, std::memory_order_release);

    for (auto& worker : workers) {
        worker.join();
    }
}
#endif

BenchmarkOutput run_single_benchmark(const std::string& protocol,
                                     int concurrency,
                                     const std::function<void(int, std::vector<TimestampedResult>&)>& bench_fn) {
    std::vector<TimestampedResult> warmup_results;
    for (int i = 0; i < 3; ++i) {
        bench_fn(std::min(concurrency, 10), warmup_results);
    }

    std::vector<TimestampedResult> results;
    const auto bench_start = Clock::now();
    bench_fn(concurrency, results);
    const auto bench_end = Clock::now();

    std::vector<std::int64_t> latencies;
    int success_count = 0;
    int failure_count = 0;
    for (const auto& result : results) {
        if (result.success) {
            ++success_count;
            latencies.push_back(result.latency_us);
        } else {
            ++failure_count;
        }
    }

    if (latencies.empty()) {
        return {protocol, concurrency, 0.0, 0, 0.0, success_count, failure_count};
    }

    const double avg = static_cast<double>(
        std::accumulate(latencies.begin(), latencies.end(), std::int64_t{0})) /
        static_cast<double>(latencies.size());
    const auto p99 = compute_p99(latencies);
    const auto total_duration_us = std::max<std::int64_t>(1, elapsed_us(bench_start, bench_end));
    const double throughput =
        static_cast<double>(success_count) / (static_cast<double>(total_duration_us) / 1'000'000.0);

    return {protocol, concurrency, avg, p99, throughput, success_count, failure_count};
}

}  // namespace

int main() {
    // Per-request server INFO logs dominate this small local microbenchmark.
    spdlog::set_level(spdlog::level::warn);

    auto tcp_server = make_login_backend();
    tcp_server.start();
    const auto tcp_port = tcp_server.local_port();
    if (tcp_port == 0) {
        std::cerr << "failed to start tcp backend benchmark server" << std::endl;
        return 1;
    }

#ifdef BOOST_BUILD_GRPC
    GrpcServerRunner grpc_server(0);
    if (!grpc_server.start() || grpc_server.port() == 0) {
        std::cerr << "failed to start grpc benchmark server" << std::endl;
        tcp_server.stop();
        return 1;
    }
#endif

    std::cout << "protocol,concurrency,avg_latency_us,p99_latency_us,throughput_req_s,success_count,failure_count"
              << std::endl;

    for (const int concurrency : kConcurrencyLevels) {
        const auto tcp_result = run_single_benchmark(
            "tcp",
            concurrency,
            [tcp_port](int c, std::vector<TimestampedResult>& results) {
                run_tcp_benchmark(tcp_port, c, results);
            });
        std::cout << tcp_result.protocol << ","
                  << tcp_result.concurrency << ","
                  << tcp_result.avg_latency_us << ","
                  << tcp_result.p99_latency_us << ","
                  << tcp_result.throughput_req_s << ","
                  << tcp_result.success_count << ","
                  << tcp_result.failure_count << std::endl;
    }

#ifdef BOOST_BUILD_GRPC
    for (const int concurrency : kConcurrencyLevels) {
        const auto grpc_result = run_single_benchmark(
            "grpc",
            concurrency,
            [&grpc_server](int c, std::vector<TimestampedResult>& results) {
                run_grpc_benchmark(grpc_server.port(), c, results);
            });
        std::cout << grpc_result.protocol << ","
                  << grpc_result.concurrency << ","
                  << grpc_result.avg_latency_us << ","
                  << grpc_result.p99_latency_us << ","
                  << grpc_result.throughput_req_s << ","
                  << grpc_result.success_count << ","
                  << grpc_result.failure_count << std::endl;
    }
    grpc_server.stop();
#else
    std::cerr << "grpc benchmark unavailable: build without BOOST_BUILD_GRPC" << std::endl;
#endif

    tcp_server.stop();
    return 0;
}
